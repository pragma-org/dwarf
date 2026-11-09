"""F109: a deploy recovers a topology whose retained runtime outlived every Docker resource.

The dashboard calls such a topology absent (no managed resources), so it offers deploy and refuses
remove; the controller refused deploy because the retained runtime directory still existed. With no
containers and no network left, the directory is archived and the deploy proceeds (live: profile-g).
"""
from __future__ import annotations

import threading

import pytest

from controller.docker_runtime import DockerRuntime, DockerRuntimeError


class _Stop(Exception):
    pass


def _runtime(tmp_path, monkeypatch, *, before, network=None):
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=lambda *a, **k: None)
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_: {})
    monkeypatch.setattr(runtime, "_project_preflight", lambda *a: (before, network))
    monkeypatch.setattr(runtime, "_compose", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "_verify_absent_postcondition", lambda **_: None)
    monkeypatch.setattr(runtime, "_deployment_config", lambda *a: {})
    monkeypatch.setattr(runtime, "_assert_operator_registration_state", lambda *a: None)

    def invoke(*_args):
        raise _Stop("reached the deployment")

    monkeypatch.setattr(runtime, "_invoke_deployment", invoke)
    current = runtime._runtime_parent("profile-g") / "current"
    current.mkdir(parents=True)
    (current / "runtime.json").write_text("{}", encoding="utf-8")
    return runtime, current


PLAN = {"action": "deploy", "topology_id": "profile-g",
        "deployment": {"compose_project": "dwarf-profile-g", "substrate": {"nodes": []}}}


def test_orphaned_runtime_is_archived_and_the_deploy_proceeds(tmp_path, monkeypatch):
    runtime, current = _runtime(tmp_path, monkeypatch, before=[])
    with pytest.raises(_Stop):
        runtime._apply_deployment(dict(PLAN), "exec-1", threading.Event())
    archived = [p.name for p in current.parent.iterdir() if p.name.startswith("orphaned-")]
    assert archived == ["orphaned-exec-1"]
    assert (current.parent / "orphaned-exec-1" / "runtime.json").exists()


@pytest.mark.parametrize("before,network", [([{"Name": "dwarf-profile-g-node1-1"}], None), ([], {"Name": "dwarf-profile-g-net"})])
def test_a_runtime_with_live_resources_still_refuses_deploy(tmp_path, monkeypatch, before, network):
    runtime, current = _runtime(tmp_path, monkeypatch, before=before, network=network)
    with pytest.raises(DockerRuntimeError, match="topology is not absent"):
        runtime._apply_deployment(dict(PLAN), "exec-1", threading.Event())
    assert current.exists()
