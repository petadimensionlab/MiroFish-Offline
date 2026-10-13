"""Tests for scripts/experiment/dashboard_compare.py (uses make_fixture from test_dashboard)."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts", "experiment"))

pytest.importorskip("altair")
import dashboard_compare as DC  # noqa: E402
from test_dashboard import SP, make_fixture, spec_from_html  # noqa: E402


def _two(tmp_path, game):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    pa, sa = make_fixture(a, game=game, seed=3)
    pb, sb = make_fixture(b, game=game, seed=7, n=12, network=False)
    return [("ba", str(pa), str(sa), None), ("none", str(pb), str(sb), None)]


@pytest.mark.parametrize("game", ["pd", "pgg"])
def test_compare_synthetic(tmp_path, game):
    res = DC.build(_two(tmp_path, game), str(tmp_path / "c.html"), "T")
    spec = spec_from_html(res["html"])
    assert spec["vconcat"]
    assert "SECRET" not in res["html"]
    assert "ba" in res["html"] and "synth001" in res["html"]


def test_parse_run():
    assert DC.parse_run("x=/a/b.csv:/sim@S1") == ("x", "/a/b.csv", "/sim", "S1")
    assert DC.parse_run("x=/a/b.csv") == ("x", "/a/b.csv", None, None)


def test_mixed_games_rejected(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    pa, sa = make_fixture(a, game="pd")
    pb, sb = make_fixture(b, game="pgg")
    with pytest.raises(SystemExit):
        DC.build([("x", str(pa), str(sa), None), ("y", str(pb), str(sb), None)], str(tmp_path / "c.html"))
