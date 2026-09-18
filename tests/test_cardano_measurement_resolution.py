import json
from pathlib import Path

import pytest

from profile_manager.deployment_versions import build_deployment_version_preview, build_measurement_target_identity
from profile_manager.measurement_resolution import MeasurementResolutionError, resolve_measurements
from profile_manager.scenario import scenario_from_body


VERSION = "11.1.2"
REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DIGEST = "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"


def _scenario(*, profile=None, measurements=None):
    body = {
        "spec_version": "v1",
        "id": "cardano-measurement-resolution",
        "title": "Cardano measurement resolution",
        "target": {"implementation": "cardano-node", "version": VERSION},
        "runtime": "library",
        "setup": [], "load": [], "faults": [], "probes": [], "assertions": [], "teardown": [],
    }
    if profile is not None:
        body["measurement_profile"] = profile
    if measurements is not None:
        body["measurements"] = measurements
    return scenario_from_body((json.dumps(body) + "\n").encode())


def _identity(**updates):
    body = {
        "implementation": "cardano-node",
        "version": VERSION,
        "source_revision": REVISION,
        "mode": "stock",
        "image_reference": f"ghcr.io/intersectmbo/cardano-node@{DIGEST}",
        "image_digest": DIGEST,
        "executable_digest": None,
        "version_catalog_revision": "a" * 64,
    }
    body.update(updates)
    return body


def test_cardano_deployment_preview_builds_exact_stock_measurement_identity():
    profile = json.loads(Path("dwarf/profiles/profile-n-cardano-latest-confirmed/profile.yaml").read_text())
    preview = build_deployment_version_preview(profile)
    identity = build_measurement_target_identity(preview, implementation="cardano-node", mode="stock")
    assert identity["version"] == VERSION
    assert identity["source_revision"] == REVISION
    assert identity["image_digest"] == DIGEST
    assert identity["image_reference"].endswith("@" + DIGEST)


def test_cardano_implicit_default_resolves_only_proven_capabilities():
    resolution = resolve_measurements(
        _scenario(),
        target_identity=_identity(),
        capabilities={"dwarf-workload-events"},
    )
    assert resolution["profile"]["id"] == "cardano-security-default"
    assert resolution["profile"]["source"] == "implicit-default"
    assert [row["id"] for row in resolution["resolved"]] == [
        "cardano-external-workload-accounting"
    ]
    assert resolution["skipped"]
    assert resolution["incompatible"] == []


@pytest.mark.parametrize(
    ("measurement_id", "identity_updates", "capabilities", "message"),
    [
        ("cardano-stock-blockfetch", {"version": "10.7.1"}, {"cardano-new-tracing", "cardano-blockfetch-traces"}, "version"),
        ("cardano-stock-blockfetch", {"source_revision": "c" * 40}, {"cardano-new-tracing", "cardano-blockfetch-traces"}, "source revision"),
        ("cardano-patched-protocol-decode", {}, {"cardano-patched-protocol-decode"}, "target mode"),
        ("cardano-stock-blockfetch", {}, {"cardano-new-tracing"}, "missing capabilities"),
    ],
)
def test_cardano_explicit_incompatibility_fails_before_execution(
    measurement_id, identity_updates, capabilities, message
):
    scenario = _scenario(profile="none", measurements=[{"id": measurement_id, "enabled": True}])
    with pytest.raises(MeasurementResolutionError, match=message):
        resolve_measurements(
            scenario,
            target_identity=_identity(**identity_updates),
            capabilities=capabilities,
        )
