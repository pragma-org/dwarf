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


def test_serve_case_spec_terminates_the_forger_process(monkeypatch, tmp_path):
    """Regression: the serve path must terminate the forger *process* (not the
    binary Path). This is the seam every live iteration runs, so a live smoke
    would crash on iteration 0 if it regressed."""
    class FakeProc:
        def __init__(self):
            self.terminated = False
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    proc = FakeProc()
    monkeypatch.setattr(soak.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(soak.det, "_now_docker_ts", lambda: "T")
    monkeypatch.setattr(soak.det, "served_hash_by_case", lambda lines: {"valid-control": "hh"})
    monkeypatch.setattr(soak.det, "_read_consumer_events", lambda *a, **k: [])
    monkeypatch.setattr(soak.det, "verdict_by_hash", lambda ev: {"hh": {"verdict": "accepted", "reason": None}})
    ctx = {"name": "c", "listen_port": 1, "upstream": "1.2.3.4:3001",
           "kes_skey": "k", "cold_skey": "c", "slots_per_kes": 100, "max_kes_evo": 15,
           "implementation": "cardano-node"}
    spec = {"family": "encoding-form", "base_case": "valid-control", "iteration": 0}
    served, observed = soak._serve_case_spec(spec, ctx, peer_bin="/bin/true",
                                             per_iteration_timeout=5,
                                             output_dir=str(tmp_path), iteration=0)
    assert served == "hh" and observed["verdict"] == "accepted"
    assert proc.terminated is True


def test_dispatch_picks_amaru_control_when_lifecycle(monkeypatch):
    """Fix 1: a runtime with the amaru-control lifecycle / actual_topology takes
    the amaru-control consumer path, not the haskell_nodes path."""
    called = {}
    monkeypatch.setattr(soak, "_open_amaru_control_consumers",
                        lambda runtime, nodes, **k: called.setdefault("amaru", nodes) or {"x": {}})
    monkeypatch.setattr(soak, "_open_local_cardano_consumers",
                        lambda runtime, nodes, **k: called.setdefault("local", nodes) or {"x": {}})
    rt = {"lifecycle": "cardano_amaru_relay_bootstrap_control",
          "actual_topology": {"amaru_services": ["amaru-relay-1"]},
          "compose_project": "p"}
    soak._open_consumers(rt, ["node1", "amaru-relay-1"], output_dir="/tmp/x")
    assert "amaru" in called and "local" not in called


def test_dispatch_picks_local_when_haskell_nodes(monkeypatch):
    """Fix 1: a generated-cardano-local runtime (haskell_nodes, no lifecycle)
    takes the local cardano consumer path."""
    called = {}
    monkeypatch.setattr(soak, "_open_amaru_control_consumers",
                        lambda runtime, nodes, **k: called.setdefault("amaru", nodes) or {"x": {}})
    monkeypatch.setattr(soak, "_open_local_cardano_consumers",
                        lambda runtime, nodes, **k: called.setdefault("local", nodes) or {"x": {}})
    rt = {"haskell_nodes": [{"id": "node1", "impl": "cardano-node"}], "compose_project": "p"}
    soak._open_consumers(rt, ["node1"], output_dir="/tmp/x")
    assert "local" in called and "amaru" not in called


def test_differential_amaru_control_opens_both_consumers(monkeypatch, tmp_path):
    """Fix 1: a differential soak on the amaru-control substrate opens BOTH the
    cardano (node1) and amaru (amaru-relay-1) consumers, via the shared
    deterministic-driver helpers, each on its own listen port."""
    opened = {}

    def fake_amaru(runtime, work, *, listen_port, name_suffix=""):
        opened[name_suffix] = ("amaru", listen_port)
        return {"name": f"amaru{name_suffix}", "implementation": "amaru",
                "volumes": [f"v{name_suffix}"], "listen_port": listen_port}

    def fake_cardano(runtime, work, *, listen_port, consumer_port, name_suffix=""):
        opened[name_suffix] = ("cardano-node", listen_port)
        return {"name": f"cn{name_suffix}", "implementation": "cardano-node",
                "volumes": [f"v{name_suffix}"], "listen_port": listen_port}

    monkeypatch.setattr(soak.det, "open_amaru_consumer", fake_amaru)
    monkeypatch.setattr(soak.det, "open_amaru_control_cardano_consumer", fake_cardano)
    monkeypatch.setattr(soak, "_wait_amaru_consumer_ready", lambda *a, **k: True)
    rt = {"lifecycle": "cardano_amaru_relay_bootstrap_control", "compose_project": "p"}
    consumers = soak._open_amaru_control_consumers(rt, ["node1", "amaru-relay-1"],
                                                   output_dir=str(tmp_path))
    assert set(consumers) == {"node1", "amaru-relay-1"}
    assert consumers["node1"]["implementation"] == "cardano-node"
    assert consumers["amaru-relay-1"]["implementation"] == "amaru"
    # distinct listen ports so the two sides never collide
    assert consumers["node1"]["listen_port"] != consumers["amaru-relay-1"]["listen_port"]




def test_result_written_with_seed_and_duration(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                    target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    disk = json.loads((out / "result.json").read_text())
    assert disk["seed"] == 5 and disk["family"] == "accept-boundary" and "duration_seconds" in disk
    assert disk == r
