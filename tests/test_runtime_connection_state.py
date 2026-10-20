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
