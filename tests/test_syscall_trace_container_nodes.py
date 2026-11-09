"""F113: a container node's syscalls are traced from the capture-tools image in its pid namespace."""
from __future__ import annotations

import json
import subprocess

from scripts import runtime_common as rc
from scripts import runtime_syscall_trace as subject


class _Proc:
    def __init__(self, argv, stderr=None, **kwargs):
        self.argv = argv
        if stderr is not None and hasattr(stderr, "write"):
            stderr.write('12 openat(AT_FDCWD, "/db", O_RDONLY) = 3\n12 read(3, "", 4096) = 0\n12 read(3, "", 4096) = 0\n')
        self.signals = []

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        return 0


def test_container_node_trace_runs_strace_in_the_node_pid_namespace(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"name": "node1", "id": "node1", "port": 34301,
                                                      "container_name": "dwarf-p-node1-1", "container_ip": "10.9.0.2",
                                                      "container_listen_address": "node1:3001"}]}))
    started = {}

    def popen(argv, **kwargs):
        started["argv"] = argv
        proc = _Proc(argv, **kwargs)
        started["proc"] = proc
        return proc

    connects = []

    class _Conn:
        def __init__(self, address, timeout=None):
            connects.append(address)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rc, "capture_tools_command",
                        lambda tool, **kw: ["docker", "run", f"--pid=container:{kw['pid_container']}",
                                            ",".join(kw["capabilities"]), *tool])
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subject.socket, "create_connection", _Conn)
    monkeypatch.setattr(subject.time, "sleep", lambda s: None)
    result = subject.run_trace(runtime_metadata_path=runtime, target_node="node1", output_dir=tmp_path / "out",
                               connect_attempts=2, target_host="127.0.0.1", startup_seconds=0, settle_seconds=0,
                               strace_bin="strace", sudo_bin="sudo")
    argv = started["argv"]
    assert "--pid=container:dwarf-p-node1-1" in argv and "SYS_PTRACE" in argv and "sudo" not in argv
    assert any("strace" in part and "pgrep" in part for part in argv)
    assert connects == [("10.9.0.2", 3001)] * 2
    assert result["mechanism"] == "container-pid-namespace" and result["total_syscalls"] == 3
    assert result["syscall_counts"]["read"] == 2
