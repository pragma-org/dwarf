"""F113: pcap capture of container nodes runs tcpdump from the capture-tools image in the
controller's own namespace (where the workload traffic starts) and counts packets in Python."""
from __future__ import annotations

import json
import struct
import subprocess

from scripts import runtime_common as rc
from scripts import runtime_pcap_capture as subject


def _pcap(packets: int, *, magic=0xA1B2C3D4, endian="<") -> bytes:
    header = struct.pack(endian + "IHHiIII", magic, 2, 4, 0, 0, 65535, 1)
    body = b""
    for index in range(packets):
        payload = bytes(60)
        body += struct.pack(endian + "IIII", index, 0, len(payload), len(payload)) + payload
    return header + body


def test_packets_are_counted_without_tcpdump(tmp_path):
    for magic, endian in ((0xA1B2C3D4, "<"), (0xA1B23C4D, "<"), (0xA1B2C3D4, ">")):
        path = tmp_path / "c.pcap"
        path.write_bytes(_pcap(5, magic=magic, endian=endian))
        assert subject.count_packets(path) == 5
    (tmp_path / "empty.pcap").write_bytes(b"")
    assert subject.count_packets(tmp_path / "empty.pcap") == 0
    (tmp_path / "trunc.pcap").write_bytes(_pcap(3)[:-10])
    assert subject.count_packets(tmp_path / "trunc.pcap") == 2


def test_container_capture_uses_the_controller_namespace_and_container_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [
        {"name": f"node{i}", "id": f"node{i}", "port": 34300 + i, "container_name": f"dwarf-p-node{i}-1",
         "container_ip": f"10.9.0.{i + 1}", "container_listen_address": f"node{i}:3001"} for i in (1, 2)]}))
    seen = {}

    class _Proc:
        returncode = 0
        def __init__(self, argv, stdout=None, **kwargs):
            seen["argv"] = argv
            stdout.write(_pcap(4))
        def send_signal(self, sig):
            seen["signal"] = sig
        def wait(self, timeout=None):
            return 0

    connects = []

    class _Conn:
        def __init__(self, address, timeout=None):
            connects.append(address)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rc, "capture_tools_command", lambda tool, **kw: ["docker", "run",
                        f"net={kw['network_container']}", ",".join(kw["capabilities"]), *tool])
    monkeypatch.setattr(subprocess, "Popen", _Proc)
    monkeypatch.setattr(subject.socket, "create_connection", _Conn)
    monkeypatch.setattr(subject.time, "sleep", lambda s: None)
    result = subject.run_container_capture(runtime_metadata_path=runtime, target_nodes=["node1", "node2"],
                                           output_dir=tmp_path / "out", connect_attempts=2, startup_seconds=0,
                                           settle_seconds=0)
    argv = seen["argv"]
    assert "net=controllerid" in argv and "NET_RAW,NET_ADMIN" in argv and "sudo" not in argv
    assert argv[-10:] == ["tcpdump", "-i", "any", "-n", "-s", "0", "-U", "-w", "-", "tcp"]
    assert connects == [("10.9.0.2", 3001), ("10.9.0.3", 3001)] * 2
    assert result["packet_count"] == 4 and result["connect_successes"] == 4 and result["mechanism"] == "controller-netns"
    assert result["workload_exit_code"] == 0
