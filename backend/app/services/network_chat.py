"""
Network communication with heterogeneous contact rates (NOTES.md #54).

Pair chat (#47) lets the two partners of a game talk, which fixed cooperation
at 1.00 (#50) and leaves no room for the question of the study, how talk
between people who are NOT each other's partner changes behaviour. Here every
agent sits on a fixed social graph, draws how many conversations it starts in
a round from its own contact rate lambda_i (gamma-Poisson, so a few agents
talk a lot and many talk little), and the conversations are one to one with
graph neighbours. The game partner / group members are never neighbours
(#51), so nothing said can be settled with the person it concerns.

Everything here is pure and seeded: graph and lambdas depend on (seed,
topology, agents) only, never on the session code, so sessions in different
conditions with the same seed share the same network (paired comparison).
The only I/O is participant_names (reads the simulation's persona files).
"""

import hashlib
import json
import os
import random
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import networkx as nx
import numpy as np

NET_TOPOLOGIES = ('none', 'er', 'ba', 'ws', 'ring')
LAMBDA_ASSIGNS = ('random', 'degree')
NET_IDENTITIES = ('profile', 'anon')

MAPPING_RESTARTS = 20
MAPPING_SWAPS = 5000
ER_TRIES = 200


def _graph_seed(seed: int, topology: str) -> int:
    return int(hashlib.sha256(f"graph:{seed}:{topology}".encode()).hexdigest()[:8], 16)


def _even_k(k: float, n: int) -> int:
    """Ring lattice degree: k rounded to even, at least 2, below n."""
    kk = max(2, 2 * int(round(k / 2)))
    if kk >= n:
        kk = max(2, (n - 1) // 2 * 2)
    return kk


def _abstract_graph(n: int, topology: str, k: float, ws_p: float, graph_seed: int):
    """-> (graph on nodes 0..n-1, abstract repair edges, params)."""
    params: Dict[str, Any] = {}
    repairs: List[Tuple[int, int]] = []
    if topology == 'er':
        m = min(n * (n - 1) // 2, int(round(n * k / 2)))
        g = None
        for t in range(ER_TRIES):
            g = nx.gnm_random_graph(n, m, seed=graph_seed + t)
            if nx.is_connected(g):
                break
        if not nx.is_connected(g):
            rng = random.Random(graph_seed)
            comps = [sorted(c) for c in nx.connected_components(g)]
            for other in comps[1:]:
                u, v = rng.choice(comps[0]), rng.choice(other)
                g.add_edge(u, v)
                repairs.append((u, v))
                comps[0] = comps[0] + other
    elif topology == 'ba':
        m = max(1, int(round(k / 2)))
        if m >= n:
            raise ValueError(f"ba needs more agents than m={m}, got {n}")
        params['ba_m'] = m
        g = nx.barabasi_albert_graph(n, m, seed=graph_seed)
    elif topology in ('ws', 'ring'):
        kk = _even_k(k, n)
        params['ws_k'] = kk
        if topology == 'ws':
            g = nx.connected_watts_strogatz_graph(n, kk, ws_p, tries=200, seed=graph_seed)
        else:
            g = nx.watts_strogatz_graph(n, kk, 0, seed=graph_seed)
    else:
        raise ValueError(f"unknown net_topology: {topology} (expected one of {NET_TOPOLOGIES[1:]})")
    return g, repairs, params


def _overlap(edges, perm: List[int], excl_idx: List[Set[int]]) -> int:
    return sum(1 for u, v in edges if perm[v] in excl_idx[perm[u]])


def _map_nodes(edges: List[Tuple[int, int]], agent_ids: List[int],
               exclusions: Dict[int, Set[int]], seed: int) -> List[int]:
    """Assign agents to abstract nodes so that no edge joins an excluded pair.

    perm[node] = index into agent_ids. Seeded random permutation, then
    hill-climbing by random swaps (accepted when the number of excluded edges
    does not grow), restarted up to MAPPING_RESTARTS times; stops at zero.
    """
    n = len(agent_ids)
    pos = {a: i for i, a in enumerate(agent_ids)}
    excl_idx = [{pos[x] for x in exclusions.get(a, ()) if x in pos} for a in agent_ids]
    rng = random.Random(seed)
    best, best_score = list(range(n)), None
    for _ in range(MAPPING_RESTARTS):
        perm = list(range(n))
        rng.shuffle(perm)
        score = _overlap(edges, perm, excl_idx)
        for _ in range(MAPPING_SWAPS):
            if score == 0:
                break
            i, j = rng.randrange(n), rng.randrange(n)
            if i == j:
                continue
            perm[i], perm[j] = perm[j], perm[i]
            new = _overlap(edges, perm, excl_idx)
            if new <= score:
                score = new
            else:
                perm[i], perm[j] = perm[j], perm[i]
        if best_score is None or score < best_score:
            best, best_score = list(perm), score
        if best_score == 0:
            break
    return best


def build_network(agent_ids: Iterable[int], exclusions: Dict[int, Set[int]], topology: str,
                  k: float, ws_p: float, seed: int) -> Dict[str, Any]:
    """Social graph over the agents with no edge between excluded pairs.

    Args:
        exclusions: agent -> agents it must not be linked to (game partner /
            group members, #51)
        k: target mean degree. er: exactly round(N*k/2) edges; ba:
            m = max(1, round(k/2)); ws / ring: k rounded to even, at least 2

    Returns:
        {neighbors, edges, stats, layout, graph_seed, params,
         excluded_edges_dropped, repair_edges}
    """
    ids = sorted(int(a) for a in agent_ids)
    n = len(ids)
    if n < 3:
        raise ValueError(f"a network needs at least 3 agents, got {n}")
    gseed = _graph_seed(seed, topology)
    g, abstract_repairs, params = _abstract_graph(n, topology, k, ws_p, gseed)
    abstract_edges = [(int(u), int(v)) for u, v in g.edges()]
    perm = _map_nodes(abstract_edges, ids, exclusions, gseed)

    def agent_edge(u: int, v: int) -> Tuple[int, int]:
        a, b = ids[perm[u]], ids[perm[v]]
        return (min(a, b), max(a, b))

    def excluded(a: int, b: int) -> bool:
        return b in exclusions.get(a, ()) or a in exclusions.get(b, ())

    edge_set: Set[Tuple[int, int]] = set()
    dropped: List[List[int]] = []
    for u, v in abstract_edges:
        e = agent_edge(u, v)
        if excluded(*e):
            dropped.append(list(e))
        else:
            edge_set.add(e)
    repairs = [list(agent_edge(u, v)) for u, v in abstract_repairs if agent_edge(u, v) in edge_set]

    rng = random.Random(gseed + 1)
    degree = {a: 0 for a in ids}
    for a, b in edge_set:
        degree[a] += 1
        degree[b] += 1
    for a in ids:  # an agent left without contacts gets one eligible neighbour
        if degree[a] == 0:
            eligible = [b for b in ids if b != a and not excluded(a, b)]
            if eligible:
                b = rng.choice(eligible)
                edge_set.add((min(a, b), max(a, b)))
                degree[a] += 1
                degree[b] += 1
                repairs.append([min(a, b), max(a, b)])

    edges = sorted(edge_set)
    neighbors: Dict[int, List[int]] = {a: [] for a in ids}
    for a, b in edges:
        neighbors[a].append(b)
        neighbors[b].append(a)
    neighbors = {a: sorted(v) for a, v in neighbors.items()}

    G = nx.Graph()
    G.add_nodes_from(ids)
    G.add_edges_from(edges)
    connected = nx.is_connected(G)
    try:
        with np.errstate(all='ignore'):  # regular graphs: 0 / 0 -> nan -> None
            assort = nx.degree_assortativity_coefficient(G)
        assort = None if assort != assort else round(float(assort), 4)
    except (ValueError, ZeroDivisionError):
        assort = None
    degs = [d for _, d in G.degree()]
    stats = dict(n=n, m=len(edges), mean_degree=round(2 * len(edges) / n, 3), max_degree=max(degs),
                 clustering=round(float(nx.average_clustering(G)), 4), connected=bool(connected),
                 diameter=int(nx.diameter(G)) if connected else None, degree_assortativity=assort)
    layout = {str(a): [round(float(x), 4), round(float(y), 4)]
              for a, (x, y) in nx.spring_layout(G, seed=0).items()}
    return dict(neighbors=neighbors, edges=[list(e) for e in edges], stats=stats, layout=layout,
                graph_seed=gseed, params=params, excluded_edges_dropped=dropped, repair_edges=repairs)


def draw_lambdas(agent_ids: Iterable[int], mu: float, r: float, seed: int,
                 assign: str = 'random', degrees: Optional[Dict[int, int]] = None) -> Dict[int, float]:
    """Contact rate per agent: gamma(shape r, mean mu), so the number of
    conversations an agent starts, Poisson(lambda_i), is negative binomial
    with variance mu + mu^2 / r. r <= 0: everybody has lambda = mu (Poisson).

    assign 'degree': the largest lambda goes to the highest-degree agent
    (ties by id); 'random': sorted-id order, independent of the graph.
    """
    ids = sorted(int(a) for a in agent_ids)
    if r <= 0:
        return {a: float(mu) for a in ids}
    rng = np.random.default_rng([seed, 1])
    lam = rng.gamma(r, mu / r, len(ids))
    if assign == 'degree' and degrees is not None:
        lam = np.sort(lam)[::-1]
        ids_by_degree = sorted(ids, key=lambda a: (-degrees.get(a, 0), a))
        return {a: float(x) for a, x in zip(ids_by_degree, lam)}
    return {a: float(x) for a, x in zip(ids, lam)}


def sample_contacts(neighbors: Dict[int, List[int]], lambdas: Dict[int, float], round_number: int,
                    seed: int, max_initiate: int, max_load: int
                    ) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Who starts a conversation with whom this round.

    k_drawn ~ Poisson(lambda_i); k_target = min(k_drawn, max_initiate,
    degree). In random order, round-robin over s = 1..max_initiate, an agent
    with k_target >= s picks a uniformly random neighbour whose pair is unused
    this round and whose load (initiated + received) is below max_load. No
    candidate, or the agent itself at max_load: the contact is dropped.

    Returns:
        (conversations [{conv_id, index, initiator, responder}],
         per-agent record {lambda, k_drawn, k_realized, initiated, received, dropped})
    """
    ids = sorted(neighbors)
    rng = np.random.default_rng([seed, 2, round_number])
    k_drawn = {a: int(rng.poisson(lambdas[a])) for a in ids}
    k_target = {a: min(k_drawn[a], max_initiate, len(neighbors[a])) for a in ids}
    order = [int(a) for a in rng.permutation(ids)]
    load = {a: 0 for a in ids}
    used: Set[Tuple[int, int]] = set()
    rec = {a: dict(**{'lambda': round(lambdas[a], 4)}, k_drawn=k_drawn[a], k_realized=0,
                   initiated=[], received=[], dropped=0) for a in ids}
    convs: List[Dict[str, Any]] = []
    for s in range(1, max_initiate + 1):
        for i in order:
            if k_target[i] < s:
                continue
            if load[i] >= max_load:
                rec[i]['dropped'] += 1
                continue
            cands = [j for j in neighbors[i]
                     if (min(i, j), max(i, j)) not in used and load[j] < max_load]
            if not cands:
                rec[i]['dropped'] += 1
                continue
            j = cands[int(rng.integers(len(cands)))]
            used.add((min(i, j), max(i, j)))
            load[i] += 1
            load[j] += 1
            index = len(convs)
            convs.append(dict(conv_id=f"r{round_number}c{index}", index=index, initiator=i, responder=j))
            rec[i]['initiated'].append(j)
            rec[j]['received'].append(i)
            rec[i]['k_realized'] += 1
    return convs, rec


def _speaker(conv: Dict[str, Any], turn: int) -> int:
    return conv['initiator'] if turn % 2 == 0 else conv['responder']


def next_wave(active_convs: List[Dict[str, Any]], turns: int) -> List[Tuple[Dict[str, Any], int]]:
    """Next messages to write, one wave = one batched interview.

    The speaker of a conversation's next message is the initiator at even
    turns, the responder at odd turns. Each agent speaks at most once per
    wave; when an agent could speak in several conversations, the one where
    the speaker has most messages still to write goes first, then the lower
    conversation index. Conversations with all turns done or aborted are
    skipped.

    Returns:
        [(conv, speaker_id)]
    """
    cands = []
    remaining: Dict[int, int] = {}
    live = [c for c in active_convs if not c.get('aborted') and len(c['messages']) < turns]
    for c in live:
        for t in range(len(c['messages']), turns):
            s = _speaker(c, t)
            remaining[s] = remaining.get(s, 0) + 1
    for c in live:
        s = _speaker(c, len(c['messages']))
        cands.append((-remaining[s], c['index'], s, c))
    wave, busy = [], set()
    for _, _, s, c in sorted(cands, key=lambda x: x[:2]):
        if s in busy:
            continue
        busy.add(s)
        wave.append((c, s))
    return wave


def participant_names(sim_dir: Optional[str], ids: Iterable[int], identity: str = 'profile'
                      ) -> Dict[int, Dict[str, str]]:
    """{id: {name, short, display}} for how agents refer to each other.

    profile: personas_meta.json (display "{name} ({department}, {company})"),
    else reddit_profiles.json ("{name}, {profession}"), else "Participant n".
    anon: "Participant {id+1}" for everybody.
    """
    ids = sorted(int(a) for a in ids)

    def anon(a):
        label = f"Participant {a + 1}"
        return dict(name=label, short=label, display=label)

    if identity == 'anon' or not sim_dir:
        return {a: anon(a) for a in ids}
    meta: Dict[int, Dict[str, str]] = {}
    try:
        with open(os.path.join(sim_dir, 'personas_meta.json'), encoding='utf-8') as f:
            for p in json.load(f).get('people', []):
                name = p.get('name')
                if name:
                    extra = ', '.join(x for x in (p.get('department'), p.get('company')) if x)
                    meta[int(p['agent_id'])] = dict(
                        name=name, short=name, display=f"{name} ({extra})" if extra else name)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    profiles: List[Any] = []
    if not all(a in meta for a in ids):
        try:
            with open(os.path.join(sim_dir, 'reddit_profiles.json'), encoding='utf-8') as f:
                profiles = json.load(f)
        except (OSError, ValueError):
            profiles = []
    out = {}
    for a in ids:
        if a in meta:
            out[a] = meta[a]
        elif isinstance(profiles, list) and 0 <= a < len(profiles) and isinstance(profiles[a], dict) \
                and profiles[a].get('name'):
            p = profiles[a]
            prof = p.get('profession')
            out[a] = dict(name=p['name'], short=p['name'],
                          display=f"{p['name']}, {prof}" if prof else p['name'])
        else:
            out[a] = anon(a)
    return out


def conversation_view(convs: List[Dict[str, Any]], agent_id: int,
                      names: Dict[int, Dict[str, str]]) -> List[Dict[str, Any]]:
    """This agent's non-empty conversations from its own point of view, in
    (round, index) order: [{round_number, conv_id, other, other_display,
    messages: [{who: 'You' | other's short name, text}]}]."""
    out = []
    for c in sorted(convs, key=lambda c: (c['round_number'], c['index'])):
        if agent_id not in (c['initiator'], c['responder']) or not c['messages']:
            continue
        other = c['responder'] if c['initiator'] == agent_id else c['initiator']
        nm = names.get(other) or dict(short=f"Participant {other + 1}", display=f"Participant {other + 1}")
        out.append(dict(
            round_number=c['round_number'], conv_id=c['conv_id'], other=nm['short'],
            other_display=nm['display'],
            messages=[dict(who='You' if m['agent_id'] == agent_id else nm['short'], text=m['text'])
                      for m in c['messages']]))
    return out
