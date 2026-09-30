"""Tests for scripts/experiment/add_channels.py (persona channels).

The build tests run on the real source simulation and are skipped when it is
missing; the source directory is only read (sha256 checked).
"""

import copy
import csv
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts", "experiment"))

import add_channels as ac  # noqa: E402

SRC = ac.DEFAULT_SRC
TAX = ac.DEFAULT_TAXONOMY
needs_src = pytest.mark.skipif(not os.path.isfile(os.path.join(SRC, "personas_meta.json")),
                               reason="source simulation missing")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not os.path.isfile(os.path.join(SRC, "personas_meta.json")):
        pytest.skip("source simulation missing")
    before = ac._source_hashes(SRC)
    d = tmp_path_factory.mktemp("ch")
    out = str(d / "sim_ch")
    ac.build(SRC, out, TAX)
    out2 = str(d / "sim_ch2")
    ac.build(SRC, out2, TAX)
    assert ac._source_hashes(SRC) == before
    return out, out2


def load(d, f):
    return json.load(open(os.path.join(d, f), encoding="utf-8"))


def test_taxonomy_loads_and_is_complete():
    tax = ac.load_taxonomy(TAX)
    assert len(tax["channels"]) == 35 and len(tax["media"]) == 19
    off = [c["id"] for c in tax["channels"] if not c["use_in_text"]]
    assert off == ["csr_volunteering"]
    for c in tax["channels"]:
        if c["use_in_text"]:
            assert not ac.SOFT_PRIMING.search(c["phrase"])


def test_load_taxonomy_rejects_priming(tmp_path):
    tax = json.load(open(TAX))
    bad = copy.deepcopy(tax)
    bad["channels"][0]["phrase"] = "charity drives"
    p = tmp_path / "t.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        ac.load_taxonomy(str(p))
    bad = copy.deepcopy(tax)
    bad["channels"][0]["description"] = "company strategy"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        ac.load_taxonomy(str(p))
    # use_in_text=false items may keep soft-priming words (but need a reason)
    ok = copy.deepcopy(tax)
    ok["channels"][0]["use_in_text"] = False
    ok["channels"][0]["reason"] = "x"
    ok["channels"][0]["phrase"] = "charity drives"
    p.write_text(json.dumps(ok))
    ac.load_taxonomy(str(p))


def test_normal_is_stable():
    assert ac._normal("channels:1:0:engagement") == ac._normal("channels:1:0:engagement")
    assert ac._normal("a") != ac._normal("b")


@needs_src
def test_build_refuses_source_and_existing(tmp_path):
    with pytest.raises(SystemExit):
        ac.build(SRC, SRC, TAX)
    (tmp_path / "x").mkdir()
    with pytest.raises(SystemExit):
        ac.build(SRC, str(tmp_path / "x"), TAX)


def test_verify_passes(built):
    out, _ = built
    errors, warnings = ac.verify(SRC, out, TAX, quiet=True)
    assert errors == []


def test_schema_and_levels(built):
    out, _ = built
    tax = ac.load_taxonomy(TAX)
    meta = load(out, "personas_meta.json")
    ch = load(out, "channels.json")
    cids = {c["id"] for c in tax["channels"]}
    mids = {m["id"] for m in tax["media"]}
    assert {c["id"] for c in ch["channels"]} == cids and {m["id"] for m in ch["media"]} == mids
    for p in meta["people"]:
        assert set(p["channels"]) == cids and set(p["media_habits"]) == mids
        assert all(v["level"] in ac.LEVELS for v in p["channels"].values())
        assert all(v in ac.MEDIA_LEVELS for v in p["media_habits"].values())
    # floors
    floors = {c["id"] for c in tax["channels"] if c["floor"] == "skim"}
    assert floors
    for p in meta["people"]:
        assert all(p["channels"][c]["level"] != "ignore" for c in floors)
    assert set(ch["summary"]) == cids


def test_distribution_extremes(built):
    out, _ = built
    meta = load(out, "personas_meta.json")
    n = len(meta["people"])
    for cid in meta["people"][0]["channels"]:
        lv = [p["channels"][cid]["level"] for p in meta["people"]]
        low = sum(x in ("ignore", "skim") for x in lv) / n
        high = sum(x in ("read", "act") for x in lv) / n
        assert low < 0.9 and high < 0.9, cid
    g = [p["channel_engagement"] for p in meta["people"]]
    assert 0.10 <= sum(x < -1 for x in g) / n <= 0.20


def test_priming_and_relationship(built):
    out, _ = built
    meta = load(out, "personas_meta.json")
    tax = ac.load_taxonomy(TAX)
    for p in meta["people"]:
        assert not ac.BANNED.search(p["channel_text"]) and not ac.SOFT_PRIMING.search(p["channel_text"])
        assert "volunteer" not in p["channel_text"].lower() and "community programme" not in p["channel_text"]
        assert len(p["channel_text"]) <= 420
        rel = ac._rel(p)
        assert p["persona"].endswith(rel)
        assert p["persona"] == p["persona_base"][: -len(rel)].strip() + " " + p["channel_text"] + " " + rel
    with open(os.path.join(out, "twitter_profiles.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            assert not ac.BANNED.search(row["user_char"])


def test_diff_vs_source(built):
    out, _ = built
    sm, om = load(SRC, "personas_meta.json"), load(out, "personas_meta.json")
    assert set(om) - set(sm) == {"channels_version", "channel_seed", "channels_source_sim", "channels_file"}
    for k in sm:
        if k != "people":
            assert om[k] == sm[k]
    for a, b in zip(sm["people"], om["people"]):
        for k in a:
            if k != "persona":
                assert a[k] == b[k]
    with open(os.path.join(SRC, "twitter_profiles.csv"), newline="", encoding="utf-8") as f:
        sr = list(csv.DictReader(f))
    with open(os.path.join(out, "twitter_profiles.csv"), newline="", encoding="utf-8") as f:
        orr = list(csv.DictReader(f))
    assert len(sr) == len(orr)
    for a, b in zip(sr, orr):
        assert a.keys() == b.keys()
        assert all(a[k] == b[k] for k in a if k != "user_char") and a["user_char"] != b["user_char"]
    for a, b in zip(load(SRC, "reddit_profiles.json"), load(out, "reddit_profiles.json")):
        assert set(a) == set(b) and all(a[k] == b[k] for k in a if k != "persona")
    ca, cb = load(SRC, "simulation_config.json"), load(out, "simulation_config.json")
    assert {k for k in ca if ca[k] != cb[k]} == {"simulation_id", "generation_reasoning"}
    assert cb["simulation_id"] == os.path.basename(out)


def test_reproducible_and_source_untouched(built):
    a, b = built
    ma, mb = load(a, "personas_meta.json"), load(b, "personas_meta.json")
    assert ma == mb
    ca, cb = load(a, "channels.json"), load(b, "channels.json")
    for d in (ca, cb):
        d["provenance"].pop("generated_at")
    assert ca == cb
    assert ac._source_hashes(SRC) == ca["provenance"]["source_sha256"]
    assert open(os.path.join(a, "twitter_profiles.csv"), "rb").read() == open(os.path.join(b, "twitter_profiles.csv"), "rb").read()


def test_seed_changes_assignment(tmp_path):
    if not os.path.isfile(os.path.join(SRC, "personas_meta.json")):
        pytest.skip("source simulation missing")
    tax = ac.load_taxonomy(TAX)
    people = load(SRC, "personas_meta.json")["people"]
    a, _ = ac.compute(people, tax, 1)
    b, _ = ac.compute(people, tax, 2)
    assert [u["channel_text"] for u in a] != [u["channel_text"] for u in b]


def test_sign_effects(built):
    out, _ = built
    people = load(out, "personas_meta.json")["people"]
    mean = lambda v: sum(v) / len(v)  # noqa: E731
    hr = [p["channels"]["benefits_insurance"]["score"] for p in people if p["department"] == "Human Resources"]
    rest = [p["channels"]["benefits_insurance"]["score"] for p in people if p["department"] != "Human Resources"]
    assert mean(hr) > mean(rest)
    it = [p["channels"]["it_security"]["score"] for p in people if p["department"] == "IT Infrastructure"]
    assert mean(it) > mean([p["channels"]["it_security"]["score"] for p in people])
