"""runtime_time_skew on Docker-mode composed substrates (live 20261109T194239Z-976c9109).

The controller image had no libfaketime and the script refused compose_mode "docker". The Docker path
recreates the target service through the substrate's own compose project with libfaketime preloaded,
then measures the node's own clock: each JSON log line carries the node's timestamp ("at" for
cardano-node, "timestamp" for Amaru) and `docker logs --timestamps` prefixes the daemon's receive time.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import runtime_time_skew as skew

PROJECT = "dwarf-substrate-x"
CONTAINER = f"{PROJECT}-node1-1"


def _log_lines(offset_seconds: float, count: int = 6, key: str = "at") -> str:
    lines = []
    for index in range(count):
        daemon = f"2026-11-09T19:43:3{index}.601794843Z"
        node_second = 30 + index + offset_seconds
        minute, second = divmod(int(node_second), 60)
        node = f"2026-11-09T19:{43 + minute:02d}:{second:02d}.600000000Z"
        lines.append(f"{daemon} " + json.dumps({key: node, "ns": "Forge.Loop.NodeNotLeader", "host": "node1"}))
    return "\n".join(lines) + "\n"


def _substrate(tmp_path: Path, *, impl: str = "cardano-node", environment: dict | None = None) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    (runtime_root / "env").mkdir(parents=True)
    service = {"image": "img", "container_name": CONTAINER, "command": ["run"], "volumes": ["./env:/env"]}
    if environment is not None:
        service["environment"] = dict(environment)
    compose = {"name": PROJECT, "services": {"node1": service, "node2": {"image": "img"}}}
    (runtime_root / "docker-compose.yml").write_text(json.dumps(compose), encoding="utf-8")
    node = {"id": "node1", "impl": impl, "compose_mode": "docker", "container_name": CONTAINER,
            "container_ip": "10.0.0.3", "container_id": "old", "session": "s-node1"}
    metadata = {"compose_mode": "docker", "compose_project": PROJECT, "runtime_root": str(runtime_root),
                "nodes": [dict(node)], "haskell_nodes": [dict(node)], "amaru_nodes": []}
    metadata_path = tmp_path / "runtime.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    lib = tmp_path / "libfaketime.so.1"
    lib.write_bytes(b"\x7fELF-fake")
    return metadata_path, lib


class _Docker:
    """Records docker calls; `docker logs` answers with the node clock offset of the live container."""

    def __init__(self, offsets: list[float], key: str = "at"):
        self.offsets = list(offsets)
        self.key = key
        self.calls: list[tuple[list[str], object]] = []

    def __call__(self, command, *, cwd=None, env=None, timeout=None):
        self.calls.append((list(command), cwd))
        if command[:2] == ["docker", "logs"]:
            offset = self.offsets[0] if len(self.offsets) == 1 else self.offsets.pop(0)
            if offset is None:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=_log_lines(offset, key=self.key), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def docker_env(monkeypatch):
    monkeypatch.setattr(skew, "_inspect_recreated_container", lambda metadata, node: {
        "container_id": "new", "container_ip": "10.0.0.9"})
    spawned = []
    monkeypatch.setattr(skew, "_spawn_release_session", lambda **kwargs: spawned.append(kwargs))
    monkeypatch.setattr(skew.time, "sleep", lambda seconds: None)
    return spawned


def test_faketime_offset_uses_documented_seconds_format():
    # libfaketime offsets are "+N"/"-N" seconds with optional m/h/d/y multipliers; "s" is not a multiplier.
    assert skew.faketime_offset_spec(600) == "+600"
    assert skew.faketime_offset_spec(-30) == "-30"


def test_clock_offset_is_node_timestamp_minus_daemon_timestamp(monkeypatch):
    monkeypatch.setattr(skew, "run_command", _Docker([600.0]))
    measured = skew.measure_container_clock_offset(CONTAINER)
    assert measured["measured"] is True
    assert measured["sample_count"] == 6
    assert measured["median_offset_seconds"] == pytest.approx(600.0, abs=0.01)


def test_amaru_timestamp_key_is_read(monkeypatch):
    monkeypatch.setattr(skew, "run_command", _Docker([-120.0], key="timestamp"))
    measured = skew.measure_container_clock_offset(CONTAINER)
    assert measured["measured"] is True
    assert measured["median_offset_seconds"] == pytest.approx(-120.0, abs=0.01)


def test_no_timestamped_lines_is_not_measured(monkeypatch):
    monkeypatch.setattr(skew, "run_command", _Docker([None]))
    measured = skew.measure_container_clock_offset(CONTAINER)
    assert measured["measured"] is False
    assert measured["median_offset_seconds"] is None
    assert measured["reason"]


def test_docker_apply_recreates_service_with_libfaketime_and_measures_skew(tmp_path, monkeypatch, docker_env):
    metadata_path, lib = _substrate(tmp_path)
    docker = _Docker([0.0, 600.0])
    monkeypatch.setattr(skew, "run_command", docker)
    out = tmp_path / "out"
    report = skew.apply_time_skew(runtime_metadata_path=metadata_path, output_dir=out, target_node="node1",
                                  skew_seconds=600, duration_seconds=20, healthy_timeout_seconds=5,
                                  libfaketime_path=str(lib))
    runtime_root = tmp_path / "runtime"
    assert (runtime_root / "env" / "faketime" / "libfaketime.so.1").read_bytes() == lib.read_bytes()
    service = json.loads((runtime_root / "docker-compose.yml").read_text())["services"]["node1"]
    assert service["environment"] == {"LD_PRELOAD": "/env/faketime/libfaketime.so.1", "FAKETIME": "+600"}
    up = [(cmd, cwd) for cmd, cwd in docker.calls if cmd[:2] == ["docker", "compose"]]
    assert up == [(["docker", "compose", "--project-name", PROJECT, "up", "-d", "--no-deps", "node1"], runtime_root)]
    result = report["result"]
    assert result["compose_mode"] == "docker"
    assert result["skew_requested_seconds"] == 600
    assert result["measured"] is True
    assert result["skew_applied_seconds"] == pytest.approx(600.0, abs=0.01)
    assert result["skew_confirmed"] is True
    assert result["clock_offset_before"]["median_offset_seconds"] == pytest.approx(0.0, abs=0.01)
    metadata = json.loads(metadata_path.read_text())
    for listing in ("nodes", "haskell_nodes"):
        assert metadata[listing][0]["container_ip"] == "10.0.0.9"
        assert metadata[listing][0]["container_id"] == "new"
    release = json.loads((out / "runtime_time_skew-release.json").read_text())
    assert release["docker_original_environment"] is None
    assert docker_env and docker_env[0]["duration_seconds"] == 20
    assert json.loads((out / "result.json").read_text())["result"]["skew_confirmed"] is True


def test_docker_apply_reports_unconfirmed_when_node_clock_did_not_move(tmp_path, monkeypatch, docker_env):
    metadata_path, lib = _substrate(tmp_path)
    monkeypatch.setattr(skew, "run_command", _Docker([0.0, 0.0]))
    report = skew.apply_time_skew(runtime_metadata_path=metadata_path, output_dir=tmp_path / "out",
                                  target_node="node1", skew_seconds=600, duration_seconds=20,
                                  healthy_timeout_seconds=5, libfaketime_path=str(lib))
    result = report["result"]
    assert result["measured"] is True
    assert result["skew_applied_seconds"] == pytest.approx(0.0, abs=0.01)
    assert result["skew_confirmed"] is False
    assert "600" in result["reason"]
    assert skew.exit_code_for(report) != 0


def test_docker_apply_without_node_log_samples_is_not_measured(tmp_path, monkeypatch, docker_env):
    metadata_path, lib = _substrate(tmp_path)
    monkeypatch.setattr(skew, "run_command", _Docker([0.0, None]))
    report = skew.apply_time_skew(runtime_metadata_path=metadata_path, output_dir=tmp_path / "out",
                                  target_node="node1", skew_seconds=600, duration_seconds=20,
                                  healthy_timeout_seconds=0.01, libfaketime_path=str(lib))
    result = report["result"]
    assert result["measured"] is False
    assert result["skew_applied_seconds"] is None
    assert result["skew_confirmed"] is False
    assert result["reason"]
    assert skew.exit_code_for(report) != 0


def test_docker_release_restores_original_environment_and_measures(tmp_path, monkeypatch, docker_env):
    original = {"AMARU_LOG": "error,amaru=info"}
    metadata_path, lib = _substrate(tmp_path, impl="amaru", environment=original)
    out = tmp_path / "out"
    monkeypatch.setattr(skew, "run_command", _Docker([0.0, 600.0], key="timestamp"))
    skew.apply_time_skew(runtime_metadata_path=metadata_path, output_dir=out, target_node="node1",
                         skew_seconds=600, duration_seconds=20, healthy_timeout_seconds=5,
                         libfaketime_path=str(lib))
    compose_path = tmp_path / "runtime" / "docker-compose.yml"
    during = json.loads(compose_path.read_text())["services"]["node1"]["environment"]
    assert during == {**original, "LD_PRELOAD": "/env/faketime/libfaketime.so.1", "FAKETIME": "+600"}
    docker = _Docker([2.0], key="timestamp")
    monkeypatch.setattr(skew, "run_command", docker)
    release = json.loads((out / "runtime_time_skew-release.json").read_text())
    body = skew.release_time_skew(runtime_metadata_path=metadata_path, output_dir=out, target_node="node1",
                                  skew_seconds=600, healthy_timeout_seconds=5,
                                  docker_original_environment=release["docker_original_environment"])
    assert json.loads(compose_path.read_text())["services"]["node1"]["environment"] == original
    assert any(cmd[:2] == ["docker", "compose"] and cmd[-1] == "node1" for cmd, _ in docker.calls)
    result = body["result"]
    assert result["skew_applied_seconds"] == pytest.approx(600.0, abs=0.01)
    assert result["clock_offset_after_release"]["median_offset_seconds"] == pytest.approx(2.0, abs=0.01)
    assert result["clock_restored"] is True
    assert result["host_time_at_release"] >= result["host_time_at_apply"]
    assert json.loads(metadata_path.read_text())["faults"] == []


def test_docker_release_removes_environment_key_that_was_absent(tmp_path, monkeypatch, docker_env):
    metadata_path, lib = _substrate(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(skew, "run_command", _Docker([0.0, 600.0]))
    skew.apply_time_skew(runtime_metadata_path=metadata_path, output_dir=out, target_node="node1",
                         skew_seconds=600, duration_seconds=20, healthy_timeout_seconds=5,
                         libfaketime_path=str(lib))
    monkeypatch.setattr(skew, "run_command", _Docker([0.0]))
    skew.release_time_skew(runtime_metadata_path=metadata_path, output_dir=out, target_node="node1",
                           skew_seconds=600, healthy_timeout_seconds=5, docker_original_environment=None)
    service = json.loads((tmp_path / "runtime" / "docker-compose.yml").read_text())["services"]["node1"]
    assert "environment" not in service


def test_host_mode_launch_uses_documented_offset(monkeypatch):
    seen = []
    monkeypatch.setattr(skew, "_ensure_tmux_session_absent", lambda session: None)
    monkeypatch.setattr(skew, "run_command", lambda command, **kw: seen.append(command) or SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    node = {"id": "node1", "session": "s", "resolved_binary": "/bin/cardano-node", "config_path": "c",
            "topology_path": "t", "db_dir": "d", "socket_path": "sock", "port": 3001, "pid_file": "p",
            "log_path": "l"}
    skew._launch_skewed_haskell_node(node, skew_seconds=600, libfaketime_path="/lib/libfaketime.so.1")
    assert 'FAKETIME=\\"+600\\"' in seen[0][-1]


def test_host_mode_result_says_skew_is_not_measured():
    body = skew.build_result_body(skew_seconds=600, host_time_at_apply="a", host_time_at_release="a")
    assert body["measured"] is False
    assert body["skew_applied_seconds"] is None
    assert body["skew_requested_seconds"] == 600
    assert body["reason"]


def test_framework_image_installs_libfaketime_pinned_to_the_snapshot():
    """The controller copies its libfaketime into the substrate env dir; it must exist in the image."""
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(encoding="utf-8")
    assert "ARG LIBFAKETIME_VERSION=0.9.10+2024-06-05+gba9ed5b2-0.6" in dockerfile
    assert "libfaketime=${LIBFAKETIME_VERSION}" in dockerfile
    assert skew.DEFAULT_LIBFAKETIME == "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1"


def test_primitive_passes_skew_tolerance_to_the_script(tmp_path, monkeypatch):
    from profile_manager import primitives

    captured = {}

    def fake_run(command, **kwargs):
        captured["config"] = json.loads(Path(command[command.index("--config") + 1]).read_text())
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(primitives.subprocess, "run", fake_run)
    monkeypatch.setattr(primitives, "_build_dwarf_telemetry_env", lambda handle: {})
    monkeypatch.setattr(primitives, "_resolve_output_path", lambda handle, value: tmp_path / str(value))
    primitive = primitives.RuntimeTimeSkew(params={
        "runtime_metadata_path": "runtime.json", "output_dir": "out", "target_node": "node1",
        "skew_seconds": 600, "skew_tolerance_seconds": 2.5})
    primitive.run(SimpleNamespace(run_dir=tmp_path, log=lambda **kwargs: None), None)
    assert captured["config"]["skew_tolerance_seconds"] == 2.5
