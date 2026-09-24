import json
from scripts import runtime_opcert_header_soak as soak


class FakeClock:
    def __init__(self, budget, step=1.0):
        self.t = 0.0
        self.budget = budget
        self.step = step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def _patch_substrate(monkeypatch):
    monkeypatch.setattr(soak, "_open_consumers", lambda *a, **k: {"node1": object(), "amaru-relay-1": object()})
    monkeypatch.setattr(soak, "_close_consumers", lambda *a, **k: None)
    monkeypatch.setattr(soak, "_load_runtime_root", lambda root: ({"compose_project": "p"}, "cardano-node"))


def test_budget_bounds_loop(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    calls = {"n": 0}

    def fake_serve(spec, **k):
        calls["n"] += 1
        return ("h%d" % spec["iteration"], {"verdict": "accepted", "reason": None})

    monkeypatch.setattr(soak, "_serve_one", fake_serve)
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(tmp_path / "o"),
                                    target_node="node1", time_budget_seconds=10,
                                    clock=FakeClock(budget=10, step=1.0))
    assert calls["n"] <= 11
    assert r["iterations"] == calls["n"]


def test_budget_loop_all_inconclusive_fails(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: (None, None))
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(tmp_path / "o"),
                                    target_node="node1", time_budget_seconds=5,
                                    clock=FakeClock(budget=5))
    assert r["conclusive"] == 0 and r["pass"] is False


def test_differential_missing_side_not_counted_agree(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)

    def fake_serve_node(spec, *, node, **k):
        return ("h", {"verdict": "accepted", "reason": None}) if node == "node1" else (None, None)

    monkeypatch.setattr(soak, "_serve_one_node", fake_serve_node)
    r = soak.run_opcert_header_soak(str(tmp_path), "encoding-form", 5, str(tmp_path / "o"),
                                    target_nodes=["node1", "amaru-relay-1"], time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    assert r["counters"]["agree"] == 0 and r["conclusive"] == 0 and r["pass"] is False


def test_every_iteration_appended_to_attempts(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    lines = (out / "attempts.ndjson").read_text().splitlines()
    assert len(lines) >= 1 and all(json.loads(l)["spec"]["family"] == "accept-boundary" for l in lines)


def test_result_written_with_seed_and_duration(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                    target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    disk = json.loads((out / "result.json").read_text())
    assert disk["seed"] == 5 and disk["family"] == "accept-boundary" and "duration_seconds" in disk
    assert disk == r
