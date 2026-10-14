import hashlib
import json
from dataclasses import replace

import pytest

from profile_manager import run_plan as run_plan_module
from profile_manager import launch_store as launch_store_module
from profile_manager.profiles import find_profile
from profile_manager.measurement_targets import measurement_target_record_path
from profile_manager.measurement_targets import cbor_conformance_adapter_record_path
from profile_manager.measurement_targets import plutus_conformance_adapter_record_path

from profile_manager.launch_store import (
    LaunchStoreError,
    check_launch_readiness,
    create_launch,
    load_launch,
    record_launch_preflight,
    retain_launch_inputs,
    measurement_resolution_from_environment,
)
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    classify_run_availability,
    digest_run_plan,
    resolve_run_plan,
)
from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    operator_registration_release,
)
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
from profile_manager.run_presentation import (
    historical_runtime_estimate,
    profile_qualification,
)


ELF_AMD64 = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 10 + b"\x3e\x00"


def _write_imported_amaru_20260918_target(tmp_path, monkeypatch):
    manifest = "sha256:" + "f" * 64
    config = "sha256:" + "4" * 64
    executable = "sha256:" + "e" * 64
    revision = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    patch = "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": "10.11.20260918",
        "source_revision": revision,
        "patch_set_sha256": patch,
        "image_reference": "dwarf/qualified-amaru@" + manifest,
        "runtime_image_reference": config,
        "image_digest": config,
        "image_manifest_digest": manifest,
        "image_config_digest": config,
        "qualification_image_id": manifest,
        "executable_digest": executable,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_image": "dwarf/probe@sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "transfer_source_installation_id": "install-source-test",
        "transfer_manifest_sha256": "sha256:" + "2" * 64,
    }
    path = measurement_target_record_path(
        "amaru", revision, patch, registry_root=tmp_path / "registry"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setenv(
        "ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path / "registry")
    )
    return record


def _write_plutus_adapters(tmp_path):
    identities = (
        (
            "amaru",
            "aedfe797a5b8ef00d8b362be40b47a52c3b4a379",
            "a32bbf07fe4351502a55ef25a424a17991f6e81a625c6bb0892c4a6dd37fe9f2",
        ),
        (
            "cardano-node",
            "fef83fed01d7926f3de83b3b917be5a4a48768b5",
            "0d73dc998d695bd557be97e24bce10f5b25066a8620e77578be6081f4f38fc6e",
        ),
    )
    for implementation, revision, adapter_set in identities:
        executable = tmp_path / f"{implementation}-adapter"
        executable.write_bytes(ELF_AMD64 + f"{implementation} adapter".encode())
        executable.chmod(0o755)
        record = {
            "schema_version": 1,
            "kind": "production-plutus-v2-conformance",
            "implementation": implementation,
            "source_revision": revision,
            "adapter_set_sha256": adapter_set,
            "measurement_boundary": "production-plutus-v2-vm-only",
            "executable": str(executable),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "build_result_sha256": "b" * 64,
            "build_log_sha256": "c" * 64,
        }
        path = plutus_conformance_adapter_record_path(
            implementation,
            revision,
            adapter_set,
            registry_root=tmp_path / "registry",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _write_cbor_adapter(tmp_path):
    revision = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    adapter_set = "ebe45e62397c39fb45bf01226e42d2c40c21e22cb46e9386fda006e229017bb5"
    executable = tmp_path / "amaru-cbor-adapter"
    executable.write_bytes(ELF_AMD64 + b"amaru production cbor adapter")
    executable.chmod(0o755)
    record = {
        "schema_version": 1,
        "kind": "production-cbor-conformance",
        "implementation": "amaru",
        "source_revision": revision,
        "adapter_set_sha256": adapter_set,
        "measurement_boundary": "production-codec-only",
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "build_result_sha256": "b" * 64,
        "build_log_sha256": "c" * 64,
    }
    path = cbor_conformance_adapter_record_path(
        "amaru", revision, adapter_set, registry_root=tmp_path / "registry"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return record


def test_frozen_scenario_target_pins_are_rejected_during_resolution(
    tmp_path, monkeypatch
):
    _write_imported_amaru_20260918_target(tmp_path, monkeypatch)

    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="client-example-restart-recovery-sync-amaru-20260918"
            )
        )

    assert raised.value.field == "scenario_id"
    assert "frozen target identity is incompatible" in str(raised.value)
    assert "image_digest" in str(raised.value)
    assert "executable_digest" in str(raised.value)


def test_portable_scenario_binds_exact_resolved_identity_into_launch(
    tmp_path, monkeypatch
):
    record = _write_imported_amaru_20260918_target(tmp_path, monkeypatch)

    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-restart-recovery-sync-amaru-portable-20260918"
        )
    )
    binding = plan["scenario"]["target_bindings"][0]
    assert binding["kind"] == "resolved-plan"
    assert binding["resolved"]["image_digest"] == record["image_digest"]
    assert binding["resolved"]["executable_digest"] == record["executable_digest"]
    assert binding["resolved"]["transfer_manifest_sha256"] == record[
        "transfer_manifest_sha256"
    ]

    stored = create_launch(plan, root=tmp_path / "launches")
    scenario = json.loads(
        stored["path"].joinpath("scenario.yaml").read_text(encoding="utf-8")
    )
    exact = scenario["setup"][0]
    assert exact["primitive"] == "runtime_verify_exact_target"
    assert exact["identity_binding"] == "resolved-plan"
    assert exact["image_digest"] == record["image_digest"]
    assert exact["executable_digest"] == record["executable_digest"]
    assert exact["image_manifest_digest"] == record["image_manifest_digest"]
    assert exact["image_config_digest"] == record["image_config_digest"]
    assert exact["qualification_image_id"] == record["qualification_image_id"]
    assert exact["transfer_manifest_sha256"] == record[
        "transfer_manifest_sha256"
    ]

    tampered = json.loads(json.dumps(plan))
    tampered["scenario"]["target_bindings"][0]["resolved"][
        "source_revision"
    ] = "0" * 40
    with pytest.raises(LaunchStoreError, match="violates its constraints"):
        create_launch(tampered, root=tmp_path / "tampered-launches")


@pytest.mark.parametrize(
    ("scenario_id", "required_primitive"),
    [
        (
            "client-example-invalid-mini-protocol-amaru-portable-20260918",
            "runtime_protocol_decode_cases",
        ),
        (
            "client-example-cbor-decoding-amaru-portable-20260918",
            "runtime_version_pinned_cbor_conformance",
        ),
        (
            "client-example-plutus-vm-amaru-portable-20260918",
            "runtime_controlled_plutus_transactions",
        ),
        (
            "client-example-simple-transfer-amaru-portable-20260918",
            "runtime_controlled_simple_transfers",
        ),
        (
            "client-example-block-application-amaru-portable-20260918",
            "runtime_controlled_chain_progress_window",
        ),
    ],
)
def test_diverse_portable_amaru_scenarios_bind_the_selected_qualified_target(
    tmp_path, monkeypatch, scenario_id, required_primitive
):
    record = _write_imported_amaru_20260918_target(tmp_path, monkeypatch)
    if "plutus-vm" in scenario_id:
        _write_plutus_adapters(tmp_path)
    if "cbor-decoding" in scenario_id:
        cbor_record = _write_cbor_adapter(tmp_path)

    plan = resolve_run_plan(
        RunPlanRequest(scenario_id=scenario_id),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )

    assert plan["profile"]["id"] in {
        "profile-z-amaru-20260918-nanoseconds-v3",
        "profile-za-amaru-20260918-plutus-v2",
    }
    assert required_primitive in plan["primitive_names"]
    if scenario_id == "client-example-cbor-decoding-amaru-portable-20260918":
        available = {item["id"] for item in plan["measurements"]["available"]}
        resolved = {item["id"] for item in plan["measurements"]["resolved"]}
        assert "amaru-stock-header-validation" in available
        assert "amaru-stock-header-validation" not in resolved
        adapter = plan["scenario"]["required_conformance_adapters"][0]
        assert adapter["adapter_family"] == "cbor"
        assert adapter["declared"]["adapter_set_sha256"] == cbor_record[
            "adapter_set_sha256"
        ]
        assert adapter["resolved"]["executable_sha256"] == cbor_record[
            "executable_sha256"
        ]
        launch = create_launch(plan, root=tmp_path / "cbor-launches")
        materialized = json.loads(
            launch["path"].joinpath("scenario.yaml").read_text(encoding="utf-8")
        )
        primitive = materialized["load"][0]
        assert primitive["adapter_set_sha256"] == cbor_record["adapter_set_sha256"]
        assert primitive["expected_provenance"]["executable_sha256"] == cbor_record[
            "executable_sha256"
        ]
        assert "adapter_record" not in primitive
    binding = plan["scenario"]["target_bindings"][0]
    assert binding["kind"] == "resolved-plan"
    assert binding["resolved"]["image_digest"] == record["image_digest"]
    assert binding["resolved"]["executable_digest"] == record[
        "executable_digest"
    ]

    stored = create_launch(plan, root=tmp_path / "launches")
    scenario = json.loads(
        stored["path"].joinpath("scenario.yaml").read_text(encoding="utf-8")
    )
    exact = next(
        item
        for item in scenario["setup"]
        if item["primitive"] == "runtime_verify_exact_target"
    )
    assert exact["identity_binding"] == "resolved-plan"
    assert exact["image_digest"] == record["image_digest"]
    assert exact["executable_digest"] == record["executable_digest"]
    if "plutus-vm" in scenario_id:
        conformance = next(
            item
            for item in scenario["load"]
            if item["primitive"] == "runtime_version_pinned_plutus_conformance"
        )
        assert conformance["adapters"]["amaru"]["expected_provenance"][
            "adapter_set_sha256"
        ] == "a32bbf07fe4351502a55ef25a424a17991f6e81a625c6bb0892c4a6dd37fe9f2"
        assert all(
            item["available"] is True
            for item in plan["availability"]["required_conformance_adapters"]
        )
        (tmp_path / "amaru-adapter").write_bytes(b"changed after resolution")
        refreshed = classify_run_availability(
            plan, capability_context={"host_os": "linux", "host_arch": "amd64"}
        )
        assert refreshed["state"] == "requires-build"
        assert refreshed["required_conformance_adapters"][0]["available"] is False
        assert "digest mismatch" in refreshed["required_conformance_adapters"][0]["reason"]


def test_portable_plutus_scenario_reports_missing_exact_conformance_adapters(
    tmp_path, monkeypatch
):
    _write_imported_amaru_20260918_target(tmp_path, monkeypatch)

    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-plutus-vm-amaru-portable-20260918"
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )

    adapters = plan["availability"]["required_conformance_adapters"]
    assert plan["availability"]["state"] == "requires-build"
    assert "qualified Plutus conformance adapter" in plan["availability"]["reason"]
    assert [(item["implementation"], item["available"]) for item in adapters] == [
        ("amaru", False),
        ("cardano-node", False),
    ]
    assert all("/home/" not in json.dumps(item) for item in adapters)


def test_portable_cbor_scenario_reports_missing_exact_codec_adapter(
    tmp_path, monkeypatch
):
    _write_imported_amaru_20260918_target(tmp_path, monkeypatch)

    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-cbor-decoding-amaru-portable-20260918"
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )

    adapters = plan["availability"]["required_conformance_adapters"]
    assert plan["availability"]["state"] == "requires-build"
    assert "qualified CBOR conformance adapter" in plan["availability"]["reason"]
    assert [(item["implementation"], item["available"]) for item in adapters] == [
        ("amaru", False)
    ]
    assert all("/home/" not in json.dumps(item) for item in adapters)


def test_repeated_conformance_adapter_locations_are_all_frozen_into_launch(
    tmp_path, monkeypatch
):
    _write_imported_amaru_20260918_target(tmp_path, monkeypatch)
    _write_plutus_adapters(tmp_path)
    source = (
        run_plan_module._scenarios_dir()
        / "client-example-plutus-vm-amaru-portable-20260918.yaml"
    )
    body = json.loads(source.read_text(encoding="utf-8"))
    conformance = next(
        item
        for item in body["load"]
        if item["primitive"] == "runtime_version_pinned_plutus_conformance"
    )
    body["load"].append(json.loads(json.dumps(conformance)))
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / source.name).write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(run_plan_module, "_scenarios_dir", lambda: scenarios)
    monkeypatch.setattr(launch_store_module, "_scenarios_dir", lambda: scenarios)

    plan = resolve_run_plan(
        RunPlanRequest(scenario_id=body["id"]),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )
    assert len(plan["scenario"]["required_conformance_adapters"]) == 4
    stored = create_launch(plan, root=tmp_path / "launches")
    materialized = json.loads(
        stored["path"].joinpath("scenario.yaml").read_text(encoding="utf-8")
    )
    entries = [
        item
        for item in materialized["load"]
        if item["primitive"] == "runtime_version_pinned_plutus_conformance"
    ]
    assert len(entries) == 2
    assert all(
        adapter["expected_provenance"]["platform"] == "linux/amd64"
        for entry in entries
        for adapter in entry["adapters"].values()
    )
    assert run_plan_module.scenario_module.semantic_validate_scenario(
        stored["path"] / "scenario.yaml"
    )["errors"] == []


def test_macos_host_resolves_adapter_for_linux_controller_platform(
    tmp_path, monkeypatch
):
    _write_imported_amaru_20260918_target(tmp_path, monkeypatch)
    _write_plutus_adapters(tmp_path)

    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-plutus-vm-amaru-portable-20260918"
        ),
        capability_context={"host_os": "darwin", "host_arch": "amd64"},
    )

    adapters = plan["availability"]["required_conformance_adapters"]
    assert len(adapters) == 2
    assert all(item["available"] is True for item in adapters)
    assert {item["platform"] for item in adapters} == {"linux/amd64"}


def test_portable_target_binding_rejects_missing_transfer_provenance():
    scenario = run_plan_module._load_catalog_scenario(
        "client-example-restart-recovery-sync-amaru-portable-20260918"
    )[0]
    identity = {
        "implementation": "amaru",
        "version": "10.11.20260918",
        "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379",
        "mode": "patched",
        "patch_set_sha256": "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6",
        "image_reference": "sha256:" + "4" * 64,
        "source_image_reference": "dwarf/qualified-amaru@sha256:" + "f" * 64,
        "image_digest": "sha256:" + "4" * 64,
        "image_manifest_digest": "sha256:" + "f" * 64,
        "image_config_digest": "sha256:" + "4" * 64,
        "executable_digest": "sha256:" + "e" * 64,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "qualification_image_id": "sha256:" + "f" * 64,
        "transfer_source_installation_id": "install-source-test",
    }

    with pytest.raises(RunPlanError, match="transfer_manifest_sha256"):
        run_plan_module._scenario_target_bindings(scenario, identity)


def test_portable_target_binding_rejects_semantic_target_mismatch():
    scenario = run_plan_module._load_catalog_scenario(
        "client-example-restart-recovery-sync-amaru-portable-20260918"
    )[0]
    identity = {
        "implementation": "amaru",
        "version": "10.11.20260918",
        "source_revision": "0" * 40,
        "mode": "patched",
        "patch_set_sha256": "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6",
    }

    with pytest.raises(RunPlanError, match="source_revision"):
        run_plan_module._scenario_target_bindings(scenario, identity)


def test_measurement_scenario_resolves_exact_profile_versions_and_taps():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="amaru-measurement-e2e-stock")
    )

    assert plan["scenario"]["id"] == "amaru-measurement-e2e-stock"
    assert plan["scenario"]["runtime"] == "devnet"
    assert plan["profile"]["id"] == "profile-r-amaru-measurement-stock-control"
    assert plan["versions"]["policy"] == "exact"
    assert plan["versions"]["resolved"]["amaru"]["version"] == "10.11.20260912"
    assert plan["measurements"]["profile"]["id"] == "amaru-security-default"
    assert plan["measurements"]["resolved"]
    assert "runtime_amaru_measurement_calibration" in plan["primitive_names"]
    assert plan["readiness"]["mixed_topology_required"] is False
    assert plan["readiness"]["profile_required"] is True
    assert plan["topology_shape"]["requested_target_nodes"] == 1
    assert plan["topology_shape"]["requested_support_nodes"] == 1
    assert plan["topology_shape"]["selected_node_services"] == {
        "bootstrap-cardano": "p1",
        "amaru1": "amaru-relay-1",
    }
    assert plan["topology_shape"]["selected_target_node_services"] == {
        "amaru1": "amaru-relay-1"
    }
    assert plan["topology_shape"]["selected_support_node_services"] == {
        "bootstrap-cardano": "p1"
    }
    assert plan["topology_shape"]["physical_cardano_node_count"] == 6
    assert plan["topology_shape"]["physical_amaru_node_count"] == 2
    assert "amaru-relay-2" in plan["topology_shape"][
        "fixed_additional_node_services"
    ]


def test_quickstart_availability_is_exact_and_machine_readable():
    image_digest = "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
    base = {"host_os": "linux", "host_arch": "amd64"}

    pullable = resolve_run_plan(
        RunPlanRequest(scenario_id="cardano-measurement-e2e-stock"),
        capability_context=base,
    )
    assert pullable["availability"]["state"] == "pullable"
    assert pullable["availability"]["host_platform"] == "linux/amd64"
    assert pullable["availability"]["container_platform"] == "linux/amd64"
    assert pullable["availability"]["required_image_digests"] == [image_digest]

    ready = resolve_run_plan(
        RunPlanRequest(scenario_id="cardano-measurement-e2e-stock"),
        capability_context={**base, "available_image_digests": [image_digest]},
    )
    assert ready["availability"]["state"] == "ready"
    assert digest_run_plan(ready) == digest_run_plan(pullable)

    requires_build = resolve_run_plan(
        RunPlanRequest(scenario_id="amaru-measurement-e2e-stock"),
        capability_context=base,
    )
    assert requires_build["availability"]["state"] == "requires-build"
    private_digests = requires_build["availability"]["required_image_digests"]
    private_ready = resolve_run_plan(
        RunPlanRequest(scenario_id="amaru-measurement-e2e-stock"),
        capability_context={**base, "available_image_digests": private_digests},
    )
    assert private_ready["availability"]["state"] == "ready"
    assert "already present" in private_ready["availability"]["reason"]

    requires_credential = resolve_run_plan(
        RunPlanRequest(
            scenario_id="cardano-measurement-e2e-stock",
            execution="antithesis",
        ),
        capability_context=base,
    )
    assert requires_credential["availability"]["state"] == "requires-credential"
    assert requires_credential["availability"]["missing_credentials"] == [
        "antithesis"
    ]

    unsupported = resolve_run_plan(
        RunPlanRequest(scenario_id="cardano-measurement-e2e-stock"),
        capability_context={"host_os": "windows", "host_arch": "amd64"},
    )
    assert unsupported["availability"]["state"] == "unsupported-platform"

    platform_mismatch = json.loads(json.dumps(ready))
    platform_mismatch["versions"]["resolved"]["cardano-node"]["image_platforms"] = [
        "linux/amd64"
    ]
    assert (
        classify_run_availability(
            platform_mismatch,
            capability_context={
                "host_os": "linux",
                "host_arch": "arm64",
                "available_image_digests": [image_digest],
            },
        )["state"]
        == "unsupported-platform"
    )


def _operator_registration(*, public=None, platform="linux/amd64", manifest=True):
    return {
        "registration_id": "local-amaru-1",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": (
            "local/amaru@sha256:" + "a" * 64 if manifest else "local/amaru:dev"
        ),
        "manifest_digest": "sha256:" + "a" * 64 if manifest else None,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": platform,
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": public,
            "qualification": "unverified",
        },
    }


def _operator_plan(*, public=None, platform="linux/amd64", manifest=True):
    release = operator_registration_release(
        _operator_registration(public=public, platform=platform, manifest=manifest)
    )
    return {
        "request": {"execution": "local"},
        "versions": {
            "resolved": {"amaru": run_plan_module._compact_release(release)},
            "supporting": {},
        },
        "measurements": {},
        "scenario": {"required_conformance_adapters": []},
    }


def test_local_registration_availability_requires_exact_inspected_identity():
    plan = _operator_plan()
    context = {"host_os": "linux", "host_arch": "amd64"}
    missing = classify_run_availability(plan, capability_context=context)
    assert missing["state"] == "requires-build"

    replaced = classify_run_availability(
        plan,
        capability_context={
            **context,
            "inspected_images": {
                "local/amaru@sha256:" + "a" * 64: {
                    "config_image_id": "sha256:" + "c" * 64,
                    "platform": "linux/amd64",
                }
            },
        },
    )
    assert replaced["state"] == "requires-build"
    assert "identity" in replaced["reason"]

    ready = classify_run_availability(
        plan,
        capability_context={
            **context,
            "inspected_images": {
                "local/amaru@sha256:" + "a" * 64: {
                    "config_image_id": "sha256:" + "b" * 64,
                    "platform": "linux/amd64",
                }
            },
        },
    )
    assert ready["state"] == "ready"


def test_local_amaru_registration_with_unknown_interface_is_actionably_unavailable():
    registration = _operator_registration()
    registration["runtime_interface"] = None

    with pytest.raises(
        DeploymentVersionGateError,
        match="runtime interface is unsupported or unverified",
    ) as raised:
        operator_registration_release(registration)

    assert raised.value.code == "operator-registration-interface-unverified"


def test_registered_public_pullability_and_platform_are_honest():
    base = {"host_os": "linux", "host_arch": "amd64"}
    assert classify_run_availability(
        _operator_plan(public=True), capability_context=base
    )["state"] == "pullable"
    assert classify_run_availability(
        _operator_plan(public=True, platform="linux/arm64"),
        capability_context=base,
    )["state"] == "unsupported-platform"
    assert classify_run_availability(
        _operator_plan(public=True, manifest=False), capability_context=base
    )["state"] == "requires-build"

    inspected_arm = classify_run_availability(
        _operator_plan(public=True, platform="linux/arm64"),
        capability_context={
            **base,
            "inspected_images": {
                "local/amaru@sha256:" + "a" * 64: {
                    "config_image_id": "sha256:" + "b" * 64,
                    "platform": "linux/arm64",
                }
            },
        },
    )
    assert inspected_arm["state"] == "unsupported-platform"

    config_only = _operator_plan(public=None, manifest=False)
    config_registration = config_only["versions"]["resolved"]["amaru"]
    assert classify_run_availability(
        config_only,
        capability_context={
            **base,
            "inspected_images": {
                config_registration["image_reference"]: {
                    "config_image_id": config_registration["image_config_id"],
                    "platform": "linux/amd64",
                }
            },
        },
    )["state"] == "ready"


def test_operator_registration_request_is_path_safe_and_source_bound_work_stays_unavailable():
    with pytest.raises(RunPlanError) as raised:
        RunPlanRequest.from_mapping({
            "scenario_id": "amaru-measurement-e2e-stock",
            "operator_registration_id": "../escape",
        })
    assert raised.value.field == "operator_registration_id"

    preview = {
        "resolved": {"amaru": operator_registration_release(_operator_registration())},
        "catalog_revision": "c" * 64,
        "profile_id": "profile-r",
        "deployment_adapter": "runtime_amaru_control_substrate.py",
        "status": "unknown",
    }
    with pytest.raises(Exception, match="source revision"):
        run_plan_module.build_measurement_target_identity(
            preview, implementation="amaru", mode="stock"
        )


def test_unverified_local_version_can_plan_only_measurement_free_portable_work(
    tmp_path, monkeypatch
):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    registration = _operator_registration(manifest=False)
    ledger.append_registration(registration, expected_revision=initial["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))

    request = RunPlanRequest(
        scenario_id="amaru-measurement-e2e-stock",
        operator_registration_id=registration["registration_id"],
        acknowledge_unknown_versions=True,
        measurement_profile="none",
    )
    context = {
        "host_os": "linux",
        "host_arch": "amd64",
        "inspected_images": {
            registration["image_reference"]: {
                "config_image_id": registration["config_image_id"],
                "platform": registration["platform"],
            }
        },
    }
    first = resolve_run_plan(request, capability_context=context)
    supporting = [
        digest
        for digest in first["availability"]["required_image_digests"]
        if digest != registration["config_image_id"]
    ]
    plan = resolve_run_plan(
        request,
        capability_context={**context, "available_image_digests": supporting},
    )

    assert plan["versions"]["resolved"]["amaru"]["source_revision"] is None
    assert plan["versions"]["resolved"]["amaru"]["image_reference"] == registration["config_image_id"]
    assert plan["versions"]["status"] == "unknown"
    assert plan["measurements"]["resolved"] == []
    assert plan["availability"]["state"] == "ready"

    from profile_manager.dashboard import _current_run_plan_desired_topology

    desired = _current_run_plan_desired_topology(plan)
    assert desired is not None
    assert desired["operator_registration_id"] == registration["registration_id"]
    assert {
        node["image"] for node in desired["nodes"]
        if node["implementation"] == "amaru"
    } == {registration["config_image_id"]}

    current = ledger.read()
    ledger.append_registration(
        {
            **registration,
            "registration_id": "unrelated-later-registration",
            "version": "10.12.1-local",
        },
        expected_revision=current["revision"],
    )
    assert _current_run_plan_desired_topology(plan) is None

    with pytest.raises(RunPlanError, match="retained executable digest"):
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="amaru-measurement-e2e-stock",
                operator_registration_id=registration["registration_id"],
                acknowledge_unknown_versions=True,
                measurement_profile="none",
                measurement_overrides=[
                    {"id": "amaru-stock-ledger-rules", "enabled": True}
                ],
            ),
            capability_context={**context, "available_image_digests": supporting},
        )


def test_qualified_local_target_refreshes_dashboard_availability(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    record = _write_imported_amaru_20260918_target(tmp_path, monkeypatch)
    digest = record["image_digest"]
    first = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-restart-recovery-sync-amaru-portable-20260918"
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )
    support_digests = [
        item
        for item in first["availability"]["required_image_digests"]
        if item != digest
    ]
    refreshed = resolve_run_plan(
        RunPlanRequest(
            scenario_id="client-example-restart-recovery-sync-amaru-portable-20260918"
        ),
        capability_context={
            "host_os": "linux",
            "host_arch": "amd64",
            "available_image_digests": support_digests,
        },
    )

    assert refreshed["availability"]["state"] == "ready"
    assert digest in refreshed["availability"]["required_image_digests"]


def test_attached_scenario_requires_only_its_named_topology():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="consensus-chainhold-upstream-differential")
    )

    assert plan["readiness"]["mixed_topology_required"] is True
    assert plan["readiness"]["topology_id"] == "cardano_amaru"
    assert plan["readiness"]["profile_required"] is False


def test_profile_scenario_uses_latest_confirmed_and_implicit_measurements():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="m3-runtime-blockfetch-multi-peer-historical-range")
    )

    assert plan["versions"]["policy"] == "latest-confirmed"
    assert plan["versions"]["status"] == "confirmed"
    assert plan["measurements"]["profile"]["id"] == "cardano-security-default"
    assert plan["measurements"]["profile"]["source"] == "implicit-default"
    assert plan["scenario"]["seed"] == "0xC0DE3604"
    assert plan["scenario"]["iterations"] is None


def test_library_scenario_does_not_require_mixed_topology_or_profile():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
    )

    assert plan["scenario"]["runtime"] == "library"
    assert plan["profile"] is None
    assert plan["readiness"]["mixed_topology_required"] is False
    assert plan["readiness"]["profile_required"] is False


@pytest.mark.parametrize(
    "body,field",
    [
        ({"scenario_id": "../escape"}, "scenario_id"),
        ({"scenario_id": "edge-cases-cbor-tx-body-amaru", "command": "id"}, "request"),
        ({"scenario_id": "edge-cases-cbor-tx-body-amaru", "runtime": "simulator"}, "runtime"),
    ],
)
def test_request_rejects_paths_commands_unknown_fields_and_unsupported_runtime(body, field):
    with pytest.raises(RunPlanError) as raised:
        RunPlanRequest.from_mapping(body)

    assert raised.value.field == field


def test_incompatible_profile_is_rejected():
    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="amaru-measurement-e2e-stock",
                profile_id="profile-s-cardano-measurement-stock-control",
            )
        )

    assert raised.value.field == "profile_id"


def test_amaru_short_kes_profile_is_rejected_during_plan_resolution(monkeypatch):
    profile = replace(
        find_profile("profile-r-amaru-measurement-stock-control"),
        kes_genesis_override={
            "slots_per_kes_period": 100,
            "max_kes_evolutions": 12,
        },
    )
    monkeypatch.setattr(run_plan_module, "find_profile", lambda _profile_id: profile)

    with pytest.raises(RunPlanError, match="Cardano-only runtime") as raised:
        resolve_run_plan(RunPlanRequest(scenario_id="amaru-measurement-e2e-stock"))

    assert raised.value.field == "profile_id"


def test_unknown_exact_version_requires_explicit_acknowledgement():
    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="cardano-measurement-e2e-stock",
                version_policy="exact",
                cardano_version="11.1.1",
            )
        )

    assert raised.value.field == "versions"
    assert "acknowledgement" in str(raised.value).lower()


@pytest.mark.parametrize(
    "scenario_body,expected",
    [
        (
            {
                "spec_version": "v1",
                "id": "bad-primitive",
                "title": "Bad primitive",
                "target": {"implementation": "cardano-node", "version": "any"},
                "runtime": "library",
                "load": [{"primitive": "not_registered"}],
                "assertions": [],
            },
            "not present in the primitive registry",
        ),
        (
            {
                "spec_version": "v1",
                "id": "broken-producer",
                "title": "Broken producer",
                "target": {"implementation": "cardano-node", "version": "any"},
                "runtime": "library",
                "load": [],
                "assertions": [{"primitive": "all_nodes_responsive"}],
            },
            "requires one of",
        ),
    ],
)
def test_semantically_invalid_scenarios_fail_before_launch(
    tmp_path, monkeypatch, scenario_body, expected
):
    scenario_path = tmp_path / f"{scenario_body['id']}.yaml"
    scenario_path.write_text(json.dumps(scenario_body), encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(tmp_path))

    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(RunPlanRequest(scenario_id=scenario_body["id"]))

    assert expected in str(raised.value)


def test_launch_store_writes_immutable_materialized_inputs(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="m3-runtime-blockfetch-multi-peer-historical-range",
            measurement_overrides=[
                {"id": "cardano-stock-blockfetch", "enabled": False}
            ],
        )
    )

    stored = create_launch(plan, root=tmp_path)
    loaded = load_launch(stored["launch_id"], root=tmp_path)

    assert stored["launch_id"].startswith("launch-")
    assert len(stored["launch_id"]) == len("launch-") + 24
    assert loaded["plan"]["scenario"]["id"] == plan["scenario"]["id"]
    scenario = json.loads(loaded["scenario_path"].read_text(encoding="utf-8"))
    assert scenario["profile"] == plan["profile"]["id"]
    assert scenario["target"] == plan["scenario"]["target"]
    assert scenario["measurement_profile"] == "none"
    selections = {item["id"]: item for item in scenario["measurements"]}
    assert selections["cardano-stock-blockfetch"]["enabled"] is False
    assert {
        item["id"] for item in scenario["measurements"] if item["enabled"]
    } == {item["id"] for item in plan["measurements"]["resolved"]}
    assert loaded["plan_path"].stat().st_mode & 0o777 == 0o600
    assert loaded["scenario_path"].stat().st_mode & 0o777 == 0o600


def test_launch_environment_recovers_the_exact_frozen_measurement_resolution(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="amaru-measurement-e2e-stock")
    )
    stored = create_launch(plan, root=tmp_path)

    recovered = measurement_resolution_from_environment(
        {
            "ADA2_DWARF_LAUNCH_ID": stored["launch_id"],
            "ADA2_DWARF_LAUNCH_ROOT": str(tmp_path),
        }
    )

    assert recovered == plan["measurements"]
    recovered["resolved"].clear()
    assert load_launch(stored["launch_id"], root=tmp_path)["plan"][
        "measurements"
    ]["resolved"]


def test_launch_store_rebinds_default_primitive_profile_to_selected_profile(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="amaru-measurement-e2e-stock",
            profile_id="profile-o-amaru-target-latest-confirmed",
            version_policy="exact",
            cardano_version="10.7.1",
            amaru_version="10.11.20260918",
            measurement_profile="amaru-security-default",
        )
    )

    stored = create_launch(plan, root=tmp_path)
    scenario = json.loads(stored["path"].joinpath("scenario.yaml").read_text())

    assert scenario["profile"] == "profile-o-amaru-target-latest-confirmed"
    assert scenario["load"][0]["profile_id"] == scenario["profile"]


def test_amaru_stock_basic_plan_enables_the_release_json_trace_sink():
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="amaru-measurement-e2e-stock",
            profile_id="profile-o-amaru-target-latest-confirmed",
            version_policy="exact",
            cardano_version="10.7.1",
            amaru_version="10.11.20260918",
            measurement_profile="amaru-security-default",
        )
    )

    assert plan["profile"]["snapshot"]["amaru_json_traces"] is True
    assert len(plan["measurements"]["resolved"]) == 11


def test_launch_store_rejects_traversal_and_tampering(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
    )
    stored = create_launch(plan, root=tmp_path)

    with pytest.raises(LaunchStoreError):
        load_launch("../" + stored["launch_id"], root=tmp_path)

    scenario_path = tmp_path / stored["launch_id"] / "scenario.yaml"
    scenario_path.write_text("{}", encoding="utf-8")
    with pytest.raises(LaunchStoreError, match="digest"):
        load_launch(stored["launch_id"], root=tmp_path)


def test_launch_inputs_are_retained_in_run_evidence(tmp_path):
    launch_root = tmp_path / "launches"
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    stored = create_launch(
        resolve_run_plan(
            RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
        ),
        root=launch_root,
    )

    retained = retain_launch_inputs(
        stored["launch_id"], run_dir=run_dir, root=launch_root
    )

    assert retained == run_dir / "launch"
    assert (retained / "plan.json").is_file()
    assert (retained / "scenario.yaml").is_file()


def test_profile_launch_readiness_checks_live_exact_runtime_identity(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="cardano-measurement-e2e-stock")
    )
    plan["profile"]["snapshot"]["remote_runtime_root"] = str(runtime_root)
    expected = plan["versions"]["resolved"]["cardano-node"]
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "profile_id": plan["profile"]["id"],
                "nodes": [{"id": "node1", "impl": "cardano-node", "container_name": "node1"}],
                "version_provenance": {
                    "nodes": [
                        {
                            "id": "node1",
                            "implementation": "cardano-node",
                            "resolved_version": expected["version"],
                            "source_revision": expected["source_revision"],
                            "image_digest": expected["image_digest"],
                            "identity_status": "running-version-verified",
                            "supporting": False,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    stored = create_launch(plan, root=tmp_path / "launches")

    ready = check_launch_readiness(
        stored["launch_id"],
        root=tmp_path / "launches",
        process_checker=lambda _node: True,
    )

    assert ready["state"] == "ready"
    assert all(check["passed"] for check in ready["checks"])


def test_profile_launch_readiness_blocks_version_mismatch(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="cardano-measurement-e2e-stock")
    )
    plan["profile"]["snapshot"]["remote_runtime_root"] = str(runtime_root)
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "profile_id": plan["profile"]["id"],
                "nodes": [{"id": "node1", "impl": "cardano-node", "container_name": "node1"}],
                "version_provenance": {
                    "nodes": [
                        {
                            "id": "node1",
                            "implementation": "cardano-node",
                            "resolved_version": "0.0.0",
                            "source_revision": "0" * 40,
                            "image_digest": "sha256:" + "0" * 64,
                            "identity_status": "running-version-verified",
                            "supporting": False,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    stored = create_launch(plan, root=tmp_path / "launches")

    blocked = check_launch_readiness(
        stored["launch_id"],
        root=tmp_path / "launches",
        process_checker=lambda _node: True,
    )

    assert blocked["state"] == "blocked"
    assert any(check["id"] == "exact-version-identity" and not check["passed"] for check in blocked["checks"])


def test_launch_preflight_is_retained_with_run_inputs(tmp_path):
    stored = create_launch(
        resolve_run_plan(RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")),
        root=tmp_path / "launches",
    )
    record_launch_preflight(
        stored["launch_id"],
        {"state": "ready", "checks": [{"id": "framework", "passed": True}]},
        root=tmp_path / "launches",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    retained = retain_launch_inputs(
        stored["launch_id"], run_dir=run_dir, root=tmp_path / "launches"
    )

    assert json.loads((retained / "preflight.json").read_text())["state"] == "ready"


def test_request_accepts_bounded_execution_and_seed_override():
    request = RunPlanRequest.from_mapping(
        {
            "scenario_id": "edge-cases-cbor-tx-body-amaru",
            "execution": "local",
            "seed": "0xC0DE5002",
        }
    )

    assert request.execution == "local"
    assert request.seed == "0xC0DE5002"


@pytest.mark.parametrize("seed", [None, ""])
def test_blank_seed_uses_the_scenario_default(seed):
    request = RunPlanRequest.from_mapping({
        "scenario_id": "edge-cases-cbor-tx-body-amaru",
        "seed": seed,
    })

    assert request.seed is None


@pytest.mark.parametrize("seed", ["0", "42", "0x0", "0x2a"])
def test_seed_accepts_unsigned_decimal_and_hex_including_zero(seed):
    request = RunPlanRequest.from_mapping({
        "scenario_id": "edge-cases-cbor-tx-body-amaru",
        "seed": seed,
    })

    assert request.seed == seed


@pytest.mark.parametrize(
    "seed",
    ["-1", "0x", "0xGG", "18446744073709551616"],
)
def test_request_rejects_invalid_seed_override(seed):
    with pytest.raises(RunPlanError) as raised:
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "edge-cases-cbor-tx-body-amaru",
                "seed": seed,
            }
        )

    assert raised.value.field == "seed"


def test_request_rejects_unknown_execution_and_inert_iterations_override():
    with pytest.raises(RunPlanError) as raised_execution:
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "edge-cases-cbor-tx-body-amaru",
                "execution": "simulator",
            }
        )
    assert raised_execution.value.field == "execution"

    with pytest.raises(RunPlanError) as raised_iterations:
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "edge-cases-cbor-tx-body-amaru",
                "iterations": 10,
            }
        )
    assert raised_iterations.value.field == "request"


def test_plan_records_effective_seed_source_and_inert_iteration_contract():
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="m3-runtime-blockfetch-multi-peer-historical-range",
            seed="42",
        )
    )

    assert plan["scenario"]["seed"] == "42"
    assert plan["scenario"]["seed_source"] == "operator-override"
    assert plan["scenario"]["iterations_editable"] is False
    assert "not consumed" in plan["scenario"]["iterations_reason"]


def test_launch_materializes_seed_override(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="edge-cases-cbor-tx-body-amaru",
            seed="0xC0DE5002",
        )
    )

    stored = create_launch(plan, root=tmp_path)
    scenario = json.loads(
        load_launch(stored["launch_id"], root=tmp_path)[
            "scenario_path"
        ].read_text(encoding="utf-8")
    )

    assert scenario["seed"] == "0xC0DE5002"


def test_profile_qualification_uses_resolved_evidence_status():
    assert profile_qualification({"status": "confirmed"}) == {
        "qualification": "confirmed",
        "status_label": "CONFIRMED",
        "status_reason": "This profile resolves to a confirmed version claim.",
    }
    assert profile_qualification({"status": "unknown", "reason": "not run"}) == {
        "qualification": "unmarked",
        "status_label": "",
        "status_reason": "not run",
    }
    assert profile_qualification(None) == {
        "qualification": "unmarked",
        "status_label": "",
        "status_reason": "Qualification could not be resolved.",
    }


def _write_runtime_manifest(
    root, run_id, *, scenario_id, profile_id, version, duration, exit_status="pass"
):
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scenario": {"id": scenario_id},
                "profile": {"id": profile_id},
                "target": {"implementation": "cardano-node", "version": version},
                "resource_snapshot": {"wall_time_seconds": duration},
                "exit_status": exit_status,
                "ended_at": f"2026-09-19T00:00:{run_id[-2:]}Z",
            }
        ),
        encoding="utf-8",
    )


def test_historical_runtime_estimate_uses_only_matching_successful_runs(tmp_path):
    plan = {
        "scenario": {
            "id": "example-scenario",
            "target": {"implementation": "cardano-node", "version": "10.7.1"},
        },
        "profile": {"id": "example-profile"},
    }
    for suffix, duration in (("10", 10), ("11", 12), ("12", 14)):
        _write_runtime_manifest(
            tmp_path,
            f"run-{suffix}",
            scenario_id="example-scenario",
            profile_id="example-profile",
            version="10.7.1",
            duration=duration,
        )
    _write_runtime_manifest(
        tmp_path,
        "run-13",
        scenario_id="other-scenario",
        profile_id="example-profile",
        version="10.7.1",
        duration=100,
    )
    _write_runtime_manifest(
        tmp_path,
        "run-14",
        scenario_id="example-scenario",
        profile_id="example-profile",
        version="10.7.1",
        duration=200,
        exit_status="error",
    )

    estimate = historical_runtime_estimate(plan, runs_dir=tmp_path)

    assert estimate == {
        "available": True,
        "median_seconds": 12.0,
        "minimum_seconds": 10.0,
        "maximum_seconds": 14.0,
        "sample_count": 3,
        "basis": "matching-retained-runs",
    }


def test_historical_runtime_estimate_does_not_invent_a_value(tmp_path):
    estimate = historical_runtime_estimate(
        {
            "scenario": {
                "id": "missing",
                "target": {"implementation": "amaru", "version": "10.11.0"},
            },
            "profile": None,
        },
        runs_dir=tmp_path,
    )

    assert estimate == {
        "available": False,
        "sample_count": 0,
        "basis": "no-matching-retained-runs",
    }
