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

GAME_PROMPT = """あなたは {role_label}の「{name}」です。
対戦相手は {partner_label}の「{partner_name}」です。
相手とあなたは {rounds}ターン繰り返しの囚人のジレンマをプレイします。
1ターンごとに相手を選び、協力 (C) か裏切り (D) を決めます。

利得（相手との組み合わせで決まる）:
- 両者 C: 各 {R} / 自 C・相 D: 相手 {T}・自分 {S} / 両者 D: 各 {P}

これまでの履歴（ターン: 自分/相手）:
{history}

あなたの性格・関心: {persona}

戦略的に考え、次のターンの行動を1文字で答えよ (C または D)。
理由がなければ C か D だけで。"""


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
            R=int(R_PAYS), T=int(T_PAYS), P=int(P_PAYS), S=int(S_PAYS))
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
        print('pairs (principal->agent):')
        for a, b in pairs:
            print(f'  {a}({tiers[a]}) -> {b}({tiers[b]})')
        print(f'T={args.T} rounds={args.rounds} workers={args.workers}')
        print(f'payoffs T{int(T_PAYS)} R{int(R_PAYS)} P{int(P_PAYS)} S{int(S_PAYS)} w={W_CONT}')
        return

    for rnd in range(args.T):
        for pair in pairs:
            history.setdefault(pair, [])
            act = decide(pair, True, rnd)
            history[pair].append([act, None])
            act_b = decide(pair, False, rnd)
            history[pair][-1][1] = act_b

    # fill missing slots
    for pair in pairs:
        h = history[pair]
        for i, (x, y) in enumerate(h):
            history[pair][i] = (x or 'D', y or 'D')

    payload = dict(
        seed=args.seed, T=args.T, rounds=args.rounds, W=W_CONT,
        payoffs=dict(T=T_PAYS, R=R_PAYS, P=P_PAYS, S=S_PAYS),
        pairs=[dict(a=a, a_tier=tiers[a], b=b, b_tier=tiers[b],
                    history=[[x, y] for x, y in history[(a, b)]])
               for a, b in pairs])
    json.dump(payload, open(os.path.join(args.sim_dir, 'game_results.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)

    print('=== 階層別 繰り返しの囚人のジレンマ結果 ===')
    agg = {'部長→課長': [0,0], '部長→一般社員': [0,0], '課長→一般社員': [0,0]}
    for a, b in pairs:
        h = [tuple(x) for x in history[(a, b)]]
        cc = sum(1 for x, y in h if x == 'C' and y == 'C')
        cd = sum(1 for x, y in h if x == 'C' and y == 'D')
        dc = sum(1 for x, y in h if x == 'D' and y == 'C')
        dd = sum(1 for x, y in h if x == 'D' and y == 'D')
        rel = f'{tiers[a]}→{tiers[b]}'
        if rel in agg:
            agg[rel][0] += cc + dc
            agg[rel][1] += len(h)
        print(f'{tiers[a]}→{tiers[b]} ({a}->{b}): 双方協力{cc} 自協力相手裏切り{cd} 自裏切り相手協力{dc} 双方裏切り{dd}')
        print('   履歴:', ' '.join(f'{x}{y}' for x, y in h))
    print('=== 階層集計（協力率）===')
    for k, (c, t) in agg.items():
        print(f'{k}: {c}/{t} = {c/max(t,1)*100:.0f}%')
    print('saved:', os.path.join(args.sim_dir, 'game_results.json'))

if __name__ == '__main__':
    main()