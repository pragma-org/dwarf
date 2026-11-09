"""Public-preview checks on dashboard-installed container profiles (run 20261109T175136Z-74b3755a).

The preview helpers read a host-era ``runtime.json`` under ``~/cardano-profiles/<id>`` with
top-level ``listen_address``/``chain_dir``/``log_path``/``pid_file`` and faulted the upstream peer
with ``sudo iptables``/``sudo tc`` on the host. A dashboard install keeps the profile under the
controller state, records the node per entry in ``haskell_nodes``/``amaru_nodes`` and runs it in a
container: the root is resolved through ``profiles.runtime_root``, liveness is the container state
and every upstream fault is installed in the node's own network namespace.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import runtime_common as rc
from scripts import runtime_amaru_preview_check as amaru_check
from scripts import runtime_preview_parity_check as parity
from scripts import runtime_preview_upstream_delay_check as delay_check
from scripts import runtime_preview_upstream_fault_check as drop_check
from scripts import runtime_preview_upstream_loss_check as loss_check
from scripts import runtime_preview_upstream_reset_check as reset_check

UPSTREAM = "preview2-node.play.dev.cardano.org:3001"


def _container_runtime(root: Path, *, implementation: str = "cardano-node") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs" / "node1").mkdir(parents=True)
    log_path = root / "logs" / "node1" / "stdout.log"
    log_path.write_text("start\n", encoding="utf-8")
    node = {
        "id": "node1", "name": "node1", "impl": implementation, "port": 39120,
        "listen_address": "127.0.0.1:39120", "log_path": str(log_path),
        "pid_file": str(root / "pids" / "node1.pid"),
        "container_name": "dwarf-profile-install-x-profile-g-node1-1",
        "container_ip": "10.192.8.2", "container_network": "dwarf-profile-install-x-net",
    }
    if implementation == "cardano-node":
        db_dir = root / "env" / "node-data" / "node1" / "db"
        db_dir.mkdir(parents=True)
        (db_dir / "immutable.chunk").write_bytes(b"x" * 10)
        topology = root / "env" / "node-data" / "node1" / "topology.json"
        topology.write_text(json.dumps({"bootstrapPeers": [{"address": UPSTREAM.split(":")[0], "port": 3001}]}),
                            encoding="utf-8")
        node.update({"db_dir": str(db_dir), "topology_path": str(topology), "container_listen_address": "node1:3001"})
        body = {"compose_mode": "docker", "nodes": [node], "haskell_nodes": [node], "amaru_nodes": []}
    else:
        chain_dir = root / "amaru" / "node1" / "chain.preview.db"
        chain_dir.mkdir(parents=True)
        (chain_dir / "data").write_bytes(b"x" * 10)
        node.update({"chain_dir": str(chain_dir), "container_listen_address": "node1:5001",
                     "fallback_peer_addresses": [UPSTREAM]})
        body = {"compose_mode": "docker", "nodes": [node], "haskell_nodes": [], "amaru_nodes": [node]}
    (root / "runtime.json").write_text(json.dumps(body), encoding="utf-8")
    return root


# --- resolver -----------------------------------------------------------------------------------

def test_legacy_profile_path_resolves_to_the_controller_runtime_root(tmp_path, monkeypatch):
    import profile_manager.profiles as profiles
    installed = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: tmp_path / "topology-runtimes" / pid / "current")
    assert rc.resolve_preview_runtime_root("~/cardano-profiles/profile-g") == installed
    assert rc.resolve_preview_runtime_root("/opt/dwarf/cardano-profiles/profile-g") == installed


def test_controller_state_dir_is_the_fallback_when_the_remote_base_is_unset(tmp_path, monkeypatch):
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: Path("/opt/dwarf/cardano-profiles") / pid)
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "controller-state"))
    installed = _container_runtime(tmp_path / "controller-state" / "controller" / "topology-runtimes" / "profile-g" / "current")
    assert rc.resolve_preview_runtime_root("~/cardano-profiles/profile-g") == installed


def test_existing_legacy_root_is_kept(tmp_path):
    legacy = tmp_path / "cardano-profiles" / "profile-e"
    legacy.mkdir(parents=True)
    (legacy / "runtime.json").write_text("{}", encoding="utf-8")
    assert rc.resolve_preview_runtime_root(str(legacy)) == legacy


def test_unresolvable_root_stays_as_declared_so_the_error_names_it(tmp_path, monkeypatch):
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: tmp_path / "nowhere" / pid)
    resolved = rc.resolve_preview_runtime_root(str(tmp_path / "cardano-profiles" / "profile-g"))
    with pytest.raises(RuntimeError, match="missing runtime metadata"):
        rc.preview_node_view(resolved, "cardano-node")


# --- node view ----------------------------------------------------------------------------------

def test_container_view_reads_the_per_node_record(tmp_path):
    root = _container_runtime(tmp_path / "current")
    view = rc.preview_node_view(root, "cardano-node")
    assert view.layout == "container"
    assert (view.listen_host, view.listen_port) == ("10.192.8.2", 3001)
    assert view.chain_dir == root / "env" / "node-data" / "node1" / "db"
    assert view.log_path == root / "logs" / "node1" / "stdout.log"
    assert view.container_name == "dwarf-profile-install-x-profile-g-node1-1"
    assert view.upstream_peer_address == UPSTREAM


def test_container_view_supports_amaru(tmp_path):
    root = _container_runtime(tmp_path / "current", implementation="amaru")
    view = rc.preview_node_view(root, "amaru")
    assert (view.listen_host, view.listen_port) == ("10.192.8.2", 5001)
    assert view.chain_dir == root / "amaru" / "node1" / "chain.preview.db"
    assert view.upstream_peer_address == UPSTREAM


def test_container_view_without_a_node_of_the_implementation_fails(tmp_path):
    root = _container_runtime(tmp_path / "current", implementation="amaru")
    with pytest.raises(RuntimeError, match="no cardano-node node"):
        rc.preview_node_view(root, "cardano-node")


def test_host_view_keeps_the_top_level_fields(tmp_path):
    (tmp_path / "chain").mkdir()
    body = {"listen_address": "127.0.0.1:39120", "chain_dir": str(tmp_path / "chain"),
            "log_path": str(tmp_path / "node.log"), "pid_file": str(tmp_path / "node.pid"),
            "upstream_peer_address": UPSTREAM}
    (tmp_path / "runtime.json").write_text(json.dumps(body), encoding="utf-8")
    view = rc.preview_node_view(tmp_path, "cardano-node")
    assert view.layout == "host"
    assert (view.listen_host, view.listen_port) == ("127.0.0.1", 39120)
    assert view.pid_file == tmp_path / "node.pid" and view.container_name is None


def test_container_liveness_is_the_container_state(tmp_path):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")

    assert view.is_running(runner=runner) is True
    assert calls == [["docker", "inspect", "-f", "{{.State.Running}}", view.container_name]]
    stopped = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="false\n", stderr="")
    assert view.is_running(runner=stopped) is False
    missing = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr="No such object")
    assert view.is_running(runner=missing) is False


# --- netns helpers ------------------------------------------------------------------------------

class _NetnsRecorder:
    """Stands in for ``run_in_node_netns``: records each script, answers ss / route / show queries."""

    def __init__(self, ss_output="", route_dev="eth0", qdisc_after_remove="qdisc noqueue 0: root refcnt 2\n",
                 iptables_after_remove="-P OUTPUT ACCEPT\n"):
        self.scripts = []
        self.containers = []
        self.ss_output = ss_output
        self.route_dev = route_dev
        self.qdisc_after_remove = qdisc_after_remove
        self.iptables_after_remove = iptables_after_remove

    def __call__(self, container, script, *, runner=None):
        self.containers.append(container)
        self.scripts.append(script)
        if script.startswith("ss "):
            out = self.ss_output
        elif "route get" in script:
            out = "".join(f"{ip} via 10.192.8.1 dev {self.route_dev} src 10.192.8.2 uid 0\n"
                          for ip in ("1.2.3.4", "5.6.7.8") if ip in script)
        elif "tc qdisc del" in script:
            out = self.qdisc_after_remove
        elif "iptables -w -D" in script:
            out = self.iptables_after_remove
        else:
            out = ""
        return subprocess.CompletedProcess(["docker"], 0, stdout=out, stderr="")


SS_OUTPUT = (
    "0      0      10.192.8.2:41234     1.2.3.4:3001\n"
    "0      0      10.192.8.2:41236     [::ffff:5.6.7.8]:3001\n"
    "0      0      10.192.8.2:3001      10.192.8.9:52000\n"
    "0      0      10.192.8.2:41240     10.192.8.3:3001\n"
)


def test_active_upstream_ips_come_from_the_node_namespace(tmp_path, monkeypatch):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    view = rc.PreviewNodeView(**{**view.__dict__, "local_peer_ips": ("10.192.8.3",)})
    recorder = _NetnsRecorder(ss_output=SS_OUTPUT)
    monkeypatch.setattr(rc, "run_in_node_netns", recorder)
    assert rc.preview_active_upstream_ips(view, 3001) == ["1.2.3.4", "5.6.7.8"]
    assert recorder.containers == [view.container_name]
    assert recorder.scripts[0].startswith("ss -Htn state established")


def test_drop_rules_are_installed_and_removed_in_the_node_namespace(tmp_path, monkeypatch):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    recorder = _NetnsRecorder()
    monkeypatch.setattr(rc, "run_in_node_netns", recorder)
    target = ["DROP"]
    rc.preview_apply_iptables(view, ["1.2.3.4"], 3001, target)
    rc.preview_remove_iptables(view, ["1.2.3.4"], 3001, target)
    assert "iptables -w -I OUTPUT 1 -p tcp -d 1.2.3.4 --dport 3001 -j DROP" in recorder.scripts[0]
    assert "iptables -w -D OUTPUT -p tcp -d 1.2.3.4 --dport 3001 -j DROP" in recorder.scripts[1]
    assert "sudo" not in "".join(recorder.scripts)


def test_iptables_removal_that_leaves_a_rule_fails_visibly(tmp_path, monkeypatch):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    leftover = "-P OUTPUT ACCEPT\n-A OUTPUT -d 1.2.3.4/32 -p tcp -m tcp --dport 3001 -j REJECT --reject-with tcp-reset\n"
    monkeypatch.setattr(rc, "run_in_node_netns", _NetnsRecorder(iptables_after_remove=leftover))
    with pytest.raises(RuntimeError, match="still present"):
        rc.preview_remove_iptables(view, ["1.2.3.4"], 3001, ["REJECT", "--reject-with", "tcp-reset"])


def test_netem_fault_is_scoped_to_the_upstream_and_removed(tmp_path, monkeypatch):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    recorder = _NetnsRecorder()
    monkeypatch.setattr(rc, "run_in_node_netns", recorder)
    state = rc.preview_apply_netem(view, ["1.2.3.4", "5.6.7.8"], 3001, ["delay", "500ms", "100ms"])
    assert state["interface"] == "eth0"
    apply_script = recorder.scripts[-1]
    assert "tc qdisc add dev eth0 root handle 1: prio bands 4" in apply_script
    assert "tc qdisc add dev eth0 parent 1:4 handle 40: netem delay 500ms 100ms" in apply_script
    for ip in ("1.2.3.4", "5.6.7.8"):
        assert f"match ip dst {ip}/32 match ip protocol 6 0xff match ip dport 3001 0xffff flowid 1:4" in apply_script
    rc.preview_remove_netem(view, state)
    assert "tc qdisc del dev eth0 root" in recorder.scripts[-1]


def test_netem_removal_that_leaves_the_qdisc_fails_visibly(tmp_path, monkeypatch):
    view = rc.preview_node_view(_container_runtime(tmp_path / "current"), "cardano-node")
    recorder = _NetnsRecorder(qdisc_after_remove="qdisc prio 1: root refcnt 2 bands 4\nqdisc netem 40: parent 1:4\n")
    monkeypatch.setattr(rc, "run_in_node_netns", recorder)
    with pytest.raises(RuntimeError, match="still present"):
        rc.preview_remove_netem(view, {"interface": "eth0", "upstream_ips": ["1.2.3.4"], "upstream_port": 3001})


# --- end-to-end through each helper -------------------------------------------------------------

def _scenario(tmp_path, implementation="cardano-node"):
    path = tmp_path / "scenario.yaml"
    path.write_text(json.dumps({"target": {"implementation": implementation}}), encoding="utf-8")
    return path


def _wire(monkeypatch, module, tmp_path, root, implementation="cardano-node"):
    """Container profile at ``root``; the node is running and writes log lines while the helper sleeps."""
    run_dir = tmp_path / "run"
    env_key = f"ADA2_DWARF_PREVIEW_{implementation.upper().replace('-', '_')}_ROOT"
    monkeypatch.setenv(env_key, "~/cardano-profiles/profile-g")
    monkeypatch.setenv("ADA2_DWARF_RUNTIME_METRICS_DIR", str(run_dir / "metrics" / "runtime"))
    monkeypatch.setenv("ADA2_DWARF_TARGET_EVENT_LOG", str(run_dir / "events" / "target-hooks.ndjson"))
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: root)
    rc_module = module.rc
    monkeypatch.setattr(rc_module.PreviewNodeView, "is_running", lambda self, runner=None: True)
    probed = []
    monkeypatch.setattr(module, "_listener_ok", lambda host, port: probed.append((host, port)) or True)
    log_path = root / "logs" / "node1" / "stdout.log"

    def fake_sleep(seconds):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("block adopted\n")

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    recorder = _NetnsRecorder(ss_output=SS_OUTPUT)
    monkeypatch.setattr(rc_module, "run_in_node_netns", recorder)
    monkeypatch.setattr(rc_module.socket, "getaddrinfo",
                        lambda host, port, **kwargs: [(2, 1, 6, "", ("1.2.3.4", port))])
    return run_dir, recorder, probed


def _metric(run_dir, name):
    lines = (run_dir / "metrics" / "runtime" / f"{name}.ndjson").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])["value"]


def test_parity_baseline_measures_a_container_profile(tmp_path, monkeypatch):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    run_dir, recorder, probed = _wire(monkeypatch, parity, tmp_path, root)
    assert parity.run_baseline(scenario_path=_scenario(tmp_path), sample_seconds=1) == 0
    assert probed == [("10.192.8.2", 3001)]
    assert _metric(run_dir, "preview_log_bytes_delta") == len("block adopted\n")
    assert _metric(run_dir, "preview_progress_ok") == 1
    assert _metric(run_dir, "preview_listener_port") == 3001
    assert recorder.scripts == []  # a baseline never touches the namespace


@pytest.mark.parametrize("module,runner_name,expected_rule", [
    (drop_check, "run_drop", "-j DROP"),
    (reset_check, "run_reset", "-j REJECT --reject-with tcp-reset"),
])
def test_iptables_faults_run_in_the_node_namespace(tmp_path, monkeypatch, module, runner_name, expected_rule):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    run_dir, recorder, _probed = _wire(monkeypatch, module, tmp_path, root)
    assert getattr(module, runner_name)(scenario_path=_scenario(tmp_path), fault_seconds=1, recovery_seconds=1) == 0
    applied = [s for s in recorder.scripts if "iptables -w -I" in s]
    removed = [s for s in recorder.scripts if "iptables -w -D" in s]
    assert len(applied) == 1 and len(removed) == 1
    for ip in ("1.2.3.4", "5.6.7.8"):
        assert f"-d {ip} --dport 3001 {expected_rule}" in applied[0]
    assert set(recorder.containers) == {"dwarf-profile-install-x-profile-g-node1-1"}
    assert _metric(run_dir, "preview_fault_window_log_bytes_delta") > 0
    assert _metric(run_dir, "preview_postfault_window_log_bytes_delta") > 0


@pytest.mark.parametrize("module,runner_name,kwargs,netem", [
    (delay_check, "run_delay", {"delay_ms": 500, "jitter_ms": 100}, "netem delay 500ms 100ms"),
    (loss_check, "run_loss", {"loss_pct": 30}, "netem loss 30%"),
])
def test_netem_faults_run_in_the_node_namespace(tmp_path, monkeypatch, module, runner_name, kwargs, netem):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    run_dir, recorder, _probed = _wire(monkeypatch, module, tmp_path, root)
    assert getattr(module, runner_name)(scenario_path=_scenario(tmp_path), fault_seconds=1, recovery_seconds=1,
                                        **kwargs) == 0
    applied = [s for s in recorder.scripts if "netem" in s]
    assert len(applied) == 1 and netem in applied[0]
    assert any("tc qdisc del dev eth0 root" in s for s in recorder.scripts)
    assert _metric(run_dir, "preview_postfault_window_progress_ok") == 1


def test_fault_is_removed_when_the_window_fails(tmp_path, monkeypatch):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    run_dir, recorder, _probed = _wire(monkeypatch, drop_check, tmp_path, root)

    def boom(seconds):
        raise RuntimeError("window interrupted")

    monkeypatch.setattr(drop_check.time, "sleep", boom)
    with pytest.raises(RuntimeError, match="window interrupted"):
        drop_check.run_drop(scenario_path=_scenario(tmp_path), fault_seconds=1, recovery_seconds=1)
    assert any("iptables -w -D" in s for s in recorder.scripts)


def test_stopped_container_fails_the_check(tmp_path, monkeypatch):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g" / "current")
    _wire(monkeypatch, parity, tmp_path, root)
    monkeypatch.setattr(parity.rc.PreviewNodeView, "is_running", lambda self, runner=None: False)
    with pytest.raises(RuntimeError, match="not running"):
        parity.run_baseline(scenario_path=_scenario(tmp_path), sample_seconds=1)


def test_amaru_preview_proof_measures_a_container_profile(tmp_path, monkeypatch):
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-f" / "current", implementation="amaru")
    run_dir, _recorder, probed = _wire(monkeypatch, amaru_check, tmp_path, root, implementation="amaru")
    assert amaru_check.run_proof(runtime_root=root, sample_seconds=1) == 0
    assert probed == [("10.192.8.2", 5001)]
    assert _metric(run_dir, "amaru_preview_progress_ok") == 1
    assert _metric(run_dir, "amaru_preview_log_bytes_delta") == len("block adopted\n")


# --- primitives hand the helpers the deployed root ----------------------------------------------

class _Handle:
    def __init__(self, run_dir, prepared=None):
        self.run_dir = run_dir
        self.logs = []
        if prepared is not None:
            self._prepared_runtime_metadata_path = str(prepared)

    def log(self, **entry):
        self.logs.append(entry)


def test_primitive_passes_the_prepared_runtime_of_the_same_profile(tmp_path, monkeypatch):
    from profile_manager import primitives
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: Path("/nonexistent/cardano-profiles") / pid)
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g-haskell-preview2-proof" / "current")
    handle = _Handle(tmp_path / "run", prepared=root / "runtime.json")
    declared = "~/cardano-profiles/profile-g-haskell-preview2-proof"
    assert primitives._preview_runtime_root(handle, declared) == str(root)
    # a different profile's prepared runtime is never substituted
    other = "~/cardano-profiles/profile-f-amaru-preview2-proof"
    assert primitives._preview_runtime_root(handle, other) == other
    assert primitives._preview_runtime_root(_Handle(tmp_path / "run"), declared) == declared


def test_drop_primitive_exports_the_resolved_root_to_the_helper(tmp_path, monkeypatch):
    from profile_manager import primitives
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: Path("/nonexistent/cardano-profiles") / pid)
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-g-haskell-preview2-proof" / "current")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    primitive = primitives.RuntimePreviewUpstreamDrop(params={
        "preview_cardano_node_root": "~/cardano-profiles/profile-g-haskell-preview2-proof",
        "preview_amaru_root": "~/cardano-profiles/profile-f-amaru-preview2-proof",
    })
    primitive.run(_Handle(run_dir, prepared=root / "runtime.json"), None)
    assert seen["env"]["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] == str(root)
    assert seen["env"]["ADA2_DWARF_PREVIEW_AMARU_ROOT"] == "~/cardano-profiles/profile-f-amaru-preview2-proof"


def test_live_implementation_baseline_uses_the_prepared_container_root(tmp_path, monkeypatch):
    """F145: phase1 parity baseline declared ~/cardano-profiles/profile-c-...; the runner died
    before creating a run (live launch-93d1f631: run_adapter_identity_missing)."""
    import subprocess
    from types import SimpleNamespace
    from profile_manager import primitives
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: Path("/nonexistent/cardano-profiles") / pid)
    root = _container_runtime(tmp_path / "topology-runtimes" / "profile-c-mixed-haskell-amaru-minimal" / "current")
    handle = _Handle(tmp_path / "run", prepared=root / "runtime.json")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    prim = primitives.RuntimeLiveImplementationBaseline(params={
        "runtime_root": "~/cardano-profiles/profile-c-mixed-haskell-amaru-minimal", "timeout_seconds": 5})
    import random
    prim.run(handle, random.Random(0))
    assert str(root) in seen["argv"]


def test_preview_baseline_keeps_sampling_until_the_chain_grows():
    """Preview averages one block per 20 s, so a fixed 20 s window sees no block ~37% of the time
    (live run 20261109T233341Z failed on a healthy, following node). Sampling continues until growth,
    up to six expected block intervals."""
    from scripts import runtime_preview_parity_check as parity

    sizes = iter([100, 100, 100, 100, 160])          # chain size at each read: grows on the 4th poll
    clock = {"t": 0.0}
    result = parity._sample_until_growth(
        read_chain=lambda: next(sizes), read_log=lambda: 50, is_running=lambda: True,
        min_seconds=20, max_seconds=120, poll_seconds=2,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"])
    assert result["chain_before"] == 100 and result["chain_after"] == 160
    assert result["log_before"] == result["log_after"] == 50
    assert result["sampled_seconds"] == 26


def test_preview_baseline_gives_up_after_the_budget():
    from scripts import runtime_preview_parity_check as parity

    clock = {"t": 0.0}
    result = parity._sample_until_growth(
        read_chain=lambda: 100, read_log=lambda: 50, is_running=lambda: True,
        min_seconds=20, max_seconds=120, poll_seconds=2,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"])
    assert result["chain_after"] == 100 and result["sampled_seconds"] >= 120
