"""Run 20261110T101845Z: local-query stress measured 0.0 % CPU and the assertion read it as 100 %
(`value or 100.0`). The 0.0 was not real either: `ps -p <pid>` ran in the controller's pid namespace,
where the node's pid is not visible. CPU now comes from the container's cgroup (docker stats), the
same source the resource profile uses; no sample means no CPU claim, and the assertion fails."""
from __future__ import annotations

import json
import subprocess

import pytest

import profile_manager.primitives as prim
from scripts import runtime_exposure_probe as probe


@pytest.fixture
def events(monkeypatch):
    store: dict[str, dict] = {}

    def fake(handle, *, phase, event, primitive):
        payload = store.get(primitive)
        return [{"payload": payload}] if payload is not None else []

    monkeypatch.setattr(prim, "_events_from_handle", fake)
    return store


def _stress(**result):
    body = {"rate_limit_triggered": True, "critical_work_blocked": False, "cpu_ceiling_pct": 80.0, **result}
    return {"outcome": "ok", "result": body}


def test_a_measured_zero_percent_is_below_the_ceiling(events):
    events["runtime_local_query_stress"] = _stress(peak_cpu_pct=0.0, cpu_sample_count=3)
    out = prim.LocalQueryAmplificationBounded(params={}).evaluate(object())
    assert out["evaluated_value"]["peak_cpu_pct"] == 0.0
    assert out["result"] == "pass"


def test_no_cpu_sample_is_no_pass(events):
    events["runtime_local_query_stress"] = _stress(peak_cpu_pct=None, cpu_sample_count=0)
    out = prim.LocalQueryAmplificationBounded(params={}).evaluate(object())
    assert out["result"] == "fail"
    events["runtime_local_query_stress"] = _stress(cpu_sample_count=0)
    assert prim.LocalQueryAmplificationBounded(params={}).evaluate(object())["result"] == "fail"


def test_a_peak_over_the_ceiling_fails(events):
    events["runtime_local_query_stress"] = _stress(peak_cpu_pct=93.5, cpu_sample_count=2)
    assert prim.LocalQueryAmplificationBounded(params={}).evaluate(object())["result"] == "fail"


def _docker_stats(cpu):
    def run(cmd, **kw):
        assert cmd[:2] == ["docker", "stats"] and cmd[-1] == "c-node1"
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"CPUPerc": cpu, "MemUsage": "1MiB / 2MiB",
                                                               "NetIO": "0B / 0B", "BlockIO": "0B / 0B",
                                                               "PIDs": "9"}) + "\n", "")
    return run


def test_container_cpu_comes_from_the_cgroup():
    assert probe._sample_node_cpu_pct({"container_name": "c-node1"}, {"pid": 4242}, runner=_docker_stats("37.25%")) == 37.25


def test_unreadable_container_cpu_is_none_not_zero():
    def fail(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "no such container")
    assert probe._sample_node_cpu_pct({"container_name": "c-node1"}, {"pid": 4242}, runner=fail) is None


def test_an_invisible_pid_is_none_not_zero():
    def fail(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "")
    assert probe._sample_node_cpu_pct({}, {"pid": 4242}, runner=fail) is None
