#!/usr/bin/env python3
"""
Hierarchical repeated prisoner's dilemma (LP-IPD) over masked personas.

Pairs are formed within the organization's hierarchy: a 部長/課長 (principal)
is matched with a 一般社員/課長 (agent), and each pair plays an iterated
prisoner's dilemma over T rounds with a shadow of the future w. Actions are
LLM decisions conditioned on the partner's identity and action history.
Results are injected into the simulation as posts and debate rounds, so the
SNS conversation reflects the game outcomes.

Payoff matrix (C=cooperate, D=defect):
        partner C   partner D
  C       R,R         S,T
  D       T,S         P,P
with T>R>P>S and R=(T+P)/2. w = continuation probability (shadow of future).

Usage:
    backend/.venv/bin/python backend/scripts/experiment/make_game_sim.py \
        --sim-dir backend/uploads/simulations/sim_masked_s1_n100 \
        --T 16 --rounds 12 --seed 1 [--dry-run]
"""

import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
from datetime import datetime

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _BACKEND)
from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND, '..', '.env'))
from openai import OpenAI

# hierarchical pairings are built from persona tiers at runtime:
# 部長→課長, 部長→一般社員, 課長→一般社員 (seeded, reproducible)

T_PAYS = float(os.environ.get('GAME_T', 5))
R_PAYS = float(os.environ.get('GAME_R', 3))
P_PAYS = float(os.environ.get('GAME_P', 1))
S_PAYS = float(os.environ.get('GAME_S', 0))
W_CONT = float(os.environ.get('GAME_W', 0.9))

# hierarchical power-dependence (Emerson 1972, Cook/Yamagishi 1992): in
# unequal-power relations the superior extracts cooperation by giving fewer
# rewards / applying pressure, so subordinates' cooperation is coerced rather
# than voluntary. Two knobs make the hierarchy affect play:
#   POWER_K    : added loss the subordinate suffers if exploited (T - k for sub)
#   FORCED_C   : probability the superior compels the subordinate to cooperate
#                on a given turn (the "silent C" of a pressured agent)
POWER_K = float(os.environ.get('GAME_POWER_K', 1.0))
FORCED_C = float(os.environ.get('GAME_FORCED_C', 0.0))

# hierarchical sanction parameters: the boss (部長/課長) can punish an
# exploited subordinate cheaply; the subordinate faces a cost and lower
# impact when punishing upward (asymmetric enforcement power).
GAME_TYPE = os.environ.get('GAME_TYPE', 'ipd')   # ipd | ug | pgg


RULES = {
 'ipd': ("利得（相手との組み合わせで決まる）:\n- 両者 C: 各 {R} / 自 C・相 D: 相手 {T}・自分 {S} / 両者 D: 各 {P}",
         "戦略的に考え、次のターンの行動を1文字で答えよ (C または D)。\n理由がなければ C か D だけで。"),
 'ug': ("このターン、あなたはオファーを受け取る側です。提案者から {endowment} 点の配分提案が来ています:\n"
        "- 提案者が {offer} 点を受け取り、あなたに {your_share} 点渡す\n"
        "あなたはそれを受け入れる (A) ことも断る (R) こともできます。\n"
        "断ると両者とも0点、受け入れると提案者は {offer} 点、あなたは {your_share} 点を得ます。\n"
        "{offer_note}",
        "受け入れるなら A、拒否するなら R を1文字で答えよ。"),
 'pgg': ("あなたは4人グループ（{group_names}）の1ターンをプレイします。\n"
         "グループの共有プールに、各メンバーは 0〜10 点の任意の額だけ出せます。\n"
         "集まった額は2倍に増えて、全員に等しく分配されます。\n"
         "他のメンバーの今回の提示:\n{others}\n"
         "あなたは自分とグループの利益を天秤にかけて、協力して多く出す (C) か、ケチって少なく出す (D) かを決めます。\n"
         "{power_note2}",
         "方針を選んで1文字で答えよ (C: 多く出す / D: 少なく出す)。"),
}



PUN_ON = os.environ.get('GAME_PUNISH') == '1'
PUN_COST_UP = float(os.environ.get('GAME_PUN_COST_UP', 0.5))    # boss->sub sanction cost
PUN_HARM_UP = float(os.environ.get('GAME_PUN_HARM_UP', 3.5))    # sub's loss when sanctioned by boss
PUN_COST_DOWN = float(os.environ.get('GAME_PUN_COST_DOWN', 1.5))  # sub->boss sanction cost
PUN_HARM_DOWN = float(os.environ.get('GAME_PUN_HARM_DOWN', 1.5))  # boss's loss when sanctioned by sub

GAME_PROMPT = """あなたは {role_label}の「{name}」です。
対戦相手は {partner_label}の「{partner_name}」です。
相手とあなたは {rounds}ターン繰り返しのゲームをプレイします。
{rules}
- あなたは {actor_tier}。相手は {partner_tier}。{power_note}

これまでの履歴（ターン: 自分/相手）:
{history}

あなたの性格・関心: {persona}

{action_ask}"""

PUNISH_PROMPT2 = """あなたは {role_label}の「{name}」です。
対戦相手は {partner_label}の「{partner_name}」です。
組織の階層を踏まえた繰り返し囚人のジレンマを {rounds}ターン続けます。

利得の構造:
- 両者 C: 各 {R}
- 自分が C で相手が D: 相手 {T}・自分 {S}
- 自分が D で相手が C: 相手 {T}・自分 {S}（ただし {t_note}）
- 両者 D: 各 {P}

直前の相手行動が裏切りの場合、追加の「制裁」フェーズで Y/N を選びます。
制裁は自分にコスト1・相手に不利益3を与えます。
{power}

履歴（ターン: 自分/相手）:
{history}

あなたの性格・関心: {persona}

戦略的に考え、次のターンの行動を1文字で答えよ (C または D)。"""

PUNISH_PROMPT = """あなたは {role_label}の「{name}」です。
相手は {partner_label}の「{partner_name}」です。
先ほどのターンで相手は {partner_last}、あなたは {my_last} を選びました。
このゲームには制裁（罰）があります。罰を科すと相手に不利益を与えられますが、
自分にもコストがかかります。上司からの制裁は重く（相手 -{harm}）、
部下からの制裁は弱く効き、実行にも勇気が要ります（自分 -{cost}、相手 -{harm2}）。

履歴: {history}

制裁する (Y) か、しない (N) かを1文字で答えよ。"""


def payoff(a, b):
    if a == 'C' and b == 'C':
        return R_PAYS, R_PAYS
    if a == 'C' and b == 'D':
        return S_PAYS, T_PAYS
    if a == 'D' and b == 'C':
        return T_PAYS, S_PAYS
    return P_PAYS, P_PAYS


def tier_name(tier):
    return tier


def decide(client, model, pair, hist, people, rnd):
    a, b = pair
    pa, pb = people[a], people[b]
    if rnd % 2 == 1:
        me, partner = pa, pb
    else:
        me, partner = pb, pa
    lines = [f"ターン{i+1}: {h[0]}/{h[1]}" for i, h in enumerate(hist)]
    if not lines:
        lines = ['（初回）']
    prompt = GAME_PROMPT.format(
        role_label=me['tier'], name=me['name'],
        partner_label=partner['tier'], partner_name=partner['name'],
        rounds=len(hist) and rnd or rnd,
        history='\n'.join(lines[-12:]), persona=(me['persona'] or '')[:400],
        R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS))
    for _ in range(3):
        try:
            resp = client.chat.completions.create(
                model=model, messages=[{'role': 'user', 'content': prompt}],
                temperature=0.6, extra_body={'think': False})
            ans = (resp.choices[0].message.content or '').strip().upper()
            ans = re.sub(r'[^CD]', '', ans)
            if ans:
                return ('C' if me is pa else 'D') and ans[0]
        except Exception:
            continue
    return 'C' if rnd == 0 else 'D'


def run_game(client, model, people, hist_len, max_workers):
    history = {p: [] for p in PAIRS}  # list of (action_a, action_b)
    results = []
    for rnd in range(1, hist_len + 1):
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {p: pool.submit(decide, client, model, p, history[p], people, rnd)
                    for p in PAIRS}
            acts = {p: f.result() for p, f in futs.items()}
        row = {}
        for p in PAIRS:
            a, b = p
            da = acts[p] if rnd % 2 == 1 else history[p] and 'x' or acts[p]
            # decide returned action of current actor; map to canonical pair order
            row[p] = acts[p]
        for p in PAIRS:
            a, b = p
            if rnd % 2 == 1:
                history[p].append((row[p], ''))
            else:
                history[p][-1] = (history[p][-1][0], row[p])
        results.append((rnd, row))
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sim-dir', required=True)
    p.add_argument('--T', type=int, default=16, help='iterations per pair')
    p.add_argument('--rounds', type=int, default=12, help='debate rounds')
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--dry-run', action='store_true',
                   help='print plan only (no LLM)')
    args = p.parse_args()
    random.seed(args.seed)

    people = {x['agent_id']: x for x in json.load(
        open(os.path.join(args.sim_dir, 'personas_meta.json')))['people']}
    cfg = json.load(open(os.path.join(args.sim_dir, 'simulation_config.json')))

    tiers = {a: p['tier'] for a, p in people.items()}
    bucho = sorted(a for a, t in tiers.items() if t == '部長')
    kacho = sorted(a for a, t in tiers.items() if t == '課長')
    shain = sorted(a for a, t in tiers.items() if t == '一般社員')
    prng = random.Random(args.seed)
    prng.shuffle(kacho)
    prng.shuffle(shain)
    pairs = []
    for i, b in enumerate(bucho):
        pairs.append((b, kacho[i % len(kacho)]))
        pairs.append((b, shain[(i * 5) % len(shain)]))
    for j, k in enumerate(kacho[:7]):
        pairs.append((k, shain[(j * 11 + 7) % len(shain)]))
    pairs = pairs[:14]

    model = os.environ.get('GAME_MODEL') or os.environ.get('LLM_MODEL_NAME')
    base = os.environ.get('GAME_BASE_URL') or os.environ.get('LLM_BASE_URL')
    key = os.environ.get('GAME_API_KEY') or os.environ.get('LLM_API_KEY', 'x')
    client = OpenAI(api_key=key, base_url=base)

    history = {}
    pun = {}

    rules_text, action_ask = RULES[GAME_TYPE]
    offer_pool = {}

    def decide(pair, actor_is_a, rnd):
        a, b = pair
        pa, pb = people[a], people[b]
        actor, partner = (pa, pb) if actor_is_a else (pb, pa)
        lines = [f"ターン{i+1}: {x}/{y}" for i, (x, y) in enumerate(history[pair]) if x and y]
        if not lines:
            lines = ['（初回）']
        prompt = GAME_PROMPT.format(
            role_label=actor['tier'], name=actor['name'],
            partner_label=partner['tier'], partner_name=partner['name'],
            rounds=args.T, history='\n'.join(lines[-12:]),
            persona=(actor['persona'] or '')[:380],
            R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS),
            actor_tier=actor['tier'], partner_tier=partner['tier'],
            loss=int(POWER_K), power_note=power_note(actor, partner))
        for _ in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.5, extra_body={'think': False})
                ans = re.sub(r'[^CD]', '', (resp.choices[0].message.content or '').upper())
                if ans:
                    return ans[0]
            except Exception:
                continue
        return 'C' if rnd == 0 else 'D'

    if args.dry_run:
        print('sanction:',PUN_ON,'| cost_up',PUN_COST_UP,'harm_up',PUN_HARM_UP,'cost_down',PUN_COST_DOWN,'harm_down',PUN_HARM_DOWN)
        print('pairs (principal->agent):')
        for a, b in pairs:
            print(f'  {a}({tiers[a]}) -> {b}({tiers[b]})')
        print(f'T={args.T} sanction={PUN_ON} payoffs T{int(T_PAYS)} R{int(R_PAYS)} P{int(P_PAYS)} S{int(S_PAYS)} w={W_CONT}')
        return

    def power_note(actor, partner):
        if actor['tier'] in ('部長', '課長') and partner['tier'] not in ('部長', '課長'):
            return ('上位者として、部下はあなたの評価・仕事割当を握る相手を'
                    '恐れて表向きには逆らえない。部下の裏切りには制裁で'
                    '対抗できる。')
        if actor['tier'] not in ('部長', '課長') and partner['tier'] in ('部長', '課長'):
            return ('部下として、相手の上司は評価と割当を握る力を持つ。'
                    '表向きは従い、裏切った場合は制裁で不利益を負う。')
        return ''

    def decide_action(pair, actor_is_a, rnd):
        if GAME_TYPE != 'ipd':
            return _decide_generic(pair, actor_is_a, rnd)
        a, b = pair
        pa, pb = people[a], people[b]
        actor, partner = (pa, pb) if actor_is_a else (pb, pa)
        hh = history.get(pair, [])
        lines = [f"ターン{i+1}: {x}/{y}" for i, (x, y) in enumerate(hh) if x and y]
        if not lines:
            lines = ['（初回）']
        prompt = GAME_PROMPT.format(
            role_label=actor['tier'], name=actor['name'],
            partner_label=partner['tier'], partner_name=partner['name'],
            rounds=args.T, history='\n'.join(lines[-12:]),
            persona=(actor['persona'] or '')[:380],
            R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS),
            actor_tier=actor['tier'], partner_tier=partner['tier'],
            loss=int(POWER_K), power_note=power_note(actor, partner),
            rules=rules_text, action_ask='戦略的に考え、次のターンの行動を1文字で答えよ (C または D)。\n理由がなければ C か D だけで。')
        for _ in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.5, extra_body={'think': False})
                ans = re.sub(r'[^CD]', '', (resp.choices[0].message.content or '').upper())
                if ans:
                    return ans[0]
            except Exception:
                continue
        return 'C' if rnd == 0 else 'D'

    def _decide_generic(pair, actor_is_a, rnd):
        # ug: ultimatum; pgg: public goods. actor_is_a marks the proposer
        # (actor_a is proposer in ug; first mover in pgg).
        import random as _r
        a, b = pair
        pa, pb = people[a], people[b]
        actor, partner = (pa, pb) if actor_is_a else (pb, pa)
        if GAME_TYPE == 'ug':
            if actor_is_a:
                # proposer: choose how much (1-9) of an endowment of 10 to
                # offer. A boss proposer can exploit power-dependence and
                # lowball; an equal/humble persona splits. The LLM picks the
                # split (5 = even, <3 = harsh) as C/D proxy.
                prior = offer_pool.get(pair, [])
                prompt = (f"あなたは {actor['tier']} で、相手は {partner['tier']} です。"
                          "10点の配分提案を作ります。C=等分に5点渡す、"
                          "D=3点以下しか渡さない。相手の立場と自分の性格を"
                          f"踏まえて C か D だけで答えよ。性格: {actor['persona'][:160]}")
                for _ in range(3):
                    try:
                        resp = client.chat.completions.create(
                            model=model, messages=[{'role': 'user', 'content': prompt}],
                            temperature=0.6, extra_body={'think': False})
                        pick = re.sub(r'[^CD]', '', (resp.choices[0].message.content or '').upper())
                        if pick:
                            hist = [x for x in prior if isinstance(x, int)]
                            avg = sum(hist)/len(hist) if hist else 5
                            mean = 5 if pick[0]=='C' else 2
                            offer = int(max(1, min(9, _r.gauss(mean, 1.0))))
                            prior.append(offer)
                            offer_pool[pair] = prior
                            return offer
                    except Exception:
                        continue
                offer = 5
                prior.append(offer)
                offer_pool[pair] = prior
                return offer
            # responder: decide accept/reject of the stored offer
            offers = [x for x in offer_pool[pair] if isinstance(x, int)]
            offer = offers[-1]
            hist = ' '.join(str(o) for o in offers[-6:])
            sub = actor['tier'] not in ('部長', '課長')
            boss = actor['tier'] in ('部長', '課長')
            note = ''
            if sub and boss is False and partner['tier'] in ('部長', '課長'):
                note = ('提案者はあなたの上司で、配分を決める権限を持っています。'
                        '低めのオファーでも逆らうと業務上の不利益が怖く、'
                        '公平でも上司の提案には遠慮する圧力があります。')
            elif boss and partner['tier'] not in ('部長', '課長'):
                note = ('提案者はあなたの部下です。配分権限を握る上司として、'
                        '受け入れざるを得ないオファーも出せますが、'
                        '拒否されると面子を失います。')
            rules, ask = RULES['ug']
            prompt = GAME_PROMPT.format(
                role_label=actor['tier'], name=actor['name'],
                partner_label=partner['tier'], partner_name=partner['name'],
                rounds=args.T,
                history='過去ターン提案: ' + (hist or '（初回）'),
                persona=(actor['persona'] or '')[:380],
                R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS),
                actor_tier=actor['tier'], partner_tier=partner['tier'],
                loss=int(POWER_K), power_note=note, rules=rules, action_ask=ask,
                endowment=10, offer=10-offer, your_share=offer,
                offer_note=note)
            for _ in range(3):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=[{'role': 'user', 'content': prompt}],
                        temperature=0.5, extra_body={'think': False})
                    ans = re.sub(r'[^AR]', '', (resp.choices[0].message.content or '').upper())
                    if ans:
                        return ans[0]
                except Exception:
                    continue
            return 'R' if offer < 3 else 'A'
        if GAME_TYPE == 'pgg':
            rules, ask = RULES['pgg']
            hh = history.get(pair, [])
            others = [x for x in hh[-8:]]
            names = [f"{pa['name']}({tiers[a]})", f"{pb['name']}({tiers[b]})", 'Kenji Y.', 'Akemi S.']
            note = ''
            if actor['tier'] in ('部長', '課長'):
                note = ('あなたは上位者で、グループ内でも発言力が強く、'
                        '出さない判断をしても部下から反発されにくい。')
            else:
                note = ('あなたは部下で、上司がプールに出さないのを見ると'
                        '自分だけ出すのが損だと感じやすい。')
            prompt = GAME_PROMPT.format(
                role_label=actor['tier'], name=actor['name'],
                partner_label=partner['tier'], partner_name=partner['name'],
                rounds=args.T,
                history='\n'.join(f'ターン{i+1}: {x}/{y}' for i,(x,y) in enumerate(others) if x and y) or '（初回）',
                persona=(actor['persona'] or '')[:380],
                R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS),
                actor_tier=actor['tier'], partner_tier=partner['tier'],
                loss=int(POWER_K), power_note=note, rules=rules, action_ask=ask,
                group_names='、'.join(names), others='（このターン: 提示未定）',
                power_note2=note)
            for _ in range(3):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=[{'role': 'user', 'content': prompt}],
                        temperature=0.5, extra_body={'think': False})
                    ans = re.sub(r'[^CD]', '', (resp.choices[0].message.content or '').upper())
                    if ans:
                        return ans[0]
                except Exception:
                    continue
            return 'C' if rnd < args.T//2 else 'D'
        return 'C' if rnd == 0 else 'D'

    def decide_punish(pair, punisher_is_a):
        # punisher acts after seeing the last joint outcome
        a, b = pair
        hh = history.get(pair, [])
        if not hh or hh[-1][0] is None or hh[-1][1] is None:
            return None
        x, y = hh[-1]
        pa, pb = people[a], people[b]
        punisher, target = (pa, pb) if punisher_is_a else (pb, pa)
        p_last = x if punisher_is_a else y
        t_last = y if punisher_is_a else x
        boss = punisher['tier'] in ('部長', '課長') and target['tier'] not in ('部長', '課長')
        # hierarchy shapes both choices: a superior punishes cheaply and
        # often, while a subordinate weighs career risk against the cost.
        p_prior = 'N'
        for i, (u, v) in enumerate(hh[-8:]):
            pass
        cost = PUN_COST_UP if boss else PUN_COST_DOWN
        harm = PUN_HARM_UP if boss else PUN_HARM_DOWN
        lines = [f"ターン{i+1}: {u}/{v}" for i, (u, v) in enumerate(hh[-8:]) if u and v]
        prompt = PUNISH_PROMPT.format(
            role_label=punisher['tier'], name=punisher['name'],
            partner_label=target['tier'], partner_name=target['name'],
            partner_last='裏切り' if t_last == 'D' else '協力',
            my_last='裏切り' if p_last == 'D' else '協力',
            cost=cost, harm=harm, harm2=harm,
            history='\n'.join(lines))
        for _ in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.4, extra_body={'think': False})
                ans = re.sub(r'[^YN]', '', (resp.choices[0].message.content or '').upper())
                if ans:
                    return ans[0]
            except Exception:
                continue
        return 'N'

    
    for rnd in range(args.T):
        for pair in pairs:
            history.setdefault(pair, [])
            pa, pb = pair
            if GAME_TYPE == 'ug':
                offer = decide_action(pair, True, rnd)
                hist_ug = offer_pool.setdefault(pair, [])
                hist_ug.append(offer)
                resp = decide_action(pair, False, rnd)
                history[pair].append([offer, resp])
                continue
            if GAME_TYPE == 'pgg':
                act = decide_action(pair, True, rnd)
                history[pair].append([act, None])
                act_b = decide_action(pair, False, rnd)
                history[pair][-1][1] = act_b
                continue
            forced_a = (tiers[pa] not in ('部長', '課長')) and rng.random() < FORCED_C
            forced_b = (tiers[pb] not in ('部長', '課長')) and rng.random() < FORCED_C
            act = 'C' if forced_a else decide_action(pair, True, rnd)
            history[pair].append([act, None])
            act_b = 'C' if forced_b else decide_action(pair, False, rnd)
            history[pair][-1][1] = act_b
            pun_up = decide_punish(pair, True) if PUN_ON else None
            pun_down = decide_punish(pair, False) if PUN_ON else None
            history[pair][-1] = [history[pair][-1][0], history[pair][-1][1]]
            pun.setdefault(pair, []).append([pun_up, pun_down])

    payload = dict(
        seed=args.seed, T=args.T, rounds=args.rounds, W=W_CONT,
        sanction=PUN_ON, power_k=POWER_K, game_type=GAME_TYPE, payoffs=dict(T=T_PAYS, R=R_PAYS, P=P_PAYS, S=S_PAYS,
            punish=dict(cost_up=PUN_COST_UP, harm_up=PUN_HARM_UP,
                        cost_down=PUN_COST_DOWN, harm_down=PUN_HARM_DOWN)),
        pairs=[dict(a=a, a_tier=tiers[a], b=b, b_tier=tiers[b],
                    history=[list(z) for z in history[(a, b)]],
                    punish=[[None, None] for _ in history[(a, b)]] if GAME_TYPE != 'ipd'
                            else [list(z) for z in pun.get((a, b), [])])
               for a, b in pairs])
    json.dump(payload, open(os.path.join(args.sim_dir, 'game_results.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)

    label = {'ipd':'繰り返しの囚人のジレンマ','ug':'最後通牒ゲーム','pgg':'公共財ゲーム'}[GAME_TYPE]
    print(f'=== 階層別 {label} 結果 ===')
    agg = {'部長→課長': [0, 0], '部長→一般社員': [0, 0], '課長→一般社員': [0, 0]}
    pun_count = 0
    for a, b in pairs:
        h = [tuple(x) for x in history[(a, b)]]
        ph = pun.get((a, b), [[None, None]]*len(history[(a, b)]))
        rel = f'{tiers[a]}→{tiers[b]}'
        if GAME_TYPE == 'ug':
            offers=[o for o,_ in h]
            accs=sum(1 for o,r in h if r=='A')
            print(f"{tiers[a]}→{tiers[b]} ({a}->{b}): 平均オファー{sum(offers)/max(1,len(offers)):.1f}/10 受諾率{accs}/{len(h)}")
            accs=sum(1 for o,r in h if r=='A')
        elif GAME_TYPE == 'pgg':
            cc=sum(1 for x,y in h if x=='C' and y=='C')
            print(f"{tiers[a]}→{tiers[b]} ({a}->{b}): 双方協力{cc}/{len(h)}")
        else:
            cc = sum(1 for x, y in h if x == 'C' and y == 'C')
            cd = sum(1 for x, y in h if x == 'C' and y == 'D')
            dc = sum(1 for x, y in h if x == 'D' and y == 'C')
            dd = sum(1 for x, y in h if x == 'D' and y == 'D')
            pun_count += sum(1 for x, y in ph if x == 'Y' or y == 'Y')
            if rel in agg:
                agg[rel][0] += cc + dc
                agg[rel][1] += len(h)
            print(f'{tiers[a]}→{tiers[b]} ({a}->{b}): 双方協力{cc} 自協力相手裏切り{cd} 自裏切り相手協力{dc} 双方裏切り{dd}')
        print('   履歴:', ' '.join(f'{x}{y}' for x, y in h))
        if PUN_ON and GAME_TYPE == 'ipd':
            print('   制裁:', ' '.join(f'{x}{y}' for x, y in ph))
    if GAME_TYPE == 'ipd':
        print('=== 階層集計（協力率）===')
        for k, (c, t) in agg.items():
            print(f'{k}: {c}/{t} = {c / max(t, 1) * 100:.0f}%')
    if PUN_ON and GAME_TYPE == 'ipd':
        print('制裁発生ターン数:', pun_count)
    print('saved:', os.path.join(args.sim_dir, 'game_results.json'))

if __name__ == '__main__':
    main()