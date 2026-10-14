import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from profile_manager import operator_version_decisions as operator_decisions
from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import Profile, deployment_adapter_for_profile, load_profiles
from profile_manager.version_catalog import (
    CatalogError,
    load_version_catalog,
    resolve_profile_versions,
    validate_version_catalog,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "dwarf" / "versions" / "catalog.json"


def _operator_evidence_from_plan(plan):
    target = dict(plan["measurements"]["target_identity"])
    target.setdefault("image_config_digest", "sha256:" + "c" * 64)
    return {
        "scenario": {"id": plan["scenario"]["id"], "sha256": plan["scenario"]["digest"]},
        "profile": {"id": plan["profile"]["id"]},
        "versions": plan["versions"],
        "measurements": {"target_identity": target},
    }


def _profile(**updates):
    data = {
        "id": "profile-version-test",
        "label": "Version test",
        "node_type": "cardano-node",
        "node_count": 1,
        "amaru_node_count": 0,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "latest-stable",
    }
    data.update(updates)
    return data


@pytest.mark.parametrize("missing_value", [None, ""])
def test_omitted_or_blank_policy_uses_safe_confirmed_default(missing_value):
    data = _profile()
    if missing_value is None:
        del data["version_policy"]
    else:
        data["version_policy"] = missing_value

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))
    profile = Profile.from_dict(data)

    assert resolved["policy"] == "latest-confirmed"
    assert resolved["policy_source"] == "implicit-default"
    assert resolved["status"] == "confirmed"
    assert resolved["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert resolved["resolved"]["cardano-node"]["artifacts"][0]["digest"].startswith(
        "sha256:"
    )
    assert profile.version_policy == "latest-confirmed"
    assert profile.version_policy_source == "implicit-default"


def test_loaded_implicit_default_remains_implicit_through_cli_preview_serialization():
    data = _profile()
    del data["version_policy"]
    profile = Profile.from_dict(data)

    preview = build_deployment_version_preview(asdict(profile))

    assert preview["policy"] == "latest-confirmed"
    assert preview["policy_source"] == "implicit-default"


def test_every_shipped_profile_declares_safe_policy_and_preserves_adapter_class():
    expected_adapters = {
        "block-apply-adversary": "amaru-control",
        "profile-a-haskell-peersharing-disabled": "generated-cardano-local",
        "profile-b-haskell-peersharing-enabled": "generated-cardano-local",
        "profile-c-mixed-haskell-amaru-minimal": "amaru-control",
        "profile-d-amaru-preview-proof": "amaru-public-peer",
        "profile-e-haskell-preview-proof": "cardano-public-peer",
        "profile-f-amaru-preview2-proof": "amaru-public-peer",
        "profile-g-haskell-preview2-proof": "cardano-public-peer",
        "profile-h-generated-mixed-haskell2-amaru1": "amaru-control",
        "profile-i-generated-haskell3": "generated-cardano-local",
        "profile-j-haskell-preprod-proof": "cardano-public-peer",
        "profile-k-amaru-preprod-proof": "amaru-public-peer",
        "profile-l-amaru-closed-devnet": "amaru-control",
        "profile-m-consensus-threshold": "generated-cardano-local",
        "profile-n-cardano-latest-confirmed": "generated-cardano-local",
        "profile-o-amaru-target-latest-confirmed": "amaru-control",
        "profile-p-mixed-latest-confirmed": "amaru-control",
        "profile-q-amaru-measurement-patched": "amaru-control",
        "profile-r-amaru-measurement-stock-control": "amaru-control",
        "profile-s-cardano-measurement-stock-control": "generated-cardano-local",
        "profile-t-cardano-measurement-patched": "generated-cardano-local",
        "profile-u-amaru-measurement-nanoseconds-v2": "amaru-control",
        "profile-v-cardano-measurement-nanoseconds-v2": "generated-cardano-local",
        "profile-w-amaru-measurement-plutus-v2": "amaru-control",
        "profile-x-amaru-cbor-fix-regression-nanoseconds-v2": "amaru-control",
        "profile-y-amaru-block-application-nanoseconds-v3": "amaru-control",
        "profile-z-amaru-20260918-nanoseconds-v3": "amaru-control",
        "profile-za-amaru-20260918-plutus-v2": "amaru-control",
        "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3": "amaru-control",
        "profile-zc-mixed-1112-amaru-20260918-v16-repro": "amaru-control",
        "profile-opcert-aged-kes-cardano-1112": "generated-cardano-local",
    }
    profiles = load_profiles()

    assert len(profiles) == 31
    assert {profile.id for profile in profiles} == set(expected_adapters)
    for profile in profiles:
        source = next(
            CATALOG_PATH.parents[1].glob(f"profiles/{profile.id}/profile.yaml")
        )
        raw = json.loads(source.read_text(encoding="utf-8"))
        expected_policy = (
            "exact"
            if profile.id in {
                "profile-q-amaru-measurement-patched",
                "profile-r-amaru-measurement-stock-control",
                "profile-s-cardano-measurement-stock-control",
                "profile-t-cardano-measurement-patched",
                "profile-u-amaru-measurement-nanoseconds-v2",
                "profile-v-cardano-measurement-nanoseconds-v2",
                "profile-w-amaru-measurement-plutus-v2",
                "profile-x-amaru-cbor-fix-regression-nanoseconds-v2",
                "profile-y-amaru-block-application-nanoseconds-v3",
                "profile-z-amaru-20260918-nanoseconds-v3",
                "profile-za-amaru-20260918-plutus-v2",
                "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
                "profile-zc-mixed-1112-amaru-20260918-v16-repro",
                "profile-opcert-aged-kes-cardano-1112",
            }
            else "latest-confirmed"
        )
        assert raw["version_policy"] == expected_policy
        assert profile.version_policy_source == "explicit"
        assert deployment_adapter_for_profile(profile) == expected_adapters[profile.id]
        preview = build_deployment_version_preview(raw)
        if (
            expected_adapters[profile.id]
            in {"cardano-public-peer", "amaru-public-peer"}
        ):
            assert preview["status"] == "unknown"
            assert preview["requires_acknowledgement"] is True
        else:
            assert preview["status"] == "confirmed"


def test_latest_stable_cardano_resolves_newest_qualified_release():
    resolved = resolve_profile_versions(_profile(), load_version_catalog(CATALOG_PATH))

    assert resolved["policy_source"] == "explicit"
    assert resolved["scope"] == "cardano-only"
    assert resolved["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert resolved["status"] == "confirmed"
    assert resolved["requires_acknowledgement"] is False
    assert resolved["blocked"] is False


def test_latest_confirmed_mixed_uses_exact_default_pair_and_artifacts():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="latest-confirmed",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["pair"]["id"] == "cardano-11.1.2__amaru-10.11.20260918"
    assert resolved["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert resolved["resolved"]["amaru"]["version"] == "10.11.20260918"
    assert resolved["resolved"]["cardano-node"]["artifacts"][0]["digest"].startswith("sha256:")
    assert resolved["requires_acknowledgement"] is False


def test_confirmed_amaru_default_resolves_its_qualified_cardano_support():
    catalog = load_version_catalog(CATALOG_PATH)
    modified = copy.deepcopy(catalog)
    for item in modified["releases"]:
        if item["implementation"] == "amaru" and "amaru-only" in item["verification"]:
            item["verification"]["amaru-only"]["default"] = False
    release = next(
        item
        for item in modified["releases"]
        if item["implementation"] == "amaru"
        and item["version"] == "10.11.20260730"
    )
    release["verification"]["amaru-only"] = {
        "status": "confirmed",
        "default": True,
        "checked_at": "2026-09-18T05:00:00Z",
        "supporting_cardano_version": "10.7.1",
        "reason": "Passed with the exact supporting Cardano release.",
        "evidence": ["qualification:demo"],
        "issues": [],
    }

    resolved = resolve_profile_versions(
        _profile(
            node_type="amaru",
            node_count=0,
            amaru_node_count=1,
            version_policy="latest-confirmed",
        ),
        validate_version_catalog(modified),
    )

    assert resolved["resolved"]["amaru"]["version"] == "10.11.20260730"
    assert resolved["supporting"]["cardano-node"]["version"] == "10.7.1"


def test_exact_known_pair_resolves_by_catalog_id():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        compatibility_pair="cardano-10.7.1__amaru-10.11.0",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["pair"]["topology_revision"]


def test_exact_unlisted_pair_is_unknown_not_implicitly_compatible():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        cardano_version="11.1.1",
        amaru_version="10.11.20260912",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "unknown"
    assert resolved["pair"] is None
    assert resolved["requires_acknowledgement"] is True
    assert "not present in the compatibility matrix" in resolved["reason"]


def test_incompatible_pair_is_a_hard_block():
    catalog = load_version_catalog(CATALOG_PATH)
    incompatible = copy.deepcopy(catalog)
    incompatible["compatibility_pairs"].append(
        {
            "id": "incompatible-current",
            "cardano_version": "11.1.1",
            "amaru_version": "10.11.20260912",
            "status": "incompatible",
            "default": False,
            "checked_at": "2026-09-17T22:00:00Z",
            "topology_revision": "a" * 40,
            "dwarf_revision": "b" * 40,
            "reason": "Runtime genesis contract failed.",
            "evidence": ["run:failed"],
            "issues": [],
        }
    )
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        compatibility_pair="incompatible-current",
    )

    resolved = resolve_profile_versions(data, validate_version_catalog(incompatible))

    assert resolved["status"] == "incompatible"
    assert resolved["blocked"] is True
    assert resolved["requires_acknowledgement"] is False


def test_profile_model_retains_version_intent():
    profile = Profile.from_dict(
        _profile(version_policy="exact", cardano_version="11.1.2")
    )

    assert profile.version_policy == "exact"
    assert profile.cardano_version == "11.1.2"
    assert profile.amaru_version is None
    assert profile.compatibility_pair is None


def test_invalid_policy_fails_closed():
    with pytest.raises(CatalogError, match="version_policy"):
        resolve_profile_versions(
            _profile(version_policy="whatever-is-newest"),
            load_version_catalog(CATALOG_PATH),
        )


def test_shipped_version_aware_defaults_resolve_exact_confirmed_contracts():
    catalog = load_version_catalog(CATALOG_PATH)
    profiles_root = CATALOG_PATH.parents[1] / "profiles"
    expected = {
        "profile-n-cardano-latest-confirmed": ("cardano-only", "11.1.2", None),
        "profile-o-amaru-target-latest-confirmed": ("amaru-only", "10.11.20260912", "10.7.1"),
        "profile-p-mixed-latest-confirmed": ("mixed", "10.11.20260918", None),
    }
    for profile_id, (scope, target_version, support_version) in expected.items():
        body = json.loads((profiles_root / profile_id / "profile.yaml").read_text(encoding="utf-8"))
        resolved = resolve_profile_versions(body, catalog)
        assert body["version_policy"] == "latest-confirmed"
        assert resolved["scope"] == scope
        assert resolved["status"] == "confirmed"
        implementation = "cardano-node" if scope == "cardano-only" else "amaru"
        assert resolved["resolved"][implementation]["version"] == target_version
        if support_version:
            assert resolved["supporting"]["cardano-node"]["version"] == support_version


def test_fixed_amaru_regression_profile_resolves_exact_confirmed_commit():
    body = json.loads(
        (
            CATALOG_PATH.parents[1]
            / "profiles/profile-x-amaru-cbor-fix-regression-nanoseconds-v2/profile.yaml"
        ).read_text()
    )

    resolved = resolve_profile_versions(body, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["requires_acknowledgement"] is False
    assert resolved["resolved"]["amaru"]["version"] == "v10.11.20260912-30-gd3a6dafc"
    assert resolved["resolved"]["amaru"]["source_revision"] == (
        "d3a6dafcced78f5809a96619e883cf04911d2bdc"
    )
    assert resolved["supporting"]["cardano-node"]["version"] == "10.7.1"
    assert body["measurement_revision"] == "nanoseconds-v2"
    assert body["measurement_patch_revision"] == (
        "d3a6dafcced78f5809a96619e883cf04911d2bdc"
    )
    verification = resolved["resolved"]["amaru"]["verification"]["amaru-only"]
    assert "run:20260920T235440Z-050046a4" in verification["evidence"]
    assert verification["default"] is False


def test_amaru_block_application_v3_profile_pins_new_exact_artifact():
    body = json.loads(
        (
            CATALOG_PATH.parents[1]
            / "profiles/profile-y-amaru-block-application-nanoseconds-v3/profile.yaml"
        ).read_text()
    )

    resolved = resolve_profile_versions(body, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert body["measurement_revision"] == "nanoseconds-v3"
    assert body["measurement_patch_revision"] == (
        "b159172f25a9c389f82f20bca4f15e3032791638"
    )
    assert body["measurement_patch_set_sha256"] == (
        "042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00"
    )


def test_amaru_20260918_profile_resolves_exact_confirmed_release():
    body = json.loads(
        (
            CATALOG_PATH.parents[1]
            / "profiles/profile-z-amaru-20260918-nanoseconds-v3/profile.yaml"
        ).read_text()
    )

    resolved = resolve_profile_versions(body, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["requires_acknowledgement"] is False
    assert resolved["resolved"]["amaru"]["version"] == "10.11.20260918"
    assert resolved["resolved"]["amaru"]["source_revision"] == (
        "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    )
    assert resolved["supporting"]["cardano-node"]["version"] == "10.7.1"
    assert body["measurement_revision"] == "nanoseconds-v3"
    assert body["measurement_patch_revision"] == (
        "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    )
    assert body["measurement_patch_set_sha256"] == (
        "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
    )
    verification = resolved["resolved"]["amaru"]["verification"]["amaru-only"]
    assert verification["default"] is False
    assert any("dwarf-qual-amaru-10-7-1-10-11-20260918" in item for item in verification["evidence"])


def test_operator_subject_plan_projection_freezes_exact_context_but_is_not_run_evidence():
    from profile_manager.run_plan import resolve_run_plan

    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    subject = operator_decisions.decision_subject_from_plan_projection(_operator_evidence_from_plan(plan))
    target = _operator_evidence_from_plan(plan)["measurements"]["target_identity"]
    assert subject == {
        "implementation": target["implementation"], "version": target["version"],
        "image_reference": target["image_reference"],
        "manifest_digest": target["image_manifest_digest"],
        "config_image_id": target["image_config_digest"],
        "scenario_id": plan["scenario"]["id"],
        "scenario_sha256": plan["scenario"]["digest"].removeprefix("sha256:"),
        "scope": plan["versions"]["scope"], "topology_profile_id": plan["profile"]["id"],
        "deployment_identity_sha256": plan["versions"]["deployment_identity_sha256"].removeprefix("sha256:"),
        "mixed_pair": plan["versions"]["pair"],
    }


def test_actual_manifest_shape_without_immutable_version_binding_cannot_create_decision():
    manifest = {
        "run_id": "20261011T141236Z-c5491555",
        "scenario": {"id": "stock", "path": "scenario.yaml", "sha256": "1" * 64, "spec_version": "v1"},
        "profile": {"id": "profile-r", "sha256": "2" * 64},
        "target": {"implementation": "amaru", "version": "10.11.20260912"},
        "measurements": {"target_identity": {"implementation": "amaru", "version": "10.11.20260912"}},
        "run_service": {"plan_path": "inputs/run-service-plan.json", "plan_sha256": "3" * 64},
    }
    with pytest.raises(operator_decisions.InvalidOperatorVersionState, match="immutable.*binding"):
        operator_decisions.decision_subject_from_evidence(manifest)


def test_operator_preference_is_exact_clearable_and_never_mutates_catalog(tmp_path, monkeypatch):
    from profile_manager.run_plan import digest_run_plan, resolve_run_plan

    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    catalog_before = CATALOG_PATH.read_bytes()
    original = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    subject = operator_decisions.decision_subject_from_plan_projection(_operator_evidence_from_plan(original))
    ledger = operator_decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    event = {
        "decision_id": "decision-current-cardano", "subject_key": operator_decisions.subject_key(subject),
        "subject": subject, "decision": "supported", "reason": "reviewed exact canonical evidence",
        "actor": "local-operator", "decided_at": "2026-10-11T16:00:00Z",
        "evidence": {"run_id": "run-current-cardano", "manifest_sha256": "1" * 64,
                     "operational_outcome": "completed", "assertion_verdict": "pass"},
    }
    decided = ledger.append_decision(event, expected_revision=initial["revision"])
    pref = {"preference_id": "preference-current-cardano",
            "scope_key": operator_decisions.preference_scope_key(subject), "action": "set",
            "decision_id": event["decision_id"], "recorded_at": "2026-10-11T16:01:00Z"}
    preferred = ledger.append_preference(pref, expected_revision=decided["revision"])
    resolved = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    assert resolved["versions"]["operator_preference"] == {
        "preference_id": pref["preference_id"], "decision_id": event["decision_id"],
        "subject_key": event["subject_key"], "overlay_revision": preferred["revision"],
    }
    assert digest_run_plan(resolved) != digest_run_plan(original)
    assert CATALOG_PATH.read_bytes() == catalog_before

    unrelated = resolve_run_plan({"scenario_id": "cardano-measurement-overhead-calibration-stock"})
    assert "operator_preference" not in unrelated["versions"]

    import profile_manager.run_plan as run_plan_module
    from dataclasses import replace as dataclass_replace

    real_loader = run_plan_module._load_catalog_scenario

    def changed_scenario(identifier):
        loaded, warnings = real_loader(identifier)
        if identifier == "cardano-measurement-e2e-stock":
            loaded = dataclass_replace(loaded, raw_bytes=loaded.raw_bytes + b"\n")
        return loaded, warnings

    monkeypatch.setattr(run_plan_module, "_load_catalog_scenario", changed_scenario)
    with pytest.raises(run_plan_module.RunPlanError, match="stale scenario revision"):
        resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})

    ledger.append_preference(
        {"preference_id": "preference-current-cardano-clear", "scope_key": pref["scope_key"],
         "action": "clear", "decision_id": None, "recorded_at": "2026-10-11T16:02:00Z"},
        expected_revision=preferred["revision"],
    )
    assert "operator_preference" not in resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})["versions"]


def test_operator_preference_fails_closed_for_changed_deployment_and_malformed_overlay(tmp_path, monkeypatch):
    from profile_manager.run_plan import RunPlanError, resolve_run_plan

    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    subject = operator_decisions.decision_subject_from_plan_projection(_operator_evidence_from_plan(plan))
    subject["deployment_identity_sha256"] = "2" * 64
    ledger = operator_decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    event = {"decision_id": "decision-stale-cardano", "subject_key": operator_decisions.subject_key(subject),
             "subject": subject, "decision": "supported", "reason": "historical exact context",
             "actor": "local-operator", "decided_at": "2026-10-11T16:00:00Z",
             "evidence": {"run_id": "run-stale-cardano", "manifest_sha256": "3" * 64,
                          "operational_outcome": "completed", "assertion_verdict": "pass"}}
    state = ledger.append_decision(event, expected_revision=initial["revision"])
    ledger.append_preference(
        {"preference_id": "preference-stale-cardano", "scope_key": operator_decisions.preference_scope_key(subject),
         "action": "set", "decision_id": event["decision_id"], "recorded_at": "2026-10-11T16:01:00Z"},
        expected_revision=state["revision"],
    )
    with pytest.raises(RunPlanError, match="deployment identity"):
        resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})

    ledger.path.write_text("{}", encoding="utf-8")
    with pytest.raises(RunPlanError, match="operator version state"):
        resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})


def test_operator_preference_survives_additive_catalog_provenance_but_plan_stays_fresh(tmp_path, monkeypatch):
    import profile_manager.run_plan as run_plan

    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    original = run_plan.resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    subject = operator_decisions.decision_subject_from_plan_projection(_operator_evidence_from_plan(original))
    ledger = operator_decisions.OperatorVersionDecisionLedger(tmp_path)
    state = ledger.read()
    event = {"decision_id": "decision-enrichment", "subject_key": operator_decisions.subject_key(subject),
             "subject": subject, "decision": "supported", "reason": "exact runtime contract",
             "actor": "local-operator", "decided_at": "2026-10-11T16:00:00Z",
             "evidence": {"run_id": "run-enrichment", "manifest_sha256": "5" * 64,
                          "operational_outcome": "completed", "assertion_verdict": "pass"}}
    state = ledger.append_decision(event, expected_revision=state["revision"])
    ledger.append_preference(
        {"preference_id": "preference-enrichment", "scope_key": operator_decisions.preference_scope_key(subject),
         "action": "set", "decision_id": event["decision_id"], "recorded_at": "2026-10-11T16:01:00Z"},
        expected_revision=state["revision"],
    )
    real_preview = run_plan.build_deployment_version_preview

    def enriched(*args, **kwargs):
        preview = real_preview(*args, **kwargs)
        return {**preview, "catalog_revision": "6" * 64}

    monkeypatch.setattr(run_plan, "build_deployment_version_preview", enriched)
    refreshed = run_plan.resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    assert refreshed["versions"]["deployment_identity_sha256"] == original["versions"]["deployment_identity_sha256"]
    assert refreshed["versions"]["topology_plan_sha256"] != original["versions"]["topology_plan_sha256"]
    frozen_desired = refreshed["versions"]["expected_desired_topology"]
    assert frozen_desired["deployment_sha256"] == refreshed["versions"][
        "deployment_identity_sha256"
    ]
    assert frozen_desired["plan_sha256"] == refreshed["versions"][
        "topology_plan_sha256"
    ]
    assert isinstance(frozen_desired["catalog_reuse_metadata"]["artifacts"], list)
    assert refreshed["versions"]["operator_preference"]["decision_id"] == event["decision_id"]


def test_registered_preference_survives_own_ledger_revision_and_stales_old_plan(
    tmp_path, monkeypatch
):
    from profile_manager.run_plan import (
        RunPlanError,
        RunPlanRequest,
        _validate_operator_preference_subject,
        digest_run_plan,
        resolve_run_plan,
    )
    from profile_manager.profiles import (
        desired_topology_from_substrate,
        find_profile,
        versioned_substrate_for_profile,
    )

    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    registration = {
        "registration_id": "local-amaru-self-preference",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "local/amaru:self-preference",
        "manifest_digest": None,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "inspected_at": "2026-10-11T18:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = operator_decisions.OperatorVersionDecisionLedger(tmp_path)
    registered = ledger.append_registration(
        registration, expected_revision=ledger.read()["revision"]
    )
    request = RunPlanRequest(
        scenario_id="amaru-measurement-e2e-stock",
        operator_registration_id=registration["registration_id"],
        acknowledge_unknown_versions=True,
        measurement_profile="none",
    )
    capability = {
        "host_os": "linux",
        "host_arch": "amd64",
        "inspected_images": {
            registration["image_reference"]: {
                "config_image_id": registration["config_image_id"],
                "platform": registration["platform"],
            }
        },
    }
    preview = resolve_run_plan(request, capability_context=capability)
    supporting = [
        digest
        for digest in preview["availability"]["required_image_digests"]
        if digest != registration["config_image_id"]
    ]
    prepared = resolve_run_plan(
        request,
        capability_context={**capability, "available_image_digests": supporting},
    )
    resolved_registration = prepared["versions"]["resolved"]["amaru"]
    subject = {
        "implementation": "amaru",
        "version": resolved_registration["version"],
        "image_reference": resolved_registration["image_reference"],
        "manifest_digest": resolved_registration["image_digest"],
        "config_image_id": resolved_registration["image_config_id"],
        "scenario_id": prepared["scenario"]["id"],
        "scenario_sha256": prepared["scenario"]["digest"].removeprefix("sha256:"),
        "scope": prepared["versions"]["scope"],
        "topology_profile_id": prepared["profile"]["id"],
        "deployment_identity_sha256": prepared["versions"][
            "deployment_identity_sha256"
        ].removeprefix("sha256:"),
        "mixed_pair": prepared["versions"]["pair"],
    }
    event = {
        "decision_id": "decision-local-self-preference",
        "subject_key": operator_decisions.subject_key(subject),
        "subject": subject,
        "decision": "supported",
        "reason": "operator reviewed the exact local artifact and scenario",
        "actor": "local-operator",
        "decided_at": "2026-10-11T18:01:00Z",
        "evidence": {
            "run_id": "run-local-self-preference",
            "manifest_sha256": "1" * 64,
            "operational_outcome": "completed",
            "assertion_verdict": "pass",
        },
    }
    preference = {
        "preference_id": "preference-local-self-preference",
        "scope_key": operator_decisions.preference_scope_key(subject),
        "action": "set",
        "decision_id": event["decision_id"],
        "recorded_at": "2026-10-11T18:02:00Z",
    }
    preferred_state = ledger.append_decision_and_optional_preference(
        event,
        expected_revision=registered["revision"],
        prefer=True,
        preference_event=preference,
    )

    implicitly_selected = resolve_run_plan(
        RunPlanRequest(
            scenario_id=request.scenario_id,
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        ),
        capability_context={**capability, "available_image_digests": supporting},
    )

    assert preferred_state["revision"] != registered["revision"]
    assert implicitly_selected["versions"]["operator_registration_id"] == registration[
        "registration_id"
    ]
    assert implicitly_selected["versions"]["operator_preference"]["decision_id"] == event[
        "decision_id"
    ]
    assert implicitly_selected["versions"]["resolved"]["amaru"][
        "image_config_id"
    ] == registration["config_image_id"]
    assert implicitly_selected["versions"]["resolved"]["amaru"][
        "source_revision"
    ] is None
    assert implicitly_selected["versions"]["deployment_identity_sha256"] == prepared[
        "versions"
    ]["deployment_identity_sha256"]
    assert implicitly_selected["versions"]["topology_plan_sha256"] != prepared[
        "versions"
    ]["topology_plan_sha256"]
    assert digest_run_plan(implicitly_selected) != digest_run_plan(prepared)

    profile = find_profile(prepared["profile"]["id"])

    def deployment_identity_for(candidate):
        state_dir = tmp_path / candidate["registration_id"]
        candidate_ledger = operator_decisions.OperatorVersionDecisionLedger(state_dir)
        candidate_ledger.append_registration(
            candidate, expected_revision=candidate_ledger.read()["revision"]
        )
        candidate_preview = build_deployment_version_preview(
            asdict(profile),
            operator_registration_id=candidate["registration_id"],
            operator_state_dir=state_dir,
        )
        substrate = versioned_substrate_for_profile(profile, candidate_preview)
        return desired_topology_from_substrate(profile.id, substrate)[
            "deployment_sha256"
        ]

    for label, changes in (
        ("registration", {"registration_id": "local-amaru-other-registration"}),
        ("artifact", {"config_image_id": "sha256:" + "c" * 64}),
        ("platform", {"platform": "linux/arm64"}),
        (
            "source",
            {
                "provenance": {
                    **registration["provenance"],
                    "source_revision": "d" * 40,
                }
            },
        ),
    ):
        changed_identity = deployment_identity_for(
            {
                **registration,
                "registration_id": f"local-amaru-changed-{label}",
                **changes,
            }
        )
        assert changed_identity != prepared["versions"]["deployment_identity_sha256"]
        changed_versions = copy.deepcopy(implicitly_selected["versions"])
        changed_versions["deployment_identity_sha256"] = changed_identity
        with pytest.raises(
            RunPlanError,
            match="operator preference no longer matches.*deployment identity",
        ):
            _validate_operator_preference_subject(subject, changed_versions, profile.id)


def test_mixed_preference_requires_the_complete_exact_pair_record():
    from profile_manager.run_plan import RunPlanError, _validate_operator_preference_subject

    subject = {
        "implementation": "amaru", "version": "10.11.20260918", "scope": "mixed",
        "topology_profile_id": "profile-zb", "deployment_identity_sha256": "7" * 64,
        "manifest_digest": "sha256:" + "8" * 64,
        "mixed_pair": {"id": "pair-1", "status": "confirmed", "topology_revision": "a" * 40},
    }
    versions = {
        "scope": "mixed", "deployment_identity_sha256": "sha256:" + "7" * 64,
        "pair": {"id": "pair-1", "status": "unknown", "topology_revision": "b" * 40},
        "resolved": {"amaru": {"version": "10.11.20260918", "image_digest": "sha256:" + "8" * 64}},
    }
    with pytest.raises(RunPlanError, match="mixed pair"):
        _validate_operator_preference_subject(subject, versions, "profile-zb")


@pytest.mark.parametrize("stamped_hash, manifest_hash", [(None, None), ("", ""), ("3" * 64, None)])
def test_canonical_subject_refuses_absent_or_unbound_run_plan_hashes(stamped_hash, manifest_hash):
    subject = {
        "implementation": "amaru", "version": "10.11.20260918",
        "image_reference": "registry.invalid/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64, "config_image_id": "sha256:" + "b" * 64,
        "scenario_id": "scenario-1", "scenario_sha256": "c" * 64,
        "scope": "amaru-only", "topology_profile_id": "profile-1",
        "deployment_identity_sha256": "d" * 64, "mixed_pair": None,
    }
    manifest = {
        "run_service": {"plan_sha256": manifest_hash},
        "operator_version_evidence": {
            "subject": subject, "subject_key": operator_decisions.subject_key(subject),
            "run_service_plan_sha256": stamped_hash,
            "binding_sha256": "sha256:" + "e" * 64,
        },
    }
    with pytest.raises(operator_decisions.InvalidOperatorVersionState, match="run-plan binding"):
        operator_decisions.decision_subject_from_evidence(manifest)
