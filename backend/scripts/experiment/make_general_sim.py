"""
Create a simulation directory of general-public personas for game experiments.

The document-derived simulations (e.g. researchers of one paper) make every
debate about that paper, never about the game (NOTES.md #34). This builds a
population of ordinary social-media users instead:

  - demographic attributes drawn with a fixed seed (reproducible)
  - persona text written by the LLM from those attributes, with the game's
    themes (trust, cooperation, strategy, economics) kept out to avoid priming
  - uniform activity settings, active 8-22h (NOTES.md #24), equal influence
  - a few everyday initial posts unrelated to the game

Output: <out_dir>/{twitter_profiles.csv, reddit_profiles.json,
simulation_config.json, personas_meta.json}

Usage:
    backend/.venv/bin/python backend/scripts/experiment/make_general_sim.py \
        --n 48 --seed 1 [--out-dir backend/uploads/simulations/sim_general_s1_n48]
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

OCCUPATIONS = [
    'nurse', 'retail cashier', 'primary school teacher', 'truck driver', 'university student',
    'retired postal worker', 'software developer', 'dairy farmer', 'bakery owner', 'accountant',
    'line cook', 'hairdresser', 'electrician', 'warehouse picker', 'bus driver', 'pharmacist',
    'call-center agent', 'stay-at-home parent', 'graphic designer', 'plumber', 'security guard',
    'librarian', 'real estate agent', 'factory technician', 'delivery rider', 'dental assistant',
    'high school student', 'gardener', 'insurance clerk', 'freelance photographer',
]
COUNTRIES = ['US', 'UK', 'Canada', 'Australia', 'India', 'Philippines', 'Nigeria', 'Kenya',
             'Brazil', 'Mexico', 'Germany', 'Ireland', 'Japan', 'South Africa', 'New Zealand']
GENDERS = ['female', 'male', 'female', 'male', 'non-binary']
MBTI = [a + b + c + d for a in 'EI' for b in 'SN' for c in 'TF' for d in 'JP']
INTERESTS = ['cooking', 'football', 'gardening', 'video games', 'parenting', 'music', 'travel',
             'fitness', 'pets', 'local news', 'movies', 'DIY', 'fashion', 'cars', 'books',
             'hiking', 'baking', 'photography', 'TV series', 'knitting', 'cycling', 'fishing']
# Kept out of personas so the population does not arrive primed for the game
BANNED = re.compile(r'\b(trust|cooperat\w*|game theory|strateg\w*|econom\w*|prisoner|dilemma)\b', re.I)

INITIAL_POSTS = [
    "Anyone else find it harder to get going this week? Coffee is doing all the work.",
    "Tried a new recipe tonight and it came out better than expected!",
    "Our local park finally reopened. Nice to see so many families out.",
]

PERSONA_PROMPT = """Write a short profile of an ordinary social media user with these attributes:
- age: {age}
- gender: {gender}
- country: {country}
- occupation: {occupation}
- personality type (MBTI): {mbti}
- interests: {interests}

Describe their everyday life, what they usually post about, and how they talk online.
Do not mention trust, cooperation, games, strategy or economics.
Reply with only a JSON object:
{{"name": "first name and last initial", "username": "lowercase handle", "bio": "profile bio, under 160 characters", "persona": "3 to 5 sentences"}}"""


def draw_attributes(n, seed):
    rng = random.Random(seed)
    people = []
    for i in range(n):
        people.append(dict(
            agent_id=i,
            age=rng.randint(18, 75),
            gender=rng.choice(GENDERS),
            country=rng.choice(COUNTRIES),
            occupation=OCCUPATIONS[i % len(OCCUPATIONS)] if i < len(OCCUPATIONS) else rng.choice(OCCUPATIONS),
            mbti=rng.choice(MBTI),
            interests=rng.sample(INTERESTS, 3),
            activity_level=round(rng.uniform(0.3, 0.8), 2),
        ))
    rng.shuffle(people)  # occupation order should not follow agent_id
    for i, p in enumerate(people):
        p['agent_id'] = i
    return people


def _parse(text):
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    text = re.sub(r'```(?:json)?', '', text)
    for m in re.finditer(r'\{.*\}', text, re.S):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if all(isinstance(obj.get(k), str) and obj[k].strip() for k in ('name', 'username', 'bio', 'persona')):
            return obj
    return None


def write_persona(client, model, person, attempts=3):
    prompt = PERSONA_PROMPT.format(
        age=person['age'], gender=person['gender'], country=person['country'],
        occupation=person['occupation'], mbti=person['mbti'], interests=', '.join(person['interests']))
    last = None
    for _ in range(attempts):
        resp = client.chat.completions.create(model=model, messages=[{'role': 'user', 'content': prompt}])
        obj = _parse(resp.choices[0].message.content)
        if obj and not BANNED.search(obj['bio'] + ' ' + obj['persona']):
            return obj, None
        last = 'banned theme' if obj else 'unparseable'
    # Fallback keeps the run going; flagged in personas_meta.json
    return dict(
        name=f"User {person['agent_id']}",
        username=f"user{person['agent_id']}",
        bio=f"{person['occupation'].capitalize()} from {person['country']}. Into {', '.join(person['interests'])}.",
        persona=(f"A {person['age']}-year-old {person['occupation']} from {person['country']} who posts "
                 f"about {', '.join(person['interests'])} in a casual, friendly way."),
    ), last


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--n', type=int, default=48)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--out-dir')
    p.add_argument('--workers', type=int, default=8)
    args = p.parse_args()

    out_dir = args.out_dir or os.path.join(_BACKEND, 'uploads', 'simulations',
                                           f'sim_general_s{args.seed}_n{args.n}')
    if os.path.exists(os.path.join(out_dir, 'simulation_config.json')):
        sys.exit(f"{out_dir} already has a simulation; pick another --out-dir or --seed")
    os.makedirs(out_dir, exist_ok=True)

    model = os.environ.get('LLM_MODEL_NAME')
    client = OpenAI(api_key=os.environ.get('LLM_API_KEY', 'ollama'), base_url=os.environ.get('LLM_BASE_URL'))
    people = draw_attributes(args.n, args.seed)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda person: write_persona(client, model, person), people))

    usernames = set()
    for person, (text, error) in zip(people, results):
        handle = re.sub(r'[^a-z0-9_]', '', text['username'].lower()) or f"user{person['agent_id']}"
        while handle in usernames:
            handle += str(person['agent_id'])
        usernames.add(handle)
        person.update(name=text['name'].strip(), username=handle, bio=text['bio'].strip(),
                      persona=text['persona'].strip(), persona_fallback=error)

    with open(os.path.join(out_dir, 'twitter_profiles.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['user_id', 'name', 'username', 'user_char', 'description'])
        for person in people:
            w.writerow([person['agent_id'], person['name'], person['username'],
                        f"{person['bio']} {person['persona']}", person['bio']])

    today = datetime.now().strftime('%Y-%m-%d')
    reddit = [dict(user_id=person['agent_id'], username=person['username'], name=person['name'],
                   bio=person['bio'], persona=person['persona'], karma=1000, created_at=today,
                   age=person['age'], gender=person['gender'], mbti=person['mbti'],
                   country=person['country'], profession=person['occupation'],
                   interested_topics=person['interests']) for person in people]
    json.dump(reddit, open(os.path.join(out_dir, 'reddit_profiles.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    rng = random.Random(f"posts:{args.seed}")
    posters = rng.sample(range(args.n), min(len(INITIAL_POSTS), args.n))
    sim_id = os.path.basename(out_dir.rstrip('/'))
    config = dict(
        simulation_id=sim_id,
        project_id=None,
        graph_id=None,
        simulation_requirement='General-public population for oTree game experiments',
        time_config=dict(
            total_simulation_hours=168, minutes_per_round=60,
            agents_per_hour_min=8, agents_per_hour_max=12,
            peak_hours=[19, 20, 21, 22], peak_activity_multiplier=1.5,
            off_peak_hours=[0, 1, 2, 3, 4, 5], off_peak_activity_multiplier=0.3,
        ),
        agent_configs=[dict(
            agent_id=person['agent_id'], entity_uuid=None, entity_name=person['name'],
            entity_type='GeneralPublic', activity_level=person['activity_level'],
            posts_per_hour=0.5, comments_per_hour=0.5, active_hours=list(range(8, 23)),
            response_delay_min=5, response_delay_max=60, sentiment_bias=0.0,
            stance='neutral', influence_weight=1.0,
        ) for person in people],
        event_config=dict(initial_posts=[
            dict(content=content, poster_type='GeneralPublic', poster_agent_id=agent_id)
            for content, agent_id in zip(INITIAL_POSTS, posters)
        ]),
        twitter_config=dict(platform='twitter', recency_weight=0.4, popularity_weight=0.3,
                            relevance_weight=0.3, viral_threshold=10, echo_chamber_strength=0.5),
        reddit_config=dict(platform='reddit', recency_weight=0.3, popularity_weight=0.4,
                           relevance_weight=0.3, viral_threshold=15, echo_chamber_strength=0.6),
        llm_model=model,
        llm_base_url=os.environ.get('LLM_BASE_URL'),
        generated_at=datetime.now().isoformat(),
        generation_reasoning=f'make_general_sim.py seed={args.seed} n={args.n}',
    )
    json.dump(config, open(os.path.join(out_dir, 'simulation_config.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump(dict(seed=args.seed, n=args.n, model=model, people=people),
              open(os.path.join(out_dir, 'personas_meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    fallbacks = sum(1 for person in people if person['persona_fallback'])
    print(f"wrote {out_dir}: {args.n} personas ({fallbacks} fallback)")


if __name__ == '__main__':
    main()
