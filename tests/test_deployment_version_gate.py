import copy
import json
from pathlib import Path

import pytest

from profile_manager import dashboard
from profile_manager import deployment_versions
from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    build_deployment_version_preview,
    enforce_deployment_version_gate,
    profile_deployment_version_preview,
)
from profile_manager.version_catalog import load_version_catalog


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "dwarf/versions/catalog.json"


def _profile(tmp_path: Path, monkeypatch, *, mixed=False, policy="latest-stable") -> str:
    root = tmp_path / "profiles"
    profile_id = "profile-version-gate"
    directory = root / profile_id
    directory.mkdir(parents=True)
    body = {
        "id": profile_id,
        "label": "Version gate",
        "node_type": "mixed" if mixed else "cardano-node",
        "node_count": 1,
        "amaru_node_count": 1 if mixed else 0,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": policy,
    }
    (directory / "profile.yaml").write_text(json.dumps(body) + "\n", encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(root))
    return profile_id


def test_preview_exposes_exact_release_status_digest_and_catalog_revision(tmp_path, monkeypatch):
    profile_id = _profile(tmp_path, monkeypatch)

    preview = profile_deployment_version_preview(profile_id)

    assert preview["profile_id"] == profile_id
    assert preview["policy"] == "latest-stable"
    assert preview["scope"] == "cardano-only"
    assert preview["status"] == "confirmed"
    assert preview["requires_acknowledgement"] is False
    assert preview["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert preview["resolved"]["cardano-node"]["artifacts"][0]["digest"].startswith("sha256:")
    assert len(preview["catalog_revision"]) == 64


def test_unknown_requires_one_run_acknowledgement(tmp_path, monkeypatch):
    profile_id = _profile(tmp_path, monkeypatch)
    preview = profile_deployment_version_preview(profile_id)
    preview = {**preview, "status": "unknown", "requires_acknowledgement": True}

    with pytest.raises(DeploymentVersionGateError, match="acknowledgement"):
        enforce_deployment_version_gate(preview, acknowledge_unknown=False)

    accepted = enforce_deployment_version_gate(preview, acknowledge_unknown=True)
    assert accepted["allowed"] is True
    assert accepted["acknowledgement"]["status"] == "unknown"
    assert accepted["acknowledgement"]["one_run_only"] is True


def test_incompatible_and_blocked_are_hard_stops():
    catalog = load_version_catalog(CATALOG_PATH)
    modified = copy.deepcopy(catalog)
    pair = modified["compatibility_pairs"][0]
    pair["status"] = "incompatible"
    pair["reason"] = "Genesis contract mismatch."
    profile = {
        "id": "mixed-blocked",
        "label": "Mixed blocked",
        "node_count": 1,
        "amaru_node_count": 1,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "latest-confirmed",
    }

    with pytest.raises(DeploymentVersionGateError, match="not confirmed"):
        build_deployment_version_preview(profile, modified, catalog_revision="a" * 64)

    profile["version_policy"] = "exact"
    profile["compatibility_pair"] = pair["id"]
    preview = build_deployment_version_preview(profile, modified, catalog_revision="a" * 64)
    with pytest.raises(DeploymentVersionGateError, match="Genesis contract mismatch"):
        enforce_deployment_version_gate(preview, acknowledge_unknown=True)


def test_preview_endpoint_is_read_only_and_returns_json(tmp_path, monkeypatch):
    profile_id = _profile(tmp_path, monkeypatch, mixed=True, policy="latest-confirmed")

    response = dashboard.dispatch_deployment_preview_request(
        method="GET", path=f"/api/deploy/preview?profile={profile_id}"
    )

    assert response is not None and response[0] == 200
    payload = json.loads(response[2])
    assert payload["status"] == "confirmed"
    assert payload["pair"]["id"] == "cardano-10.7.1__amaru-10.11.0"


def test_dashboard_deploy_blocks_unknown_until_acknowledged(tmp_path, monkeypatch):
    profile_id = _profile(tmp_path, monkeypatch)
    built = []
    preview = profile_deployment_version_preview(profile_id)
    preview = {**preview, "status": "unknown", "requires_acknowledgement": True}
    monkeypatch.setattr(
        deployment_versions,
        "profile_deployment_version_preview",
        lambda _profile_id: preview,
    )

    def builder(action, **kwargs):
        built.append((action, kwargs))
        return ["true"]

    refused = dashboard.dispatch_mutating_request(
        method="POST",
        path=f"/api/deploy?token=secret&profile={profile_id}",
        expected_token="secret",
        cli_command_builder=builder,
    )
    assert refused[0] == 409
    assert b"unknown-version-ack-required" in refused[2]
    assert built == []

    accepted = dashboard.dispatch_mutating_request(
        method="POST",
        path=(
            f"/api/deploy?token=secret&profile={profile_id}"
            "&acknowledge_unknown_version=1"
        ),
        expected_token="secret",
        cli_command_builder=builder,
    )
    assert accepted[0] == 200
    list(accepted[2])
    assert built[0][1]["acknowledge_unknown_version"] is True


def test_default_cli_builder_forwards_unknown_acknowledgement():
    command = dashboard._default_cli_command_builder(
        "deploy",
        profile="profile-version-gate",
        acknowledge_unknown_version=True,
    )

    assert command[-1] == "--acknowledge-unknown-version"
