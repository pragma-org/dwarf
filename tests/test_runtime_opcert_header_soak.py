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


def _gate_proc(monkeypatch):
    class FakeProc:
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass
    proc = FakeProc()
    monkeypatch.setattr(soak.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(soak.det, "_now_docker_ts", lambda: "T")
    monkeypatch.setattr(soak.det, "served_hash_by_case", lambda lines: {"counter-behind": "hh"})
    monkeypatch.setattr(soak.det, "_read_consumer_events", lambda *a, **k: [])
    return proc


def _gate_ctx():
    return {"name": "c", "listen_port": 1, "upstream": "1.2.3.4:3001",
            "kes_skey": "k", "cold_skey": "c", "slots_per_kes": 100, "max_kes_evo": 15,
            "implementation": "cardano-node"}


def test_serve_case_spec_adopt_gate_confirms_adoption(monkeypatch, tmp_path):
    """Fix 1: with the gate armed, the served replay hash AND the counter-N block
    hash both observed accepted -> returns (served, observed, adopted=True)."""
    _gate_proc(monkeypatch)
    monkeypatch.setattr(soak.det, "verdict_by_hash",
                        lambda ev: {"hh": {"verdict": "accepted", "reason": None},
                                    "ff": {"verdict": "accepted", "reason": None}})
    spec = {"family": "restart-persistence", "base_case": "counter-behind", "iteration": 0}
    served, observed, adopted = soak._serve_case_spec(
        spec, _gate_ctx(), peer_bin="/bin/true", per_iteration_timeout=5,
        output_dir=str(tmp_path), iteration=0, adopt_hash="ff", adopt_impl="cardano-node")
    assert served == "hh" and observed["verdict"] == "accepted" and adopted is True


def test_serve_case_spec_adopt_gate_stale_returns_not_adopted(monkeypatch, tmp_path):
    """Fix 1: replay accepted but the counter-N block hash never observed on the
    consumer -> returns adopted=False (the driver then scores inconclusive)."""
    _gate_proc(monkeypatch)
    monkeypatch.setattr(soak.det, "verdict_by_hash",
                        lambda ev: {"hh": {"verdict": "accepted", "reason": None}})
    spec = {"family": "restart-persistence", "base_case": "counter-behind", "iteration": 0}
    served, observed, adopted = soak._serve_case_spec(
        spec, _gate_ctx(), peer_bin="/bin/true", per_iteration_timeout=4,
        output_dir=str(tmp_path), iteration=0, adopt_hash="ff", adopt_impl="cardano-node")
    assert served == "hh" and observed["verdict"] == "accepted" and adopted is False


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


def test_family_c_rotate_restart_replay_and_finding(monkeypatch, tmp_path):
    """Fix 2: the family-C path performs a real rotate→restart→forge→replay each
    iteration and scores accept-after-restart as a finding carrying the
    reproducing seed; the original opcert is restored."""
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_load_runtime_root",
                        lambda root: ({"compose_project": "p", "network_magic": 42}, "cardano-node"))
    events = {"rotate": 0, "restart": 0, "serve": 0, "backup": 0, "restore": 0}
    monkeypatch.setattr(soak, "_rotation_context", lambda runtime, node: {"mode": "hostdir"})
    monkeypatch.setattr(soak, "_backup_opcert", lambda rot: events.__setitem__("backup", events["backup"] + 1))
    monkeypatch.setattr(soak, "_restore_opcert", lambda rot: events.__setitem__("restore", events["restore"] + 1))
    monkeypatch.setattr(soak, "_producer_tip_slot", lambda rot: (100, 5000))

    def fake_rotate(rot):
        events["rotate"] += 1
        return 2  # achieved counter N=2

    def fake_restart(rot):
        events["restart"] += 1

    def fake_forge(rot, baseline, deadline):
        return ((baseline or 0) + 3, "forgedhash")  # a counter-N block was forged

    def fake_serve(spec, **k):
        events["serve"] += 1
        # Amaru/cardano ACCEPTS a replay below the rotated counter AND the
        # consumer HAS adopted counter-N (adopt gate satisfied) -> real finding.
        return ("h%d" % spec["iteration"], {"verdict": "accepted", "reason": None}, True)

    monkeypatch.setattr(soak, "_rotate_producer", fake_rotate)
    monkeypatch.setattr(soak, "_restart_producer", fake_restart)
    monkeypatch.setattr(soak, "_wait_for_forge", fake_forge)
    monkeypatch.setattr(soak, "_serve_one_gated", fake_serve)

    out = tmp_path / "o"
    r = soak.run_opcert_header_soak(str(tmp_path), "restart-persistence", 4242, str(out),
                                    target_node="node1", time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    assert events["rotate"] >= 1 and events["restart"] >= 1 and events["serve"] >= 1
    assert events["backup"] == 1 and events["restore"] == 1
    # accept-after-restart is a mismatch finding, and the finding carries the seed
    assert r["counters"]["mismatch"] >= 1 and r["pass"] is False
    finding = r["mismatches"][0]
    assert finding["spec"]["seed"] == 4242
    assert finding["spec"]["params"]["rotated_to_counter_actual"] == 2


def test_family_c_incomplete_cycle_is_inconclusive(monkeypatch, tmp_path):
    """Fix 2: a cycle that never completes the restart/forge is fail-closed
    inconclusive (never a pass)."""
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_load_runtime_root",
                        lambda root: ({"compose_project": "p", "network_magic": 42}, "cardano-node"))
    monkeypatch.setattr(soak, "_rotation_context", lambda runtime, node: {"mode": "hostdir"})
    monkeypatch.setattr(soak, "_backup_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_restore_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_producer_tip_slot", lambda rot: (100, 5000))
    monkeypatch.setattr(soak, "_rotate_producer", lambda rot: 2)
    monkeypatch.setattr(soak, "_restart_producer", lambda rot: None)
    monkeypatch.setattr(soak, "_wait_for_forge", lambda rot, b, d: (None, None))  # never forged
    served = {"n": 0}
    monkeypatch.setattr(soak, "_serve_one_gated",
                        lambda spec, **k: served.__setitem__("n", served["n"] + 1) or ("h", None, False))
    r = soak.run_opcert_header_soak(str(tmp_path), "restart-persistence", 7, str(tmp_path / "o"),
                                    target_node="node1", time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    assert r["conclusive"] == 0 and r["pass"] is False and served["n"] == 0


def test_family_c_adopt_gate_stale_view_is_inconclusive(monkeypatch, tmp_path):
    """Fix 1 (adopt gate): an iteration whose consumer has NOT yet adopted the
    rotated counter-N block accepts the replay from its stale view — this must be
    classified inconclusive (fail-closed), never accept-after-restart."""
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_load_runtime_root",
                        lambda root: ({"compose_project": "p", "network_magic": 42}, "cardano-node"))
    monkeypatch.setattr(soak, "_rotation_context", lambda runtime, node: {"mode": "hostdir"})
    monkeypatch.setattr(soak, "_backup_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_restore_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_producer_tip_slot", lambda rot: (100, 5000))
    monkeypatch.setattr(soak, "_rotate_producer", lambda rot: 2)
    monkeypatch.setattr(soak, "_restart_producer", lambda rot: None)
    monkeypatch.setattr(soak, "_wait_for_forge", lambda rot, b, d: ((b or 0) + 3, "forgedhash"))
    # Replay ACCEPTED but consumer never adopted counter-N (adopted=False).
    monkeypatch.setattr(soak, "_serve_one_gated",
                        lambda spec, **k: ("h%d" % spec["iteration"],
                                           {"verdict": "accepted", "reason": None}, False))
    r = soak.run_opcert_header_soak(str(tmp_path), "restart-persistence", 99, str(tmp_path / "o"),
                                    target_node="node1", time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    # No mismatch finding, no pass: every accept-without-adoption is inconclusive.
    assert r["counters"]["mismatch"] == 0
    assert r["counters"]["inconclusive"] >= 1
    assert r["conclusive"] == 0 and r["pass"] is False


def test_family_c_adopt_gate_reject_is_conclusive_pass(monkeypatch, tmp_path):
    """A replay REJECTED (counter-too-small) is conclusive regardless of the
    adopt flag — a stale consumer would accept, not reject, so a reject already
    implies it knows counter-N; reason-matched reject is a pass."""
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_load_runtime_root",
                        lambda root: ({"compose_project": "p", "network_magic": 42}, "cardano-node"))
    monkeypatch.setattr(soak, "_rotation_context", lambda runtime, node: {"mode": "hostdir"})
    monkeypatch.setattr(soak, "_backup_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_restore_opcert", lambda rot: None)
    monkeypatch.setattr(soak, "_producer_tip_slot", lambda rot: (100, 5000))
    monkeypatch.setattr(soak, "_rotate_producer", lambda rot: 2)
    monkeypatch.setattr(soak, "_restart_producer", lambda rot: None)
    monkeypatch.setattr(soak, "_wait_for_forge", lambda rot, b, d: ((b or 0) + 3, "forgedhash"))
    monkeypatch.setattr(soak, "_serve_one_gated",
                        lambda spec, **k: ("h%d" % spec["iteration"],
                                           {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}, False))
    r = soak.run_opcert_header_soak(str(tmp_path), "restart-persistence", 11, str(tmp_path / "o"),
                                    target_node="node1", time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    assert r["counters"]["pass"] >= 1 and r["counters"]["mismatch"] == 0 and r["pass"] is True


def test_result_written_with_seed_and_duration(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                    target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    disk = json.loads((out / "result.json").read_text())
    assert disk["seed"] == 5 and disk["family"] == "accept-boundary" and "duration_seconds" in disk
    assert disk == r
