"""
Create a simulation of employees of one company group for game experiments.

Everyone works for the same (fictional) group, but in a different company
and department: 4 group companies x 12 departments, one employee per cell
for n=48. They are not strangers - same group, same internal social
network, group-wide events - but they share no targets, budgets or
reporting lines and owe each other nothing in particular.

  - attributes (company, department, seniority, age, ...) drawn with a
    fixed seed; the relationship to the other departments is one fixed
    sentence, identical for everyone, appended to the LLM-written persona
  - persona text written by the LLM: job, temperament and values at work,
    how they post on the group's internal network; the game's themes are
    kept out (same filter as make_general_sim.py)
  - personas_meta.json holds a seating order (`agent_order`) for oTree's
    session config `agent_ids`: consecutive pairs and groups of 4 are in the
    same company but different departments

Output: <out_dir>/{twitter_profiles.csv, reddit_profiles.json,
simulation_config.json, personas_meta.json}

Usage:
    backend/.venv/bin/python backend/scripts/experiment/make_workplace_sim.py \
        --n 48 --seed 1 [--out-dir backend/uploads/simulations/sim_workplace_s1_n48]
"""

import argparse
import concurrent.futures
import os
import random
import sys

from openai import OpenAI

sys.path.insert(0, os.path.dirname(__file__))
from make_general_sim import (  # noqa: E402  (loads .env)
    _BACKEND, BANNED, GENDERS, MBTI, _parse, write_simulation,
)

GROUP = 'Kestrel Harbour Group'
COMPANIES = ['Kestrel Harbour Logistics', 'Kestrel Harbour Foods', 'Kestrel Harbour Digital',
             'Kestrel Harbour Property']
DEPARTMENTS = ['Sales', 'Finance', 'Human Resources', 'Software Engineering', 'Customer Support',
               'Procurement', 'Legal', 'Marketing', 'Operations', 'Research and Development',
               'Facilities', 'IT Infrastructure']
SENIORITY = ['junior staff member', 'staff member', 'senior staff member', 'team lead', 'manager']
COUNTRIES = ['UK', 'Ireland', 'Canada', 'Australia', 'US', 'New Zealand', 'India', 'Singapore']

# The same for every agent; worded without the game's themes
RELATIONSHIP = (
    "{name} works at {company}, one of the four companies of the {group}. They know people in the "
    "other departments and group companies mostly by name or from group-wide events and the group's "
    "internal social network, but they share no targets, budgets or reporting lines with them, and "
    "have no particular obligation to go out of their way for them."
)

INITIAL_POSTS = [
    "Reminder: the canteen on the 3rd floor is closed for refurbishment until Friday.",
    "Anyone else's badge stop working at the east entrance this morning?",
    "Photos from last month's group family day are up on the intranet!",
]

PERSONA_PROMPT = """Write a short profile of an employee with these attributes:
- company: {company} (part of the {group})
- department: {department}
- role: {seniority}
- age: {age}
- gender: {gender}
- based in: {country}
- personality type (MBTI): {mbti}

Describe their job and working day, their temperament and what they care about at work,
and how they post on the company group's internal social network.
Do not mention trust, cooperation, games, strategy or economics.
Reply with only a JSON object:
{{"name": "first name and last initial", "username": "lowercase handle", "bio": "profile bio, under 160 characters", "persona": "3 to 5 sentences"}}"""


def draw_attributes(n, seed):
    rng = random.Random(seed)
    cells = [(c, d) for c in COMPANIES for d in DEPARTMENTS]
    if n > len(cells):
        raise SystemExit(f"n={n} exceeds {len(cells)} company x department cells")
    rng.shuffle(cells)
    people = []
    for i, (company, department) in enumerate(cells[:n]):
        people.append(dict(
            agent_id=i, company=company, department=department,
            seniority=rng.choice(SENIORITY), age=rng.randint(22, 64),
            gender=rng.choice(GENDERS), country=rng.choice(COUNTRIES), mbti=rng.choice(MBTI),
            activity_level=round(rng.uniform(0.3, 0.8), 2),
            # for write_simulation / reddit profile
            occupation=f"{department} ({company})", interests=[department.lower(), 'work', 'company news'],
        ))
    return people


def agent_order(people, block=4):
    """Seating order: fill blocks of `block` agents from one company, all in
    different departments (true by construction), so pairs (1-2, 3-4) and
    groups of 4 are same company, different departments."""
    by_company = {}
    for p in people:
        by_company.setdefault(p['company'], []).append(p['agent_id'])
    order = []
    while any(len(v) >= block for v in by_company.values()):
        for company in COMPANIES:
            ids = by_company.get(company, [])
            if len(ids) >= block:
                order += ids[:block]
                del ids[:block]
    leftovers = [a for v in by_company.values() for a in v]
    return order + leftovers


def write_persona(client, model, person, attempts=3):
    prompt = PERSONA_PROMPT.format(group=GROUP, **person)
    last = None
    for _ in range(attempts):
        resp = client.chat.completions.create(model=model, messages=[{'role': 'user', 'content': prompt}])
        obj = _parse(resp.choices[0].message.content)
        if obj and not BANNED.search(obj['bio'] + ' ' + obj['persona']):
            break
        last, obj = ('banned theme' if obj else 'unparseable'), None
    if obj is None:
        obj = dict(name=f"Employee {person['agent_id']}", username=f"employee{person['agent_id']}",
                   bio=f"{person['seniority'].capitalize()}, {person['department']}, {person['company']}.",
                   persona=f"A {person['age']}-year-old {person['seniority']} in {person['department']}.")
    else:
        last = None
    obj['persona'] = obj['persona'].strip() + ' ' + RELATIONSHIP.format(
        name=obj['name'].split()[0], company=person['company'], group=GROUP)
    return obj, last


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--n', type=int, default=48)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--out-dir')
    p.add_argument('--workers', type=int, default=8)
    args = p.parse_args()

    out_dir = args.out_dir or os.path.join(_BACKEND, 'uploads', 'simulations',
                                           f'sim_workplace_s{args.seed}_n{args.n}')
    if os.path.exists(os.path.join(out_dir, 'simulation_config.json')):
        sys.exit(f"{out_dir} already has a simulation; pick another --out-dir or --seed")
    os.makedirs(out_dir, exist_ok=True)

    model = os.environ.get('LLM_MODEL_NAME')
    client = OpenAI(api_key=os.environ.get('LLM_API_KEY', 'ollama'), base_url=os.environ.get('LLM_BASE_URL'))
    people = draw_attributes(args.n, args.seed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda person: write_persona(client, model, person), people))

    write_simulation(out_dir, people, results, model, seed=args.seed,
                     requirement=f'Employees of the {GROUP} (different companies and departments) '
                                 'for oTree game experiments',
                     entity_type='Employee', initial_posts=INITIAL_POSTS,
                     reasoning=f'make_workplace_sim.py seed={args.seed} n={args.n}',
                     extra_meta=dict(group=GROUP, agent_order=agent_order(people)))


if __name__ == '__main__':
    main()
