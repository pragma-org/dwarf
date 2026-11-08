"""F78: resource profiling of a containerised node without host /proc visibility.

Inside the controller the node runs in another container: its pid is not in the controller's
/proc and the image has no ``ps``. The sampler then reads the container runtime's cgroup
accounting (``docker stats``) instead of ``/proc/<pid>``, keeping the sample schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import runtime_resource_profile as rp

STATS = {"BlockIO": "90MB / 270MB", "CPUPerc": "1.43%", "Container": "dwarf-x-node1-1", "ID": "4e4cc9bc440f",
         "MemPerc": "0.42%", "MemUsage": "116.7MiB / 27.19GiB", "Name": "dwarf-x-node1-1", "NetIO": "41.2MB / 41.2MB", "PIDs": "14"}


def _runner_factory(calls, *, ps_missing=True):
    def runner(command, **_kwargs):
        calls.append(list(command))
        if command[0] == "ps" and ps_missing:
            raise FileNotFoundError(2, "No such file or directory", "ps")
        if command[:2] == ["docker", "stats"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(STATS) + "\n", stderr="")
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"State": {"Pid": 999999}}]), stderr="")
        if command[:2] == ["docker", "top"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")
    return runner


@pytest.mark.parametrize("text,expected", [
    ("116.7MiB", int(116.7 * 1024 ** 2)), ("27.19GiB", int(27.19 * 1024 ** 3)), ("90MB", 90_000_000),
    ("41.2kB", 41_200), ("512B", 512), ("0B", 0),
])
def test_docker_size_strings_parse_to_bytes(text, expected):
    assert rp._parse_docker_size(text) == expected


def test_container_samples_come_from_docker_stats_and_keep_the_sample_schema():
    calls = []
    samples = rp.collect_container_samples(
        container_name="dwarf-x-node1-1", expected_name="cardano-node",
        sample_count=2, sample_interval_seconds=0, runner=_runner_factory(calls),
    )
    assert len(samples) == 2
    assert [c[:3] for c in calls] == [["docker", "stats", "--no-stream"]] * 2
    first = samples[0]
    assert first["rss_bytes"] == int(116.7 * 1024 ** 2)
    assert first["cpu_percent"] == pytest.approx(1.43)
    assert first["threads"] == 14
    assert first["network_rx_bytes"] == 41_200_000 and first["network_tx_bytes"] == 41_200_000
    assert first["disk_read_bytes"] == 90_000_000 and first["disk_write_bytes"] == 270_000_000
    assert first["fd_count"] is None and first["cpu_time_seconds"] is None
    assert first["source"] == "docker-stats" and first["memory_scope"] == "cgroup"
    assert first["name"] == "cardano-node" and first["state"] == "running"
    for key in ("sample_index", "pid", "monotonic_seconds", "ts_epoch_s", "network_scope"):
        assert key in first


def test_sampling_target_falls_back_to_docker_stats_when_no_pid_is_visible(tmp_path):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 3001,
                                                      "container_name": "dwarf-x-node1-1", "pid_file": str(tmp_path / "none.pid")}],
                                   "amaru_nodes": []}), encoding="utf-8")
    target = rp.resolve_sampling_target(runtime, "node1", proc_root=tmp_path / "proc", runner=_runner_factory([]))
    assert target == {"mode": "docker-stats", "pid": None, "container_name": "dwarf-x-node1-1", "expected_name": "cardano-node"}


def test_sampling_target_prefers_a_visible_pid(tmp_path):
    proc = tmp_path / "proc" / "4242"
    proc.mkdir(parents=True)
    (proc / "status").write_text("Name:\tcardano-node\nVmRSS:\t1024 kB\nThreads:\t3\n", encoding="utf-8")
    (tmp_path / "node1.pid").write_text("4242\n", encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 3001,
                                                      "pid_file": str(tmp_path / "node1.pid")}], "amaru_nodes": []}), encoding="utf-8")
    target = rp.resolve_sampling_target(runtime, "node1", proc_root=tmp_path / "proc", runner=_runner_factory([]))
    assert target["mode"] == "proc" and target["pid"] == 4242


def test_host_node_without_a_visible_pid_still_fails_loudly(tmp_path):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 3001}], "amaru_nodes": []}), encoding="utf-8")
    with pytest.raises((RuntimeError, FileNotFoundError)):
        rp.resolve_sampling_target(runtime, "node1", proc_root=tmp_path / "proc", runner=_runner_factory([]))
