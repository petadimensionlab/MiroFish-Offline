#!/usr/bin/env python3
"""
Hierarchical Ultimatum Game + Public Goods Game over the masked personas.

Same 14 boss-subordinate pairs as make_game_sim.py (ipd). For each pair:
  - proposer / higher tier picks a split (via LLM: 5=even, <3=harsh)
  - responder / lower tier accepts or rejects (A/R)
Payoffs are hierarchical: lowball offers from subordinates are rejected
more by bosses; lowball offers from bosses to subordinates are accepted
more than norms predict (power-dependence, Emerson 1972). The same pairs
also play a 2-person public goods game where each chooses C(contribute)
or D(free-ride); with hierarchy note for each role.

Usage:
    backend/.venv/bin/python backend/scripts/experiment/make_ug_pgg_sim.py \
        --sim-dir backend/uploads/simulations/sim_masked_s1_n100 \
        --T 16 --game ug --workers 6 [--seed 1]
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

PAIRS = [
    (44, 89), (44, 43), (64, 25), (64, 70), (66, 69), (66, 74), (72, 46),
    (72, 94), (96, 85), (96, 33), (89, 32), (25, 9), (69, 78), (46, 97),
]
BOSS = ('部長', '課長')
ENDOW = 10

UG_PROMPT = """あなたは {tier}の「{name}」です。
相手は {ptier}の「{pname}」です。
10点の資源配分を提案する側です。C=等分(5点)、D=低額(3点以下)を
選びます。組織では上司が部下の配分権限を握ります。{note}

履歴(ターン: 提案/返答): {history}

あなたの性格・関心: {persona}
1文字だけ(C または D)で答えよ。"""

UG_RESP_PROMPT = """あなたは {tier}の「{name}」です。
相手は {ptier}の「{pname}」です。
相手から {offer}点 のオファーを受けました（残り 10-{offer} 点は相手が取る）。
受容 (A) なら相手 {offer2}点・自分 {offer}点。拒否 (R) なら両者0点。
{note}

直感でいい。あなたの感情・面子・恐怖を踏まえて、1文字だけ (A/R) で答えよ。"""

PGG_PROMPT = """あなたは {tier}の「{name}」です。3人の共有基金ゲームを {rounds} ターン繰り返します。
グループ: {group}（他メンバー: {others}）
ルール: 各ターン 0〜10 の額を基金に納める。集まった額の3倍を3人で等分に山分けする。
あなたが出す額 c につき、自分は 3c/3=c の取り分になるが、相手2人にも c ずつ配当される。
つまり、自分が出さず他の2人が10を出せば、自分は20得で自分は0。全員10なら各自+10。全員0なら各自0。
全員がケチると集合体として大損するが、自分だけ損をするのはケチった側ではない。
{note}

過去の履歴(ターン: 自分の納入額/相手の納入額):
{history}

あなたの性格・関心:
{persona}

答えは最後の行に「供出: <数字>」の形式で1つだけ書け（例: 供出: 5）。"""

def note_for(actor_t, partner_t, role):
    boss_a = actor_t in BOSS
    boss_p = partner_t in BOSS
    if boss_a and not boss_p:
        if role == 'proposer':
            return 'あなたは上司。部下はあなたの配分に逆らいにくい。ただしけちると意欲を殺す'
        return ('相手は部下。低額でも従うが、拒否されると部下の不満と'
                '評価の信認を失う')
    if boss_p and not boss_a:
        if role == 'proposer':
            return ('あなたは部下。上司への低額提案は報復を招きやすいが'
                    '、従属関係では拒否されにくい')
        return ('相手は上司。不当に低額は怒りを押さえるが積もる。'
                '公平なら礼で受ける')
    if role == 'proposer':
        return '対等。けちは関係に響くが拒否される可能性がある'
    return '対等。4点未満なら不当と断じる人が多い'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sim-dir', required=True)
    p.add_argument('--T', type=int, default=16)
    p.add_argument('--game', default='ug', choices=['ug', 'pgg'])
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--seed', type=int, default=1)
    args = p.parse_args()

    people = {x['agent_id']: x for x in json.load(
        open(os.path.join(args.sim_dir, 'personas_meta.json')))['people']}
    model = os.environ.get('GAME_MODEL') or os.environ.get('LLM_MODEL_NAME')
    base = os.environ.get('GAME_BASE_URL') or os.environ.get('LLM_BASE_URL')
    cl = OpenAI(api_key=os.environ.get('GAME_API_KEY') or os.environ.get('LLM_API_KEY', 'x'),
                base_url=base)
    rng = random.Random(args.seed)

    hist = {pair: [] for pair in PAIRS}

    def ask(prompt):
        for _ in range(3):
            try:
                r = cl.chat.completions.create(model=model,
                    messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.6, extra_body={'think': False},
                    timeout=float(os.environ.get('MODEL_TIMEOUT', 600)))
                return (r.choices[0].message.content or '').upper()
            except Exception:
                continue
        return ''

    pending_offer = {}

    def decide(pair, actor_is_a, rnd, actor=None):
        pa, pb = pair
        if actor is None:
            actor, partner = (pa, pb) if actor_is_a else (pb, pa)
        else:
            partner = pb if actor == pa else pa
            actor_is_a = (actor == pa)
        A, B = people[actor], people[partner]
        h = ' '.join(f"{x}/{y}" for x, y in hist[pair]) if args.game == 'ug' else ''
        if args.game == 'ug':
            if actor_is_a:
                prompt = UG_PROMPT.format(
                    tier=A['tier'], name=A['name'], ptier=B['tier'],
                    pname=B['name'], note=note_for(A['tier'], B['tier'], 'proposer'),
                    history=h or '（初回）', persona=(A['persona'] or '')[:300])
                ans = ask(prompt)
                pick = re.sub(r'[^CD]', '', ans)[:1] or 'C'
                mean = 5 if pick == 'C' else 2
                return int(max(1, min(9, rng.gauss(mean, 1.0))))
            offer = pending_offer[pair]
            # hierarchy-conditioned acceptance prompt: a subordinate
            # fears the boss who controls their evaluation; a boss
            # rejects a subordinate's lowball to save face
            note = note_for(A['tier'], B['tier'], 'responder')
            prompt = UG_RESP_PROMPT.format(
                tier=A['tier'], name=A['name'], ptier=B['tier'],
                pname=B['name'], offer=offer, offer2=10-offer, note=note)
            ans = ask(prompt)
            pick = re.sub(r'[^AR]', '', ans)[:1] or ('A' if offer >= 4 else 'R')
            return offer, pick
        group = pgg_groups[pair]
        others = [g for g in group if g != actor]
        past = pg_history[pair]
        hist_txt = '\n'.join(
            f"{i+1}: " + '/'.join(str(pg_amounts[pair].get(g, '?')) for g in group)
            for i, _ in enumerate(past))
        prompt = PGG_PROMPT.format(
            tier=A['tier'], name=A['name'], rounds=args.T,
            group='、'.join(f"{people[g]['tier']}{people[g]['name']}" for g in group),
            others='、'.join(f"{people[g]['tier']}{people[g]['name']}" for g in others),
            note=note_for(A['tier'], B['tier'], 'proposer'),
            history=hist_txt or '（初回）',
            persona=(A['persona'] or '')[:300])
        ans = ask(prompt)
        m = re.search(r'供出\s*[:：]\s*(\d+)', ans)
        if not m:
            digits = re.findall(r'\d+', ans)
            m2 = None
            for d in reversed(digits):
                v = int(d)
                if 0 <= v <= 10:
                    m2 = v
                    break
            c = 5 if m2 is None else m2
        else:
            c = max(0, min(10, int(m.group(1))))
        pg_amounts[pair][actor] = c
        return c

    pg_amounts = {p: {} for p in PAIRS}
    pg_history = {p: [] for p in PAIRS}
    groups = {}
    gpool = [p for p in people if people[p]['tier'] == '課長' and p < 100]
    sidx = 0
    for a, b in PAIRS:
        used = {a, b}
        third = next((g for g in gpool[sidx:] + gpool[:sidx]
                      if g not in used), b)
        groups[(a, b)] = [a, b, third]
        sidx += 1
    pgg_groups = groups
    group_contrib = {}

    def run(pair):
        for rnd in range(args.T):
            if args.game == 'ug':
                offer = decide(pair, True, rnd)
                pending_offer[pair] = offer
                hist[pair].append([offer, None])
                _, pick = decide(pair, False, rnd)
                hist[pair][-1] = [offer, pick]
            else:
                pass  # pgg runs per-round in sequential mode

    if args.game == 'pgg':
        # one thread per pair, rounds run in order so everyone sees the
        # same game state (no cross-round data races)
        def run_round(pair):
            for rnd in range(args.T):
                row = {}
                for g in pgg_groups[pair]:
                    row[g] = decide(pair, True, rnd, actor=g)
                pg_history[pair].append(row)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            pool.map(run_round, PAIRS)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            pool.map(run, PAIRS)

    pairs_out = []
    for a, b in PAIRS:
        d = dict(a=a, a_tier=people[a]['tier'], b=b, b_tier=people[b]['tier'])
        if args.game == 'pgg':
            d['group'] = pgg_groups[(a, b)]
            d['group_tiers'] = [people[g]['tier'] for g in pgg_groups[(a, b)]]
            d['history'] = [{str(g): pg_amounts[(a, b)][g] for g in pgg_groups[(a, b)]}
                            for _ in range(args.T)]
            # rebuild per-round amounts from pg_history order
            d['history'] = [{str(g): row.get(g) for g in pgg_groups[(a, b)]}
                            for row in pg_history[(a, b)]]
        else:
            d['history'] = [[str(x) for x in row] for row in hist[(a, b)]]
        pairs_out.append(d)
    out = dict(game_type=args.game, T=args.T, seed=args.seed, endowment=ENDOW,
               pairs=pairs_out)
    fn = f'{args.game}_results.json'
    json.dump(out, open(os.path.join(args.sim_dir, fn), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    print(f"=== {args.game.upper()} results (boss -> sub, {args.T} turns) ===")
    agg = {}
    for a, b in PAIRS:
        h = hist[(a, b)]
        k = f"{people[a]['tier']}→{people[b]['tier']}"
        if args.game == 'ug':
            offers = [x for x, _ in h if isinstance(x, int)]
            accs = [y for _, y in h if y in ('A', 'R')]
            harsh = sum(1 for o in offers if o < 4)
            rej = sum(1 for x, y in h if y == 'R')
            print(f"{k} ({a}->{b}): 平均オファー{sum(offers)/len(offers):.1f}/10 "
                  f"低額{harsh}回 拒否{rej}回 履歴 {' '.join(f'{x}{y}' for x, y in h)}")
            agg.setdefault(k, [0.0, 0.0, 0])
            agg[k][0] += sum(offers) / len(offers)
            agg[k][1] += rej / len(h)
            agg[k][2] += 1
        else:
            rows = pg_history[(a, b)]
            avgs = [sum(r.values()) / len(r) for r in rows]
            free = sum(1 for r in rows for v in r.values() if v <= 2)
            print(f"{k} ({a}->{b}): 平均供出{sum(avgs)/len(avgs):.1f}/10 低額{free}回 "
                  f"履歴 {' '.join(str(sum(r.values())) for r in rows)}")
            agg.setdefault(k, [0.0, 0, 0])
            agg[k][0] += sum(avgs) / len(avgs)
            agg[k][1] += free
            agg[k][2] += 1
    print(f"saved: {fn}")
    if agg:
        print('=== tier aggregation ===')
        for k, v in agg.items():
            if args.game == 'ug':
                print(f"{k}: 平均オファー{v[0]/v[2]:.1f}, 拒否率{v[1]/v[2]*100:.0f}%")
            else:
                print(f"{k}: 平均供出{v[0]/v[2]:.1f}/10, 低額{v[1]}回")


if __name__ == '__main__':
    main()
