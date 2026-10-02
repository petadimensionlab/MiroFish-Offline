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
The only I/O is participant_names and channel_profiles (read the simulation's
persona files).

Dyads (NOTES.md #55): with persona channels (#53) every pair gets a
compatibility A_ij from (a) how alike the two people's channel profiles are
(topic) and (b) how reliably they answer each other on peer media (medium).
A changes who talks to whom (exp(beta * z_ij) weights on the neighbour choice)
and whether a conversation is answered. All of it is off by default: with no
DyadSampling, sample_contacts is the #54 code line for line and never touches
the extra random stream.
"""

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
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
                    seed: int, max_initiate: int, max_load: int,
                    dyad: Optional['DyadSampling'] = None
                    ) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Who starts a conversation with whom this round.

    k_drawn ~ Poisson(lambda_i); k_target = min(k_drawn, max_initiate,
    degree). In random order, round-robin over s = 1..max_initiate, an agent
    with k_target >= s picks a uniformly random neighbour whose pair is unused
    this round and whose load (initiated + received) is below max_load. No
    candidate, or the agent itself at max_load: the contact is dropped.

    dyad (NOTES.md #55), None = everything above unchanged: with weights the
    neighbour is drawn with probability proportional to weights[i][j] instead
    of uniformly (one rng.random() instead of one rng.integers(); beta 0 gives
    weights None and so the uniform line). Independently, each created
    conversation draws its medium and its reply from a second stream
    default_rng([seed, 3, round]), two draws per conversation in creation
    order, so the contacts of the main stream never depend on them. Under
    reply_model 'reach' a conversation whose reply draw fails is unanswered:
    it uses up the pair for the round and the initiator's load, but not the
    responder's, and does not count as received.

    Returns:
        (conversations [{conv_id, index, initiator, responder}] (dyad: plus
         medium, reply_draw, replied),
         per-agent record {lambda, k_drawn, k_realized, initiated, received, dropped}
         (dyad: plus unanswered, ignored))
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
    rng_ch = None
    if dyad is not None:
        rng_ch = np.random.default_rng([seed, 3, round_number])
        for a in ids:
            rec[a]['unanswered'] = 0
            rec[a]['ignored'] = []
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
            if dyad is None or dyad.weights is None:
                j = cands[int(rng.integers(len(cands)))]
            else:
                cum = np.cumsum([dyad.weights[i][c] for c in cands])
                j = cands[min(len(cands) - 1, int(np.searchsorted(cum, rng.random() * cum[-1], side='right')))]
            used.add((min(i, j), max(i, j)))
            load[i] += 1
            index = len(convs)
            conv = dict(conv_id=f"r{round_number}c{index}", index=index, initiator=i, responder=j)
            replied = True
            if dyad is not None:
                u_m, u_r = float(rng_ch.random()), float(rng_ch.random())
                medium = _pick_medium(dyad.pi[i], u_m)
                reply_draw = u_r < dyad.reply_prob[j][medium]
                replied = reply_draw or dyad.reply_model != 'reach'
                conv.update(medium=medium, reply_draw=bool(reply_draw), replied=bool(replied))
            if replied:
                load[j] += 1
            convs.append(conv)
            rec[i]['initiated'].append(j)
            rec[i]['k_realized'] += 1
            if replied:
                rec[j]['received'].append(i)
            else:
                rec[i]['unanswered'] += 1
                rec[j]['ignored'].append(i)
    return convs, rec


# -- dyad compatibility (NOTES.md #55) ---------------------------------------------

LEVEL_VALUE = {'ignore': 0.0, 'skim': 1 / 3, 'read': 2 / 3, 'act': 1.0}
# Media two colleagues use one to one; the only ones that say how reliably A
# reaches B (broadcast media say nothing about a personal conversation)
PEER_MEDIA = ('direct_email', 'direct_message', 'enterprise_chat', 'one_on_one', 'team_meeting')
SEND_WEIGHT = {'rarely': 1.0, 'sometimes': 2.0, 'habitually': 3.0}
REPLY_PROB = {'rarely': 0.2, 'sometimes': 0.6, 'habitually': 0.95}
REPLY_MODELS = ('always', 'reach')
# How the medium of a conversation is named in the chat prompt (net_channel_prompt 'medium')
MEDIUM_PHRASE = {'direct_email': ' by email', 'direct_message': ' in a direct message',
                 'enterprise_chat': ' on the team chat', 'one_on_one': ' in a one-on-one meeting',
                 'team_meeting': ' after a team meeting'}
SHARED_TOP = 3


def channel_profiles(sim_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    """Channel levels and media habits of everybody in the simulation (#53).

    Returns None when the simulation has no channels.json / no person with
    channels. Otherwise {channels: [id], media: [PEER_MEDIA], people:
    {agent_id: {levels: [0..1 per channel], habits: {medium: habit},
    engagement}}, sha256 of channels.json}. The population is everybody in
    personas_meta.json, not only the agents of one session.
    """
    if not sim_dir:
        return None
    try:
        with open(os.path.join(sim_dir, 'channels.json'), 'rb') as f:
            raw = f.read()
        tax = json.loads(raw.decode('utf-8'))
        with open(os.path.join(sim_dir, 'personas_meta.json'), encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    channels = [c['id'] for c in tax.get('channels', []) if isinstance(c, dict) and 'id' in c]
    media_ids = {m['id'] for m in tax.get('media', []) if isinstance(m, dict) and 'id' in m}
    if not channels:
        return None
    missing = [m for m in PEER_MEDIA if m not in media_ids]
    if missing:
        raise ValueError(f"channels.json lacks the peer media {missing}")
    people: Dict[int, Dict[str, Any]] = {}
    for p in meta.get('people', []):
        ch = p.get('channels')
        if not isinstance(ch, dict) or not ch:
            continue
        levels = [LEVEL_VALUE.get((ch.get(c) or {}).get('level'), 0.0) for c in channels]
        habits = {m: (p.get('media_habits') or {}).get(m, 'sometimes') for m in PEER_MEDIA}
        people[int(p['agent_id'])] = dict(levels=levels, habits=habits,
                                          engagement=p.get('channel_engagement'))
    if not people:
        return None
    return dict(channels=channels, media=list(PEER_MEDIA), people=people,
                sha256=hashlib.sha256(raw).hexdigest())


@dataclass
class Compat:
    """Dyad compatibility over a whole population (pairs keyed (a, b) with a < b;
    reach keyed (i, j) for i writing to j)."""
    ids: List[int]
    topic: Dict[Tuple[int, int], float]
    media: Dict[Tuple[int, int], float]
    compat: Dict[Tuple[int, int], float]
    z: Dict[Tuple[int, int], float]
    reach: Dict[Tuple[int, int], float]
    pi: Dict[int, List[Tuple[str, float]]]
    reply_prob: Dict[int, Dict[str, float]]
    shared: Dict[Tuple[int, int], List[str]]
    pop_mean: float
    pop_sd: float
    topic_weight: float = 0.5


def compatibility(profiles: Dict[str, Any], topic_weight: float = 0.5) -> Compat:
    """A_ij = w * T_ij + (1 - w) * M_ij (NOTES.md #55).

    T_ij = (1 + cos) / 2 of the channel-level vectors, each channel centred by
    its mean over ALL people (raw cosines are 0.48-0.97, dominated by overall
    engagement). M_ij = mean of R_ij and R_ji, R_i->j = sum_m pi_i(m) *
    REPLY_PROB[habit_j(m)] with pi_i(m) proportional to SEND_WEIGHT of i's
    habit: how likely a message i writes by its usual medium reaches j.
    z_ij standardises A over all population pairs.
    """
    if not 0.0 <= topic_weight <= 1.0:
        raise ValueError(f"topic_weight must be in [0, 1], got {topic_weight}")
    ids = sorted(profiles['people'])
    n = len(ids)
    media = profiles['media']
    X = np.array([profiles['people'][a]['levels'] for a in ids], dtype=float)
    Xc = X - X.mean(axis=0)
    norm = np.linalg.norm(Xc, axis=1)
    with np.errstate(all='ignore'):
        cos = (Xc @ Xc.T) / np.outer(norm, norm)
    zero = norm == 0
    cos[zero, :] = 0.0
    cos[:, zero] = 0.0
    T = (1.0 + cos) / 2.0
    send = np.array([[SEND_WEIGHT[profiles['people'][a]['habits'][m]] for m in media] for a in ids])
    Pi = send / send.sum(axis=1, keepdims=True)
    Rp = np.array([[REPLY_PROB[profiles['people'][a]['habits'][m]] for m in media] for a in ids])
    R = Pi @ Rp.T
    M = (R + R.T) / 2.0
    A = topic_weight * T + (1.0 - topic_weight) * M
    iu = np.triu_indices(n, 1)
    vals = A[iu]
    mean = float(vals.mean()) if len(vals) else 0.0
    sd = float(vals.std()) if len(vals) else 0.0
    levels = X
    topic, med, comp, zz, shared = {}, {}, {}, {}, {}
    for x, y in zip(*iu):
        key = (ids[x], ids[y])
        topic[key] = float(T[x, y])
        med[key] = float(M[x, y])
        comp[key] = float(A[x, y])
        zz[key] = float((A[x, y] - mean) / sd) if sd > 0 else 0.0
        both = [(min(levels[x, c], levels[y, c]), levels[x, c] + levels[y, c], -c)
                for c in range(levels.shape[1]) if min(levels[x, c], levels[y, c]) >= LEVEL_VALUE['read'] - 1e-9]
        shared[key] = [profiles['channels'][-c] for _, _, c in sorted(both, reverse=True)[:SHARED_TOP]]
    reach = {(ids[x], ids[y]): float(R[x, y]) for x in range(n) for y in range(n) if x != y}
    pi = {ids[x]: [(m, float(Pi[x, k])) for k, m in enumerate(media)] for x in range(n)}
    reply = {ids[x]: {m: float(Rp[x, k]) for k, m in enumerate(media)} for x in range(n)}
    return Compat(ids=ids, topic=topic, media=med, compat=comp, z=zz, reach=reach, pi=pi,
                  reply_prob=reply, shared=shared, pop_mean=mean, pop_sd=sd, topic_weight=topic_weight)


@dataclass
class DyadSampling:
    """What sample_contacts needs of the compatibility: neighbour weights
    (None = uniform, beta 0), each agent's medium distribution, each
    responder's reply probability per medium, and the reply model."""
    weights: Optional[Dict[int, Dict[int, float]]]
    pi: Dict[int, List[Tuple[str, float]]]
    reply_prob: Dict[int, Dict[str, float]]
    reply_model: str = 'always'


def dyad_sampling(compat: Compat, neighbors: Dict[int, List[int]], beta: float,
                  reply_model: str = 'always') -> DyadSampling:
    """Weights exp(beta * z_ij) on the graph's edges (None when beta == 0)."""
    if beta < 0:
        raise ValueError(f"net_channel_beta must be >= 0, got {beta}")
    if reply_model not in REPLY_MODELS:
        raise ValueError(f"unknown net_reply_model: {reply_model} (expected one of {REPLY_MODELS})")
    weights = None
    if beta > 0:
        weights = {i: {j: math.exp(beta * compat.z[(min(i, j), max(i, j))]) for j in nb}
                   for i, nb in neighbors.items()}
    return DyadSampling(weights=weights, pi=compat.pi, reply_prob=compat.reply_prob,
                        reply_model=reply_model)


def _pick_medium(pi: List[Tuple[str, float]], u: float) -> str:
    acc = 0.0
    for medium, p in pi:
        acc += p
        if u < acc:
            return medium
    return pi[-1][0]


def _speaker(conv: Dict[str, Any], turn: int) -> int:
    return conv['initiator'] if turn % 2 == 0 else conv['responder']


def _limit(c: Dict[str, Any], turns: int) -> int:
    return c.get('max_turns', turns)


def next_wave(active_convs: List[Dict[str, Any]], turns: int) -> List[Tuple[Dict[str, Any], int]]:
    """Next messages to write, one wave = one batched interview.

    The speaker of a conversation's next message is the initiator at even
    turns, the responder at odd turns. Each agent speaks at most once per
    wave; when an agent could speak in several conversations, the one where
    the speaker has most messages still to write goes first, then the lower
    conversation index. Conversations with all turns done or aborted are
    skipped. A conversation may carry its own max_turns (an unanswered one has
    1, NOTES.md #55).

    Returns:
        [(conv, speaker_id)]
    """
    cands = []
    remaining: Dict[int, int] = {}
    live = [c for c in active_convs if not c.get('aborted') and len(c['messages']) < _limit(c, turns)]
    for c in live:
        for t in range(len(c['messages']), _limit(c, turns)):
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

    When a name is not unique among ids (the workplace personas have two
    "Elena V."), short becomes "{name} ({department})" ("{name} ({profession})"
    for general personas), with " #{id}" added to short and display if that is
    still ambiguous (NOTES.md #55). Sessions without duplicates are unchanged.
    """
    ids = sorted(int(a) for a in ids)

    def anon(a):
        label = f"Participant {a + 1}"
        return dict(name=label, short=label, display=label)

    if identity == 'anon' or not sim_dir:
        return {a: anon(a) for a in ids}
    meta: Dict[int, Dict[str, str]] = {}
    tags: Dict[int, Optional[str]] = {}
    try:
        with open(os.path.join(sim_dir, 'personas_meta.json'), encoding='utf-8') as f:
            for p in json.load(f).get('people', []):
                name = p.get('name')
                if name:
                    extra = ', '.join(x for x in (p.get('department'), p.get('company')) if x)
                    meta[int(p['agent_id'])] = dict(
                        name=name, short=name, display=f"{name} ({extra})" if extra else name)
                    tags[int(p['agent_id'])] = p.get('department')
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
            tags[a] = prof
        else:
            out[a] = anon(a)
    counts: Dict[str, int] = {}
    for a in ids:
        counts[out[a]['short']] = counts.get(out[a]['short'], 0) + 1
    dup = [a for a in ids if counts[out[a]['short']] > 1 and out[a]['name'] == out[a]['short']]
    if dup:
        for a in dup:
            if tags.get(a):
                out[a] = dict(out[a], short=f"{out[a]['name']} ({tags[a]})")
        counts = {}
        for a in ids:
            counts[out[a]['short']] = counts.get(out[a]['short'], 0) + 1
        for a in dup:
            if counts[out[a]['short']] > 1:
                out[a] = dict(out[a], short=f"{out[a]['short']} #{a}", display=f"{out[a]['display']} #{a}")
    return out


def conversation_view(convs: List[Dict[str, Any]], agent_id: int,
                      names: Dict[int, Dict[str, str]]) -> List[Dict[str, Any]]:
    """This agent's non-empty conversations from its own point of view, in
    (round, index) order: [{round_number, conv_id, other, other_display,
    messages: [{who: 'You' | other's short name, text}]}] (+ unanswered: True
    for a conversation with replied False)."""
    out = []
    for c in sorted(convs, key=lambda c: (c['round_number'], c['index'])):
        if agent_id not in (c['initiator'], c['responder']) or not c['messages']:
            continue
        other = c['responder'] if c['initiator'] == agent_id else c['initiator']
        nm = names.get(other) or dict(short=f"Participant {other + 1}", display=f"Participant {other + 1}")
        view = dict(
            round_number=c['round_number'], conv_id=c['conv_id'], other=nm['short'],
            other_display=nm['display'],
            messages=[dict(who='You' if m['agent_id'] == agent_id else nm['short'], text=m['text'])
                      for m in c['messages']])
        if c.get('replied') is False:  # #55: written, never answered
            view['unanswered'] = True
        out.append(view)
    return out
