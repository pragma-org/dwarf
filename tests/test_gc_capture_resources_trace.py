"""F114: GC capture of a container node reads the live Resources trace instead of restarting it.

The helper restarted the node through tmux with GHCRTS=-s and waited on a host pid (live:
5f7f0c94). A container node is not restarted: GC counters come from cardano-node's Resources
trace (once per second), as deltas over the sample window.
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_compose_substrate as compose
from scripts import runtime_haskell_gc_capture as subject


def _record(at, **data):
    base = {"Alloc": 1000, "CentiCpu": 100, "CentiGC": 10, "CentiMut": 90, "GcsMajor": 5, "GcsMinor": 50,
            "Heap": 64 * 1024 * 1024, "Live": 10_000_000, "kind": "ResourceStats"}
    base.update(data)
    return json.dumps({"at": at, "ns": "Resources", "data": base, "sev": "Info"})


def test_resources_deltas_map_onto_the_gc_summary():
    lines = [
        "not json",
        _record("2026-11-09T07:00:00Z"),
        json.dumps({"at": "2026-11-09T07:00:00.5Z", "ns": "ChainDB", "data": {}}),
        _record("2026-11-09T07:00:01Z", Alloc=5000, CentiCpu=130, CentiGC=14, CentiMut=116, GcsMajor=6, GcsMinor=58,
                Live=12_000_000, Heap=80 * 1024 * 1024),
    ]
    summary = subject.summarize_resources(lines)
    assert summary["source"] == "resources-trace" and summary["samples"] == 2
    assert summary["total_bytes_allocated"] == 4000
    assert summary["gen_0_collections"] == 8 and summary["gen_1_collections"] == 1
    assert summary["gc_cpu_seconds"] == pytest.approx(0.04) and summary["mutator_cpu_seconds"] == pytest.approx(0.26)
    assert summary["total_cpu_seconds"] == pytest.approx(0.30)
    assert summary["max_residency_bytes"] == 12_000_000 and summary["total_memory_in_use_mib"] == 80
    assert summary["productivity_user_pct"] == pytest.approx(0.26 / 0.30 * 100)
    assert summary["bytes_copied_during_gc"] is None and summary["max_pause_seconds"] is None


def test_fewer_than_two_samples_is_an_error():
    with pytest.raises(RuntimeError, match="Resources"):
        subject.summarize_resources([_record("2026-11-09T07:00:00Z")])


def test_observability_traces_enable_the_resources_namespace(tmp_path):
    path = tmp_path / "configuration.yaml"
    path.write_text(json.dumps({"TraceOptions": {}}), encoding="utf-8")
    compose._enable_cardano_observability_traces(path)
    assert json.loads(path.read_text())["TraceOptions"]["Resources"]["severity"] == "Info"


def test_profile_i_declares_observability_traces():
    from profile_manager.profiles import find_profile
    assert find_profile("profile-i-generated-haskell3").cardano_observability_traces is True


def test_container_node_is_sampled_from_its_logs_without_a_restart(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"name": "node2", "id": "node2", "port": 34302,
                                                      "container_name": "dwarf-i-node2-1", "container_ip": "10.9.0.3",
                                                      "container_listen_address": "node2:3001"}]}))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["docker", "logs"], argv
        out = "\n".join([_record("t0"), _record("t1", Alloc=3000, CentiCpu=120, CentiMut=105, CentiGC=15)])
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subject, "_connect_burst", lambda host, port, attempts: (attempts, 0) if (host, port) == ("10.9.0.3", 3001) else (0, attempts))
    monkeypatch.setattr(subject.time, "sleep", lambda s: None)
    result = subject.run_gc_capture(runtime_metadata_path=runtime, target_node="node2", output_dir=tmp_path / "out",
                                    target_host="127.0.0.1", connect_attempts=2, sample_seconds=0,
                                    startup_timeout_seconds=1, restore_timeout_seconds=1)
    assert result["mechanism"] == "resources-trace" and result["connect_successes"] == 2
    assert result["total_bytes_allocated"] == 2000 and calls[0][-1] == "dwarf-i-node2-1"
    assert (tmp_path / "out" / "resources-trace.ndjson").read_text().count("Resources") == 2
