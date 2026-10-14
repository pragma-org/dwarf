from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from controller.docker_runtime import DockerRuntime, DockerRuntimeError
from controller.service import ControllerService
from profile_manager.dashboard import dispatch_topology_configuration_request
from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager import profiles as profiles_module
from profile_manager.profiles import (
    Profile,
    find_profile,
    persist_topology_configuration,
    versioned_substrate_for_profile,
)
from profile_manager.topology_export import (
    TopologyExportError,
    attest_exact_configuration,
    build_catalog_profile_export,
    build_exact_deployed_snapshot,
    generate_topology_configuration,
    generate_topology_shape_configuration,
    replay_profile_from_snapshot,
    validate_topology_configuration,
    verify_exact_configuration_attestation,
)


NOW = "2026-10-08T12:00:00Z"


def _profile_and_substrate():
    profile = find_profile("profile-e-haskell-preview-proof")
    preview = build_deployment_version_preview(asdict(profile))
    return profile, preview, versioned_substrate_for_profile(profile, preview)


def _observed(substrate: dict) -> list[dict]:
    return [
        {
            "id": node["id"],
            "implementation": node["impl"],
            "version": node["version"],
            "image": node["image"],
            "state": "running",
        }
        for node in substrate["nodes"]
    ]


def _reseal(value: dict) -> dict:
    result = dict(value)
    result.pop("content_sha256", None)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["content_sha256"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return result


def test_catalog_profile_export_is_explicitly_not_deployment_evidence():
    profile, preview, substrate = _profile_and_substrate()

    exported = build_catalog_profile_export(profile, preview, exported_at=NOW)

    assert exported["schema_version"] == "dwarf.topology-configuration/v1"
    assert exported["artifact_kind"] == "catalog_profile"
    assert exported["topology_id"] == profile.id
    assert exported["deployment_evidence"] is False
    assert "observed" not in exported
    assert "remote_runtime_root" not in exported["profile"]
    assert "compose_project" not in exported["profile"]
    assert exported["resolved"]["substrate"] == substrate
    assert exported["provenance"]["catalog_revision"] == substrate["catalog_revision"]
    assert exported["content_sha256"].startswith("sha256:")
    validate_topology_configuration(exported, allow_unverified_exact=True)


def test_exact_deployed_snapshot_contains_matching_runtime_and_redacts_secrets():
    profile, preview, substrate = _profile_and_substrate()
    observed = _observed(substrate)
    observed[0]["api_token"] = "do-not-export-either"
    retained = {
        "schema_version": "v1",
        "substrate": substrate,
        "runtime_root": "/var/dwarf/state/controller/topology-runtimes/example/current",
        "compose_project": "dwarf-profile-install-example",
        "healthy_timeout_seconds": 300,
    }

    exported = build_exact_deployed_snapshot(
        profile=profile,
        version_preview=preview,
        retained_deployment=retained,
        observed_nodes=observed,
        reconciliation_state="converged",
        exported_at=NOW,
        semantic_health={
            "state": "healthy",
            "api_token": "do-not-export",
            "nested": {"password": "also-secret", "safe": "kept"},
        },
    )

    assert exported["artifact_kind"] == "exact_deployed_snapshot"
    assert exported["deployment_evidence"] is False
    assert exported["resolved"]["runtime_parameters"] == {
        "compose_project": "dwarf-profile-install-example",
        "healthy_timeout_seconds": 300,
        "runtime_root": "/var/dwarf/state/controller/topology-runtimes/example/current",
    }
    assert exported["resolved"]["substrate"]["nodes"][0]["image"].count("@sha256:") == 1
    assert exported["observed"]["semantic_health"]["api_token"] == "[REDACTED]"
    assert exported["observed"]["semantic_health"]["nested"] == {
        "password": "[REDACTED]",
        "safe": "kept",
    }
    assert sorted(exported["redacted_paths"]) == [
        "observed.nodes[0].api_token",
        "observed.semantic_health.api_token",
        "observed.semantic_health.nested.password",
    ]
    assert exported["observed"]["nodes"] == [
        {**_observed(substrate)[0], "api_token": "[REDACTED]"}
    ]
    assert "observed.nodes[0].api_token" in exported["redacted_paths"]
    validate_topology_configuration(exported, allow_unverified_exact=True)


@pytest.mark.parametrize("state", ["absent", "drifted", "partially_deployed"])
def test_exact_snapshot_refuses_any_incomplete_or_drifted_topology(state):
    profile, preview, substrate = _profile_and_substrate()

    with pytest.raises(TopologyExportError, match="complete identity-matching"):
        build_exact_deployed_snapshot(
            profile=profile,
            version_preview=preview,
            retained_deployment={"substrate": substrate},
            observed_nodes=_observed(substrate),
            reconciliation_state=state,
            exported_at=NOW,
        )


def test_exact_snapshot_can_retain_an_identity_matching_but_unhealthy_configuration():
    profile, preview, substrate = _profile_and_substrate()

    exported = build_exact_deployed_snapshot(
        profile=profile,
        version_preview=preview,
        retained_deployment={"substrate": substrate},
        observed_nodes=_observed(substrate),
        reconciliation_state="unhealthy",
        exported_at=NOW,
    )

    attested = attest_exact_configuration(
        exported,
        secret="controller-secret-" + "x" * 32,
        installation_id="install-proof",
        release_revision="revision-proof",
    )

    assert exported["observed"]["reconciliation_state"] == "unhealthy"
    assert replay_profile_from_snapshot(
        attested, attestation_secret="controller-secret-" + "x" * 32
    ) == exported["profile"]


def test_exact_snapshot_refuses_observed_identity_drift():
    profile, preview, substrate = _profile_and_substrate()
    observed = _observed(substrate)
    observed[0]["image"] = "ghcr.io/example/wrong@sha256:" + "f" * 64

    with pytest.raises(TopologyExportError, match="observed node identity"):
        build_exact_deployed_snapshot(
            profile=profile,
            version_preview=preview,
            retained_deployment={"substrate": substrate},
            observed_nodes=observed,
            reconciliation_state="converged",
            exported_at=NOW,
        )


def test_generator_resolves_an_exact_supported_release_and_is_deterministic():
    first = generate_topology_configuration(
        topology_id="generated-cardano-two",
        node_count=2,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    second = generate_topology_configuration(
        topology_id="generated-cardano-two",
        node_count=2,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )

    assert first == second
    assert first["artifact_kind"] == "generated_configuration"
    assert first["deployment_evidence"] is False
    assert first["generator"] == {
        "implementation": "cardano-node",
        "network_magic": 42,
        "node_count": 2,
        "version": "11.1.2",
    }
    assert first["profile"]["generated_topology"] == {
        "schema_version": "v1",
        **first["generator"],
    }
    assert first["profile"]["version_policy"] == "exact"
    assert first["profile"]["cardano_version"] == "11.1.2"
    assert len(first["resolved"]["substrate"]["nodes"]) == 2
    assert all("@sha256:" in node["image"] for node in first["resolved"]["substrate"]["nodes"])
    validate_topology_configuration(first)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"node_count": 0}, "node_count"),
        ({"node_count": 33}, "node_count"),
        ({"implementation": "shell"}, "implementation"),
        ({"version": "latest"}, "unknown release"),
        ({"network_magic": 0}, "network_magic"),
        ({"network_magic": 2**32}, "network_magic"),
        ({"network_magic": 10**100}, "network_magic"),
    ],
)
def test_generator_refuses_unbounded_or_non_catalog_inputs(updates, message):
    request = {
        "topology_id": "generated-safe",
        "node_count": 1,
        "implementation": "cardano-node",
        "version": "11.1.2",
        "network_magic": 42,
        "exported_at": NOW,
    }
    request.update(updates)

    with pytest.raises(TopologyExportError, match=message):
        generate_topology_configuration(**request)


def test_shape_generator_builds_replayable_mixed_topology_from_exact_catalog_pins():
    generated = generate_topology_shape_configuration(
        topology_id="generated-mixed-two",
        haskell_count=1,
        amaru_count=1,
        cardano_version="11.1.2",
        amaru_version="10.11.20260918",
        network_magic=42,
        exported_at=NOW,
    )

    assert generated["generator"] == {
        "schema_version": "v2",
        "implementation": "mixed",
        "network_magic": 42,
        "haskell_count": 1,
        "amaru_count": 1,
        "cardano_version": "11.1.2",
        "amaru_version": "10.11.20260918",
    }
    assert generated["profile"]["generated_topology"] == generated["generator"]
    assert {node["impl"] for node in generated["resolved"]["substrate"]["nodes"]} == {
        "cardano-node", "amaru"
    }
    assert replay_profile_from_snapshot(generated) == generated["profile"]


def test_shape_generator_rejects_empty_or_mismatched_counts_and_versions():
    base = {
        "topology_id": "generated-shape",
        "haskell_count": 1,
        "amaru_count": 1,
        "cardano_version": "11.1.2",
        "amaru_version": "10.11.20260918",
        "network_magic": 42,
        "exported_at": NOW,
    }
    for updates in (
        {"haskell_count": 0, "amaru_count": 0},
        {"haskell_count": 33},
        {"amaru_count": 33},
        {"haskell_count": 1, "cardano_version": None},
        {"amaru_count": 1, "amaru_version": None},
    ):
        with pytest.raises(TopologyExportError):
            generate_topology_shape_configuration(**{**base, **updates})


def test_validation_rejects_any_tampering_and_snapshot_replay_is_exact():
    generated = generate_topology_configuration(
        topology_id="generated-replay",
        node_count=1,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    profile = replay_profile_from_snapshot(generated)

    assert profile == generated["profile"]

    tampered = {**generated, "profile": {**generated["profile"], "node_count": 2}}
    with pytest.raises(TopologyExportError, match="content digest"):
        validate_topology_configuration(tampered)
    with pytest.raises(TopologyExportError, match="content digest"):
        replay_profile_from_snapshot(tampered)


def test_exact_export_rejects_generated_provenance_on_a_mixed_profile():
    source = Path(
        "dwarf/profiles/profile-c-mixed-haskell-amaru-minimal/profile.yaml"
    )
    profile_data = json.loads(source.read_text())
    profile_data["generated_topology"] = {
        "schema_version": "v1",
        "implementation": "cardano-node",
        "network_magic": 42,
        "node_count": 1,
        "version": "11.1.2",
    }
    profile = Profile.from_dict(profile_data)
    preview = build_deployment_version_preview(asdict(profile))
    substrate = versioned_substrate_for_profile(profile, preview)

    with pytest.raises(TopologyExportError, match="generator provenance"):
        build_exact_deployed_snapshot(
            profile=profile,
            version_preview=preview,
            retained_deployment={"substrate": substrate},
            observed_nodes=_observed(substrate),
            reconciliation_state="converged",
            exported_at=NOW,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"label": "silently renamed generated topology"},
        {"listen_address": "127.0.0.1:39999"},
        {"config_source_dir": "/tmp/not-portable"},
    ],
)
def test_exact_export_rejects_any_generated_profile_shape_divergence(updates):
    generated = generate_topology_configuration(
        topology_id="generated-renamed",
        node_count=1,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    profile = Profile.from_dict({**generated["profile"], **updates})
    preview = build_deployment_version_preview(asdict(profile))
    substrate = versioned_substrate_for_profile(profile, preview)

    with pytest.raises(TopologyExportError, match="generator provenance"):
        build_exact_deployed_snapshot(
            profile=profile,
            version_preview=preview,
            retained_deployment={"substrate": substrate},
            observed_nodes=_observed(substrate),
            reconciliation_state="converged",
            exported_at=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_source_dir", "/etc"),
        ("upstream_peer_address", "127.0.0.1:22"),
        ("listen_address", "0.0.0.0:8787"),
    ],
)
def test_self_sealed_import_cannot_inject_host_paths_or_endpoints(field, value):
    generated = generate_topology_configuration(
        topology_id="generated-hostile",
        node_count=1,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    generated["profile"] = {**generated["profile"], field: value}
    generated["resolved"] = {
        "substrate": {**generated["resolved"]["substrate"], field: value}
    }
    hostile = _reseal(generated)

    with pytest.raises(TopologyExportError, match="generator resolution"):
        replay_profile_from_snapshot(hostile)


def test_exact_export_refuses_full_execution_substrate_drift():
    profile, preview, substrate = _profile_and_substrate()
    retained = {
        "substrate": {
            **substrate,
            "network": "preprod",
            "network_magic": 1,
            "upstream_peer_address": "preprod-node.world.dev.cardano.org:30000",
        }
    }

    with pytest.raises(TopologyExportError, match="execution substrate"):
        build_exact_deployed_snapshot(
            profile=profile,
            version_preview=preview,
            retained_deployment=retained,
            observed_nodes=_observed(substrate),
            reconciliation_state="converged",
            exported_at=NOW,
        )


def test_replay_refuses_catalog_profile_because_it_is_not_an_exact_selection():
    profile, preview, _ = _profile_and_substrate()
    catalog_export = build_catalog_profile_export(profile, preview, exported_at=NOW)

    with pytest.raises(TopologyExportError, match="not replayable"):
        replay_profile_from_snapshot(catalog_export)


def test_fabricated_exact_evidence_is_rejected_without_controller_attestation():
    generated = generate_topology_configuration(
        topology_id="generated-forged-evidence",
        node_count=1,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    forged = _reseal(
        {
            **generated,
            "artifact_kind": "exact_deployed_snapshot",
            "deployment_evidence": True,
            "resolved": {
                **generated["resolved"],
                "runtime_parameters": {},
            },
            "observed": {
                "reconciliation_state": "converged",
                "semantic_health": {"state": "healthy"},
                "nodes": _observed(generated["resolved"]["substrate"]),
            },
            "redacted_paths": [],
        }
    )

    with pytest.raises(TopologyExportError, match="controller attestation"):
        replay_profile_from_snapshot(forged, attestation_secret="x" * 32)


def test_controller_attestation_authenticates_exact_evidence():
    profile, preview, substrate = _profile_and_substrate()
    candidate = build_exact_deployed_snapshot(
        profile=profile,
        version_preview=preview,
        retained_deployment={"substrate": substrate},
        observed_nodes=_observed(substrate),
        reconciliation_state="converged",
        exported_at=NOW,
        semantic_health={"state": "healthy"},
    )
    attested = attest_exact_configuration(
        candidate,
        secret="controller-secret-" + "x" * 32,
        installation_id="install-proof",
        release_revision="revision-proof",
    )

    assert attested["deployment_evidence"] is True
    assert verify_exact_configuration_attestation(
        attested, secret="controller-secret-" + "x" * 32
    )
    validate_topology_configuration(
        attested, attestation_secret="controller-secret-" + "x" * 32
    )
    with pytest.raises(TopologyExportError, match="controller attestation"):
        validate_topology_configuration(attested, attestation_secret="wrong" * 8)


@pytest.mark.parametrize(
    "profile_id",
    [
        "profile-e-haskell-preview-proof",
        "profile-r-amaru-measurement-stock-control",
        "profile-s-cardano-measurement-stock-control",
    ],
)
def test_catalog_exact_snapshot_reopens_trusted_profile_without_overwrite(
    monkeypatch, profile_id,
):
    from profile_manager.data import catalog_definitions

    profile = find_profile(profile_id)
    preview = build_deployment_version_preview(asdict(profile))
    substrate = versioned_substrate_for_profile(profile, preview)
    candidate = build_exact_deployed_snapshot(
        profile=profile,
        version_preview=preview,
        retained_deployment={"substrate": substrate},
        observed_nodes=_observed(substrate),
        reconciliation_state="converged",
        exported_at=NOW,
        semantic_health={"state": "healthy"},
    )
    assert "generator" not in candidate
    secret = "controller-secret-" + "x" * 32
    attested = attest_exact_configuration(
        candidate,
        secret=secret,
        installation_id="install-proof",
        release_revision="revision-proof",
    )
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", secret)

    def reject_write(*_args, **_kwargs):
        raise AssertionError("catalog-backed replay must not write or overwrite")

    monkeypatch.setattr(catalog_definitions, "save_definition", reject_write)

    assert persist_topology_configuration(attested, create=True) == profile


def test_generated_configuration_is_atomically_persisted_as_a_profile(
    monkeypatch, tmp_path
):
    generated = generate_topology_configuration(
        topology_id="generated-persisted",
        node_count=1,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(profiles_module, "PROFILE_ROOT", tmp_path)

    saved = persist_topology_configuration(generated, create=True)

    assert saved.id == "generated-persisted"
    assert saved.cardano_version == "11.1.2"
    assert saved.version_policy == "exact"
    assert saved.generated_topology == generated["profile"]["generated_topology"]
    source = (tmp_path / "generated-persisted" / "profile.yaml").read_text()
    assert "remote_runtime_root" not in source
    assert "generated_topology" in source
    assert find_profile("generated-persisted") == saved

    with pytest.raises(ValueError, match="already exists"):
        persist_topology_configuration(generated, create=True)


def test_controller_runtime_exports_the_verified_retained_deployment(monkeypatch, tmp_path):
    profile, preview, substrate = _profile_and_substrate()
    substrate = {
        **substrate,
        "controller_ownership": {
            "installation_id": "install",
            "topology_id": profile.id,
        },
        "docker_host_runtime_root": "/srv/dwarf/current",
    }
    retained = {
        "schema_version": "v1",
        "substrate": substrate,
        "runtime_root": str(tmp_path / "runtime"),
        "compose_project": f"dwarf-profile-install-{profile.id}",
        "healthy_timeout_seconds": 300,
    }
    node = substrate["nodes"][0]
    container = {
        "Id": "container-id",
        "Name": "/managed-node",
        "Config": {
            "Image": node["image"],
            "Labels": {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install",
                "io.dwarf.topology": profile.id,
                "io.dwarf.component": "node",
                "io.dwarf.node_id": node["id"],
                "io.dwarf.implementation": node["impl"],
                "io.dwarf.version": node["version"],
            },
        },
        "State": {"Status": "running"},
    }
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    current = runtime._runtime_parent(profile.id) / "current"
    current.mkdir(parents=True)
    (current / "versioned-deployment.json").write_text(
        json.dumps(retained), encoding="utf-8"
    )
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_: retained)
    monkeypatch.setattr(runtime, "_all_containers", lambda: [container])
    monkeypatch.setattr(
        runtime,
        "topology_health",
        lambda _: {"state": "unhealthy", "reason_code": "chain_not_progressing"},
    )

    exported = runtime.export_topology(profile.id)

    assert exported["artifact_kind"] == "exact_deployed_snapshot"
    assert exported["profile"]["id"] == profile.id
    assert exported["observed"]["nodes"][0]["container_name"] == "managed-node"
    assert exported["observed"]["reconciliation_state"] == "unhealthy"
    assert exported["observed"]["semantic_health"]["reason_code"] == "chain_not_progressing"
    assert exported["deployment_evidence"] is False

    container["Config"]["Image"] = "ghcr.io/example/drift@sha256:" + "e" * 64
    with pytest.raises(DockerRuntimeError, match="observed node identity"):
        runtime.export_topology(profile.id)


def test_dashboard_preview_is_read_only_and_save_uses_validated_artifact():
    calls = []
    artifact = {"artifact_kind": "generated_configuration", "topology_id": "generated-ui"}

    def generator(**request):
        calls.append(("preview", request))
        return artifact

    def persister(configuration, *, create):
        calls.append(("save", configuration, create))
        return type("Saved", (), {"id": "generated-ui"})()

    preview = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=secret",
        body=json.dumps(
            {
                "operation": "preview",
                "topology_id": "generated-ui",
                "node_count": 2,
                "implementation": "cardano-node",
                "version": "11.1.2",
                "network_magic": 42,
            }
        ).encode(),
        expected_token="secret",
        generator=generator,
        persister=persister,
    )
    assert preview[0] == 200
    assert json.loads(preview[2])["configuration"] == artifact
    assert [call[0] for call in calls] == ["preview"]

    saved = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=secret",
        body=json.dumps({"operation": "save", "configuration": artifact}).encode(),
        expected_token="secret",
        generator=generator,
        persister=persister,
    )
    assert saved[0] == 200
    assert json.loads(saved[2])["next_url"].endswith("profile_id=generated-ui")
    assert calls[-1] == ("save", artifact, True)


def test_dashboard_shape_preview_returns_mixed_v2_without_persisting():
    response = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=secret",
        body=json.dumps({
            "operation": "preview-shape",
            "topology_id": "generated-ui-mixed",
            "implementation": "mixed",
            "haskell_count": 1,
            "amaru_count": 1,
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260918",
            "network_magic": 42,
        }).encode(),
        expected_token="secret",
    )

    payload = json.loads(response[2])
    assert response[0] == 200
    assert payload["ok"] is True
    assert payload["configuration"]["generator"]["schema_version"] == "v2"
    assert payload["configuration"]["profile"]["haskell_count"] == 1
    assert payload["configuration"]["profile"]["amaru_count"] == 1


def test_dashboard_export_calls_only_typed_controller_save():
    calls = []

    def controller(operation, parameters, timeout, idempotency_key=None):
        calls.append((operation, parameters, timeout, idempotency_key))
        return {
            "status": "applied",
            "configuration": {"artifact_kind": "exact_deployed_snapshot"},
            "content_sha256": "sha256:" + "a" * 64,
        }

    response = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=secret",
        body=json.dumps({"operation": "export", "profile_id": "generated-ui"}).encode(),
        expected_token="secret",
        request_controller=controller,
    )

    assert response[0] == 200
    assert calls[0][0:3] == (
        "topology.save",
        {"topology_id": "generated-ui", "file_name": "generated-ui.json"},
        30,
    )
    assert calls[0][3].startswith("topology-export-generated-ui-")


def test_dashboard_configuration_endpoint_is_token_gated_and_strict():
    unauthorized = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=wrong",
        body=b"{}",
        expected_token="secret",
    )
    assert unauthorized[0] == 401

    invalid = dispatch_topology_configuration_request(
        method="POST",
        path="/api/topology/configurations?token=secret",
        body=json.dumps(
            {
                "operation": "preview",
                "topology_id": "x",
                "node_count": 1,
                "implementation": "cardano-node",
                "version": "11.1.2",
                "network_magic": 42,
                "image": "attacker/image:latest",
            }
        ).encode(),
        expected_token="secret",
    )
    assert invalid[0] == 400


def test_status_builder_requires_preview_before_save_and_has_no_direct_deploy():
    root = Path(__file__).resolve().parents[1]
    template = (root / "dwarf/dashboard/templates/operate/status.j2").read_text()
    script = (root / "dwarf/dashboard/static/js/deployment-builder.js").read_text()

    assert 'id="topology-configuration-builder"' in template
    assert 'data-builder-action="save" disabled' in template
    assert 'data-builder-action="export"' in template
    assert 'data-builder-action="import"' in template
    assert 'name="network_magic" type="number" min="1" max="4294967295"' in template
    assert 'pattern="[a-z0-9][a-z0-9\\-]{0,62}"' in template
    assert "previewFingerprint !== fingerprint()" in script
    assert "saveButton.disabled = true" in script
    assert "profileIdentityPattern.test(value || '')" in script
    assert "exportButton.disabled = !activeProfile" in script
    assert "let activeProfile" in script
    assert "window.addEventListener('dwarf:topology-profile'" in script
    assert "new URLSearchParams(window.location.search).get('profile_id')" not in script
    assert "/api/deploy" not in script


def test_status_builder_adopts_only_valid_controller_discovered_profile():
    root = Path(__file__).resolve().parents[1]
    script = root / "dwarf/dashboard/static/js/deployment-builder.js"
    probe = r"""
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const inertButton = () => ({disabled: false, addEventListener: () => {}});
const exportButton = inertButton();
exportButton.disabled = true;
const form = {
  elements: {
    implementation: {value: 'cardano-node'},
    version: {options: [], selectedOptions: []},
  },
  addEventListener: () => {},
};
const elements = {
  '[data-builder-form]': form,
  '[data-builder-action="preview"]': inertButton(),
  '[data-builder-action="save"]': inertButton(),
  '[data-builder-action="export"]': exportButton,
  '[data-builder-action="import"]': {addEventListener: () => {}},
};
const builder = {
  dataset: {endpoint: '/api/topology/configurations', dashboardToken: '', activeProfile: ''},
  querySelector: (selector) => elements[selector] || null,
};
const context = {
  document: {getElementById: (id) => id === 'topology-configuration-builder' ? builder : null},
  window: {addEventListener: (name, listener) => {listeners[name] = listener;}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
listeners['dwarf:topology-profile']({detail: {profileId: '../unsafe'}});
const invalidDisabled = exportButton.disabled;
listeners['dwarf:topology-profile']({detail: {profileId: 'task43-live'}});
const enabledAfterValid = !exportButton.disabled;
listeners['dwarf:topology-profile']({detail: {profileId: ''}});
console.log(JSON.stringify({
  invalidDisabled,
  enabledAfterValid,
  clearedDisabled: exportButton.disabled,
  activeProfile: builder.dataset.activeProfile,
}));
"""
    completed = subprocess.run(
        ["node", "-e", probe, str(script)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "invalidDisabled": True,
        "enabledAfterValid": True,
        "clearedDisabled": True,
        "activeProfile": "",
    }


def test_generated_controller_lifecycle_export_replay_preserves_resolved_snapshot(
    monkeypatch, tmp_path
):
    first_root = tmp_path / "profiles-first"
    first_root.mkdir()
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(first_root))
    monkeypatch.setattr(profiles_module, "PROFILE_ROOT", first_root)
    generated = generate_topology_configuration(
        topology_id="generated-lifecycle-proof",
        node_count=2,
        implementation="cardano-node",
        version="11.1.2",
        network_magic=42,
        exported_at=NOW,
    )
    persisted = persist_topology_configuration(generated, create=True)
    image = generated["resolved"]["substrate"]["nodes"][0]["image"]
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", image)
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "integration-token-" + "x" * 32)

    class LifecycleRuntime:
        def __init__(self):
            self.adapter = DockerRuntime(
                installation_id="integration",
                state_dir=tmp_path,
                host_state_dir=tmp_path,
            )
            self.current = {}
            self.retained = None

        def health(self):
            return {"docker": "ok", "compose": "ok", "golden_images": "ok"}

        def inventory(self):
            return {"managed": list(self.current.get("resources") or [])}

        def topology_health(self, topology_id):
            return {
                "schema_version": "v1",
                "topology_id": topology_id,
                "state": "healthy" if self.current.get("resources") else "absent",
                "reason_code": "integration-proof",
            }

        def snapshot(self, topology_id):
            if self.current.get("topology_id") == topology_id:
                return self.current
            return {"topology_id": topology_id, "resources": []}

        def prepare_topology(self, topology):
            return self.adapter.prepare_topology(topology)

        def apply(self, plan, execution_id, cancel_event: threading.Event):
            if plan["action"] == "remove":
                self.current = {"topology_id": plan["topology_id"], "resources": []}
                self.retained = None
                return {"status": "applied", "topology_id": plan["topology_id"]}
            deployment = plan["deployment"]
            substrate = deployment["substrate"]
            self.retained = {
                "schema_version": "v1",
                "substrate": substrate,
                "runtime_root": str(tmp_path / "controller-runtime"),
                "compose_project": deployment["compose_project"],
                "healthy_timeout_seconds": 300,
            }
            self.current = {
                "topology_id": plan["topology_id"],
                "resources": [
                    {
                        "id": node["id"],
                        "implementation": node["impl"],
                        "version": node["version"],
                        "image": node["image"],
                        "state": "running",
                    }
                    for node in substrate["nodes"]
                ],
            }
            return {"status": "applied", "topology_id": plan["topology_id"]}

        def export_topology(self, topology_id):
            profile = find_profile(topology_id)
            preview = build_deployment_version_preview(asdict(profile))
            return build_exact_deployed_snapshot(
                profile=profile,
                version_preview=preview,
                retained_deployment=self.retained,
                observed_nodes=self.current["resources"],
                reconciliation_state="converged",
                exported_at=NOW,
                semantic_health=self.topology_health(topology_id),
            )

    runtime = LifecycleRuntime()
    service = ControllerService(
        token="integration-token-" + "x" * 32,
        state_dir=tmp_path,
        runtime=runtime,
        release_revision="integration-revision",
        installation_id="integration",
    )

    def dispatch(operation, *, idempotency_key=None, **parameters):
        body = {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "timeout_seconds": 10,
            **parameters,
        }
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        status, result = service.dispatch(
            "POST",
            "/v1/requests",
            {"Authorization": "Bearer integration-token-" + "x" * 32},
            json.dumps(body).encode(),
        )
        assert status == 200, result
        return result

    def thin_topology(profile):
        preview = build_deployment_version_preview(asdict(profile))
        substrate = versioned_substrate_for_profile(profile, preview)
        return {
            "topology_id": profile.id,
            "network_magic": profile.network_magic,
            "nodes": [
                {
                    "id": node["id"],
                    "implementation": node["impl"],
                    "version": node["version"],
                    "image": node["image"],
                }
                for node in substrate["nodes"]
            ],
        }

    first_plan = dispatch(
        "topology.plan", action="deploy", topology=thin_topology(persisted)
    )
    dispatch(
        "topology.apply",
        plan_id=first_plan["plan_id"],
        plan_digest=first_plan["plan_digest"],
        idempotency_key="integration-deploy-first",
    )
    first_export = dispatch(
        "topology.save",
        topology_id=persisted.id,
        file_name="generated-first.json",
        idempotency_key="integration-export-first",
    )["configuration"]
    remove_plan = dispatch("topology.plan", action="remove", topology_id=persisted.id)
    dispatch(
        "topology.apply",
        plan_id=remove_plan["plan_id"],
        plan_digest=remove_plan["plan_digest"],
        idempotency_key="integration-remove",
    )

    second_root = tmp_path / "profiles-second"
    second_root.mkdir()
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(second_root))
    monkeypatch.setattr(profiles_module, "PROFILE_ROOT", second_root)
    replayed = persist_topology_configuration(first_export, create=True)
    second_plan = dispatch(
        "topology.plan", action="deploy", topology=thin_topology(replayed)
    )
    dispatch(
        "topology.apply",
        plan_id=second_plan["plan_id"],
        plan_digest=second_plan["plan_digest"],
        idempotency_key="integration-deploy-second",
    )
    second_export = dispatch(
        "topology.save",
        topology_id=replayed.id,
        file_name="generated-second.json",
        idempotency_key="integration-export-second",
    )["configuration"]

    assert first_export["resolved"] == second_export["resolved"]
    assert first_export["generator"] == second_export["generator"]
    assert first_export["content_sha256"] == second_export["content_sha256"]
