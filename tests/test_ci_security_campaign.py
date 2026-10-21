"""Bounded orchestration tests: runtime operations are isolated at one adapter."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from profile_manager.campaign_evidence import evaluate_campaign_evidence
from profile_manager.campaign_work_items import execution_work_items
from profile_manager.security_campaign import _digest
from scripts import ci_security_campaign as api
from test_campaign_evidence import campaign_plan, evidence, freeze, measurement, single_cell


@pytest.fixture
def campaign(campaign_plan):
    return deepcopy(campaign_plan)


class Runtime:
    def __init__(self, manifest, root):
        self.manifest, self.root = manifest, root
        self.ev = evidence(manifest)
        self.calls = []
        self.serial = 0
        self.fail = set()
        self.preflight_passed = True
        self.samples = True
        self.security_measurements = []
        self.owned_projects = set()
        self.calibration_results = {}
        self.before = {"dwarf-substrate-existing"}
        self.removed = []

    def projects(self):
        self.calls.append("inventory")
        return self.before | self.owned_projects | {"dwarf-substrate-other-operator"}

    def preflight(self, manifest, builds):
        self.calls.append("preflight")
        return {"passed": self.preflight_passed, "executed": True,
                "qualified_topology_ids": ([row["id"] for row in manifest["topologies"]]
                                           if self.preflight_passed else [])}

    def run(self, manifest, scenario_id, *, entry=None, execution_entry=None, cell=None, plan_cell=None, mode="stock", control=None, topology_id=None):
        key = entry["id"] if entry else cell["measurement_id"] if cell else "control"
        self.calls.append((key, scenario_id, mode))
        self.serial += 1
        run_id = f"run-{self.serial}"
        self.calibration_results[run_id] = calibration_leg(mode)
        self.owned_projects.add("dwarf-substrate-" + run_id)
        if key in self.fail:
            raise RuntimeError("scenario failed after creating its project")
        result = {"run_id": run_id, "execution_complete": True, "measurements": []}
        result["control"] = deepcopy(self.ev["security"][0]["normal_control"])
        result["control"].update(run_id=run_id, scenario_id=scenario_id,
                                scenario_digest=manifest["definitions"]["scenarios"][scenario_id]["digest"])
        for target in result["control"]["targets"].values():
            target["mode"] = mode
            if mode == "patched": target["patch_set_sha256"] = "d" * 64
            if mode == "coverage": target["coverage_harness_sha256"] = "d" * 64
        if entry:
            row = deepcopy(self.ev["security"][0])
            row.update(entry_id=entry["id"], run_id=run_id, contract_digest=_digest(entry["contract"]),
                       execution_digest=_digest(entry["execution"]), normal_control=control["control"])
            for observation in row["reachability"] + row["oracle"]["observations"]:
                observation["run_id"] = run_id
            result["security"] = row
            result["measurements"] = deepcopy(self.security_measurements)
            for measured in result["measurements"]:
                measured["result"]["run_id"] = run_id
                measured["normal_control"] = deepcopy(control["control"])
                for sample in measured["samples"] + measured["events"]:
                    sample["run_id"] = run_id
        if cell:
            row = measurement(manifest, cell, self.ev, mode=mode)
            row["result"]["run_id"] = run_id
            for sample in row["samples"] + row["events"]:
                sample["run_id"] = run_id
            row["result"]["provenance"]["scenario_id"] = scenario_id
            row["scenario_digest"] = manifest["definitions"]["scenarios"][scenario_id]["digest"]
            row["normal_control"] = deepcopy(control["control"])
            if not self.samples:
                row["samples"] = []
                row["result"]["sample_count"] = 0
            result["measurements"] = [row]
        return result

    def calibration_result(self, run_id):
        return deepcopy(self.calibration_results[run_id])

    def archive(self, run_id):
        self.calls.append(("archive", run_id))
        archive = self.root / f"{run_id}.tar.gz"
        signature = self.root / f"{run_id}.signature.json"
        archive.write_bytes(run_id.encode())
        signature.write_text("signed")
        return {"run_id": run_id, "archive": str(archive), "signature": str(signature),
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "signature_sha256": hashlib.sha256(signature.read_bytes()).hexdigest()}

    def verify_archive(self, receipt):
        self.calls.append(("verify", receipt["run_id"]))
        return api.receipt_files_valid(receipt)

    def cleanup(self, projects):
        self.removed.extend(sorted(projects))


def run(manifest, tmp_path, runtime=None, **kwargs):
    runtime = runtime or Runtime(manifest, tmp_path)
    return api.run_campaign(manifest, directory=tmp_path / "campaign", runtime=runtime,
                            builds=runtime.ev["builds"], lock_path=tmp_path / "host.lock", **kwargs), runtime


def calibration_leg(mode, *, source="f" * 40, patch="sha256:" + "e" * 64,
                    status="available", mean=100.0):
    target = {
        "implementation": "amaru",
        "version": "candidate",
        "source_revision": source,
        "mode": mode,
        "image_digest": "sha256:" + ("a" if mode == "stock" else "b") * 64,
    }
    if mode == "patched":
        target["patch_set_sha256"] = patch
    return {
        "status": status,
        "target": target,
        "workload_identity": {"scenario_id": "calibration", "seed": "0xA11CE",
                              "workload_digest": "sha256:" + "c" * 64,
                              "attempt_count": 40},
        "runner": {"script": "calibration.py", "script_sha256": "sha256:" + "d" * 64},
        "timing_policy": {"clock": "monotonic-perf-counter-ns", "warmup": "none"},
        "hardware": {"architecture": "x86_64", "logical_cpu_count": 8},
        "attempts": {"total": 40, "outcomes": {"rejected": 40}},
        "measurements": {"handshake_rejection_roundtrip": {
            "unit": "us", "sample_count": 40, "mean": mean, "median": mean,
            "p95": mean, "p99": mean,
        }},
    }


def test_security_work_item_runs_only_its_selected_entry(campaign, tmp_path):
    second = deepcopy(campaign["entries"][0])
    second["id"] = "second-security-entry"
    campaign["entries"].append(second)
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    item = next(
        row
        for row in execution_work_items(campaign)["items"]
        if row["kind"] == "security" and row["entry_id"] == campaign["entries"][0]["id"]
    )

    state, _ = run(campaign, tmp_path, runtime, work_item=item)

    assert "security:" + campaign["entries"][0]["id"] in state["cells"]
    assert "security:second-security-entry" not in state["cells"]
    assert api.work_item_succeeded(state, item) is True


def test_measurement_work_item_skips_security_and_other_recipes(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    cell["runtime"] = {"status": "implemented"}
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    item = next(row for row in execution_work_items(campaign)["items"] if row["kind"] == "measurement")

    state, _ = run(campaign, tmp_path, runtime, work_item=item)

    assert not any(key.startswith("security:") for key in state["cells"])
    assert list(state["cells"]) == [api._cell_key(cell, "stock")]
    assert api.work_item_succeeded(state, item) is True


def test_work_item_success_does_not_claim_full_campaign_completion(campaign, tmp_path):
    item = next(row for row in execution_work_items(campaign)["items"] if row["kind"] == "security")
    state, _ = run(campaign, tmp_path, Runtime(campaign, tmp_path), work_item=item)

    assert api.work_item_succeeded(state, item) is True
    assert state["status"]["measurement_completeness"] == "incomplete"


def test_patched_primary_requires_successful_bounded_calibration():
    decision = api.decide_patched_primary(
        calibration_leg("stock"),
        calibration_leg("patched", mean=103.0),
        expected_source_revision="f" * 40,
        expected_patch_set_sha256="sha256:" + "e" * 64,
    )
    assert decision["selected_mode"] == "patched"
    assert decision["status"] == "passed"
    assert decision["reason"] == "paired-calibration-passed"


@pytest.mark.parametrize("case", ["execution", "overhead", "identity"])
def test_patched_calibration_failure_selects_stock_once(case):
    stock = calibration_leg("stock")
    patched = calibration_leg("patched", mean=103.0)
    if case == "execution":
        patched["status"] = "unavailable"
    elif case == "overhead":
        patched["measurements"]["handshake_rejection_roundtrip"].update(
            mean=110.0, median=110.0, p95=110.0, p99=110.0
        )
    else:
        patched["target"]["source_revision"] = "0" * 40
    decision = api.decide_patched_primary(
        stock,
        patched,
        expected_source_revision="f" * 40,
        expected_patch_set_sha256="sha256:" + "e" * 64,
    )
    assert decision["selected_mode"] == "stock"
    assert decision["status"] == "failed"
    assert decision["reason"].startswith("paired-calibration-")
    assert decision["retry_build"] is False


def test_calibration_phase_runs_only_the_frozen_mode_scenario(campaign, tmp_path):
    scenario_id = campaign["execution_policy"]["calibration"]["stock_scenario_id"]

    state, runtime = run(
        campaign,
        tmp_path,
        phase="calibration",
        calibration_mode="stock",
    )

    calls = [call for call in runtime.calls if isinstance(call, tuple) and call[0] not in {"archive", "verify"}]
    assert calls == [("control", scenario_id, "stock")]
    assert state["cells"]["calibration:stock"]["status"] == "complete"
    assert state["cells"]["calibration:stock"]["calibration_result"]["status"] == "available"
    assert not any(key.startswith("security:") for key in state["cells"])


def test_local_runtime_reads_only_the_canonical_calibration_result(tmp_path):
    runtime = api.LocalRuntime.__new__(api.LocalRuntime)
    runtime.runs = tmp_path / "runs"
    result_path = (
        runtime.runs
        / "run-1"
        / "outputs"
        / "amaru-measurement-calibration"
        / "result.json"
    )
    result_path.parent.mkdir(parents=True)
    expected = calibration_leg("patched")
    result_path.write_text(json.dumps(expected))

    assert runtime.calibration_result("run-1") == expected
    with pytest.raises(ValueError, match="canonical run ID"):
        runtime.calibration_result("../run-1")


def test_measurement_control_and_result_share_one_prepared_recipe(campaign, tmp_path):
    manifest, cell = single_cell(campaign)

    class RecipeRuntime(Runtime):
        @contextmanager
        def recipe(self, manifest, scenario_id, *, cell, mode, topology_id):
            identity = (topology_id, cell["node_id"], scenario_id, mode)
            self.calls.append(("recipe-enter", identity))
            try:
                yield
            finally:
                self.calls.append(("recipe-exit", identity))

    runtime = RecipeRuntime(manifest, tmp_path)
    state, runtime = run(manifest, tmp_path, runtime=runtime)

    identity = (cell["topology_id"], cell["node_id"], cell["scenario_id"], "stock")
    assert [call for call in runtime.calls if call[0] == "recipe-enter"] == [
        ("recipe-enter", identity)
    ]
    assert [call for call in runtime.calls if call[0] == "recipe-exit"] == [
        ("recipe-exit", identity)
    ]
    scenario_calls = [
        call for call in runtime.calls
        if len(call) == 3 and call[1] == cell["scenario_id"]
    ]
    assert scenario_calls == [
        ("control", cell["scenario_id"], "stock"),
        (cell["measurement_id"], cell["scenario_id"], "stock"),
    ]
    assert state["cells"][api._cell_key(cell, "stock")]["status"] == "complete"


def test_local_runtime_recipe_removes_the_exact_profile_after_failure(
    campaign, tmp_path, monkeypatch
):
    manifest, cell = single_cell(campaign)
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "catalog.json",
        producer_version="99.0.0",
        signing_key=tmp_path / "key",
    )
    project = "dwarf-profile-gh-campaign-profile-o-amaru-target-latest-confirmed"
    topology_id = "profile-o-amaru-target-latest-confirmed"
    deployment = {
        "adapter": "runtime_amaru_control_substrate.py",
        "compose_project": project,
        "substrate": {
            "profile_id": topology_id,
            "nodes": [
                {
                    "target_mode": "stock",
                    "image": "example.invalid/cardano@sha256:" + "a" * 64,
                },
                {
                    "target_mode": "patched",
                    "image": "sha256:" + "b" * 64,
                },
            ],
        },
    }
    calls = []

    class DockerRuntime:
        def apply(self, plan, execution_id, cancel_event):
            calls.append(
                (
                    deepcopy(plan),
                    execution_id,
                    cancel_event.is_set(),
                    os.environ.get("DWARF_GOLDEN_IMAGES"),
                )
            )
            return {"status": "applied", "topology_id": topology_id}

        def resolve_run_network(self, selected_profile_id):
            assert selected_profile_id == topology_id
            return project + "-default"

    monkeypatch.setattr(
        runtime,
        "_recipe_deployment",
        lambda *args, **kwargs: (topology_id, deployment),
        raising=False,
    )
    monkeypatch.setattr(runtime, "_docker_runtime", lambda: DockerRuntime(), raising=False)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", "prior-allowlist")

    monkeypatch.delenv("ADA2_DWARF_RUN_NETWORK", raising=False)
    monkeypatch.delenv("ADA2_DWARF_REMOTE_BASE", raising=False)
    with pytest.raises(RuntimeError, match="measured run failed"):
        with runtime.recipe(
            manifest,
            cell["scenario_id"],
            cell=cell,
            mode="stock",
            topology_id=cell["topology_id"],
        ):
            assert os.environ["ADA2_DWARF_REMOTE_BASE"] == str(
                runtime.profile_runtime_base
            )
            assert os.environ["ADA2_DWARF_RUN_NETWORK"] == project + "-default"
            raise RuntimeError("measured run failed")

    assert [call[0]["action"] for call in calls] == ["deploy", "remove"]
    expected_golden = "example.invalid/cardano@sha256:" + "a" * 64
    assert [call[3] for call in calls] == [expected_golden, expected_golden]
    assert os.environ["DWARF_GOLDEN_IMAGES"] == "prior-allowlist"
    assert calls[0][0]["deployment"] == deployment
    assert calls[1][0] == {
        "action": "remove",
        "topology_id": topology_id,
        "deployment": deployment,
    }
    assert project not in runtime.owned_projects
    assert "ADA2_DWARF_RUN_NETWORK" not in os.environ
    assert "ADA2_DWARF_REMOTE_BASE" not in os.environ


def test_local_runtime_repeated_recipe_uses_fresh_mutation_ids(
    campaign, tmp_path, monkeypatch
):
    manifest, cell = single_cell(campaign)
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "catalog.json",
        producer_version=None,
        signing_key=tmp_path / "key",
    )
    profile_id = "profile-n-cardano-latest-confirmed"
    deployment = {
        "adapter": "runtime_compose_substrate.py",
        "compose_project": "dwarf-profile-gh-campaign-" + profile_id,
        "substrate": {"profile_id": profile_id, "nodes": []},
    }
    calls = []

    class DockerRuntime:
        def apply(self, plan, execution_id, cancel_event):
            calls.append((plan["action"], execution_id))
            return {"status": "applied"}

        def resolve_run_network(self, selected_profile_id):
            assert selected_profile_id == profile_id
            return deployment["compose_project"] + "-default"

    monkeypatch.setattr(
        runtime,
        "_recipe_deployment",
        lambda *args, **kwargs: (profile_id, deployment),
    )
    monkeypatch.setattr(runtime, "_docker_runtime", lambda: DockerRuntime())
    for _ in range(2):
        with runtime.recipe(
            manifest,
            cell["scenario_id"],
            cell=cell,
            mode="stock",
            topology_id=cell["topology_id"],
        ):
            pass

    assert [action for action, _ in calls] == ["deploy", "remove", "deploy", "remove"]
    assert calls[0][1] != calls[2][1]
    assert calls[1][1] != calls[3][1]


def test_local_runtime_inline_substrate_recipe_owns_its_lifecycle(
    campaign, tmp_path, monkeypatch
):
    manifest, cell = single_cell(campaign)
    scenario = manifest["definitions"]["scenarios"][cell["scenario_id"]][
        "document"
    ]
    scenario.pop("profile", None)
    scenario["substrate"] = {
        "compose_mode": "docker",
        "nodes": [{"id": "node1", "impl": "amaru"}],
    }
    topology = next(
        row for row in manifest["topologies"] if row["id"] == cell["topology_id"]
    )
    topology.pop("deployment_profile", None)
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "catalog.json",
        producer_version=None,
        signing_key=tmp_path / "key",
    )
    monkeypatch.setattr(
        runtime,
        "_recipe_deployment",
        lambda *args, **kwargs: pytest.fail(
            "inline substrate must not receive a separate profile deployment"
        ),
    )

    with runtime.recipe(
        manifest,
        cell["scenario_id"],
        cell=cell,
        mode="stock",
        topology_id=cell["topology_id"],
    ):
        pass


def test_local_runtime_recipe_builds_the_frozen_exact_deployment(
    campaign, tmp_path, monkeypatch
):
    from profile_manager import deployment_versions, profiles

    manifest, cell = single_cell(campaign)
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "catalog.json",
        producer_version="99.0.0",
        signing_key=tmp_path / "key",
    )
    scenario_id = cell["scenario_id"]
    profile_id = manifest["definitions"]["scenarios"][scenario_id]["document"][
        "profile"
    ]
    expected_desired = {"schema_version": "v1", "topology_id": profile_id}
    captured = {}
    monkeypatch.setattr(
        runtime,
        "_catalog_binding",
        lambda: {"catalog": {"releases": []}, "path": "frozen", "sha256": "0" * 64},
    )
    monkeypatch.setattr(
        runtime,
        "_plan",
        lambda *args, **kwargs: {
            "profile": {"id": profile_id},
            "versions": {
                "resolved": {"amaru": {"version": "proposal"}},
                "expected_desired_topology": expected_desired,
            }
        },
    )
    monkeypatch.setattr(
        runtime,
        "_campaign_measurement_target",
        lambda *args, **kwargs: {"target_mode": "patched"},
    )

    def preview(profile_data, catalog, *, catalog_revision):
        captured["profile_data"] = profile_data
        captured["catalog"] = catalog
        captured["catalog_revision"] = catalog_revision
        return {"resolved": {}, "supporting": {}}

    def substrate(profile, version_preview, *, measurement_target_override=None):
        captured["profile"] = profile
        captured["override"] = measurement_target_override
        return {
            "profile_id": profile.id,
            "deployment_adapter": "amaru-control",
            "compose_mode": "docker",
            "host_strategy": "local",
            "nodes": [],
        }

    monkeypatch.setattr(deployment_versions, "build_deployment_version_preview", preview)
    monkeypatch.setattr(deployment_versions, "version_catalog_revision", lambda **kwargs: "rev")
    monkeypatch.setattr(profiles, "versioned_substrate_for_profile", substrate)
    monkeypatch.setattr(
        profiles,
        "desired_topology_from_substrate",
        lambda frozen_profile_id, value: expected_desired,
    )
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "gh-campaign")
    monkeypatch.setenv("DWARF_CONTROLLER_HOST_STATE_DIR", "/host/runtime-state")

    actual_profile_id, deployment = runtime._recipe_deployment(
        manifest,
        scenario_id,
        cell=cell,
        mode="patched",
        topology_id=cell["topology_id"],
    )

    assert actual_profile_id == profile_id
    assert captured["profile_data"]["version_policy"] == "exact"
    assert captured["profile_data"]["amaru_version"] == "proposal"
    assert captured["override"] == {"target_mode": "patched"}
    assert deployment["adapter"] == "runtime_amaru_control_substrate.py"
    assert deployment["compose_project"] == f"dwarf-profile-gh-campaign-{profile_id}"
    assert deployment["substrate"]["controller_ownership"] == {
        "installation_id": "gh-campaign",
        "topology_id": profile_id,
    }
    assert deployment["substrate"]["docker_host_runtime_root"] == (
        f"/host/runtime-state/controller/topology-runtimes/{profile_id}/current"
    )


def test_local_runtime_recipe_rejects_an_unfrozen_resolved_profile(
    campaign, tmp_path, monkeypatch
):
    manifest, cell = single_cell(campaign)
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "missing-catalog.json",
        producer_version=None,
        signing_key=tmp_path / "key",
    )
    monkeypatch.setattr(
        runtime,
        "_plan",
        lambda *args, **kwargs: {
            "profile": {"id": "profile-not-frozen"},
            "versions": {"expected_desired_topology": {}},
        },
    )

    with pytest.raises(ValueError, match="resolved.*frozen deployment profile"):
        runtime._recipe_deployment(
            manifest,
            cell["scenario_id"],
            cell=cell,
            mode="stock",
            topology_id=cell["topology_id"],
        )


def test_local_runtime_cleanup_uses_profile_lifecycle_for_owned_volumes(
    tmp_path, monkeypatch
):
    runtime = api.LocalRuntime(
        tmp_path / "runtime",
        catalog_path=tmp_path / "catalog.json",
        producer_version=None,
        signing_key=tmp_path / "key",
    )
    profile_id = "profile-n-cardano-latest-confirmed"
    project = "dwarf-profile-gh-campaign-profile-n-cardano-latest-confirmed"
    deployment = {
        "adapter": "runtime_compose_substrate.py",
        "compose_project": project,
        "substrate": {"profile_id": profile_id},
    }
    calls = []

    class DockerRuntime:
        def apply(self, plan, execution_id, cancel_event):
            calls.append(deepcopy(plan))
            return {"status": "applied"}

    runtime.owned_projects.add(project)
    runtime._profile_projects[project] = (profile_id, deployment)
    monkeypatch.setattr(runtime, "_docker_runtime", lambda: DockerRuntime())

    runtime.cleanup({project})

    assert calls == [
        {
            "action": "remove",
            "topology_id": profile_id,
            "deployment": deployment,
        }
    ]
    assert project not in runtime.owned_projects


@pytest.mark.parametrize("patched_mean, expected_mode", [(103.0, "patched"), (110.0, "stock")])
def test_patched_first_campaign_calibrates_before_selecting_one_manifest(
    campaign, tmp_path, monkeypatch, patched_mean, expected_mode
):
    primary = deepcopy(campaign)
    stock = deepcopy(campaign)
    primary["campaign_id"] = "campaign-primary"
    primary["campaign_digest"] = "sha256:" + "1" * 64
    stock["campaign_id"] = "campaign-stock"
    stock["campaign_digest"] = "sha256:" + "2" * 64
    calls = []

    def execute(manifest, **kwargs):
        phase = kwargs.get("phase", "full")
        mode = kwargs.get("calibration_mode")
        calls.append((phase, mode, manifest["campaign_id"]))
        if phase == "calibration":
            mean = patched_mean if mode == "patched" else 100.0
            return {"cells": {f"calibration:{mode}": {
                "status": "complete",
                "calibration_result": calibration_leg(
                    mode,
                    source=primary["candidate"]["repository_commit"],
                    mean=mean,
                ),
            }}}
        return {
            "selected_campaign_id": manifest["campaign_id"],
            "campaign_digest": manifest["campaign_digest"],
            "cells": {},
            "evaluation": {"security_validity": {}, "measurement_completeness": {}},
        }

    monkeypatch.setattr(api, "run_campaign", execute)
    authority = {
        "authority": "patched-primary-stock-confirmed",
        "stock_security_result": "pass",
        "entries": [],
    }
    monkeypatch.setattr(api, "evaluate_stock_replay_authority", lambda *args: deepcopy(authority))
    result = api.run_patched_first_campaign(
        primary,
        stock,
        directory=tmp_path / "campaign",
        runtime=object(),
        builds=[{"mode": "patched", "patch_set_digest": "sha256:" + "e" * 64}],
        lock_path=tmp_path / "host.lock",
    )

    assert calls[:2] == [
        ("calibration", "stock", "campaign-stock"),
        ("calibration", "patched", "campaign-primary"),
    ]
    selected = primary if expected_mode == "patched" else stock
    assert calls[2] == ("full", None, selected["campaign_id"])
    if expected_mode == "patched":
        assert calls[3] == ("security-replay", None, stock["campaign_id"])
        assert result["stock_replay"]["selected_campaign_id"] == stock["campaign_id"]
        assert result["campaign"]["evaluation"]["stock_replay_authority"] == authority
    else:
        assert len(calls) == 3
        assert result["stock_replay"] is None
    assert result["decision"]["selected_mode"] == expected_mode
    assert result["campaign"]["selected_campaign_id"] == selected["campaign_id"]
    retained_manifest = json.loads((tmp_path / "campaign/selected-manifest.json").read_text())
    assert retained_manifest == selected
    assert "checkpoint_digest" not in retained_manifest
    assert "checkpoint_digest" not in json.loads(
        (tmp_path / "campaign/calibration-decision.json").read_text()
    )


def test_security_replay_phase_skips_measurement_cells(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    cell["runtime"] = {"status": "implemented"}
    cell["fallback"] = {"status": "blocked", "reason": "not needed"}
    freeze(campaign)

    state, runtime = run(campaign, tmp_path, phase="security-replay")

    executed = [call[0] for call in runtime.calls if isinstance(call, tuple)
                and call[0] not in {"archive", "verify"}]
    assert executed == ["control", campaign["entries"][0]["id"]]
    assert state["phase"] == "security-replay"
    assert not any(key.startswith("measurement:") for key in state["cells"])


def test_readiness_precedes_deterministic_security_workloads_and_faults(campaign, tmp_path):
    extra = deepcopy(campaign["entries"][0])
    extra["id"] = "z-last"
    campaign["entries"] = [extra, campaign["entries"][0]]
    freeze(campaign)
    state, runtime = run(campaign, tmp_path)
    calls = [call[0] for call in runtime.calls if isinstance(call, tuple) and call[0] not in {"archive", "verify"}]
    assert runtime.calls.index("preflight") < next(i for i, c in enumerate(runtime.calls) if isinstance(c, tuple))
    assert calls[:4] == ["control", campaign["entries"][1]["id"], "control", "z-last"]
    assert state["evaluation"]["security_validity"]["classification"] == "pass"
    assert all(campaign["definitions"]["scenarios"][entry["contract"]["scenario_id"]]["document"].get("load") for entry in campaign["entries"])


def test_failed_readiness_starts_no_scenarios_and_is_retained(campaign, tmp_path):
    runtime = Runtime(campaign, tmp_path)
    runtime.preflight_passed = False
    state, _ = run(campaign, tmp_path, runtime)
    assert not any(isinstance(call, tuple) for call in runtime.calls)
    assert state["readiness"]["passed"] is False
    assert json.loads((tmp_path / "campaign/state.json").read_text())["readiness"] == state["readiness"]


def test_security_is_evaluated_before_conditional_fallback(campaign, tmp_path, monkeypatch):
    campaign, cell = single_cell(campaign)
    cell["runtime"] = {"status": "implemented"}
    cell["fallback"] = {"scenario_id": cell["scenario_id"], "status": "ready", "activation_condition": "unsatisfied rule"}
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    original = api.evaluate_campaign_evidence
    evaluations = []
    def evaluate(manifest, ev):
        evaluations.append(deepcopy(ev))
        return original(manifest, ev)
    monkeypatch.setattr(api, "evaluate_campaign_evidence", evaluate)
    state, _ = run(campaign, tmp_path, runtime)
    assert any(ev["security"] and not ev["measurements"] for ev in evaluations)
    assert state["evaluation"]["measurement_completeness"]["measurement_complete"] is True


def test_satisfied_tap_does_not_run_calibration_or_fallback(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    cell["scenario_id"] = campaign["entries"][0]["contract"]["scenario_id"]
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.security_measurements = [measurement(campaign, cell, runtime.ev)]
    state, _ = run(campaign, tmp_path, runtime)
    assert state["evaluation"]["measurement_completeness"]["measurement_complete"] is True
    assert not any(isinstance(call, tuple) and call[0] == cell["measurement_id"] for call in runtime.calls)


@pytest.mark.parametrize("mode", ["stock", "patched", "coverage"])
def test_zero_samples_in_any_required_mode_cannot_complete(campaign, tmp_path, mode):
    campaign, cell = single_cell(campaign, modes=[mode])
    cell["runtime"] = {"status": "implemented"}
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.samples = False
    state, _ = run(campaign, tmp_path, runtime)
    assert state["evaluation"]["security_validity"]["classification"] == "pass"
    assert state["evaluation"]["measurement_completeness"]["measurement_complete"] is False


def test_successful_runs_roundtrip_and_failure_retains_partial_results(campaign, tmp_path):
    extra = deepcopy(campaign["entries"][0])
    extra["id"] = "z-fails"
    campaign["entries"].append(extra)
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.fail = {"z-fails"}
    state, _ = run(campaign, tmp_path, runtime)
    assert state["cells"]["security:z-fails"]["status"] == "failed"
    assert state["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "complete"
    assert state["evidence"]["security"]
    assert len(state["cells"]["security:" + campaign["entries"][0]["id"]]["archives"]) == 2
    assert all(("verify", call[1]) in runtime.calls for call in runtime.calls if isinstance(call, tuple) and call[0] == "archive")
    assert set(runtime.removed) == runtime.owned_projects
    assert "dwarf-substrate-other-operator" not in runtime.removed


def test_failed_normal_control_retains_native_diagnostics(campaign, tmp_path, monkeypatch):
    runtime = Runtime(campaign, tmp_path)
    original = runtime.run

    def incomplete_control(*args, **kwargs):
        if kwargs.get("execution_entry") is not None:
            return {
                "run_id": "run-control-failed",
                "execution_complete": False,
                "native_assertions": [
                    {"primitive": "runtime_compose_substrate", "outcome": "unexpected_exit"}
                ],
                "control": {"status": "unknown"},
                "measurements": [],
            }
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "run", incomplete_control)

    state, _ = run(campaign, tmp_path, runtime)

    record = state["cells"]["security:" + campaign["entries"][0]["id"]]
    assert record["status"] == "failed"
    assert record["control"]["run_id"] == "run-control-failed"
    assert record["control"]["native_assertions"][0]["outcome"] == "unexpected_exit"


def test_resume_only_failed_cells_and_rechecks_completed_archive(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.fail = {campaign["entries"][0]["id"]}
    run(campaign, tmp_path, runtime)
    runtime.fail.clear()
    runtime.calls.clear()
    state, _ = run(campaign, tmp_path, runtime, resume=True)
    assert state["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "complete"
    runtime.calls.clear()
    state, _ = run(campaign, tmp_path, runtime, resume=True)
    assert not any(isinstance(call, tuple) and call[0] == campaign["entries"][0]["id"] for call in runtime.calls)
    receipt = state["cells"]["security:" + campaign["entries"][0]["id"]]["archives"][0]
    Path(receipt["archive"]).write_bytes(b"tampered")
    runtime.calls.clear()
    run(campaign, tmp_path, runtime, resume=True)
    assert any(isinstance(call, tuple) and call[0] == campaign["entries"][0]["id"] for call in runtime.calls)


def test_resume_rejects_changed_snapshot_or_builds(campaign, tmp_path):
    _, runtime = run(campaign, tmp_path)
    runtime.ev["builds"][0]["artifacts"]["image_digest"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="build"):
        run(campaign, tmp_path, runtime, resume=True)


def test_host_lock_contends_across_campaign_directories(tmp_path):
    with api.host_lock(tmp_path / "host.lock"):
        with pytest.raises(RuntimeError, match="lock"):
            with api.host_lock(tmp_path / "host.lock"):
                pytest.fail("second campaign acquired the same host lock")


@pytest.mark.parametrize("unsafe", ["../../outside", "/tmp/scenario.yaml", "$(touch pwned)"])
def test_rejects_untrusted_scenario_paths_before_runtime(campaign, tmp_path, unsafe):
    runtime = Runtime(campaign, tmp_path)
    campaign["entries"][0]["contract"]["scenario_id"] = unsafe
    freeze(campaign)
    with pytest.raises(ValueError):
        run(campaign, tmp_path, runtime)
    assert runtime.calls == []


def test_recomputed_digest_does_not_authorize_untrusted_shell(campaign, tmp_path):
    scenario = next(iter(campaign["definitions"]["scenarios"].values()))
    scenario["document"]["load"] = [{"primitive": "shell", "command": "touch /tmp/pwned"}]
    scenario["digest"] = _digest(scenario["document"])
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    with pytest.raises(ValueError, match="trusted|catalog"):
        run(campaign, tmp_path, runtime)
    assert runtime.calls == []


def test_declared_ready_fallback_only_is_accepted_by_evaluator(campaign):
    campaign, cell = single_cell(campaign)
    scenario_id = next(key for key in campaign["definitions"]["scenarios"] if key != cell["scenario_id"])
    cell["fallback"] = {"scenario_id": scenario_id, "status": "ready", "activation_condition": "unsatisfied"}
    freeze(campaign)
    ev = evidence(campaign)
    row = measurement(campaign, cell, ev)
    row["result"]["provenance"]["scenario_id"] = scenario_id
    row["scenario_digest"] = campaign["definitions"]["scenarios"][scenario_id]["digest"]
    row["normal_control"].update(scenario_id=scenario_id, scenario_digest=row["scenario_digest"])
    ev["measurements"] = [row]
    assert evaluate_campaign_evidence(campaign, ev)["measurement_completeness"]["measurement_complete"] is True
    cell["fallback"]["status"] = "blocked"
    cell["fallback"]["reason"] = "prerequisites missing"
    freeze(campaign)
    ev["campaign_digest"] = campaign["campaign_digest"]
    assert evaluate_campaign_evidence(campaign, ev)["measurement_completeness"]["measurement_complete"] is False


def test_different_ready_fallback_runs_only_after_unsatisfied_primary(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    fallback_id = next(key for key in campaign["definitions"]["scenarios"] if key != cell["scenario_id"])
    cell["fallback"] = {"scenario_id": fallback_id, "status": "ready", "activation_condition": "unsatisfied rule"}
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    original = runtime.run
    def execute(manifest, scenario_id, **kwargs):
        runtime.samples = scenario_id == fallback_id
        return original(manifest, scenario_id, **kwargs)
    runtime.run = execute
    state, _ = run(campaign, tmp_path, runtime)
    calls = [call[1] for call in runtime.calls if isinstance(call, tuple) and call[0] == cell["measurement_id"]]
    assert calls == [cell["scenario_id"], fallback_id]
    assert state["evaluation"]["measurement_completeness"]["measurement_complete"] is True


def test_local_runtime_uses_signed_bundle_roundtrip_helpers(tmp_path, monkeypatch):
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / "catalog.json", producer_version="99.0", signing_key=tmp_path / "key.pem")
    calls = []
    environments = []
    run_id = "run-123"
    export = runtime.runs / run_id / "outputs/export"
    export.mkdir(parents=True)
    (export / f"{run_id}-bundle-export.tar.gz").write_bytes(b"archive")
    (export / "signature.json").write_text("signature")
    def command(argv, **kwargs):
        calls.append(argv)
        environments.append(kwargs.get("env", {}))
        return ""
    monkeypatch.setattr(runtime, "_command", command)
    receipt = runtime.archive(run_id)
    assert runtime.verify_archive(receipt) is True
    assert [argv[2:4] for argv in calls] == [["bundle", "sign"], ["bundle", "export"], ["bundle", "import"], ["bundle", "verify"]]
    assert all(env["ADA2_DWARF_BUNDLE_SIGNING_KEY"] == str(tmp_path / "key.pem") for env in environments[:2])
    assert "--signature-path" in calls[2]
    assert receipt["archive_sha256"] == hashlib.sha256(b"archive").hexdigest()


def test_local_cleanup_uses_exact_owned_labels_and_preserves_unrelated_projects(tmp_path, monkeypatch):
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / "catalog", producer_version="99.0", signing_key=tmp_path / "key")
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        return "container-id\n" if argv[:3] == ["docker", "ps", "-aq"] else "network-id\n" if argv[:4] == ["docker", "network", "ls", "-q"] else ""
    monkeypatch.setattr(runtime, "_command", command)
    runtime.owned_projects.add("dwarf-substrate-run-1")
    runtime.cleanup({"dwarf-substrate-run-1"})
    assert calls == [["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=dwarf-substrate-run-1"],
                     ["docker", "rm", "-f", "container-id"],
                     ["docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project=dwarf-substrate-run-1"],
                     ["docker", "network", "rm", "network-id"]]
    with pytest.raises(ValueError, match="owned"):
        runtime.cleanup({"dwarf-substrate-unrelated"})


def test_cli_rejects_arbitrary_shell_command_option():
    with pytest.raises(SystemExit) as result:
        api.main(["--command", "touch /tmp/pwned"])
    assert result.value.code == 2


def test_malformed_scenario_evidence_is_retained_without_aborting_other_entries(campaign, tmp_path):
    extra = deepcopy(campaign["entries"][0])
    extra["id"] = "z-valid"
    campaign["entries"].append(extra)
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    original = runtime.run
    def execute(manifest, scenario_id, **kwargs):
        result = original(manifest, scenario_id, **kwargs)
        if (kwargs.get("entry") or {}).get("id") == campaign["entries"][0]["id"]:
            result["security"]["assertions"] = "malformed"
        return result
    runtime.run = execute
    state, _ = run(campaign, tmp_path, runtime)
    assert state["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "failed"
    assert state["cells"]["security:z-valid"]["status"] == "complete"


def test_calibration_control_uses_its_exact_topology(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    seen = []
    original = runtime.run
    def execute(manifest, scenario_id, **kwargs):
        seen.append(kwargs.get("topology_id"))
        return original(manifest, scenario_id, **kwargs)
    runtime.run = execute
    run(campaign, tmp_path, runtime)
    assert seen and all(topology == cell["topology_id"] for topology in seen)


def test_calibration_control_keeps_its_per_node_plan_binding(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    seen = []
    original = runtime.run

    def execute(manifest, scenario_id, **kwargs):
        if kwargs.get("cell") is None and scenario_id == cell["scenario_id"]:
            seen.append(kwargs.get("plan_cell"))
        return original(manifest, scenario_id, **kwargs)

    runtime.run = execute
    run(campaign, tmp_path, runtime)

    assert seen == [cell]


def test_shared_measurement_recipe_runs_once_per_node_and_mode(campaign, tmp_path):
    shared = [
        deepcopy(cell)
        for cell in campaign["required_tap_cells"]
        if cell["implementation"] == "amaru"
        and cell["scenario_id"] == "amaru-measurement-e2e-stock-portable"
    ][:2]
    assert len(shared) == 2
    for cell in shared:
        cell["target_modes"] = ["stock"]
        cell["runtime"] = {"status": "implemented"}
        cell["fallback"] = {
            "scenario_id": cell["scenario_id"],
            "status": "ready",
            "activation_condition": "unsatisfied rule",
        }
    campaign["required_tap_cells"] = shared
    freeze(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.samples = False

    run(campaign, tmp_path, runtime)

    recipe_calls = [
        call
        for call in runtime.calls
        if isinstance(call, tuple) and call[1] == shared[0]["scenario_id"]
    ]
    assert len(recipe_calls) == 2  # one control and one measured execution


def test_archive_failure_does_not_erase_security_finding(campaign, tmp_path):
    runtime = Runtime(campaign, tmp_path)
    runtime.ev["security"][0]["assertions"][0]["result"] = "fail"
    original = runtime.archive
    def archive(run_id):
        if any(isinstance(call, tuple) and call[0] == campaign["entries"][0]["id"] for call in runtime.calls):
            raise RuntimeError("archive storage failed")
        return original(run_id)
    runtime.archive = archive
    state, _ = run(campaign, tmp_path, runtime)
    assert state["evaluation"]["security_validity"]["classification"] == "finding"
    assert state["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "failed"


def test_resume_retries_a_cell_when_signature_verification_fails(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    state, runtime = run(campaign, tmp_path)
    original = runtime.verify_archive
    failed_run = state["cells"]["security:" + campaign["entries"][0]["id"]]["archives"][0]["run_id"]
    def verify(receipt):
        if receipt["run_id"] == failed_run:
            raise RuntimeError("signature is invalid")
        return original(receipt)
    runtime.verify_archive = verify
    runtime.calls.clear()
    state, _ = run(campaign, tmp_path, runtime, resume=True)
    assert any(isinstance(call, tuple) and call[0] == campaign["entries"][0]["id"] for call in runtime.calls)


def test_unrelated_run_evidence_cannot_complete_or_skip_a_cell(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    original = runtime.run
    def execute(manifest, scenario_id, **kwargs):
        result = original(manifest, scenario_id, **kwargs)
        if kwargs.get("entry"):
            result["security"]["run_id"] = "unrelated-run"
        return result
    runtime.run = execute
    state, _ = run(campaign, tmp_path, runtime)
    assert state["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "failed"
    assert state["evaluation"]["security_validity"]["security_valid"] is False


def test_preflight_reuses_readiness_with_explicit_producer_and_frozen_artifact(campaign, tmp_path, monkeypatch):
    from profile_manager import version_catalog
    from scripts import qualify_node_versions
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / "catalog", producer_version="99.1.2", signing_key=tmp_path / "key")
    runtime.catalog_path.write_text(json.dumps({"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []}))
    ev = evidence(campaign)
    release = {"implementation": "amaru", "version": "proposal", "source_revision": "a" * 40,
               "artifacts": [{"kind": "oci", "availability": "available", "digest": ev["builds"][0]["artifacts"]["image_digest"]}]}
    monkeypatch.setattr(version_catalog, "load_version_catalog", lambda path: {})
    monkeypatch.setattr(version_catalog, "resolve_release", lambda *args: release)
    calls = []
    monkeypatch.setenv("DWARF_CONTROLLER_HOST_STATE_DIR", "/host/runtime-state")
    monkeypatch.setattr(qualify_node_versions, "run_qualification", lambda **kwargs: calls.append(kwargs) or {"passed": True})
    result = runtime.preflight(campaign, ev["builds"])
    assert result["passed"] is True
    assert calls[0]["scope"] == "amaru-only"
    assert calls[0]["base_package"] == qualify_node_versions.BASE_PACKAGE
    assert calls[0]["docker_host_state_dir"] == Path("/host/runtime-state")
    assert calls[0]["cardano_version"] == "99.1.2"
    assert calls[0]["amaru_version"] == "proposal"
    release["source_revision"] = "b" * 40
    with pytest.raises(ValueError, match="identity"):
        runtime.preflight(campaign, ev["builds"])
    assert len(calls) == 1


def test_runtime_plan_rejects_unbound_artifacts_before_scenario_execution(campaign, tmp_path, monkeypatch):
    from profile_manager import run_plan
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / "catalog", producer_version="99.1.2", signing_key=tmp_path / "key")
    runtime.catalog_path.write_text(json.dumps({"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []}))
    runtime.builds = evidence(campaign)["builds"]
    requests = []
    monkeypatch.setattr(run_plan, "resolve_run_plan", lambda request, **kwargs: requests.append(request) or {"versions": None})
    with pytest.raises(ValueError, match="exact frozen"):
        runtime._plan(campaign, campaign["entries"][0]["contract"]["scenario_id"], campaign["topologies"][0], "stock", campaign["entries"][0], None)
    assert requests[0].amaru_version == "proposal"
    assert requests[0].version_policy == "exact"


def test_campaign_patched_target_is_derived_from_verified_build_and_catalog(campaign, tmp_path):
    runtime = api.LocalRuntime(
        tmp_path,
        catalog_path=tmp_path / "catalog",
        producer_version="99.1.2",
        signing_key=tmp_path / "key",
    )
    topology = campaign["topologies"][0]
    candidate = next(
        node for node in topology["nodes"] if node["identity"]["kind"] == "candidate"
    )
    candidate["identity"]["mode"] = "patched"
    image_digest = "sha256:" + "7" * 64
    executable_digest = "sha256:" + "6" * 64
    patch_set = "sha256:" + "5" * 64
    runtime.builds = [{
        "mode": "patched",
        "source": campaign["candidate"],
        "artifacts": {
            "image_digest": image_digest,
            "executable_digest": executable_digest,
        },
        "patch_set_digest": patch_set,
        "build_result_sha256": "sha256:" + "4" * 64,
        "runtime_probe_log_sha256": "sha256:" + "3" * 64,
    }]
    catalog = {"releases": [{
        "implementation": "amaru",
        "version": candidate["identity"]["version"],
        "source_revision": candidate["identity"]["source_revision"],
        "artifacts": [{
            "kind": "oci",
            "mode": "patched",
            "availability": "available",
            "reference": "localhost:5000/dwarf/amaru-candidate",
            "digest": image_digest,
            "executable_digest": executable_digest,
            "patch_set_digest": patch_set,
        }],
    }]}

    target = runtime._campaign_measurement_target(
        campaign, topology, "patched", catalog
    )

    assert target == {
        "implementation": "amaru",
        "version": candidate["identity"]["version"],
        "source_revision": candidate["identity"]["source_revision"],
        "target_mode": "patched",
        "image": "localhost:5000/dwarf/amaru-candidate@" + image_digest,
        "image_digest": image_digest,
        "executable_digest": executable_digest,
        "patch_set_sha256": patch_set.removeprefix("sha256:"),
        "build_result_sha256": "sha256:" + "4" * 64,
        "runtime_probe_log_sha256": "sha256:" + "3" * 64,
    }


def test_successful_runtime_qualification_is_bound_to_the_patched_build(tmp_path):
    runtime = api.LocalRuntime(
        tmp_path,
        catalog_path=tmp_path / "catalog",
        producer_version="99.1.2",
        signing_key=tmp_path / "key",
    )
    build = {"mode": "patched"}
    observation = {"passed": True, "image_id": "sha256:" + "2" * 64}

    runtime._retain_runtime_qualification(build, observation)

    assert build["runtime_probe_log_sha256"] == api._digest(observation)


def test_runtime_plan_passes_campaign_owned_patched_target_override(
    campaign, tmp_path, monkeypatch
):
    from profile_manager import deployment_versions, run_plan

    runtime = api.LocalRuntime(
        tmp_path,
        catalog_path=tmp_path / "catalog",
        producer_version="99.1.2",
        signing_key=tmp_path / "key",
    )
    topology = campaign["topologies"][0]
    candidate = next(
        node for node in topology["nodes"] if node["identity"]["kind"] == "candidate"
    )
    candidate["identity"]["mode"] = "patched"
    image_digest = "sha256:" + "7" * 64
    executable_digest = "sha256:" + "6" * 64
    patch_set = "sha256:" + "5" * 64
    runtime.builds = [{
        "mode": "patched",
        "source": campaign["candidate"],
        "artifacts": {"image_digest": image_digest, "executable_digest": executable_digest},
        "patch_set_digest": patch_set,
        "build_result_sha256": "sha256:" + "4" * 64,
        "runtime_probe_log_sha256": "sha256:" + "3" * 64,
    }]
    catalog = {"releases": [{
        "implementation": "amaru",
        "version": candidate["identity"]["version"],
        "source_revision": candidate["identity"]["source_revision"],
        "artifacts": [{
            "kind": "oci", "mode": "patched", "availability": "available",
            "reference": "localhost:5000/dwarf/amaru-candidate", "digest": image_digest,
            "executable_digest": executable_digest, "patch_set_digest": patch_set,
        }],
    }]}
    monkeypatch.setattr(runtime, "_catalog_binding", lambda: {
        "path": str(tmp_path / "catalog"), "sha256": "0" * 64, "catalog": catalog,
    })
    monkeypatch.setattr(deployment_versions, "version_catalog_revision", lambda **kwargs: "sha256:" + "0" * 64)
    captured = {}

    def resolve(request, **kwargs):
        captured.update(kwargs)
        resolved = {}
        for node in topology["nodes"]:
            identity = node["identity"]
            artifacts = (
                runtime.builds[0]["artifacts"]
                if identity["kind"] == "candidate"
                else identity["artifacts"]
            )
            resolved[node["implementation"]] = {
                "version": identity["version"],
                "source_revision": identity["source_revision"],
                "image_digest": artifacts["image_digest"],
            }
        return {"versions": {"resolved": resolved}, "measurements": {
            "target_identity": {"mode": "patched"}
        }}

    monkeypatch.setattr(run_plan, "resolve_run_plan", resolve)
    scenario_id = campaign["execution_policy"]["calibration"]["patched_scenario_id"]
    runtime._plan(campaign, scenario_id, topology, "patched", None, None)

    target = captured["capability_context"]["measurement_target_override"]
    assert target["source_revision"] == candidate["identity"]["source_revision"]
    assert target["patch_set_sha256"] == patch_set.removeprefix("sha256:")


def test_per_node_calibration_does_not_require_unselected_reference_artifact(
    campaign, tmp_path, monkeypatch
):
    from profile_manager import deployment_versions, run_plan

    runtime = api.LocalRuntime(
        tmp_path,
        catalog_path=tmp_path / "catalog",
        producer_version="99.1.2",
        signing_key=tmp_path / "key",
    )
    topology = campaign["topologies"][0]
    candidate = next(
        node for node in topology["nodes"] if node["identity"]["kind"] == "candidate"
    )
    topology["nodes"].append({
        "node_id": "node2",
        "implementation": "cardano-node",
        "role": "reference",
        "identity": {
            "kind": "release",
            "implementation": "cardano-node",
            "version": "11.1.2",
            "source_revision": "f" * 40,
            "artifacts": {"image_digest": "sha256:" + "8" * 64},
        },
    })
    image_digest = "sha256:" + "7" * 64
    runtime.builds = [{
        "mode": "stock",
        "source": campaign["candidate"],
        "artifacts": {
            "image_digest": image_digest,
            "executable_digest": "sha256:" + "6" * 64,
        },
    }]
    monkeypatch.setattr(runtime, "_catalog_binding", lambda: {
        "path": str(tmp_path / "catalog"), "sha256": "0" * 64, "catalog": {"releases": []},
    })
    monkeypatch.setattr(
        deployment_versions,
        "version_catalog_revision",
        lambda **kwargs: "sha256:" + "0" * 64,
    )
    monkeypatch.setattr(
        run_plan,
        "resolve_run_plan",
        lambda *_args, **_kwargs: {
            "versions": {"resolved": {"amaru": {
                "version": candidate["identity"]["version"],
                "source_revision": candidate["identity"]["source_revision"],
                "image_digest": image_digest,
            }}},
            "measurements": {"target_identity": {"mode": "stock"}},
        },
    )

    scenario_id = campaign["execution_policy"]["calibration"]["stock_scenario_id"]
    plan = runtime._plan(campaign, scenario_id, topology, "stock", None, None)

    assert set(plan["versions"]["resolved"]) == {"amaru"}


def test_native_scenario_without_boundary_index_remains_inconclusive(campaign, tmp_path, monkeypatch):
    from profile_manager import launch_store
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / "catalog", producer_version="99.1.2", signing_key=tmp_path / "key")
    runtime.catalog_path.write_text(json.dumps({"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []}))
    scenario_id = campaign["entries"][0]["contract"]["scenario_id"]
    monkeypatch.setattr(runtime, "_plan", lambda *args: {})
    scenario_path = tmp_path / "launch"
    scenario_path.mkdir()
    scenario = campaign["definitions"]["scenarios"][scenario_id]["document"]
    (scenario_path / "scenario.yaml").write_text(json.dumps(scenario))
    monkeypatch.setattr(launch_store, "create_launch", lambda *args, **kwargs: {"path": scenario_path, "launch_id": "launch-" + "a" * 24})
    calls = []
    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        run_dir = runtime.runs / "run-123"
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
        (run_dir / "assertions.json").write_text(json.dumps([{ "primitive": "check", "result": "pass"}]))
        return "run_id: run-123\n"
    monkeypatch.setattr(runtime, "_command", command)
    result = runtime.run(campaign, scenario_id, entry=campaign["entries"][0], topology_id=campaign["topologies"][0]["id"])
    assert result["security"]["reachability"] == []
    assert result["native_assertions"]
    assert result["control"]["status"] == "unknown"
    assert runtime.owned_projects == {"dwarf-substrate-run-123"}
    assert json.loads((scenario_path / "scenario.yaml").read_text()) == scenario
    assert "--backend" in calls[0][0]
    assert "ADA2_DWARF_BUNDLE_SIGNING_KEY" not in calls[0][1]["env"]
    environment = calls[0][1]["env"]
    snapshot = Path(environment["ADA2_DWARF_CAMPAIGN_CATALOG"])
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == environment["ADA2_DWARF_CAMPAIGN_CATALOG_SHA256"]


def test_local_runtime_preserves_pre_run_failure_diagnostics(campaign, tmp_path, monkeypatch):
    from profile_manager import launch_store

    runtime = api.LocalRuntime(
        tmp_path,
        catalog_path=tmp_path / "catalog",
        producer_version="99.1.2",
        signing_key=tmp_path / "key",
    )
    scenario_id = campaign["entries"][0]["contract"]["scenario_id"]
    monkeypatch.setattr(runtime, "_plan", lambda *args: {})
    monkeypatch.setattr(
        runtime,
        "_catalog_binding",
        lambda: {"path": str(tmp_path / "catalog"), "sha256": "0" * 64},
    )
    launch = tmp_path / "launch"
    launch.mkdir()
    (launch / "scenario.yaml").write_text("{}")
    monkeypatch.setattr(
        launch_store,
        "create_launch",
        lambda *args, **kwargs: {
            "path": launch,
            "launch_id": "launch-" + "a" * 24,
        },
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=(
                "MeasurementExecutionError: fresh deployed runtime is unavailable: "
                "/var/dwarf/state/profiles/runtime.json\n"
            ),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="return code 1.*fresh deployed runtime is unavailable",
    ):
        runtime.run(
            campaign,
            scenario_id,
            entry=campaign["entries"][0],
            topology_id=campaign["topologies"][0]["id"],
        )


def test_complete_calibration_wins_over_incomplete_security_observation(campaign):
    campaign, cell = single_cell(campaign)
    ev = evidence(campaign)
    good = measurement(campaign, cell, ev)
    bad = deepcopy(good)
    bad["result"]["run_id"] = "security-1"
    bad["result"]["sample_count"] = 0
    bad["samples"] = []
    state = {"builds": ev["builds"], "cells": {
        "measurement:calibration": {"result": {"measurements": [good]}},
        "security:z-later": {"result": {"measurements": [bad]}}}}
    merged = api._evidence(campaign, state)
    assert merged["measurements"] == [good]
    state["cells"] = dict(reversed(list(state["cells"].items())))
    assert api._evidence(campaign, state) == merged


def test_same_run_same_cell_conflicting_evidence_is_rejected(campaign):
    campaign, cell = single_cell(campaign)
    ev = evidence(campaign)
    first = measurement(campaign, cell, ev)
    second = deepcopy(first)
    second["samples"][0]["value"] = 100
    state = {"builds": ev["builds"], "cells": {
        "one": {"result": {"measurements": [first]}}, "two": {"result": {"measurements": [second]}}}}
    with pytest.raises(ValueError, match="conflict"):
        api._evidence(campaign, state)


def test_measurement_control_must_be_the_exact_verified_campaign_control(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    original = runtime.run
    def execute(manifest, scenario_id, **kwargs):
        result = original(manifest, scenario_id, **kwargs)
        if kwargs.get("cell"):
            result["measurements"][0]["normal_control"]["run_id"] = "not-the-control-executed"
        return result
    runtime.run = execute
    state, _ = run(campaign, tmp_path, runtime)
    assert state["evaluation"]["measurement_completeness"]["measurement_complete"] is False
    assert any("control" in record.get("error", "") for record in state["cells"].values())


def library_fixture(campaign, tmp_path):
    scenario_id = campaign["entries"][0]["contract"]["scenario_id"]
    binary = tmp_path / "decoder"
    binary.write_bytes(b"frozen trusted decoder fixture")
    binary.chmod(0o755)
    manifests = tmp_path / "trusted-manifests"
    manifests.mkdir()
    target_id = "amaru-cbor-decode-tx-body"
    target = {"id": target_id, "implementation": "amaru", "upstream_commit": "a" * 40,
              "binary": str(binary), "input_format": "stdin_bytes"}
    source = json.dumps(target).encode()
    (manifests / f"{target_id}.yaml").write_bytes(source)
    campaign["definitions"]["scenarios"][scenario_id]["document"]["load"][0]["manifests_dir"] = str(manifests)
    builds = evidence(campaign)["builds"]
    builds[0]["artifacts"]["executable_digest"] = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    builds[0]["artifacts"]["binary_digest"] = builds[0]["artifacts"]["executable_digest"]
    return scenario_id, binary, manifests, builds


def test_library_resolution_freezes_exact_manifest_and_harness_without_deployment_versions(campaign, tmp_path, monkeypatch):
    from profile_manager import run_plan
    scenario_id, binary, manifests, builds = library_fixture(campaign, tmp_path)
    runtime = api.LocalRuntime(tmp_path / "runtime", catalog_path=tmp_path / "catalog", producer_version="99", signing_key=tmp_path / "key")
    runtime.catalog_path.write_text(json.dumps({"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []}))
    runtime.builds = builds
    plan = {"scenario": {"runtime": "library", "target": {"implementation": "amaru", "version": "any"}}, "versions": None}
    monkeypatch.setattr(run_plan, "resolve_run_plan", lambda request, **kwargs: deepcopy(plan))
    resolved = runtime._plan(campaign, scenario_id, campaign["topologies"][0], "stock", None, None)
    assert resolved["scenario"]["target"]["version"] == "proposal"
    bindings = resolved["scenario"]["library_target_bindings"]
    assert len(bindings) == 1
    assert bindings[0]["identity"]["source_revision"] == "a" * 40
    frozen_manifest = json.loads(Path(bindings[0]["manifests_dir"]).joinpath("amaru-cbor-decode-tx-body.yaml").read_text())
    frozen_binary = Path(frozen_manifest["binary"])
    assert frozen_binary != binary and frozen_binary.read_bytes() == binary.read_bytes()
    binary.write_bytes(b"changed")
    assert frozen_binary.read_bytes() != binary.read_bytes()
    with pytest.raises(ValueError, match="identity|digest|artifact"):
        runtime._plan(campaign, scenario_id, campaign["topologies"][0], "stock", campaign["entries"][0], None)


def test_native_decoder_outputs_supply_boundary_control_and_oracle_facts(campaign, tmp_path):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    scenario_id = campaign["entries"][0]["contract"]["scenario_id"]
    ev = evidence(campaign)
    target = deepcopy(ev["security"][0]["targets"]["node1"])
    binding = {"target_id": "amaru-cbor-decode-tx-body", "node_id": "node1", "identity": target}
    plan = {"scenario": {"library_target_bindings": [binding]},
            "campaign_execution_digest": _digest(campaign["entries"][0]["execution"])}
    run_dir = tmp_path / "native-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text(json.dumps(ev["security"][0]["assertions"]))
    event = {"phase": "load", "primitive": "cbor_fuzz_structured", "event": "iteration", "payload": {
        "i": 0, "outcome": "ok", "mutated": False, "input_sha256": "f" * 64, "returncode": 0,
        "timed_out": False, "stdout_head": "OK 00", "target_id": binding["target_id"]}}
    (run_dir / "log.ndjson").write_text(json.dumps(event) + "\n")
    control = read_native_evidence(campaign, run_dir, scenario_id=scenario_id, topology=campaign["topologies"][0], mode="stock", plan=plan)
    result = read_native_evidence(campaign, run_dir, scenario_id=scenario_id, topology=campaign["topologies"][0], mode="stock", plan=plan, entry=campaign["entries"][0], control=control)
    assert result["control"]["status"] == "pass"
    assert result["security"]["boundary"] == "decoder"
    assert result["security"]["reachability"][0]["input_digest"] == "sha256:" + "f" * 64
    ev["security"] = [result["security"]]
    assert evaluate_campaign_evidence(campaign, ev)["security_validity"]["classification"] == "pass"
    event["payload"].pop("input_sha256")
    (run_dir / "log.ndjson").write_text(json.dumps(event) + "\n")
    missing = read_native_evidence(campaign, run_dir, scenario_id=scenario_id, topology=campaign["topologies"][0], mode="stock", plan=plan, entry=campaign["entries"][0], control=control)
    assert not missing["security"]["reachability"]


def test_native_resource_artifacts_generate_correlated_measurement_rows(campaign, tmp_path):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    campaign, cell = single_cell(campaign)
    ev = evidence(campaign)
    result = measurement(campaign, cell, ev)["result"]
    run_dir = tmp_path / result["run_id"]
    tap_dir = run_dir / "measurements" / cell["measurement_id"]
    collectors = run_dir / "measurements/collectors" / cell["measurement_id"]
    collectors.mkdir(parents=True)
    tap_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text("[]")
    result["artifacts"] = [f"measurements/collectors/{cell['measurement_id']}/samples.json"]
    (tap_dir / "result.json").write_text(json.dumps(result))
    (collectors / "samples.json").write_text(json.dumps([{"target_node": "node1", "rss_bytes": 4096, "sample_index": 0}]))
    control = {"run_id": "executed-control", "control": deepcopy(measurement(campaign, cell, ev)["normal_control"])}
    control["control"]["run_id"] = control["run_id"]
    native = read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode="stock", plan={}, cell=cell, control=control)
    assert len(native["measurements"]) == 1
    row = native["measurements"][0]
    assert row["normal_control"]["run_id"] == "executed-control"
    assert row["samples"] == [{"target_node": "node1", "rss_bytes": 4096, "sample_index": 0, "run_id": run_dir.name}]
    ev["measurements"] = [row]
    assert evaluate_campaign_evidence(campaign, ev)["measurement_completeness"]["measurement_complete"] is True
    result["artifacts"] = ["../../outside.json"]
    (tap_dir / "result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="artifact"):
        read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode="stock", plan={}, cell=cell, control=control)


def test_structured_decoder_events_retain_input_identity_and_process_outcome(tmp_path, monkeypatch):
    import random
    import subprocess
    from profile_manager import primitives
    binary = tmp_path / "decoder"
    binary.write_bytes(b"fixture")
    monkeypatch.setattr(primitives, "load_target_manifest", lambda *args, **kwargs: type("Manifest", (), {"binary": str(binary), "upstream_commit": "a" * 40})())
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=b"OK 00", stderr=b""))
    events = []
    handle = type("Handle", (), {"log": lambda self, **kwargs: events.append(kwargs)})()
    primitive = primitives.CborFuzzStructured(params={"target_id": "amaru-cbor-decode-tx-body", "manifests_dir": str(tmp_path), "iterations": 1, "mutation_rate": 0, "shape": {"type": "uint", "max": 1}})
    primitive.run(handle, random.Random(1))
    payload = next(row["payload"] for row in events if row["event"] == "iteration")
    assert len(payload["input_sha256"]) == 64
    assert payload["returncode"] == 0 and payload["timed_out"] is False
    assert payload["mutated"] is False


def test_native_calibration_positive_transcript_produces_a_usable_control(campaign, tmp_path):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    campaign, cell = single_cell(campaign)
    ev = evidence(campaign)
    run_dir = tmp_path / "control-native"
    calibration = run_dir / "outputs/amaru-measurement-calibration"
    calibration.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text("[]")
    target = ev["security"][0]["targets"]["node1"]
    report = {"schema_version": 1, "status": "available", "target": target,
              "target_health": {"checks": {"target_running_before": True, "target_running_after": True,
                  "target_not_oom_killed": True, "target_restart_count_unchanged": True,
                  "honest_chain_progressed": True, "no_fatal_signals": True}}}
    (calibration / "result.json").write_text(json.dumps(report))
    request = b"positive-wire-control"
    attempt = {"attempt_id": "handshake-0000", "outcome": "accepted", "detail": "accept-response",
               "listener_reached": True, "expected_external_outcome": "accepted", "request_hex": request.hex(),
               "request_sha256": "sha256:" + hashlib.sha256(request).hexdigest(), "response_hex": "00"}
    (calibration / "attempts.ndjson").write_text(json.dumps(attempt) + "\n")
    result = read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode="stock", plan={})
    assert result["control"]["status"] == "pass"
    assert result["control"]["targets"]["node1"] == target
    assert result["control"]["evidence"][0]["input_digest"] == attempt["request_sha256"]
    attempt["request_sha256"] = "sha256:" + "0" * 64
    (calibration / "attempts.ndjson").write_text(json.dumps(attempt) + "\n")
    assert read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode="stock", plan={})["control"]["status"] == "unknown"


def test_per_node_measurement_control_requires_only_its_selected_node(campaign, tmp_path):
    from profile_manager.campaign_runtime_evidence import read_native_evidence

    campaign, cell = single_cell(campaign)
    topology = campaign["topologies"][0]
    topology["nodes"].append({
        "node_id": "node2",
        "implementation": "cardano-node",
        "role": "reference",
        "identity": {
            "kind": "release",
            "implementation": "cardano-node",
            "version": "11.1.2",
            "source_revision": "f" * 40,
            "artifacts": {"image_digest": "sha256:" + "8" * 64},
        },
    })
    ev = evidence(campaign)
    run_dir = tmp_path / "control-native"
    calibration = run_dir / "outputs/amaru-measurement-calibration"
    calibration.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text("[]")
    target = ev["security"][0]["targets"]["node1"]
    report = {
        "schema_version": 1,
        "status": "available",
        "target": target,
        "target_health": {"checks": {
            "target_running_before": True,
            "target_running_after": True,
            "target_not_oom_killed": True,
            "target_restart_count_unchanged": True,
            "honest_chain_progressed": True,
            "no_fatal_signals": True,
        }},
    }
    (calibration / "result.json").write_text(json.dumps(report))
    request = b"positive-wire-control"
    attempt = {
        "attempt_id": "handshake-0000",
        "outcome": "accepted",
        "detail": "accept-response",
        "listener_reached": True,
        "expected_external_outcome": "accepted",
        "request_hex": request.hex(),
        "request_sha256": "sha256:" + hashlib.sha256(request).hexdigest(),
        "response_hex": "00",
    }
    (calibration / "attempts.ndjson").write_text(json.dumps(attempt) + "\n")

    result = read_native_evidence(
        campaign,
        run_dir,
        scenario_id=cell["scenario_id"],
        topology=topology,
        mode="stock",
        plan={},
        cell=cell,
    )

    assert result["control"]["status"] == "pass"
    assert set(result["control"]["targets"]) == {cell["node_id"]}


def test_library_launch_materializes_frozen_catalog_directory(campaign, tmp_path, monkeypatch):
    from profile_manager import launch_store
    scenario_id = campaign["entries"][0]["contract"]["scenario_id"]
    original = deepcopy(campaign["definitions"]["scenarios"][scenario_id]["document"])
    original["target"]["version"] = "proposal"
    catalog = tmp_path / "scenarios"
    catalog.mkdir()
    raw = json.dumps(original).encode()
    (catalog / f"{scenario_id}.yaml").write_bytes(raw)
    monkeypatch.setattr(launch_store, "_scenarios_dir", lambda: catalog)
    plan = {"scenario": {"id": scenario_id, "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
             "target": original["target"], "seed": original["seed"],
             "library_target_bindings": [{"location": ["load", 0], "target_id": "amaru-cbor-decode-tx-body", "manifests_dir": str(tmp_path / "frozen")}]}}
    materialized = json.loads(launch_store.materialized_scenario(plan))
    assert materialized["load"][0]["manifests_dir"] == str(tmp_path / "frozen")
    assert materialized["load"][0]["shape"] == original["load"][0]["shape"]


def test_archive_failed_finding_survives_an_empty_failed_resume(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    runtime.ev["security"][0]["assertions"][0]["result"] = "fail"
    original = runtime.archive
    def archive(run_id):
        if any(isinstance(call, tuple) and call[0] == campaign["entries"][0]["id"] for call in runtime.calls):
            raise RuntimeError("archive storage failed")
        return original(run_id)
    runtime.archive = archive
    first, _ = run(campaign, tmp_path, runtime)
    assert first["evaluation"]["security_validity"]["classification"] == "finding"
    finding_run = first["evidence"]["security"][0]["run_id"]
    runtime.archive = original
    runtime.fail = {campaign["entries"][0]["id"]}
    runtime.calls.clear()
    resumed, _ = run(campaign, tmp_path, runtime, resume=True)
    assert resumed["evaluation"]["security_validity"]["classification"] == "finding"
    assert resumed["evidence"]["security"][0]["run_id"] == finding_run
    assert resumed["cells"]["security:" + campaign["entries"][0]["id"]]["status"] == "failed"


def test_new_valid_finding_supersedes_retained_pass(campaign, tmp_path):
    campaign, cell = single_cell(campaign)
    runtime = Runtime(campaign, tmp_path)
    first, _ = run(campaign, tmp_path, runtime)
    receipt = first["cells"]["security:" + campaign["entries"][0]["id"]]["archives"][0]
    Path(receipt["archive"]).write_bytes(b"corrupt")
    runtime.ev["security"][0]["assertions"][0]["result"] = "fail"
    resumed, _ = run(campaign, tmp_path, runtime, resume=True)
    assert resumed["evaluation"]["security_validity"]["classification"] == "finding"
    assert resumed["evidence"]["security"][0]["run_id"] != first["evidence"]["security"][0]["run_id"]


@pytest.mark.parametrize("tap_id,mode,source", [
    ("amaru-stock-fork-switch", "stock", "amaru-b159-stock-traces.ndjson"),
    ("amaru-patched-protocol-decode", "patched", "amaru-b159-patched-traces.ndjson"),
])
def test_native_normalized_metric_rows_are_samples_without_duplicate_inflation(campaign, tmp_path, tap_id, mode, source):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    from profile_manager.measurement_collectors import amaru_stock, amaru_patched
    raw = (Path(__file__).parent / "fixtures" / source).read_bytes()
    if mode == "stock":
        normalized = amaru_stock._load_amaru_telemetry_bytes(json_sources=[("fixture", raw, False)], otlp_sources=[])["events"]
        metrics = amaru_stock._build_measurements(tap_id, normalized)[0]
    else:
        normalized = amaru_patched._load_sources([("fixture", raw, False)])["events"]
        metrics = amaru_patched._build_measurements(tap_id, normalized)
    cell = deepcopy(next(row for row in campaign["required_tap_cells"] if row["measurement_id"] == tap_id))
    cell["target_modes"] = [mode]
    cell["rule"] = {"min_samples": 1, "correlation": ["run_id"]}
    campaign["required_tap_cells"] = [cell]
    freeze(campaign)
    ev = evidence(campaign)
    row = measurement(campaign, cell, ev, mode=mode)
    result = row["result"]
    result["records"] = [{"name": name, **metric} for name, metric in metrics.items()]
    result["sample_count"] = max(metric.get("sample_count", 0) for metric in metrics.values())
    run_dir = tmp_path / result["run_id"]
    collector = run_dir / "measurements/collectors" / tap_id
    collector.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text("[]")
    tap_dir = run_dir / "measurements" / tap_id
    tap_dir.mkdir()
    result["artifacts"] = [f"measurements/collectors/{tap_id}/normalized.ndjson"]
    (tap_dir / "result.json").write_text(json.dumps(result))
    (collector / "normalized.ndjson").write_text("".join(json.dumps(event) + "\n" for event in normalized))
    control = {"run_id": row["normal_control"]["run_id"], "control": row["normal_control"]}
    translated = read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode=mode, plan={}, cell=cell, control=control)
    measured = translated["measurements"][0]
    assert len(measured["samples"]) == result["sample_count"] > 0
    assert measured["events"]
    ev["measurements"] = [measured]
    assert evaluate_campaign_evidence(campaign, ev)["measurement_completeness"]["measurement_complete"] is True
    original_count = len(measured["samples"])
    polluted = [*normalized, normalized[0], {"kind": "startup", "fields": {"message": "configured"}}, {"kind": "protocol-decode", "fields": {"decode_micros": "not-a-number"}}]
    (collector / "normalized.ndjson").write_text("".join(json.dumps(event) + "\n" for event in polluted))
    translated = read_native_evidence(campaign, run_dir, scenario_id=cell["scenario_id"], topology=campaign["topologies"][0], mode=mode, plan={}, cell=cell, control=control)
    assert len(translated["measurements"][0]["samples"]) == original_count


def test_custom_catalog_candidate_binds_readiness_plan_and_execution(campaign, tmp_path, monkeypatch):
    from profile_manager import run_plan, version_catalog, version_discovery
    from profile_manager.security_campaign import resolve_security_campaign
    from profile_manager.security_campaign_suite import load_security_campaign_suite, load_measurement_suite
    from profile_manager.testplease_candidates import CandidateSnapshot
    from scripts import qualify_node_versions
    root = Path(__file__).resolve().parents[1]
    digest = "sha256:" + "d" * 64
    catalog = {"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "compatibility_pairs": [], "releases": [{
        "implementation": "cardano-node", "version": "99.88.77-candidate", "channel": "stable", "source_revision": "a" * 40,
        "artifacts": [{"kind": "oci", "reference": "example.invalid/candidate:99", "availability": "available", "digest": digest}],
        "verification": {}}]}
    source = tmp_path / "selected-catalog.json"
    source.write_text(json.dumps(catalog))
    candidate = CandidateSnapshot("cardano-node", "proposal", "a" * 40, "b" * 64, tmp_path / "unused")
    manifest = resolve_security_campaign(candidate,
        load_security_campaign_suite(root / "dwarf/security-suites/github-security-regression-v1.yaml"),
        load_measurement_suite(root / "dwarf/qualification-suites/node-measurements-v1.yaml"),
        selection={"entry_ids": ["cardano-measurement-e2e-stock-portable"], "implementations": {
            "cardano-node": {"kind": "candidate", "version": "99.88.77-candidate",
                             "compatibility": {"era": "conway", "genesis_digest": digest, "protocol_version": "10"}}}}, catalog=catalog)
    builds = [{"mode": "stock", "source": manifest["candidate"], "artifacts": {"image_digest": digest, "executable_digest": digest, "binary_digest": digest}}]
    runtime = api.LocalRuntime(tmp_path / "campaign", catalog_path=source, producer_version=None, signing_key=tmp_path / "key")
    calls = []
    monkeypatch.setattr(qualify_node_versions, "run_qualification", lambda **kwargs: calls.append(kwargs) or {"passed": True})
    duplicate = deepcopy(manifest["topologies"][0])
    duplicate["id"] = "topology-equivalent-version-pair"
    manifest["topologies"].append(duplicate)
    readiness = runtime.preflight(manifest, builds)
    assert readiness["passed"] is True
    assert len(calls) == 1
    assert set(readiness["qualified_topology_ids"]) == {manifest["topologies"][0]["id"], duplicate["id"]}
    # Disable optional metric compatibility here; this regression exercises real
    # deployment/catalog resolution, with no artifact present in the default catalog.
    monkeypatch.setattr(run_plan, "_measurement_context", lambda *args, **kwargs: run_plan.explicit_no_measurements_context())
    entry = manifest["entries"][0]
    plan = runtime._plan(manifest, entry["contract"]["scenario_id"], manifest["topologies"][0], "stock", entry, None)
    assert plan["versions"]["resolved"]["cardano-node"]["version"] == "99.88.77-candidate"
    assert plan["versions"]["resolved"]["cardano-node"]["image_digest"] == digest
    selected_path = Path(plan["campaign_catalog"]["path"])
    assert selected_path == calls[0]["catalog_path"]
    assert selected_path.read_bytes() == source.read_bytes()
    source_bytes = source.read_bytes()
    source.write_bytes(source_bytes + b" ")
    with pytest.raises(ValueError, match="catalog changed"):
        runtime._plan(manifest, entry["contract"]["scenario_id"], manifest["topologies"][0], "stock", entry, None)
    source.write_bytes(source_bytes)
    monkeypatch.setenv("ADA2_DWARF_CAMPAIGN_CATALOG", str(selected_path))
    monkeypatch.setenv("ADA2_DWARF_CAMPAIGN_CATALOG_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(version_discovery, "merge_discovery_candidate", lambda *args: pytest.fail("discovery overlay changed frozen catalog"))
    assert version_catalog.resolve_release(version_discovery.load_effective_version_catalog(), "cardano-node", "99.88.77-candidate")["source_revision"] == "a" * 40
    selected_path.write_text("{}")
    with pytest.raises(ValueError, match="catalog"):
        version_catalog.load_version_catalog()
    source.unlink()
    with pytest.raises((ValueError, OSError), match="catalog"):
        runtime._plan(manifest, entry["contract"]["scenario_id"], manifest["topologies"][0], "stock", entry, None)


def _native_count_fixture(tap_id):
    from profile_manager.measurement_collectors.cardano_stock import _build_measurements
    events = [json.loads(line) for line in (Path(__file__).parent / "fixtures/cardano-stock-count-normalized.ndjson").read_text().splitlines()]
    metrics, _ = _build_measurements(tap_id, events)
    return events, {"sample_count": 0, "records": [{"name": name, **metric} for name, metric in metrics.items()]}


@pytest.mark.parametrize("tap_id", ["cardano-stock-chain-lifecycle", "cardano-stock-ledger-block-epoch", "cardano-stock-plutus-execution"])
def test_native_count_metrics_retain_one_positive_aggregate_sample(tap_id):
    from profile_manager.campaign_runtime_evidence import _normalized_signals
    events, result = _native_count_fixture(tap_id)
    samples, retained = _normalized_signals(tap_id, events, result)
    assert len(samples) == 1
    assert samples[0]["evidence_kind"] == "native-count-aggregate"
    assert samples[0]["value"] == 1
    assert samples[0]["source_event_digests"]
    assert retained == events
    assert result["sample_count"] == 0  # The retained native result is untouched.


@pytest.mark.parametrize("value", [0, -1, "1", None, float("nan"), float("inf"), True])
def test_nonpositive_or_malformed_count_values_cannot_supply_samples(value):
    from profile_manager.campaign_runtime_evidence import _normalized_signals
    events, result = _native_count_fixture("cardano-stock-chain-lifecycle")
    for record in result["records"]:
        record["value"] = value
    assert _normalized_signals("cardano-stock-chain-lifecycle", events, result)[0] == []


def test_count_metrics_reject_unrelated_malformed_and_duplicate_inflation():
    from profile_manager.campaign_runtime_evidence import _normalized_signals
    tap_id = "cardano-stock-chain-lifecycle"
    events, result = _native_count_fixture(tap_id)
    assert _normalized_signals(tap_id, events[1:], result)[0] == []
    malformed = deepcopy(events[0])
    malformed["fields"] = {"kind": "not-a-chain-event"}
    assert _normalized_signals(tap_id, [malformed], result)[0] == []
    for record in result["records"]:
        if record["value"]:
            record["value"] = 2
    assert _normalized_signals(tap_id, [events[0], events[0]], result)[0] == []
    for record in result["records"]:
        record["name"] = "undeclared_counter"
    assert _normalized_signals(tap_id, events, result)[0] == []


def test_positive_count_aggregate_is_one_sample_not_the_aggregate_value(tmp_path):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    from profile_manager.measurement_collectors.cardano_stock import _build_measurements
    tap_id = "cardano-stock-chain-lifecycle"
    events, _ = _native_count_fixture(tap_id)
    source = events[0]
    events = [dict(source, timestamp=f"2026-10-19T12:00:0{i}Z", block_hash=str(i) * 64,
                   fields={**source["fields"], "block": str(i) * 64}) for i in range(3)]
    metrics, _ = _build_measurements(tap_id, events)
    run_dir = tmp_path / "count-run"
    collector = run_dir / "measurements/collectors" / tap_id
    collector.mkdir(parents=True)
    tap = run_dir / "measurements" / tap_id
    tap.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"exit_status": "pass"}))
    (run_dir / "assertions.json").write_text("[]")
    (collector / "normalized.ndjson").write_text("".join(json.dumps(row) + "\n" for row in events))
    target = {"implementation": "cardano-node", "version": "99", "source_revision": "a" * 40, "mode": "stock"}
    result = {"measurement_id": tap_id, "run_id": run_dir.name, "target": target, "status": "collected", "sample_count": 0,
              "provenance": {"scenario_id": "scenario"}, "records": [{"name": name, **metric} for name, metric in metrics.items()],
              "artifacts": [f"measurements/collectors/{tap_id}/normalized.ndjson"]}
    (tap / "result.json").write_text(json.dumps(result))
    cell = {"topology_id": "topology", "node_id": "node1", "measurement_id": tap_id, "target_modes": ["stock"]}
    manifest = {"definitions": {"scenarios": {"scenario": {"digest": "sha256:" + "d" * 64}}},
                "required_tap_cells": [cell], "candidate": {"implementation": "cardano-node"}}
    output = read_native_evidence(manifest, run_dir, scenario_id="scenario", topology={"id": "topology", "nodes": [{"node_id": "node1", "implementation": "cardano-node"}]}, mode="stock", plan={})
    row = output["measurements"][0]
    assert len(row["samples"]) == row["result"]["sample_count"] == 1
    assert row["samples"][0]["value"] == 3
    assert len(row["samples"][0]["source_event_digests"]) == 3
    assert "block_hash" not in row["samples"][0]
    assert row["result"]["provenance"]["campaign_sample_count_basis"]["native_sample_count"] == 0
    assert json.loads((tap / "result.json").read_text())["sample_count"] == 0


@pytest.fixture(scope="module")
def mixed_runtime_inputs():
    from profile_manager import security_campaign as planner
    from profile_manager.security_campaign_suite import load_security_campaign_suite, load_measurement_suite
    from profile_manager.testplease_candidates import CandidateSnapshot
    root = Path(__file__).resolve().parents[1]
    compat = {'era': 'conway', 'genesis_digest': 'sha256:' + '1' * 64, 'protocol_version': '10'}
    catalog = {'schema_version': 1, 'updated_at': '2026-10-19T00:00:00Z', 'compatibility_pairs': [], 'releases': [
        {'implementation': impl, 'version': version, 'channel': 'stable', 'source_revision': source,
         'artifacts': [{'kind': 'oci', 'reference': 'example.invalid/' + impl + ':' + version,
                        'availability': 'available', 'digest': digest}], 'verification': {}}
        for impl, version, source, digest in [('amaru', 'proposal', 'a' * 40, 'sha256:' + 'd' * 64),
                                              ('cardano-node', '99.0.0', 'c' * 40, 'sha256:' + 'c' * 64)]]}
    scenario = 'ledger-submit-transaction-array-arity-differential-amaru-cardano-node'
    manifest = planner.resolve_security_campaign(CandidateSnapshot('amaru', 'proposal', 'a' * 40, 'b' * 64, Path('/unused')),
        load_security_campaign_suite(root / 'dwarf/security-suites/github-security-regression-v1.yaml'),
        load_measurement_suite(root / 'dwarf/qualification-suites/node-measurements-v1.yaml'),
        selection={'entry_ids': [scenario], 'implementations': {
            'amaru': {'kind': 'candidate', 'version': 'proposal', 'compatibility': compat},
            'cardano-node': {'kind': 'release', 'version': '99.0.0', 'compatibility': compat}}}, catalog=catalog)
    return manifest, catalog


@pytest.fixture
def mixed_runtime(mixed_runtime_inputs, tmp_path):
    manifest, catalog = deepcopy(mixed_runtime_inputs)
    source = tmp_path / 'catalog.json'
    source.write_text(json.dumps(catalog))
    runtime = api.LocalRuntime(tmp_path / 'runtime', catalog_path=source, producer_version=None, signing_key=tmp_path / 'key')
    runtime.builds = evidence(manifest)['builds']
    return manifest, runtime


@pytest.mark.parametrize("is_control", [False, True])
def test_mixed_runtime_consumes_materialized_frozen_recipe_in_one_subprocess(mixed_runtime, monkeypatch, is_control):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    calls = []
    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["env"]["ADA2_DWARF_REMOTE_BASE"] == str(
            runtime.profile_runtime_base
        )
        body = json.loads(Path(argv[4]).read_text())
        expected = entry['execution']['scenario']
        assert body['load'] == expected['load']
        assert body['assertions'] == expected['assertions']
        assert body['substrate']['topology'] == expected['substrate']['topology']
        assert body['measurement_profile'] == 'none'
        assert body['measurements'] == []
        for node in body['substrate']['nodes']:
            identity = next(n['identity'] for n in manifest['topologies'][0]['nodes'] if n['node_id'] == node['id'])
            assert node['version'] == identity['version']
            assert node['source_revision'] == identity['source_revision']
            assert node['image'].endswith('@' + node['image_digest'])
        run_dir = runtime.runs / 'mixed-123'
        run_dir.mkdir()
        (run_dir / 'manifest.json').write_text(json.dumps({'exit_status': 'pass'}))
        return 'run_id: mixed-123\n'
    monkeypatch.setattr(runtime, '_command', command)
    result = runtime.run(manifest, entry['contract']['scenario_id'], topology_id=entry['topology_id'],
                         **({'execution_entry': entry} if is_control else {'entry': entry}))
    assert len(calls) == 1
    if not is_control:
        assert result['security']['execution_digest'] == _digest(entry['execution'])
    else:
        assert 'security' not in result
    assert result['control']['execution_digest'] == _digest(entry['execution'])
    assert result['measurements'] == []
    plan = runtime._plan(manifest, entry['contract']['scenario_id'], manifest['topologies'][0], 'stock', entry, None)
    assert plan['campaign_execution_digest'] == _digest(entry['execution'])


@pytest.mark.parametrize('field', ['role', 'edge', 'load', 'faults', 'assertions', 'identity'])
def test_mixed_runtime_rejects_frozen_execution_drift_before_subprocess(mixed_runtime, monkeypatch, field):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    body = entry['execution']['scenario']
    if field == 'role': body['substrate']['nodes'][0]['role'] = 'other'
    elif field == 'edge': body['substrate']['topology']['edges'] = []
    elif field == 'identity': body['substrate']['nodes'][0]['source_revision'] = '0' * 40
    else: body[field] = [{'primitive': 'untrusted'}]
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='execution.*differs'):
        runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])


@pytest.mark.parametrize('modes', [('coverage', 'stock'), ('patched', 'patched')])
def test_mixed_runtime_rejects_unsupported_per_node_modes_before_resolution(mixed_runtime, monkeypatch, modes):
    from profile_manager import run_plan
    manifest, runtime = mixed_runtime
    for node, mode in zip(manifest['topologies'][0]['nodes'], modes): node['identity']['mode'] = mode
    monkeypatch.setattr(run_plan, 'resolve_run_plan', lambda *args, **kwargs: pytest.fail('unexpected resolution'))
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    entry = manifest['entries'][0]
    with pytest.raises(ValueError, match='unsupported-per-node-mode'):
        runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])


def test_mixed_runtime_selects_patched_candidate_beside_stock_release(mixed_runtime):
    manifest, _runtime = mixed_runtime
    topology = manifest['topologies'][0]
    candidate = next(node for node in topology['nodes'] if node['identity']['kind'] == 'candidate')
    release = next(node for node in topology['nodes'] if node['identity']['kind'] == 'release')
    candidate['identity']['mode'] = 'patched'
    release['identity']['mode'] = 'stock'
    assert api._node_modes(topology) == {
        candidate['node_id']: 'patched',
        release['node_id']: 'stock',
    }
    assert api._security_mode(topology) == 'patched'


@pytest.mark.parametrize("catalog_first_stock", [False, True])
def test_preflight_uses_frozen_candidate_mode_instead_of_stock(campaign, tmp_path, monkeypatch, catalog_first_stock):
    from profile_manager import version_catalog
    from scripts import qualify_node_versions
    runtime = api.LocalRuntime(tmp_path, catalog_path=tmp_path / 'catalog', producer_version='99.1.2', signing_key=tmp_path / 'key')
    runtime.catalog_path.write_text('{}')
    campaign['topologies'][0]['nodes'][0]['identity']['mode'] = 'patched'
    builds = evidence(campaign)['builds']
    builds[0]['artifacts']['image_digest'] = 'sha256:' + '0' * 64
    builds[0]['runtime_image_config_id'] = 'sha256:' + '2' * 64
    builds[1]['runtime_image_config_id'] = 'sha256:' + '3' * 64
    release = {'source_revision': 'a' * 40, 'artifacts': [
        {'kind': 'oci', 'mode': 'patched', 'availability': 'available', 'digest': builds[1]['artifacts']['image_digest'],
         'config_image_id': builds[1]['runtime_image_config_id']}]}
    if catalog_first_stock:
        release['artifacts'].insert(0, {'kind': 'oci', 'availability': 'available',
                                       'digest': builds[0]['artifacts']['image_digest']})
    monkeypatch.setattr(version_catalog, 'load_version_catalog', lambda path: {})
    monkeypatch.setattr(version_catalog, 'resolve_release', lambda *args: release)
    calls = []
    monkeypatch.setattr(qualify_node_versions, 'run_qualification', lambda **kwargs: calls.append(kwargs) or {'passed': True})
    assert runtime.preflight(campaign, builds)['passed'] is True
    assert len(calls) == 1
    assert calls[0]['operator_stock_content_id'] == builds[1]['runtime_image_config_id']


def test_mixed_security_plan_defers_per_node_measurements_without_changing_intent(mixed_runtime, monkeypatch):
    from profile_manager import run_plan
    manifest, runtime = mixed_runtime
    before = deepcopy(manifest)
    requests = []
    resolve = run_plan.resolve_run_plan
    def captured(request, **kwargs):
        requests.append(request)
        return resolve(request, **kwargs)
    monkeypatch.setattr(run_plan, 'resolve_run_plan', captured)
    entry = manifest['entries'][0]
    plan = runtime._plan(manifest, entry['contract']['scenario_id'], manifest['topologies'][0], 'stock', entry, None)
    assert requests[0].measurement_profile == 'none'
    assert not requests[0].measurement_overrides
    assert run_plan.is_explicit_no_measurements_context(plan['measurements'])
    assert manifest == before
    assert len(entry['measurement_attachments']) == 2
    assert manifest['required_tap_cells']


def test_mixed_control_consumes_execution_contract_before_subprocess(mixed_runtime, monkeypatch):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    entry['execution']['normal_control']['scenario']['load'] = []
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='execution.*differs'):
        runtime.run(manifest, entry['contract']['normal_control']['scenario_id'], execution_entry=entry,
                    topology_id=entry['topology_id'])


@pytest.mark.parametrize('field', ['role', 'edges'])
def test_mixed_execution_rejects_topology_contract_drift(mixed_runtime, monkeypatch, field):
    manifest, runtime = mixed_runtime
    topology = manifest['topologies'][0]
    if field == 'role': topology['nodes'][0]['role'] = 'other'
    else: topology['edges'] = []
    entry = manifest['entries'][0]
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='execution.*differs'):
        runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])


def test_mixed_nonstock_preflight_fails_before_qualification(mixed_runtime, monkeypatch):
    from scripts import qualify_node_versions
    manifest, runtime = mixed_runtime
    manifest['topologies'][0]['nodes'][0]['identity']['mode'] = 'coverage'
    monkeypatch.setattr(qualify_node_versions, 'run_qualification', lambda **kwargs: pytest.fail('unexpected qualification'))
    with pytest.raises(ValueError, match='unsupported-per-node-mode'):
        runtime.preflight(manifest, runtime.builds)


@pytest.mark.parametrize('field', ['version', 'source_revision', 'image_digest'])
def test_mixed_materialized_binding_drift_is_rejected_before_subprocess(mixed_runtime, monkeypatch, field):
    from profile_manager import run_plan
    manifest, runtime = mixed_runtime
    resolve = run_plan.resolve_run_plan
    def changed(request, **kwargs):
        plan = resolve(request, **kwargs)
        plan['scenario']['substrate_bindings'][0]['resolved'][field] = 'sha256:' + '0' * 64
        return plan
    monkeypatch.setattr(run_plan, 'resolve_run_plan', changed)
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    entry = manifest['entries'][0]
    with pytest.raises(ValueError, match='materialized.*execution.*differs'):
        runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])


def test_mixed_campaign_rejects_unsupported_modes_before_runtime_adapter(mixed_runtime, monkeypatch, tmp_path):
    manifest, runtime = mixed_runtime
    manifest['topologies'][0]['nodes'][0]['identity']['mode'] = 'coverage'
    freeze(manifest)
    monkeypatch.setattr(runtime, 'projects', lambda: pytest.fail('unexpected runtime inventory'))
    monkeypatch.setattr(runtime, 'preflight', lambda *args: pytest.fail('unexpected runtime preflight'))
    with pytest.raises(ValueError, match='unsupported-per-node-mode'):
        api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                         builds=runtime.builds, lock_path=tmp_path / 'host.lock')


@pytest.mark.parametrize('compatibility', ['pending', 'incomplete', 'conflicting'])
def test_campaign_blocks_unconfirmed_execution_compatibility_before_any_attempt(mixed_runtime, tmp_path, monkeypatch, compatibility):
    manifest, runtime = mixed_runtime
    if compatibility == 'pending':
        manifest['entries'][0]['execution']['compatibility']['status'] = 'pending-prerequisites'
    elif compatibility == 'incomplete':
        manifest['topologies'][0]['nodes'][0]['identity']['compatibility'].pop('era')
    else:
        manifest['topologies'][0]['nodes'][1]['identity']['compatibility']['protocol_version'] = '11'
    freeze(manifest)
    calls = []
    monkeypatch.setattr(runtime, 'projects', lambda: set())
    monkeypatch.setattr(runtime, 'preflight', lambda *args: {'passed': True, 'executed': True,
                                                            'qualified_topology_ids': []})
    monkeypatch.setattr(runtime, 'run', lambda *args, **kwargs: calls.append(args) or {'run_id': 'unexpected', 'execution_complete': False})
    state = api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                            builds=runtime.builds, lock_path=tmp_path / 'host.lock')
    assert calls == []
    assert state['readiness']['passed'] is False
    assert state['readiness']['status'] == 'blocked'
    assert any('compatibility' in reason for reason in state['readiness']['reasons'])
    assert state['cells'] == {}
    retained = json.loads((tmp_path / 'campaign/state.json').read_text())
    assert retained['readiness'] == state['readiness']


def test_runtime_qualification_can_authorize_pending_static_compatibility(mixed_runtime, tmp_path, monkeypatch):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    entry['execution']['compatibility']['status'] = 'pending-prerequisites'
    freeze(manifest)
    topology_id = manifest['topologies'][0]['id']
    runtime.qualified_topology_ids = {topology_id}
    monkeypatch.setattr(runtime, 'projects', lambda: set())
    monkeypatch.setattr(runtime, 'preflight', lambda *args: {
        'passed': True, 'executed': True, 'qualified_topology_ids': [topology_id]})
    monkeypatch.setattr(runtime, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('stop after authorization')))
    state = api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                             builds=runtime.builds, lock_path=tmp_path / 'host.lock')
    assert state['readiness']['passed'] is True
    assert state['cells']['security:' + entry['id']]['error'].startswith('RuntimeError: stop after authorization')


def test_direct_mixed_runtime_rejects_pending_compatibility_before_subprocess(mixed_runtime, monkeypatch):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    entry['execution']['compatibility']['status'] = 'pending-prerequisites'
    monkeypatch.setattr(runtime, '_command', lambda *args, **kwargs: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='compatibility.*not declared-compatible'):
        runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])


def test_measurement_plan_enables_all_stock_taps_sharing_one_exercise(mixed_runtime, monkeypatch):
    from profile_manager import run_plan
    manifest, runtime = mixed_runtime
    topology = manifest['topologies'][0]
    groups = {}
    for cell in manifest['required_tap_cells']:
        if cell['runtime']['status'] == 'implemented' and 'stock' in cell['target_modes']:
            groups.setdefault((cell['topology_id'], cell['node_id'], cell['scenario_id']), []).append(cell)
    cells = next(rows for rows in groups.values() if len(rows) > 1)
    captured = []

    def resolve(request, **kwargs):
        captured.append(request)
        resolved = {}
        for node in topology['nodes']:
            identity = node['identity']
            artifacts = identity['artifacts']
            if identity['kind'] == 'candidate':
                artifacts = runtime.builds[0]['artifacts']
            resolved[node['implementation']] = {
                'version': identity['version'], 'source_revision': identity['source_revision'],
                'image_digest': artifacts['image_digest']}
        return {'scenario': {'runtime': 'devnet', 'target': {'implementation': cells[0]['implementation']}},
                'versions': {'resolved': resolved}, 'measurements': {'target_identity': {'mode': 'stock'}}}

    monkeypatch.setattr(run_plan, 'resolve_run_plan', resolve)
    runtime._plan(manifest, cells[0]['scenario_id'], topology, 'stock', None, cells[0])
    assert {row['id'] for row in captured[0].measurement_overrides} == {
        cell['measurement_id'] for cell in cells}


def test_measurement_plan_may_omit_unrelated_mixed_topology_implementation(
    mixed_runtime, monkeypatch
):
    from profile_manager import run_plan

    manifest, runtime = mixed_runtime
    topology = manifest["topologies"][0]
    candidate_implementation = manifest["candidate"]["implementation"]
    cell = next(
        row for row in manifest["required_tap_cells"]
        if row["topology_id"] == topology["id"]
        and row["implementation"] != candidate_implementation
        and row["runtime"]["status"] == "implemented"
        and "stock" in row["target_modes"]
    )
    node = next(row for row in topology["nodes"] if row["node_id"] == cell["node_id"])
    identity = node["identity"]

    def resolve(request, **kwargs):
        return {
            "scenario": {
                "runtime": "devnet",
                "target": {"implementation": cell["implementation"]},
            },
            "versions": {"resolved": {cell["implementation"]: {
                "version": identity["version"],
                "source_revision": identity["source_revision"],
                "image_digest": identity["artifacts"]["image_digest"],
            }}},
            "measurements": {"target_identity": {"mode": "stock"}},
        }

    monkeypatch.setattr(run_plan, "resolve_run_plan", resolve)
    plan = runtime._plan(
        manifest, cell["scenario_id"], topology, "stock", None, cell
    )
    assert set(plan["versions"]["resolved"]) == {cell["implementation"]}


def test_legacy_local_runtime_launch_omits_execution_binding(mixed_runtime, monkeypatch):
    from profile_manager import run_plan
    manifest, runtime = mixed_runtime
    requests = []
    resolve = run_plan.resolve_run_plan
    def captured(request, **kwargs):
        requests.append(request)
        return resolve(request, **kwargs)
    monkeypatch.setattr(run_plan, 'resolve_run_plan', captured)
    entry = manifest['entries'][0]
    entry.pop('execution')
    freeze(manifest)
    calls = []
    monkeypatch.setattr(runtime, '_bind_execution', lambda *args: pytest.fail('legacy execution binding'))
    def command(argv, **kwargs):
        calls.append(argv)
        run_dir = runtime.runs / 'legacy-123'
        run_dir.mkdir()
        (run_dir / 'manifest.json').write_text(json.dumps({'exit_status': 'pass'}))
        return 'run_id: legacy-123\n'
    monkeypatch.setattr(runtime, '_command', command)
    result = runtime.run(manifest, entry['contract']['scenario_id'], entry=entry, topology_id=entry['topology_id'])
    assert len(calls) == 1
    assert 'execution_digest' not in result['security']
    assert 'execution_digest' not in result['control']
    assert requests[0].measurement_profile == entry['measurement_attachments'][0]['measurement_profile']


@pytest.mark.parametrize('shared', [True, False])
@pytest.mark.parametrize('execution_bound', [True, False])
@pytest.mark.parametrize('entry_context', [True, False])
def test_native_mixed_control_status_requires_common_input(mixed_runtime, tmp_path, shared, execution_bound, entry_context):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    topology = manifest['topologies'][0]
    bindings = [{'target_id': node['node_id'], 'node_id': node['node_id'], 'identity': node['identity']}
                for node in topology['nodes']]
    plan = {'scenario': {'library_target_bindings': bindings}}
    if execution_bound:
        plan['campaign_execution_digest'] = _digest(entry['execution'])
    run_dir = tmp_path / 'control-native'
    run_dir.mkdir()
    events = [{'primitive': 'cbor_fuzz_structured', 'event': 'iteration', 'payload': {
        'target_id': binding['target_id'], 'input_sha256': ('f' if shared or index == 0 else 'e') * 64,
        'returncode': 0, 'timed_out': False, 'outcome': 'ok', 'mutated': False, 'stdout_head': 'OK'}}
        for index, binding in enumerate(bindings)]
    (run_dir / 'log.ndjson').write_text(''.join(json.dumps(event) + '\n' for event in events))
    result = read_native_evidence(manifest, run_dir, scenario_id=entry['contract']['scenario_id'],
                                  topology=topology, mode='stock', plan=plan, entry=entry if entry_context else None)
    assert result['control']['status'] == ('pass' if shared or not (execution_bound or entry_context) else 'unknown')


@pytest.mark.parametrize('mutation', ['load', 'fault', 'assertions', 'role', 'edges', 'implementation', 'control'])
def test_execution_recipe_drift_rejects_before_any_runtime_call(mixed_runtime, tmp_path, monkeypatch, mutation):
    manifest, runtime = mixed_runtime
    execution = manifest['entries'][0]['execution']
    if mutation == 'load': execution['scenario']['load'] = []
    elif mutation == 'fault': execution['scenario']['faults'] = [{'primitive': 'unexpected'}]
    elif mutation == 'assertions': execution['scenario']['assertions'] = []
    elif mutation == 'role': manifest['topologies'][0]['nodes'][0]['role'] = 'other'
    elif mutation == 'edges': manifest['topologies'][0]['edges'] = []
    elif mutation == 'implementation': manifest['topologies'][0]['nodes'][1]['implementation'] = 'amaru'
    else: execution['normal_control']['scenario']['load'] = []
    freeze(manifest)
    calls = []
    monkeypatch.setattr(runtime, 'projects', lambda: calls.append('projects') or set())
    monkeypatch.setattr(runtime, 'preflight', lambda *args: calls.append('preflight') or {'passed': False})
    monkeypatch.setattr(runtime, 'run', lambda *args, **kwargs: pytest.fail('unexpected run'))
    with pytest.raises(ValueError, match='execution.*differs'):
        api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                         builds=runtime.builds, lock_path=tmp_path / 'host.lock')
    assert calls == []


def test_legacy_run_campaign_reaches_control_and_security_without_execution_claims(mixed_runtime, tmp_path, monkeypatch):
    manifest, runtime = mixed_runtime
    entry = manifest['entries'][0]
    entry.pop('execution')
    manifest['required_tap_cells'] = manifest['required_tap_cells'][:1]
    manifest['required_tap_cells'][0]['runtime'] = {'status': 'missing', 'reason': 'measurement execution excluded from legacy security-flow fixture'}
    for node in manifest['topologies'][0]['nodes']:
        node['identity']['compatibility'] = {}
    freeze(manifest)
    calls = []
    monkeypatch.setattr(runtime, '_bind_execution', lambda *args: pytest.fail('legacy execution binding'))
    monkeypatch.setattr(runtime, 'projects', lambda: set())
    monkeypatch.setattr(runtime, 'cleanup', lambda projects: None)
    monkeypatch.setattr(runtime, 'preflight', lambda *args: {'passed': True})
    monkeypatch.setattr(runtime, 'archive', lambda run_id: {'run_id': run_id})
    monkeypatch.setattr(runtime, 'verify_archive', lambda receipt: True)
    def command(argv, **kwargs):
        calls.append(argv)
        run_id = 'legacy-' + str(len(calls))
        run_dir = runtime.runs / run_id
        run_dir.mkdir()
        (run_dir / 'manifest.json').write_text(json.dumps({'exit_status': 'pass'}))
        return 'run_id: ' + run_id + '\n'
    monkeypatch.setattr(runtime, '_command', command)
    state = api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                            builds=runtime.builds, lock_path=tmp_path / 'host.lock')
    assert len(calls) == 2
    record = state['cells']['security:' + entry['id']]
    assert record['control']['run_id'] == 'legacy-1'
    assert record['result']['run_id'] == 'legacy-2'
    assert 'execution_digest' not in record['control']['control']
    assert 'execution_digest' not in record['result']['security']


@pytest.mark.parametrize('through_runner', [False, True])
def test_library_implementation_drift_rejects_before_runtime(campaign, tmp_path, through_runner):
    assert campaign['topologies'][0]['mode'] == 'library'
    assert not campaign['entries'][0]['execution']['scenario'].get('substrate', {}).get('nodes')
    runtime = Runtime(campaign, tmp_path)
    campaign['topologies'][0]['nodes'][0]['implementation'] = 'cardano-node'
    freeze(campaign)
    with pytest.raises(ValueError, match='execution.*implementation.*differs'):
        if through_runner:
            run(campaign, tmp_path, runtime=runtime)
        else:
            api.validate_campaign(campaign)
    assert runtime.calls == []


@pytest.mark.parametrize(('field', 'value'), [('mode', 'library'), ('deployment_profile', 'profile-n-cardano-latest-confirmed')])
def test_execution_topology_policy_drift_rejects_before_runtime(mixed_runtime, tmp_path, monkeypatch, field, value):
    manifest, runtime = mixed_runtime
    assert manifest['entries'][0]['contract']['topology'].get(field) != value
    manifest['topologies'][0][field] = value
    freeze(manifest)
    calls = []
    monkeypatch.setattr(runtime, 'projects', lambda: calls.append('projects') or set())
    monkeypatch.setattr(runtime, 'preflight', lambda *args: calls.append('preflight') or {'passed': False})
    with pytest.raises(ValueError, match='execution.*topology.*differs'):
        api.run_campaign(manifest, directory=tmp_path / 'campaign', runtime=runtime,
                         builds=runtime.builds, lock_path=tmp_path / 'host.lock')
    assert calls == []


@pytest.mark.parametrize('malformed', [pytest.param('missing', id='missing'), pytest.param(None, id='null'),
                                      pytest.param([], id='list'), pytest.param('not-an-object', id='string')])
def test_malformed_execution_contract_topology_rejects_before_runtime(campaign, tmp_path, malformed):
    runtime = Runtime(campaign, tmp_path)
    contract = campaign['entries'][0]['contract']
    if malformed == 'missing':
        contract.pop('topology')
    else:
        contract['topology'] = malformed
    freeze(campaign)
    with pytest.raises(ValueError, match='execution contract topology must be an object'):
        api.validate_campaign(campaign)
    with pytest.raises(ValueError, match='execution contract topology must be an object'):
        run(campaign, tmp_path, runtime=runtime)
    assert runtime.calls == []
