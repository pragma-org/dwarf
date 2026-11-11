import json
from pathlib import Path
from types import SimpleNamespace

from scripts import runtime_connection_state as api


def test_process_table_resolution_accepts_amaru_runtime(monkeypatch):
    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="417 amaru /usr/local/bin/amaru run --listen-address 127.0.0.1:43231\n",
            stderr="",
        ),
    )

    target = api._scan_for_runtime_process(
        {"name": "node2", "impl": "amaru", "port": 43231}
    )

    assert target["pid"] == 417
    assert target["port"] == 43231


def test_pid_file_resolution_accepts_amaru_process_name(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc"
    (proc_root / "417").mkdir(parents=True)
    (proc_root / "417/status").write_text("Name:\tamaru\n", encoding="utf-8")
    pid_file = tmp_path / "amaru.pid"
    pid_file.write_text("417\n", encoding="utf-8")
    metadata = tmp_path / "runtime.json"
    metadata.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "name": "node2",
                        "impl": "amaru",
                        "port": 43231,
                        "pid_file": str(pid_file),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        api,
        "_scan_for_runtime_process",
        lambda node: (_ for _ in ()).throw(AssertionError("unexpected process scan")),
    )

    target = api.resolve_target_process(metadata, "node2", proc_root=proc_root)

    assert target["pid"] == 417
    assert target["port"] == 43231


def test_package_node_liveness_is_a_tcp_connect_to_its_service(tmp_path, monkeypatch):
    """A2: inside an Antithesis package there is no Docker, pid or /proc of the node. Liveness is a
    connect to its compose address; a refused connect means down (same RuntimeError as before)."""
    import pytest
    node = {"id": "node1", "port": 3001, "listen_address": "node1.example:3001",
            "observation_transport": "antithesis-package"}
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"nodes": [node]}))
    monkeypatch.setattr(api.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no docker/ps in a package")))
    seen = []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(api.socket, "create_connection", lambda addr, timeout=None: seen.append(addr) or _Conn())
    target = api.resolve_target_process(path, "node1")
    assert target["pid"] is None and target["port"] == 3001 and seen[0] == ("node1.example", 3001)

    def refused(addr, timeout=None):
        raise ConnectionRefusedError()
    monkeypatch.setattr(api.socket, "create_connection", refused)
    with pytest.raises(RuntimeError):
        api.resolve_target_process(path, "node1")
