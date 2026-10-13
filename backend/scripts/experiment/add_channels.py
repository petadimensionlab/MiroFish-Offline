"""
Add internal-communication "channels" to the Kestrel Harbour workplace personas.

Employees differ in which internal messages they act on, skim or ignore
(pay and benefits notices, IT alerts, social events, ...) and in which media
they use (chat, all-staff email, notice boards, ...). This takes an existing
workplace simulation (make_workplace_sim.py) and writes a copy in which

  - every person has a level for EVERY channel (ignore < skim < read < act) and
    a habit for EVERY medium (rarely < sometimes < habitually), assigned by a
    seeded rule/probability model from department, seniority, age, MBTI,
    activity level and hints in the existing persona text (no LLM, < 2 s)
  - a short template paragraph (plain habits, no values, no pronouns) is
    inserted into the persona before the fixed RELATIONSHIP sentence; it
    mentions only the `use_in_text` channels and the top/bottom media
  - the taxonomy (scripts/experiment/workplace_channels.json, curated from the
    Haiku taxonomy) keeps items that could prime the games (charity,
    volunteering) for analysis but never mentions them in persona text; the
    text is checked against BANNED (NOTES #34) and a soft-priming regex

Levels are population-relative: pooled quantile thresholds over all
persons x channels give ~30% ignore / 30% skim / 25% read / 15% act, then the
base of any extreme channel is shifted in steps of 0.25 (recorded in
channels.json). Mandatory channels have a floor of "skim".

The source directory is only read. Output (new directory, must not exist):
<out>/{twitter_profiles.csv, reddit_profiles.json, simulation_config.json,
personas_meta.json, channels.json}; only the persona / user_char text and
added keys differ from the source. --verify-only re-checks an existing output.

Usage:
    backend/.venv/bin/python backend/scripts/experiment/add_channels.py \
        --src backend/uploads/simulations/sim_workplace_s1_n48 \
        --out backend/uploads/simulations/sim_workplace_ch_s1_n48 \
        [--taxonomy backend/scripts/experiment/workplace_channels.json] \
        [--seed <meta.seed>] [--max-chars 420] [--verify-only]
"""

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from make_general_sim import _BACKEND, BANNED  # noqa: E402
from make_workplace_sim import GROUP, RELATIONSHIP, SENIORITY  # noqa: E402

TAXONOMY_VERSION = 1
CHANNELS_VERSION = 1
DEFAULT_SRC = os.path.join(_BACKEND, 'uploads', 'simulations', 'sim_workplace_s1_n48')
DEFAULT_OUT = os.path.join(_BACKEND, 'uploads', 'simulations', 'sim_workplace_ch_s1_n48')
DEFAULT_TAXONOMY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'workplace_channels.json')
SOURCE_FILES = ['twitter_profiles.csv', 'reddit_profiles.json', 'simulation_config.json', 'personas_meta.json']

LEVELS = ['ignore', 'skim', 'read', 'act']
MEDIA_LEVELS = ['rarely', 'sometimes', 'habitually']
TARGET_SHARES = [0.30, 0.30, 0.25, 0.15]  # ignore / skim / read / act
MAX_ITER = 20
SHIFT = 0.25
MEAN_AGE, MEAN_ACT = 43, 0.55

# Words that would prime the giving games even though BANNED lets them through
SOFT_PRIMING = re.compile(
    r'\b(contribut\w*|donat\w*|charit\w*|fundrais\w*|volunteer\w*|fair\w*|reciproc\w*|invest\w*|payoff\w*|'
    r'reward\w*|selfish\w*|altruis\w*|generos\w*|partner\w*|public good|common good|pool\w*)\b', re.I)


# ------------------------------------------------------------------ taxonomy
def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _texts(item):
    """All prose fields of a taxonomy item (not the machine fields or the reason)."""
    out = []
    for k in ('label', 'phrase', 'description', 'typical_sender', 'frequency'):
        if isinstance(item.get(k), str):
            out.append(item[k])
    out += [e for e in item.get('examples', []) if isinstance(e, str)]
    return out


def load_taxonomy(path):
    """Load and validate the curated taxonomy; raise ValueError if any item that
    is used in persona text matches BANNED or the soft-priming regex."""
    tax = json.load(open(path, encoding='utf-8'))
    if tax.get('taxonomy_version') != TAXONOMY_VERSION:
        raise ValueError(f"taxonomy_version {tax.get('taxonomy_version')} != {TAXONOMY_VERSION}")
    for kind in ('channels', 'media'):
        ids = [x['id'] for x in tax[kind]]
        if len(set(ids)) != len(ids):
            raise ValueError(f'duplicate {kind} ids')
    for c in tax['channels']:
        for k in ('id', 'label', 'phrase', 'category', 'base', 'affinity', 'use_in_text'):
            if k not in c:
                raise ValueError(f"channel {c.get('id')} lacks {k}")
        if c['category'] not in tax['categories']:
            raise ValueError(f"channel {c['id']}: unknown category {c['category']}")
        if c.get('floor') not in (None, 'skim'):
            raise ValueError(f"channel {c['id']}: bad floor")
        if not c['use_in_text'] and not c.get('reason'):
            raise ValueError(f"channel {c['id']}: use_in_text=false needs a reason")
    for m in tax['media']:
        for k in ('id', 'label', 'phrase', 'base', 'affinity'):
            if k not in m:
                raise ValueError(f"medium {m.get('id')} lacks {k}")
    for item in tax['channels'] + tax['media']:
        for text in _texts(item):
            if BANNED.search(text):
                raise ValueError(f"{item['id']}: BANNED word in {text!r}")
            if item['use_in_text'] and SOFT_PRIMING.search(text):
                raise ValueError(f"{item['id']}: soft-priming word in {text!r} (set use_in_text=false)")
        if item['use_in_text'] and SOFT_PRIMING.search(item['phrase']):
            raise ValueError(f"{item['id']}: phrase primes")
    return tax


# ------------------------------------------------------------------ model
def features(p):
    return dict(dept=p['department'], sen=SENIORITY.index(p['seniority']), age_c=p['age'] - MEAN_AGE,
                mbti=set(p['mbti']), act_c=p['activity_level'] - MEAN_ACT)


def _normal(key):
    """Standard normal from a string key: stable per (seed, agent, channel)."""
    return random.Random(key).gauss(0, 1)


def _affinity(aff, x):
    return (aff.get('dept', {}).get(x['dept'], 0.0) + aff.get('seniority', 0.0) * x['sen']
            + aff.get('age', 0.0) * x['age_c'] + sum(w for k, w in aff.get('mbti', {}).items() if k in x['mbti'])
            + aff.get('activity', 0.0) * x['act_c'])


def _hints(person, tax):
    text = f"{person.get('persona_base', person['persona'])} {person['bio']}"
    hits = []
    for c in tax['channels']:
        if c.get('hints'):
            m = re.search(c['hints'], text, re.I)
            if m:
                hits.append(dict(channel=c['id'], hit=m.group(0).lower()))
    return hits


def _quantile_thresholds(values, cum):
    vs = sorted(values)
    return [vs[min(len(vs) - 1, int(q * len(vs)))] for q in cum]


def _level(s, th):
    return 0 if s < th[0] else 1 if s < th[1] else 2 if s < th[2] else 3


def assign(people, tax, seed):
    """Deterministic channel levels / media habits. Returns dict with per-person
    results, calibrated bases and calibration record."""
    chans, media = tax['channels'], tax['media']
    xs = [features(p) for p in people]
    eng = [0.8 * _normal(f"channels:{seed}:{p['agent_id']}:engagement") for p in people]
    # raw score without base: affinity + hints + engagement + category effect + channel noise
    raw = []
    for p, x, g in zip(people, xs, eng):
        aid = p['agent_id']
        hit = {h['channel'] for h in _hints(p, tax)}
        u = {k: 0.5 * _normal(f"channels:{seed}:{aid}:cat:{k}") for k in tax['categories']}
        raw.append({c['id']: _affinity(c['affinity'], x) + (0.5 if c['id'] in hit else 0.0) + g
                    + u[c['category']] + 0.6 * _normal(f"channels:{seed}:{aid}:ch:{c['id']}") for c in chans})
    base = {c['id']: float(c['base']) for c in chans}
    shifts = {c['id']: 0.0 for c in chans}
    cum = [sum(TARGET_SHARES[:i + 1]) for i in range(3)]
    iterations = 0
    for iterations in range(MAX_ITER + 1):
        scores = [{cid: r[cid] + base[cid] for cid in base} for r in raw]
        th = _quantile_thresholds([s for sc in scores for s in sc.values()], cum)
        changed = False
        if iterations == MAX_ITER:
            break
        for cid in base:
            lv = [_level(sc[cid], th) for sc in scores]
            n = len(lv)
            share = [lv.count(i) / n for i in range(4)]
            if share[0] + share[1] >= 0.9 or share[0] >= 0.7:
                base[cid] += SHIFT
                shifts[cid] += SHIFT
                changed = True
            elif share[2] + share[3] >= 0.9 or share[3] >= 0.6:
                base[cid] -= SHIFT
                shifts[cid] -= SHIFT
                changed = True
        if not changed:
            break
    floors = {c['id']: c.get('floor') for c in chans}
    channels = []
    for sc in scores:
        d = {}
        for cid, s in sc.items():
            lv = _level(s, th)
            if floors[cid] == 'skim':
                lv = max(lv, 1)
            d[cid] = dict(level=LEVELS[lv], score=round(s, 3))
        channels.append(d)
    # media
    mraw = []
    for p, x, g in zip(people, xs, eng):
        aid = p['agent_id']
        mraw.append({m['id']: m['base'] + _affinity(m['affinity'], x) + (g if m.get('engagement_term') else 0.0)
                     + 0.6 * _normal(f"channels:{seed}:{aid}:medium:{m['id']}") for m in media})
    mth = _quantile_thresholds([s for sc in mraw for s in sc.values()], [1 / 3, 2 / 3])
    habits, mscores = [], []
    for sc in mraw:
        habits.append({mid: MEDIA_LEVELS[0 if s < mth[0] else 1 if s < mth[1] else 2] for mid, s in sc.items()})
        mscores.append({mid: round(s, 3) for mid, s in sc.items()})
    hints = [_hints(p, tax) for p in people]
    return dict(engagement=eng, channels=channels, media_habits=habits, media_scores=mscores, hints=hints,
                base=base, calibration=dict(
                    target_shares=dict(zip(LEVELS, TARGET_SHARES)), thresholds=[round(t, 4) for t in th],
                    media_thresholds=[round(t, 4) for t in mth], iterations=iterations,
                    base_shifts={k: v for k, v in shifts.items() if v}))


# ------------------------------------------------------------------ text
def _join(items):
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def channel_paragraph(first, res, tax, max_chars=420):
    """Template paragraph (no pronouns) from one person's result `res`
    (keys: engagement, channels, media_habits, media_scores)."""
    phrase = {c['id']: c['phrase'] for c in tax['channels'] if c['use_in_text']}
    ch = res['channels']

    def pick(levels, n, reverse):
        ids = [c for c in phrase if ch[c]['level'] in levels]
        ids.sort(key=lambda c: ((-1 if reverse else 1) * ch[c]['score'], c))
        return [phrase[c] for c in ids[:n]]

    def lists(n, skim):
        act = pick(['act'], n, True)
        if len(act) < n:
            used = set(act)
            act += [p for p in pick(['read'], n, True) if p not in used][:n - len(act)]
        ign = pick(['ignore'], n, False)
        sk = pick(['skim'], 1, True) if skim else []
        return act, sk, ign

    mphr = {m['id']: m['phrase'] for m in tax['media'] if m['use_in_text']}
    ms, mh = res['media_scores'], res['media_habits']
    hi = sorted((m for m in mphr if mh[m] == 'habitually'), key=lambda m: (-ms[m], m))[:2]
    lo = sorted((m for m in mphr if mh[m] == 'rarely'), key=lambda m: (ms[m], m))[:1]
    def media_sentence(n_hi):
        if not hi:
            return ''
        s = f"Day to day, {first} relies on {_join([mphr[m] for m in hi[:n_hi]])}"
        return s + (f" and rarely bothers with {mphr[lo[0]]}." if lo else '.')

    tail = f" {first} leaves most all-staff announcements unopened." if res['engagement'] < -1 else ''
    # shorten step by step: drop the skim clause, then trim lists to 2, then one habit
    for n, skim, n_hi in ((3, True, 2), (3, False, 2), (2, False, 2), (2, False, 1)):
        act, sk, ign = lists(n, skim)
        parts = []
        if act:
            s = f"At work, {first} reads and acts on {_join(act)}"
            s += f", and skims {_join(sk)}." if sk else '.'
            parts.append(s)
        if ign:
            parts.append(f"{first} usually ignores {_join(ign)}.")
        if hi:
            parts.append(media_sentence(n_hi))
        text = ' '.join(parts) + tail
        if len(text) <= max_chars:
            return text
    raise ValueError(f"channel text for {first} exceeds {max_chars} chars: {len(text)}")


def _first(person):
    return person['name'].split()[0]


def _rel(person):
    return RELATIONSHIP.format(name=_first(person), company=person['company'], group=GROUP)


def compute(people, tax, seed, max_chars=420):
    """assign() + persona text; returns per-person updates and the assignment record."""
    res = assign(people, tax, seed)
    updates = []
    for i, p in enumerate(people):
        rel = _rel(p)
        if not p['persona'].endswith(rel):
            raise ValueError(f"agent {p['agent_id']}: persona does not end with the RELATIONSHIP sentence")
        base = p['persona'][:-len(rel)].strip()
        r = dict(engagement=res['engagement'][i], channels=res['channels'][i],
                 media_habits=res['media_habits'][i], media_scores=res['media_scores'][i])
        text = channel_paragraph(_first(p), r, tax, max_chars)
        updates.append(dict(persona_base=p['persona'], channel_text=text,
                            persona=f"{base} {text} {rel}",
                            channel_engagement=round(res['engagement'][i], 4),
                            channel_hints=res['hints'][i], channels=res['channels'][i],
                            media_habits=res['media_habits'][i], media_scores=res['media_scores'][i]))
    return updates, res


# ------------------------------------------------------------------ build
def _source_hashes(src):
    return {f: _sha256(os.path.join(src, f)) for f in SOURCE_FILES}


def _summary(updates, tax):
    out = {}
    for c in tax['channels']:
        cnt = {lv: 0 for lv in LEVELS}
        for u in updates:
            cnt[u['channels'][c['id']]['level']] += 1
        out[c['id']] = cnt
    return out


def build(src, out, tax_path, seed=None, max_chars=420):
    if os.path.realpath(out) == os.path.realpath(src):
        raise SystemExit('--out must differ from --src')
    if os.path.exists(out):
        raise SystemExit(f'{out} already exists; pick another --out')
    tax = load_taxonomy(tax_path)
    before = _source_hashes(src)
    meta = json.load(open(os.path.join(src, 'personas_meta.json'), encoding='utf-8'))
    seed = meta['seed'] if seed is None else seed
    people = meta['people']
    updates, res = compute(people, tax, seed, max_chars)

    tmp = out.rstrip('/') + '.tmp'
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    sim_id = os.path.basename(out.rstrip('/'))
    src_id = os.path.basename(src.rstrip('/'))

    # simulation_config.json: only simulation_id and generation_reasoning change
    cfg = json.load(open(os.path.join(src, 'simulation_config.json'), encoding='utf-8'))
    cfg['simulation_id'] = sim_id
    cfg['generation_reasoning'] = f"{cfg['generation_reasoning']} + add_channels.py taxonomy_v{TAXONOMY_VERSION} seed={seed}"
    json.dump(cfg, open(os.path.join(tmp, 'simulation_config.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    # twitter_profiles.csv: only user_char = bio + ' ' + persona
    with open(os.path.join(src, 'twitter_profiles.csv'), newline='', encoding='utf-8') as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    ci = header.index('user_char')
    assert len(body) == len(people)
    for row, p, u in zip(body, people, updates):
        assert int(row[0]) == p['agent_id'] and row[ci] == f"{p['bio']} {p['persona']}", row[0]
        row[ci] = f"{p['bio']} {u['persona']}"
    with open(os.path.join(tmp, 'twitter_profiles.csv'), 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows([header] + body)

    # reddit_profiles.json: only persona
    reddit = json.load(open(os.path.join(src, 'reddit_profiles.json'), encoding='utf-8'))
    for r, p, u in zip(reddit, people, updates):
        assert r['user_id'] == p['agent_id'] and r['persona'] == p['persona']
        r['persona'] = u['persona']
    json.dump(reddit, open(os.path.join(tmp, 'reddit_profiles.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    # personas_meta.json: all old keys kept, persona replaced, new keys added
    new_meta = copy.deepcopy(meta)
    for p, u in zip(new_meta['people'], updates):
        p.update(u)
    new_meta.update(channels_version=CHANNELS_VERSION, channel_seed=seed, channels_source_sim=src_id,
                    channels_file='channels.json')
    json.dump(new_meta, open(os.path.join(tmp, 'personas_meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    # channels.json: the calibrated taxonomy
    chan_out = []
    for c in tax['channels']:
        d = {k: c.get(k) for k in ('id', 'label', 'phrase', 'category')}
        d.update(kind=c.get('kind'), typical_sender=c.get('typical_sender'), typical_media=c.get('typical_media'),
                 description=c.get('description'), examples=c.get('examples'), base=res['base'][c['id']],
                 base_initial=c['base'], affinity=c['affinity'], hints=c.get('hints'), floor=c.get('floor'),
                 use_in_text=c['use_in_text'], reason=c.get('reason', ''))
        chan_out.append(d)
    channels = dict(
        version=CHANNELS_VERSION, taxonomy_version=TAXONOMY_VERSION, source_sim=src_id, seed=seed,
        levels=LEVELS, media_levels=MEDIA_LEVELS, categories=tax['categories'], channels=chan_out,
        media=tax['media'], calibration=res['calibration'],
        provenance=dict(taxonomy_file=os.path.abspath(tax_path), taxonomy_sha256=_sha256(tax_path),
                        haiku_source=tax['provenance']['source'], haiku_sha256=tax['provenance']['source_sha256'],
                        script='scripts/experiment/add_channels.py', source_sha256=before,
                        generated_at=datetime.now().isoformat()),
        summary=_summary(updates, tax))
    json.dump(channels, open(os.path.join(tmp, 'channels.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    if _source_hashes(src) != before:
        shutil.rmtree(tmp)
        raise SystemExit('source files changed during the build')
    os.replace(tmp, out)
    return out


# ------------------------------------------------------------------ verify
def _mean(v):
    return sum(v) / len(v) if v else float('nan')


def _corr(a, b):
    ma, mb = _mean(a), _mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def verify(src, out, tax_path, max_chars=420, quiet=False):
    """Run all checks on an existing output. Returns (errors, warnings)."""
    errors, warnings = [], []
    log = (lambda *a: None) if quiet else print
    tax = load_taxonomy(tax_path)
    ld = lambda d, f: json.load(open(os.path.join(d, f), encoding='utf-8'))  # noqa: E731
    sm, om = ld(src, 'personas_meta.json'), ld(out, 'personas_meta.json')
    ch = ld(out, 'channels.json')
    cids = [c['id'] for c in tax['channels']]
    mids = [m['id'] for m in tax['media']]
    people = om['people']
    n = len(people)

    # 1 schema
    if [c['id'] for c in ch['channels']] != cids or [m['id'] for m in ch['media']] != mids:
        errors.append('channels.json ids differ from the taxonomy')
    for p in people:
        if set(p['channels']) != set(cids):
            errors.append(f"agent {p['agent_id']}: channel ids mismatch")
        elif any(v['level'] not in LEVELS for v in p['channels'].values()):
            errors.append(f"agent {p['agent_id']}: bad level")
        if set(p['media_habits']) != set(mids) or any(v not in MEDIA_LEVELS for v in p['media_habits'].values()):
            errors.append(f"agent {p['agent_id']}: bad media habits")
    if om.get('channels_file') != 'channels.json' or om.get('channels_version') != CHANNELS_VERSION:
        errors.append('meta channels_* keys missing')

    # 2 distributions
    lvl = {c: [LEVELS.index(p['channels'][c]['level']) for p in people] for c in cids}
    log(f"\nchannel x level counts (n={n})           ign skm red act   use_in_text")
    use = {c['id']: c['use_in_text'] for c in tax['channels']}
    for c in cids:
        cnt = [lvl[c].count(i) for i in range(4)]
        log(f"  {c:26s} {cnt[0]:4d}{cnt[1]:4d}{cnt[2]:4d}{cnt[3]:4d}   {use[c]}")
        if (cnt[0] + cnt[1]) / n >= 0.9:
            errors.append(f'{c}: >=90% ignore+skim')
        if (cnt[2] + cnt[3]) / n >= 0.9:
            errors.append(f'{c}: >=90% read+act')
        if sum(1 for k in cnt if k >= 3) < 2:
            errors.append(f'{c}: <3 people at 2 different levels')
    tot = [sum(lvl[c].count(i) for c in cids) / (n * len(cids)) for i in range(4)]
    log('overall shares (ignore/skim/read/act): ' + ' / '.join(f'{100 * t:.1f}%' for t in tot))
    cat_of = {c['id']: c['category'] for c in tax['channels']}
    depts = sorted({p['department'] for p in people})
    cats = list(tax['categories'])
    log('\ndepartment x category mean score')
    log('  ' + ' ' * 26 + ''.join(f'{c[:12]:>14s}' for c in cats))
    for d in depts:
        row = []
        for k in cats:
            v = [p['channels'][c]['score'] for p in people if p['department'] == d for c in cids if cat_of[c] == k]
            row.append(_mean(v))
        log(f"  {d:26s}" + ''.join(f'{x:14.2f}' for x in row))

    sc = lambda p, c: p['channels'][c]['score']  # noqa: E731
    sen = lambda p: SENIORITY.index(p['seniority'])  # noqa: E731
    tests = [
        ('HR > rest on benefits_insurance', [sc(p, 'benefits_insurance') for p in people if p['department'] == 'Human Resources'],
         [sc(p, 'benefits_insurance') for p in people if p['department'] != 'Human Resources']),
        ('HR > rest on leave_holidays (HR policy)', [sc(p, 'leave_holidays') for p in people if p['department'] == 'Human Resources'],
         [sc(p, 'leave_holidays') for p in people if p['department'] != 'Human Resources']),
    ]
    for c in ('learning_development', 'career_vacancies'):
        tests.append((f'junior/staff > manager on {c}', [sc(p, c) for p in people if sen(p) <= 1],
                      [sc(p, c) for p in people if sen(p) == 4]))
    tech = {'Research and Development', 'Software Engineering', 'IT Infrastructure'}
    tests.append(('R&D/SWE/IT > rest on innovation', [sc(p, 'innovation') for p in people if p['department'] in tech],
                  [sc(p, 'innovation') for p in people if p['department'] not in tech]))
    hs = {'Facilities', 'Operations'}
    tests.append(('Facilities/Ops > rest on health_safety', [sc(p, 'health_safety') for p in people if p['department'] in hs],
                  [sc(p, 'health_safety') for p in people if p['department'] not in hs]))
    log('\nsign tests (mean score difference must be > 0)')
    sign_results = {}
    for name, a, b in tests:
        d = _mean(a) - _mean(b)
        sign_results[name] = d
        log(f'  {name:46s} {len(a):2d} vs {len(b):2d}  diff {d:+.2f}  {"ok" if d > 0 else "FAIL"}')
        if not d > 0:
            errors.append(f'sign test failed: {name}')
    r = _corr([p['age'] for p in people], [sc(p, 'social_recreation') for p in people])
    sign_results['age~social_recreation'] = r
    log(f'  {"age vs social_recreation (r < 0)":46s} r = {r:+.2f}  {"ok" if r < 0 else "FAIL"}')
    if not r < 0:
        errors.append('age is not negatively correlated with social_recreation')
    low = sum(1 for p in people if p['channel_engagement'] < -1) / n
    log(f'  share with engagement g < -1: {100 * low:.1f}% (want 10-20%)')
    if not 0.10 <= low <= 0.20:
        errors.append(f'share with g<-1 is {low:.3f}, outside 10-20%')

    # 3 priming
    for p in people:
        if BANNED.search(p['channel_text']) or SOFT_PRIMING.search(p['channel_text']):
            errors.append(f"agent {p['agent_id']}: priming word in channel_text")
    with open(os.path.join(out, 'twitter_profiles.csv'), newline='', encoding='utf-8') as f:
        orows = list(csv.DictReader(f))
    for row in orows:
        if BANNED.search(row['user_char']):
            errors.append(f"user {row['user_id']}: BANNED in user_char")

    # 4 diff vs source
    ss = {'reddit': ld(src, 'reddit_profiles.json'), 'cfg': ld(src, 'simulation_config.json')}
    with open(os.path.join(src, 'twitter_profiles.csv'), newline='', encoding='utf-8') as f:
        srows = list(csv.DictReader(f))
    new_top = {'channels_version', 'channel_seed', 'channels_source_sim', 'channels_file'}
    if set(om) - set(sm) != new_top or any(om[k] != sm[k] for k in sm if k != 'people'):
        errors.append('meta top-level keys differ beyond the added ones')
    new_keys = {'persona_base', 'channel_text', 'channel_engagement', 'channel_hints', 'channels',
                'media_habits', 'media_scores'}
    for sp, p in zip(sm['people'], people):
        rel = _rel(sp)
        if set(p) - set(sp) != new_keys:
            errors.append(f"agent {p['agent_id']}: unexpected person keys")
        if any(p[k] != sp[k] for k in sp if k != 'persona'):
            errors.append(f"agent {p['agent_id']}: a source field changed")
        if p['persona_base'] != sp['persona'] or not sp['persona'].endswith(rel):
            errors.append(f"agent {p['agent_id']}: persona_base mismatch")
        base = sp['persona'][:-len(rel)].strip()
        if p['persona'] != f"{base} {p['channel_text']} {rel}" or not p['persona'].endswith(rel):
            errors.append(f"agent {p['agent_id']}: persona != base + channel_text + relationship")
        if len(p['channel_text']) > max_chars:
            errors.append(f"agent {p['agent_id']}: channel_text too long")
    if len(orows) != len(srows):
        errors.append('csv row count differs')
    for a, b, p in zip(srows, orows, people):
        if a.keys() != b.keys() or any(a[k] != b[k] for k in a if k != 'user_char'):
            errors.append(f"csv row {a['user_id']}: columns other than user_char differ")
        if b['user_char'] != f"{p['bio']} {p['persona']}":
            errors.append(f"csv row {a['user_id']}: user_char != bio + persona")
    rd = ld(out, 'reddit_profiles.json')
    for a, b, p in zip(ss['reddit'], rd, people):
        if any(a[k] != b[k] for k in a if k != 'persona') or set(a) != set(b) or b['persona'] != p['persona']:
            errors.append(f"reddit {a['user_id']}: differs beyond persona")
    oc = ld(out, 'simulation_config.json')
    diff = {k for k in set(oc) | set(ss['cfg']) if oc.get(k) != ss['cfg'].get(k)}
    if diff != {'simulation_id', 'generation_reasoning'}:
        errors.append(f'config differs in {sorted(diff)}')
    if oc['simulation_id'] != os.path.basename(out.rstrip('/')):
        errors.append('simulation_id != out dir name')
    if _source_hashes(src) != ch['provenance']['source_sha256']:
        errors.append('source files changed since the build (sha256 mismatch)')

    # 5 length
    uc = [len(r['user_char']) for r in orows]
    ct = [len(p['channel_text']) for p in people]
    log(f'\nuser_char length: mean {_mean(uc):.0f}, max {max(uc)} (source mean '
        f'{_mean([len(r["user_char"]) for r in srows]):.0f}); channel_text mean {_mean(ct):.0f}, max {max(ct)}')

    # 6 reproducibility (recompute from the source meta and compare the channel fields)
    upd, res = compute(sm['people'], tax, om['channel_seed'], max_chars)
    for u, p in zip(upd, people):
        if any(u[k] != p[k] for k in u):
            errors.append(f"agent {p['agent_id']}: recomputation differs from stored channel fields")
            break
    if {k: round(v, 10) for k, v in res['base'].items()} != {c['id']: round(c['base'], 10) for c in ch['channels']}:
        errors.append('calibrated bases differ on recomputation')

    # 7 spot check
    if not quiet:
        _spot(people)
    for w in warnings:
        log('WARNING', w)
    return errors, warnings


def _spot(people):
    picks, seen = [], set()
    for p in people:
        if p['company'] not in seen:
            seen.add(p['company'])
            picks.append(p)
    lo = min(people, key=lambda p: p['channel_engagement'])
    hi = max(people, key=lambda p: p['channel_engagement'])
    print('\nspot check')
    for p in picks + [q for q in (lo, hi) if q not in picks]:
        top = sorted(p['channels'], key=lambda c: (-p['channels'][c]['score'], c))
        rel = _rel(p)
        print(f"- #{p['agent_id']} {p['name']} | {p['company']} | {p['department']} | {p['seniority']} | age {p['age']} "
              f"| {p['mbti']} | act {p['activity_level']} | g {p['channel_engagement']:+.2f}")
        print(f"    tail: {p['persona'][len(p['persona_base']) - len(rel) - 1:][:-len(rel)].strip()} [+relationship]")
        print(f"    top: {', '.join(top[:3])} | bottom: {', '.join(top[-3:])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--src', default=DEFAULT_SRC)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--taxonomy', default=DEFAULT_TAXONOMY)
    ap.add_argument('--seed', type=int, default=None, help='default: the source meta seed')
    ap.add_argument('--max-chars', type=int, default=420)
    ap.add_argument('--verify-only', action='store_true')
    args = ap.parse_args()
    if not args.verify_only:
        build(args.src, args.out, args.taxonomy, args.seed, args.max_chars)
        print(f'wrote {args.out}')
    errors, warnings = verify(args.src, args.out, args.taxonomy, args.max_chars)
    print(f"\n{len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print('ERROR', e)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
