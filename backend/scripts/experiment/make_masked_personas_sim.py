"""
Create a simulation directory of personas from a masked organizational survey.

Masking: source user ids are never written; only a whitelist of general
work-attitude constructs (see EXCLUDED below) are allowed to shape personas.
Personal identifiability is the only constraint (no real names/ids).

Output: <out_dir>/{twitter_profiles.csv, reddit_profiles.json,
simulation_config.json, personas_meta.json}

Usage:
    backend/.venv/bin/python backend/scripts/experiment/make_masked_personas_sim.py \
        --n 100 --seed 1 --source-dir <anon-dir> --meta-dir <pipeline-root>
"""

import argparse
import concurrent.futures
import csv
import json
import os
import random
import re
import sys
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
load_dotenv(os.path.join(_BACKEND, '..', '.env'))

TIERS = {
    'bucho': ('bucho.csv', '部長', 'senior manager'),
    'kacho': ('kacho.csv', '課長', 'middle manager'),
    'shain': ('shain.csv', '一般社員', 'company employee'),
}
GENDER_MAP = {'男': 'male', '女': 'female'}
AGE_MAP = {'~39': 33, '40代': 45, '50代以上': 55}
TENURE_MAP = {'10年以上': 15, '5-10年': 7, '3-5年': 4, '1-3年': 2, '~1年': 1}

# M&A / 旧個社特有のコンストラクトのみ除外（conf_integrating は除外しない）
EXCLUDED = {
    'comm_legacy_breadth', 'comm_legacy_mikata', 'comm_legacy_other',
    'comm_legacy_oyama', 'comm_legacy_sabuuru', 'comm_legacy_sagami',
    'infl_legacy_mikata', 'infl_legacy_oyama', 'infl_legacy_sabuuru',
    'infl_legacy_sagami', 'org_ident_legacy',
    'dept_prevention_focus', 'dept_promotion_focus',
}

PERSONA_PROMPT = """あなたは SNS の架空プロフィールを作る助手です。
以下の属性を持つ、日本の一般的な会社員の「普段の SNS ユーザー像」を日本語で描いてください。
アンケートや調査・旧社名・企業名・製品・業界のことは書かない。仕事への姿勢は性格や日頃の言動として自然に反映する。

- 年齢: {age}歳
- 性別: {gender}
- 役職区分: {tier}（職業: {occupation}）
- 勤続: 約{tenure}年
- 価値観・行動の傾向（強い面）: {strong}
- 価値観・行動の傾向（弱い面）: {weak}

次の JSON オブジェクトだけを出力してください:
{{"name": "姓名（ローマ字・名 姓の頭文字, 例: Taro S.）", "username": "半角英数字のみのハンドル", "bio": "140字以内の自己紹介", "persona": "3〜5文。普段何をつぶやき、どう話すか"}}"""

SCRUB = re.compile(r'(旧個社|旧所属|大山|相模|さぶーる|見方|ハム|買収|合併|M&A)', re.I)
SURVEY_BANNED = re.compile(r'(アンケート|調査|設問|回答者|尺度|クラスター|回帰)', re.I)

MBTI = [a + b + c + d for a in 'EI' for b in 'SN' for c in 'TF' for d in 'JP']


def load_item_index(meta_root):
    vars_path = os.path.join(meta_root, 'data', 'meta', 'variables.csv')
    if not os.path.exists(vars_path):
        vars_path = os.path.join(meta_root, 'meta', 'variables.csv')
    index = {}
    with open(vars_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            vid = row['variable_id']
            if vid in EXCLUDED:
                continue
            items = [c for c in row['item_codes'].split(';') if c]
            rev = {c for c in row.get('reversed_items', '').split(';') if c}
            index[vid] = (row['name_ja'], items, rev)
    return index


def respondent_fingerprint(row, index):
    scores = []
    for vid, (name, items, rev) in index.items():
        vals = []
        for it in items:
            v = row.get(it, '')
            if v in ('', '*', None):
                continue
            try:
                x = float(v)
            except ValueError:
                continue
            if it in rev:
                x = 6 - x
            vals.append(x)
        if vals:
            scores.append((sum(vals) / len(vals), name))
    if not scores:
        return [], []
    scores.sort(key=lambda t: -t[0])
    return [n for _, n in scores[:3]], [n for _, n in scores[-2:]]


def proportional_counts(avail, n):
    total = sum(avail.values())
    raw = {t: avail[t] / total * n for t in avail}
    base = {t: int(raw[t]) for t in avail}
    rem = n - sum(base.values())
    order = sorted(avail, key=lambda t: raw[t] - base[t], reverse=True)
    i = 0
    while rem > 0:
        t = order[i % len(order)]
        if base[t] < avail[t]:
            base[t] += 1
            rem -= 1
        i += 1
    return base


def select_respondents(src_dir, n, seed):
    pools = {}
    for tier, (fname, label, occ) in TIERS.items():
        path = os.path.join(src_dir, fname)
        if not os.path.exists(path):
            path = os.path.join(src_dir, 'data', 'anon', fname)
        with open(path, newline='', encoding='utf-8-sig') as f:
            pools[tier] = list(csv.DictReader(f))
    counts = proportional_counts({t: len(p) for t, p in pools.items()}, n)
    rng = random.Random(seed)
    chosen = []
    for tier, (fname, label, occ) in TIERS.items():
        pool = pools[tier][:]
        rng.shuffle(pool)
        for row in pool[:counts[tier]]:
            chosen.append((tier, label, occ, row))
    rng.shuffle(chosen)
    return chosen, counts


def map_attributes(tier_label, occ, row, rng):
    gender_raw = row.get('gender', '*')
    gender = GENDER_MAP.get(gender_raw, rng.choice(['male', 'female']))
    age = AGE_MAP.get(row.get('age_band', ''), rng.randint(32, 56))
    tenure = TENURE_MAP.get(row.get('tenure_band', ''), rng.randint(3, 20))
    role = row.get('role_group', '') or tier_label
    occupation = f"{tier_label}（{role}）" if role and role != tier_label else tier_label
    return dict(age=age, gender=gender, tenure=tenure,
                occupation=occupation, tier=tier_label, occ_en=occ,
                activity_level=round(rng.uniform(0.6, 0.95), 2),
                mbti=MBTI[rng.randrange(len(MBTI))])


def _parse(text):
    text = text or ''
    for m in re.finditer(r'\{.*\}', text, re.S):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if all(isinstance(obj.get(k), str) and obj[k].strip() for k in ('name', 'username', 'bio', 'persona')):
            return obj
    return None


def gen_persona(client, model, attrs, strong, weak, attempts=3):
    prompt = PERSONA_PROMPT.format(
        age=attrs['age'],
        gender={'male': '男性', 'female': '女性'}.get(attrs['gender'], '回答なし'),
        tier=attrs['tier'], occupation=attrs['occupation'], tenure=attrs['tenure'],
        strong='、'.join(strong) or '（特になし）',
        weak='、'.join(weak) or '（特になし）')
    last = None
    for _ in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.7,
                extra_body={'think': False},
            )
        except Exception as e:
            last = f'error:{type(e).__name__}'
            continue
        obj = _parse(resp.choices[0].message.content)
        joined = (obj.get('bio', '') + ' ' + obj.get('persona', '')) if obj else ''
        if obj and not SCRUB.search(joined) and not SURVEY_BANNED.search(joined):
            return obj, None
        last = 'scrubbed' if obj else 'unparseable'
    return dict(
        name=f"Person {attrs['agent_id']}", username=f"user{attrs['agent_id']}",
        bio=f"{attrs['occupation']}。",
        persona=f"{attrs['age']}歳、{attrs['occupation']}。"
                + ('' if not strong else ('関心: ' + '、'.join(strong) + '。'))
                + '穏やかに日常的なことを発信する。'), last


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--n', type=int, default=100)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--source-dir', required=True)
    p.add_argument('--meta-root', required=True)
    p.add_argument('--out-dir')
    args = p.parse_args()

    n = args.n
    out_dir = args.out_dir or os.path.join(_BACKEND, 'uploads', 'simulations',
                                           f'sim_masked_s{args.seed}_n{n}')
    if os.path.exists(os.path.join(out_dir, 'simulation_config.json')):
        sys.exit(f"{out_dir} already has a simulation; pick another --out-dir or --seed")
    os.makedirs(out_dir, exist_ok=True)

    model = os.environ.get('LLM_MODEL_NAME')
    client = OpenAI(api_key=os.environ.get('LLM_API_KEY', 'ollama'),
                    base_url=os.environ.get('LLM_BASE_URL'))

    chosen, counts = select_respondents(args.source_dir, n, args.seed)
    print(f"apportionment (n={n}): {counts}", file=sys.stderr)
    index = load_item_index(args.meta_root)
    rng = random.Random(f"fill:{args.seed}")

    people = []
    for agent_id, (tier, label, occ_en, row) in enumerate(chosen):
        attrs = map_attributes(label, occ_en, row, rng)
        strong, weak = respondent_fingerprint(row, index)
        attrs.update(agent_id=agent_id, strong=strong, weak=weak)
        people.append(attrs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(
            lambda pe: gen_persona(client, model, pe, pe['strong'], pe['weak']), people))

    for person, (text, error) in zip(people, results):
        handle = re.sub(r'[^a-z0-9_]', '', text['username'].lower()) or f"user{person['agent_id']}"
        seen = set(q['username'] for q in people if 'username' in q)
        while handle in seen:
            handle += str(person['agent_id'])
        person.update(name=text['name'].strip(), username=handle, bio=text['bio'].strip(),
                      persona=text['persona'].strip(), persona_fallback=error)

    names = set()
    for person in people:
        base = person['name'].strip() or f"Person {person['agent_id']}"
        name, k = base, 2
        while name in names:
            name = f"{base} {k}"
            k += 1
        names.add(name)
        person['name'] = name

    today = datetime.now().strftime('%Y-%m-%d')
    with open(os.path.join(out_dir, 'twitter_profiles.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['user_id', 'name', 'username', 'user_char', 'description'])
        for person in people:
            w.writerow([person['agent_id'], person['name'], person['username'],
                        f"{person['bio']} {person['persona']}", person['bio']])

    reddit = [dict(user_id=person['agent_id'], username=person['username'], name=person['name'],
                   bio=person['bio'], persona=person['persona'], karma=1000, created_at=today,
                   age=person['age'], gender=person['gender'], mbti=person['mbti'],
                   country='Japan', profession=person['occupation'],
                   interested_topics=person['strong']) for person in people]
    json.dump(reddit, open(os.path.join(out_dir, 'reddit_profiles.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    rngp = random.Random(f"posts:{args.seed}")
    INITIAL_POSTS = [
        "今日は久しぶりにゆっくりできた。こういう日も大事にしたい。",
        "近所の店が変わっていた。街って少しずつ変わっていくんだな。",
        "週末の予定、まだ決めてない。おすすめあったら教えてください。",
        "最近よく使うキッチン道具、一個だけ挙げるなら何？",
        "通勤時間の使い方、皆さんはどうしてる？",
        "週末の天気良さそう。どこか行くとこある？",
        "うちの子（猫）がまたやらかしてくれた。笑える話ある？",
        "夜更かししすぎて今日はしんどい。元気出すコツある？",
    ]
    posters = rngp.sample(range(len(people)), min(len(INITIAL_POSTS), len(people)))
    sim_id = os.path.basename(out_dir.rstrip('/'))
    config = dict(
        simulation_id=sim_id, project_id=None, graph_id=None,
        simulation_requirement='マスキング済み組織調査由来ペルソナ（部長/課長/一般社員の比率 N=100）',
        time_config=dict(total_simulation_hours=168, minutes_per_round=60,
            start_hour=8, agents_per_hour_min=10, agents_per_hour_max=20,
            peak_hours=[12, 19, 20, 21, 22], peak_activity_multiplier=1.5,
            off_peak_hours=[0, 1, 2, 3, 4, 5], off_peak_activity_multiplier=0.3),
        agent_configs=[dict(
            agent_id=person['agent_id'], entity_uuid=None, entity_name=person['name'],
            entity_type='MaskedRespondent', activity_level=person['activity_level'],
            posts_per_hour=0.5, comments_per_hour=0.5, active_hours=list(range(7, 24)),
            response_delay_min=5, response_delay_max=60, sentiment_bias=0.0,
            stance='neutral', influence_weight=1.0) for person in people],
        event_config=dict(initial_posts=[
            dict(content=content, poster_type='MaskedRespondent', poster_agent_id=agent_id)
            for content, agent_id in zip(INITIAL_POSTS, posters)]),
        twitter_config=dict(platform='twitter', recency_weight=0.4, popularity_weight=0.3,
            relevance_weight=0.3, viral_threshold=10, echo_chamber_strength=0.5),
        reddit_config=dict(platform='reddit', recency_weight=0.3, popularity_weight=0.4,
            relevance_weight=0.3, viral_threshold=15, echo_chamber_strength=0.6),
        llm_model=model, llm_base_url=os.environ.get('LLM_BASE_URL'),
        generated_at=datetime.now().isoformat(),
        generation_reasoning=f'make_masked_personas_sim.py seed={args.seed} n={args.n}',
    )
    json.dump(config, open(os.path.join(out_dir, 'simulation_config.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump(dict(seed=args.seed, n=n, model=model, source='masked-survey',
                   excluded=sorted(EXCLUDED), apportionment=counts,
                   people=[{k: v for k, v in person.items()} for person in people]),
              open(os.path.join(out_dir, 'personas_meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    audit = []
    for person in people:
        for field in ('name', 'username', 'bio', 'persona', 'occupation'):
            v = str(person.get(field, ''))
            if SCRUB.search(v) or SURVEY_BANNED.search(v):
                audit.append((person['agent_id'], field, v[:60]))
    if audit:
        print(f"MASKING AUDIT FAIL: {len(audit)} fields", file=sys.stderr)
        for a in audit[:10]:
            print('  ', a, file=sys.stderr)
        sys.exit(2)
    fallbacks = sum(1 for person in people if person.get('persona_fallback'))
    print(f"wrote {out_dir}: {len(people)} personas ({fallbacks} fallback), apportionment={counts}, audit clean")


if __name__ == '__main__':
    main()