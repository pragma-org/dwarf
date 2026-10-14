import json
import copy
from pathlib import Path

import pytest

from profile_manager import dashboard
from profile_manager.data.operate_profiles import operate_profile_entries
from profile_manager.data.operate_versions import version_catalog_view
from profile_manager.data.operate_versions import version_default_summary
from profile_manager.data.operate_versions import _compatibility_matrix
from profile_manager.data.operate_versions import _human_date
from profile_manager.data.operate_versions import _human_timestamp
from profile_manager.data.operate_versions import _release_sort_key
from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    operator_registration_release,
)
from profile_manager.operator_version_decisions import (
    OperatorVersionDecisionLedger,
    preference_scope_key,
    subject_key,
)
from profile_manager.views.operate_versions import render_operate_versions


ROOT = Path(__file__).resolve().parents[1]


def test_versions_basic_renders_bounded_binary_import_and_body_token_polling(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_VERSION_IMPORTS_DIR", str(tmp_path / "imports"))

    html = render_operate_versions(token="synthetic-body-token")

    assert 'data-binary-import-section' in html
    assert 'action="/api/versions/import-binary"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'name="artifact"' in html
    assert 'name="token" value="synthetic-body-token"' in html
    assert 'data-status-endpoint="/api/versions/import-binary/status"' in html
    assert "new FormData(importForm)" in html
    assert "new URLSearchParams({token:importForm.elements.token.value" in html
    assert "/api/versions/import-binary?" not in html
    assert "does not prove topology compatibility" in html
    assert "mark it supported" in html


def _profiles(tmp_path: Path, monkeypatch):
    root = tmp_path / "profiles"
    for profile_id, body in {
        "profile-latest": {
            "id": "profile-latest",
            "label": "Latest Cardano",
            "node_type": "cardano-node",
            "node_count": 1,
            "amaru_node_count": 0,
            "network_magic": 42,
            "peer_sharing": False,
            "version_policy": "latest-stable",
        },
        "profile-mixed": {
            "id": "profile-mixed",
            "label": "Confirmed mixed",
            "node_type": "mixed",
            "node_count": 1,
            "amaru_node_count": 1,
            "network_magic": 42,
            "peer_sharing": False,
            "version_policy": "latest-confirmed",
        },
    }.items():
        directory = root / profile_id
        directory.mkdir(parents=True)
        (directory / "profile.yaml").write_text(json.dumps(body) + "\n", encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(root))
    from profile_manager import profiles
    from profile_manager.data import operate_profiles

    monkeypatch.setattr(profiles, "PROFILE_ROOT", root)
    monkeypatch.setattr(operate_profiles, "PROFILE_ROOT", root)
    return root


def test_version_catalog_view_separates_releases_pairs_defaults_and_evidence():
    view = version_catalog_view()

    cardano = next(row for row in view["releases"] if row["implementation"] == "cardano-node" and row["version"] == "11.1.2")
    pair = next(row for row in view["pairs"] if row["id"] == "cardano-11.1.2__amaru-10.11.20260918")
    assert cardano["artifact_digest"].startswith("sha256:")
    assert cardano["scope_statuses"]["cardano-only"]["status"] == "confirmed"
    assert cardano["scope_statuses"]["cardano-only"]["default"] is True
    assert pair["status"] == "confirmed"
    assert pair["default"] is True
    assert pair["evidence"]
    assert view["catalog_revision"]


def test_versions_human_view_separates_scoped_defaults_from_latest_discovered():
    view = version_catalog_view()

    assert [row["implementation"] for row in view["implementation_summaries"]] == [
        "amaru", "cardano-node"
    ]
    amaru, cardano = view["implementation_summaries"]
    assert amaru["default_release"]["version"] == "10.11.20260912"
    assert amaru["latest_release"]["version"] == "10.11.20260925"
    assert amaru["default_release"]["version"] != amaru["latest_release"]["version"]
    assert cardano["default_release"]["version"] == "11.1.2"
    assert cardano["latest_release"]["version"] == "11.1.3"
    assert amaru["scope"] == "amaru-only"
    assert cardano["scope"] == "cardano-only"
    assert all("released_human" in row and "released_exact" in row for row in view["releases"])

    scope_specific = next(
        row for row in view["releases"]
        if row["implementation"] == "cardano-node" and row["version"] == "10.7.1"
    )
    assert scope_specific["scope_statuses"]["cardano-only"]["status"] == "unknown"
    assert scope_specific["scope_statuses"]["mixed"]["status"] == "confirmed"


def test_versions_human_view_keeps_stale_source_state_and_missing_artifacts_honest(
    monkeypatch,
):
    from profile_manager import version_discovery
    from profile_manager.data import operate_versions

    catalog = copy.deepcopy(version_discovery.load_effective_version_catalog())
    release = next(
        row for row in catalog["releases"]
        if row["implementation"] == "amaru" and row["version"] == "10.11.20260925"
    )
    release["artifacts"] = [{"kind": "oci", "availability": "unknown"}]
    monkeypatch.setattr(operate_versions, "load_effective_version_catalog", lambda: catalog)
    monkeypatch.setattr(
        operate_versions,
        "read_refresh_status",
        lambda: {
            "schema_version": 1,
            "state": "error",
            "manual": False,
            "last_attempt_at": "2026-10-13T12:34:56Z",
            "last_success_at": "2026-10-11T09:00:56Z",
            "source_status": {"cardano-node": "error", "amaru": "not-checked"},
            "new_versions": [],
            "error": "GitHub returned HTTP 403",
        },
    )

    view = operate_versions.version_catalog_view()
    assert view["refresh_view"]["cached"] is True
    assert view["refresh_view"]["error"] == "GitHub returned HTTP 403"
    assert [(row["implementation"], row["status"]) for row in view["source_checks"]] == [
        ("amaru", "not-checked"), ("cardano-node", "error")
    ]
    latest_amaru = next(
        row for row in view["releases"]
        if row["implementation"] == "amaru" and row["version"] == "10.11.20260925"
    )
    assert latest_amaru["artifact_eligibility"] == "unknown"
    assert latest_amaru["artifact_platforms"] == []
    assert "installed" not in latest_amaru["artifact_eligibility_label"].lower()


def test_versions_human_view_projects_independent_source_receipts(monkeypatch):
    from profile_manager.data import operate_versions

    monkeypatch.setattr(
        operate_versions,
        "read_refresh_status",
        lambda: {
            "schema_version": 1,
            "state": "error",
            "last_attempt_at": "2026-10-13T13:00:00Z",
            "last_success_at": "2026-10-13T12:00:00Z",
            "source_status": {"amaru": "error", "cardano-node": "ok"},
            "new_versions": [],
            "error": "Amaru rate limited",
            "implementations": {
                "amaru": {
                    "state": "error",
                    "last_attempt_at": "2026-10-13T13:00:00Z",
                    "last_success_at": "2026-10-12T09:00:00Z",
                    "new_versions": [],
                    "error": "Amaru rate limited",
                },
                "cardano-node": {
                    "state": "success",
                    "last_attempt_at": "2026-10-13T12:00:00Z",
                    "last_success_at": "2026-10-13T12:00:00Z",
                    "new_versions": [],
                    "error": None,
                },
            },
        },
    )

    amaru, cardano = operate_versions.version_catalog_view()["implementation_summaries"]

    assert amaru["refresh_receipt"]["state"] == "error"
    assert amaru["refresh_receipt"]["error"] == "Amaru rate limited"
    assert amaru["refresh_receipt"]["last_success_human"] == "Oct 12, 2026 · 09:00 UTC"
    assert cardano["refresh_receipt"]["state"] == "success"
    assert cardano["refresh_receipt"]["last_attempt_human"] == "Oct 13, 2026 · 12:00 UTC"


def test_versions_compatibility_matrix_uses_only_recorded_pairs_and_unknown_absence():
    view = version_catalog_view()
    matrix = view["compatibility_matrix"]
    cells = [cell for row in matrix["rows"] for cell in row["cells"]]

    recorded = [cell for cell in cells if cell["recorded"]]
    absent = [cell for cell in cells if not cell["recorded"]]
    assert len(recorded) == len(view["pairs"])
    assert {cell["pair_id"] for cell in recorded} == {row["id"] for row in view["pairs"]}
    assert {cell["status"] for cell in recorded} >= {"confirmed", "incompatible"}
    assert absent and all(cell["status"] == "not-checked" for cell in absent)
    assert all(cell["pair_id"] is None and cell["evidence"] == [] for cell in absent)
    assert all(cell["evidence"] for cell in recorded if cell["status"] in {"confirmed", "incompatible"})


def test_versions_human_timestamp_normalizes_offset_before_labeling_utc():
    assert _human_timestamp("2026-10-13T14:34:56+02:00") == "Oct 13, 2026 · 12:34 UTC"


def test_versions_release_date_and_latest_order_use_utc_not_raw_offset_text():
    later = {"version": "offset-later", "released_at": "2026-10-13T23:30:00-02:00"}
    earlier = {"version": "raw-date-later", "released_at": "2026-10-14T00:15:00+00:00"}

    assert _human_date(later["released_at"]) == "Oct 14, 2026"
    assert max([earlier, later], key=_release_sort_key) is later


def test_versions_compatibility_matrix_preserves_multiple_records_for_same_axes():
    pairs = [
        {
            "id": "pair-one",
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260918",
            "status": "confirmed",
            "scope": "mixed-a",
            "evidence": ["run-one"],
        },
        {
            "id": "pair-two",
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260918",
            "status": "incompatible",
            "scope": "mixed-b",
            "evidence": ["run-two"],
        },
    ]

    matrix = _compatibility_matrix(pairs)
    cell = matrix["rows"][0]["cells"][0]
    assert cell["recorded"] is True
    assert cell["recorded_count"] == 2
    assert [record["pair_id"] for record in cell["records"]] == ["pair-one", "pair-two"]
    assert [record["status"] for record in cell["records"]] == ["confirmed", "incompatible"]


def test_versions_initial_release_order_stably_partitions_verified_artifacts_first():
    from profile_manager.version_discovery import load_effective_version_catalog

    view = version_catalog_view()
    catalog_ids = [
        f"{release['implementation']}:{release['version']}"
        for release in load_effective_version_catalog()["releases"]
    ]
    eligibility = {
        release["release_id"]: release["artifact_eligibility"]
        for release in view["releases"]
    }
    expected = [
        release_id
        for release_id in catalog_ids
        if eligibility[release_id] == "public-platform-eligible"
    ] + [
        release_id
        for release_id in catalog_ids
        if eligibility[release_id] != "public-platform-eligible"
    ]

    assert [release["release_id"] for release in view["releases"]] == expected


def test_versions_human_view_handles_missing_implementation_and_no_default(monkeypatch):
    from profile_manager import version_discovery
    from profile_manager.data import operate_versions

    catalog = copy.deepcopy(version_discovery.load_effective_version_catalog())
    catalog["releases"] = [
        row for row in catalog["releases"] if row["implementation"] != "amaru"
    ]
    for release in catalog["releases"]:
        for verification in release.get("verification", {}).values():
            verification["default"] = False
    monkeypatch.setattr(operate_versions, "load_effective_version_catalog", lambda: catalog)

    view = operate_versions.version_catalog_view()
    amaru, cardano = view["implementation_summaries"]
    assert amaru["latest_release"] is None
    assert amaru["default_release"] is None
    assert amaru["default_state"] == "No catalog release"
    assert cardano["latest_release"] is not None
    assert cardano["default_release"] is None
    assert cardano["default_state"] == "No qualified default"


def test_versions_never_refreshed_is_not_described_as_cached_or_failed(monkeypatch):
    from profile_manager.data import operate_versions

    monkeypatch.setattr(
        operate_versions,
        "read_refresh_status",
        lambda: {
            "schema_version": 1,
            "state": "never",
            "last_attempt_at": None,
            "last_success_at": None,
            "source_status": {"cardano-node": "not-checked", "amaru": "not-checked"},
            "new_versions": [],
            "error": None,
        },
    )

    refresh = operate_versions.version_catalog_view()["refresh_view"]
    assert refresh["cached"] is False
    assert refresh["never"] is True
    assert refresh["status_message"] == "Discovery has not been checked yet."


def test_versions_route_is_filterable_and_explains_claim_boundaries():
    html = dashboard.render_route_html("/operate/versions", token="PRIVATE_REFRESH_TOKEN")
    view = version_catalog_view()

    assert html is not None
    assert "Node versions" in html
    assert 'data-version-filter' in html
    assert "11.1.2" in html
    assert "cardano-10.7.1__amaru-10.11.0" in html
    assert "stable is not the same as confirmed" in html.lower()
    assert "sha256:6365403f" in html
    assert html.count(">Fetch Latest Version</button>") == 2
    assert "Check for new versions" not in html
    assert "Last successful fetch" in html
    assert "Amaru target" in html
    assert "supporting Cardano-node" in html
    assert "Qualification reason" in html
    assert "Evidence" in html
    assert "Related issues" in html
    assert 'data-endpoint="/api/versions/refresh"' in html
    assert 'data-version-refresh data-implementation="amaru"' in html
    assert 'data-version-refresh data-implementation="cardano-node"' in html
    assert 'data-version-refresh-token value="PRIVATE_REFRESH_TOKEN"' in html
    assert "/api/versions/refresh?token=" not in html
    assert "Artifacts, source revisions and per-scope evidence in Advanced" not in html
    assert "Exact source revision" in html
    assert "Artifacts" in html
    assert "Qualification reason" in html
    assert "Local operator decisions" in html
    assert "Register an already-present local image" in html
    assert "Older runs without immutable version evidence cannot be attested" in html
    assert 'href="/operate/profiles/new"' in html
    assert 'href="/run"' in html
    assert 'action="/api/versions/register"' in html
    assert "oci-derived" in html
    assert "sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c" in html
    assert "sha256:45857be8d86b314a05cd46d310b74b24e5f5870469f90c6464994a7f78142271" in html
    assert html.count("<tr data-version-release-row") == len(view["releases"])
    assert html.count('data-pair-id="') == len(view["pairs"])
    assert html.count('data-version-pair-group') == len(
        view["compatibility_matrix"]["rows"]
    )
    assert html.count('data-version-pair-card') == sum(
        len(row["cells"]) for row in view["compatibility_matrix"]["rows"]
    )
    assert '<table class="version-matrix"' not in html
    assert "No recorded proof" not in html
    assert "Not checked" in html
    assert "Exact Cardano version" in html
    assert "Exact Amaru version" in html
    assert 'data-version-implementation-pill>Amaru</span>' in html
    assert 'data-version-implementation-pill>Cardano</span>' in html
    assert 'version-identity-pill--amaru' in html
    assert 'version-identity-pill--cardano' in html
    assert html.count('data-version-value-pill') == len(view["releases"])
    assert '>Cardano-node</strong>' not in html
    assert 'Not a catalog default' not in html
    assert html.count('data-version-default="no">No</span>') > 0
    assert html.count('data-version-detail-row') == len(view["releases"])
    assert html.count('colspan="6"') >= len(view["releases"])
    assert html.count('data-version-sort="') == 6
    assert html.count('aria-sort="none"') == 6
    assert 'data-version-default="yes"' in html
    assert 'version-status--default' in html
    assert 'version-status--neutral' in html
    assert 'class="version-table no-responsive-table"' in html


def test_versions_identity_recorded_eligibility_is_short_but_keeps_full_explanation():
    view = version_catalog_view()
    identity_recorded = next(
        release
        for release in view["releases"]
        if release["artifact_eligibility"] == "identity-recorded"
    )

    assert identity_recorded["artifact_eligibility_label"] == (
        "Immutable identity recorded · public/platform eligibility unverified"
    )
    assert identity_recorded["artifact_eligibility_display_label"] == "Unverified"

    html = dashboard.render_route_html("/operate/versions", token="PRIVATE_REFRESH_TOKEN")
    assert html is not None
    assert '>Unverified</span>' in html
    assert (
        'title="Immutable identity recorded · public/platform eligibility unverified"'
        in html
    )
    assert (
        'aria-label="Artifact eligibility: Immutable identity recorded · public/platform eligibility unverified"'
        in html
    )

    unknown = next(
        release
        for release in view["releases"]
        if release["artifact_eligibility"] == "unknown"
    )
    assert unknown["artifact_eligibility_label"] == "Artifact eligibility unknown"
    assert unknown["artifact_eligibility_display_label"] == "Unknown"
    assert 'title="Artifact eligibility unknown"' in html
    assert 'aria-label="Artifact eligibility: Artifact eligibility unknown"' in html

    verified = next(
        release
        for release in view["releases"]
        if release["artifact_eligibility"] == "public-platform-eligible"
    )
    assert verified["artifact_eligibility_label"].startswith(
        "Public immutable artifact · "
    )
    assert verified["artifact_eligibility_display_label"] == "Verified"
    assert f'title="{verified["artifact_eligibility_label"]}"' in html
    assert (
        f'aria-label="Artifact eligibility: {verified["artifact_eligibility_label"]}"'
        in html
    )
    assert '>Verified</span>' in html


def test_versions_release_pills_retain_exact_identity_for_accessibility():
    html = dashboard.render_route_html("/operate/versions", token="PRIVATE_REFRESH_TOKEN")
    assert html is not None
    exact_version = "v10.11.20260912-30-gd3a6dafc"
    assert (
        f'data-version-value-pill title="{exact_version}" '
        f'aria-label="Release version: {exact_version}">{exact_version}</span>'
        in html
    )
    assert (
        f'<dt>Exact release version</dt><dd><code>{exact_version}</code></dd>'
        in html
    )


def _supported_decision():
    subject = {
        "implementation": "amaru",
        "version": "10.11.20260912",
        "image_reference": "ghcr.io/pragma-org/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "config_image_id": "sha256:" + "b" * 64,
        "scenario_id": "portable-stock",
        "scenario_sha256": "c" * 64,
        "scope": "amaru-only",
        "topology_profile_id": "profile-r",
        "deployment_identity_sha256": "d" * 64,
        "mixed_pair": None,
    }
    return {
        "decision_id": "decision-one",
        "subject_key": subject_key(subject),
        "subject": subject,
        "decision": "supported",
        "reason": "Operator reviewed the retained run.",
        "actor": "local-operator",
        "decided_at": "2026-10-11T12:00:00Z",
        "evidence": {
            "run_id": "20261011T120000Z-example",
            "manifest_sha256": "e" * 64,
            "operational_outcome": "completed",
            "assertion_verdict": "pass",
        },
    }


def test_basic_versions_renders_local_history_exact_pins_and_clean_preference_forms(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    ledger = OperatorVersionDecisionLedger(tmp_path)
    state = ledger.read()
    registration = {
        "registration_id": "local-one",
        "implementation": "amaru",
        "version": "10.12-local",
        "image_reference": "local/amaru@sha256:" + "f" * 64,
        "manifest_digest": "sha256:" + "f" * 64,
        "config_image_id": "sha256:" + "1" * 64,
        "platform": "linux/amd64",
        "controller_installation_id": "install-browser-proof",
        "inspected_at": "2026-10-11T11:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    state = ledger.append_registration(registration, expected_revision=state["revision"])
    decision = _supported_decision()
    ledger.append_decision(decision, expected_revision=state["revision"])

    html = dashboard.render_route_html("/operate/versions", token="PRIVATE_FORM_TOKEN")

    assert "Local operator decisions" in html
    assert "local-one" in html and "sha256:" + "1" * 64 in html
    assert "Inspected by installation" in html
    assert "install-browser-proof" in html
    assert decision["evidence"]["run_id"] in html
    assert decision["reason"] in html
    assert decision["subject"]["manifest_digest"] in html
    assert 'action="/api/versions/preferences"' in html
    assert 'name="decision_id" value="decision-one"' in html
    assert 'name="expected_revision"' in html
    assert 'action="/api/versions/register"' in html
    assert 'name="image"' in html
    assert 'name="config_image_id"' not in html
    assert 'name="public"' not in html
    assert "?token=" not in html


def test_historical_supported_decision_remains_visible_but_cannot_be_preferred(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    ledger = OperatorVersionDecisionLedger(tmp_path)
    state = ledger.read()
    supported = _supported_decision()
    state = ledger.append_decision(supported, expected_revision=state["revision"])
    unsupported = _supported_decision()
    unsupported.update({
        "decision_id": "decision-later-unsupported",
        "decision": "unsupported",
        "reason": "A later retained run changed the local judgment.",
        "decided_at": "2026-10-11T13:00:00Z",
    })
    ledger.append_decision(unsupported, expected_revision=state["revision"])

    html = dashboard.render_route_html("/operate/versions", token="PRIVATE_FORM_TOKEN")

    assert supported["reason"] in html
    assert unsupported["reason"] in html
    assert 'name="decision_id" value="decision-one"' not in html
    assert 'name="decision_id" value="decision-later-unsupported"' not in html


def test_preference_endpoint_uses_body_token_lock_revision_and_clean_redirect(
    tmp_path
):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    state = ledger.read()
    decision = _supported_decision()
    state = ledger.append_decision(decision, expected_revision=state["revision"])
    body = (
        "token=right&action=set&decision_id=decision-one&expected_revision="
        + state["revision"]
    ).encode()

    denied = dashboard.dispatch_version_preference_request(
        method="POST", path="/api/versions/preferences?token=right",
        body=body.replace(b"token=right", b"token=wrong"), expected_token="right",
        ledger=ledger,
    )
    accepted = dashboard.dispatch_version_preference_request(
        method="POST", path="/api/versions/preferences", body=body,
        expected_token="right", ledger=ledger,
    )
    stale = dashboard.dispatch_version_preference_request(
        method="POST", path="/api/versions/preferences", body=body,
        expected_token="right", ledger=ledger,
    )

    assert denied[0] == 401
    assert accepted[:3] == (303, "text/plain; charset=utf-8", b"")
    assert accepted[3] == {"Location": "/operate/versions"}
    assert stale[0] == 409
    persisted = ledger.read()
    assert len(persisted["preferred_defaults"]) == 1
    assert persisted["preferred_defaults"][0]["scope_key"] == preference_scope_key(
        decision["subject"]
    )


def test_preference_endpoint_refuses_unsupported_or_missing_decision_without_write(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    before = ledger.read()
    for decision_id in ("missing", "unsupported"):
        state = ledger.read()
        if decision_id == "unsupported":
            event = _supported_decision()
            event["decision_id"] = "unsupported"
            event["decision"] = "unsupported"
            state = ledger.append_decision(event, expected_revision=state["revision"])
        body = (
            "token=right&action=set&decision_id=" + decision_id
            + "&expected_revision=" + state["revision"]
        ).encode()
        result = dashboard.dispatch_version_preference_request(
            method="POST", path="/api/versions/preferences", body=body,
            expected_token="right", ledger=ledger,
        )
        assert result[0] == 409
        assert ledger.read()["preferred_defaults"] == []

    assert before["preferred_defaults"] == []


def test_registration_endpoint_persists_only_controller_inspected_identity(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    before = ledger.read()
    calls = []
    manifest = "sha256:" + "a" * 64
    config_id = "sha256:" + "b" * 64

    def inspect(operation, parameters, timeout):
        calls.append((operation, parameters, timeout))
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "version": "10.12.0-local",
            "requested_image": "local/amaru:experiment",
            "config_image_id": config_id,
            "manifest_references": ["local/amaru@" + manifest],
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
            "declared_source_revision": "c" * 40,
            "provenance": {
                "source_revision": "unverified",
                "public": None,
                "qualification": "unverified",
            },
            "installation_id": "install-test",
            "inspected_at": "2026-10-11T12:00:00Z",
        }

    body = (
        "token=right&expected_revision=" + before["revision"]
        + "&implementation=amaru&version=10.12.0-local"
        + "&image=local%2Famaru%3Aexperiment&declared_source_revision=" + "c" * 40
    ).encode()
    result = dashboard.dispatch_version_registration_request(
        method="POST", path="/api/versions/register", body=body,
        expected_token="right", ledger=ledger, request_controller=inspect,
    )

    assert result[:3] == (303, "text/plain; charset=utf-8", b"")
    assert result[3] == {"Location": "/operate/versions"}
    assert calls == [("image.inspect", {
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image": "local/amaru:experiment",
        "declared_source_revision": "c" * 40,
    }, 20)]
    registration = ledger.read()["registrations"][0]
    assert registration["image_reference"] == "local/amaru@" + manifest
    assert registration["manifest_digest"] == manifest
    assert registration["config_image_id"] == config_id
    assert registration["platform"] == "linux/amd64"
    assert registration["runtime_interface"] == "extracted-binary"
    assert registration["controller_installation_id"] == "install-test"
    assert registration["provenance"] == {
        "source_revision": "c" * 40,
        "public": None,
        "qualification": "unverified",
    }
    assert "experiment" not in registration["image_reference"]


def test_registration_preserves_unknown_interface_without_claiming_usability(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    before = ledger.read()
    manifest = "sha256:" + "a" * 64
    config_id = "sha256:" + "b" * 64

    def inspect(*_args):
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "version": "10.12.0-local",
            "requested_image": "local/amaru:experiment",
            "config_image_id": config_id,
            "manifest_references": ["local/amaru@" + manifest],
            "platform": "linux/amd64",
            "runtime_interface": None,
            "declared_source_revision": None,
            "provenance": {
                "source_revision": "unverified",
                "public": None,
                "qualification": "unverified",
            },
            "installation_id": "install-test",
            "inspected_at": "2026-10-11T12:00:00Z",
        }

    body = (
        "token=right&expected_revision=" + before["revision"]
        + "&implementation=amaru&version=10.12.0-local"
        + "&image=local%2Famaru%3Aexperiment"
    ).encode()
    result = dashboard.dispatch_version_registration_request(
        method="POST", path="/api/versions/register", body=body,
        expected_token="right", ledger=ledger, request_controller=inspect,
    )

    assert result[0] == 303
    registration = ledger.read()["registrations"][0]
    assert registration["runtime_interface"] is None
    with pytest.raises(
        DeploymentVersionGateError,
        match="runtime interface is unsupported or unverified",
    ):
        operator_registration_release(registration)


def test_registration_refuses_browser_identity_claims_stale_state_and_bad_inspection(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    revision = ledger.read()["revision"]
    calls = []
    base = (
        "token=right&expected_revision=" + revision
        + "&implementation=amaru&version=10.12.0-local&image=local%2Famaru%3Aexperiment"
    )
    browser_claim = dashboard.dispatch_version_registration_request(
        method="POST", path="/api/versions/register",
        body=(base + "&config_image_id=sha256%3A" + "f" * 64).encode(),
        expected_token="right", ledger=ledger,
        request_controller=lambda *args: calls.append(args),
    )
    stale = dashboard.dispatch_version_registration_request(
        method="POST", path="/api/versions/register",
        body=base.replace(revision, "sha256:" + "0" * 64).encode(),
        expected_token="right", ledger=ledger,
        request_controller=lambda *args: calls.append(args),
    )

    def malformed(*_args):
        return {"schema_version": "v1", "config_image_id": "not-a-digest"}

    bad = dashboard.dispatch_version_registration_request(
        method="POST", path="/api/versions/register", body=base.encode(),
        expected_token="right", ledger=ledger, request_controller=malformed,
    )

    assert browser_claim[0] == stale[0] == bad[0] == 409
    assert calls == []
    assert ledger.read()["registrations"] == []


def test_operate_and_learn_landings_link_to_version_qualification():
    operate = dashboard.render_route_html("/operate")
    learn = dashboard.render_route_html("/learn")

    assert operate is not None
    assert 'href="/operate/versions"' in operate
    assert "Node versions" in operate
    assert "Cardano-node 11.1.2" in operate
    assert "Amaru 10.11.20260912" in operate
    assert "Mixed 11.1.2 + 10.11.20260918" in operate

    assert learn is not None
    assert 'href="/learn/versions"' in learn
    assert "Version-qualified devnets" in learn
    assert "latest-confirmed" in learn


def test_manual_version_refresh_is_token_gated_and_serialized(monkeypatch):
    calls = []

    def start(*, implementation, manual):
        calls.append((implementation, manual))
        return {"started": True, "state": "running"}

    monkeypatch.setattr("profile_manager.version_discovery.start_release_refresh", start)

    denied = dashboard.dispatch_version_refresh_request(
        method="POST", path="/api/versions/refresh?token=wrong", expected_token="right"
    )
    accepted = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=right&implementation=amaru",
        expected_token="right",
    )
    accepted_cardano = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=right&implementation=cardano-node",
        expected_token="right",
    )
    missing_implementation = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=right",
        expected_token="right",
    )
    unknown_implementation = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=right&implementation=other",
        expected_token="right",
    )
    bad_body_does_not_fall_back_to_query = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh?token=right",
        body=b"token=wrong",
        expected_token="right",
    )
    non_ascii_body_is_rejected = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=%C3%A9",
        expected_token="right",
    )
    malformed_body_is_rejected = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh",
        body=b"token=%FF",
        expected_token="right",
    )
    non_ascii_query_fallback_is_rejected = dashboard.dispatch_version_refresh_request(
        method="POST",
        path="/api/versions/refresh?token=%C3%A9",
        expected_token="right",
    )

    assert denied[0] == 401
    assert accepted[0] == 202
    assert accepted_cardano[0] == 202
    assert missing_implementation[0] == 409
    assert unknown_implementation[0] == 409
    assert bad_body_does_not_fall_back_to_query[0] == 401
    assert non_ascii_body_is_rejected[0] == 401
    assert malformed_body_is_rejected[0] == 401
    assert non_ascii_query_fallback_is_rejected[0] == 401
    assert json.loads(accepted[2])["state"] == "running"
    assert calls == [("amaru", True), ("cardano-node", True)]


def test_profile_builder_uses_catalog_backed_release_and_pair_selectors():
    html = dashboard.render_route_html("/operate/profiles/new?template=mixed-minimal")

    assert 'data-field="version_policy"' in html
    assert 'data-field="cardano_version"' in html
    assert '<option value="11.1.2"' in html
    assert 'data-field="amaru_version"' in html
    assert '<option value="10.11.20260912"' in html
    assert 'data-field="compatibility_pair"' in html
    assert '<option value="cardano-10.7.1__amaru-10.11.0"' in html
    assert "/operate/versions" in html


def test_profile_catalog_shows_policy_status_exact_versions_and_preview_first(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)

    entries = operate_profile_entries()
    html = dashboard.render_route_html("/operate/profiles")
    latest = next(entry for entry in entries if entry["id"] == "profile-latest")
    mixed = next(entry for entry in entries if entry["id"] == "profile-mixed")

    assert latest["version_status"] == "unknown"
    assert latest["version_summary"] == "cardano-node 11.1.3"
    assert latest["version_requires_acknowledgement"] is True
    assert mixed["version_status"] == "confirmed"
    assert "11.1.2" in mixed["version_summary"]
    assert "10.11.20260918" in mixed["version_summary"]
    assert "latest-stable" in html
    assert "confirmed" in html
    assert "Preview deploy" in html
    assert "/api/deploy/preview" in html
    assert "Deploy resolved version" in html


def test_current_catalog_defaults_remain_exact_and_distinct_from_local_preferences():
    assert version_default_summary() == {
        "cardano": "11.1.2",
        "amaru": "10.11.20260912",
        "amaru_support": "10.7.1",
        "mixed_cardano": "11.1.2",
        "mixed_amaru": "10.11.20260918",
    }


def test_profile_detail_includes_version_intent(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/profiles/profile-mixed")

    assert "version policy" in html.lower()
    assert "latest-confirmed" in html


def test_versions_navigation_and_responsive_contract_are_present():
    from profile_manager.data.sub_nav import OPERATE_SUB_NAV

    css = (ROOT / "dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")
    assert any(item["url"] == "/operate/versions" for item in OPERATE_SUB_NAV)
    assert ".version-catalog" in css
    assert ".version-refresh" in css
    assert ".version-card__evidence" in css
    assert "@media (max-width: 700px)" in css or "@media (max-width: 640px)" in css
