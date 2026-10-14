import json
import threading
from datetime import datetime, timezone

import pytest

from profile_manager.version_discovery import (
    load_operator_registration,
    load_operator_registrations,
    load_effective_version_catalog,
    merge_discovery_candidate,
    perform_release_refresh,
    read_refresh_status,
    refresh_is_stale,
    start_release_refresh,
)
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger


def _release(implementation, version, revision, *, digest=None, status="unknown", default=False):
    scope = "cardano-only" if implementation == "cardano-node" else "amaru-only"
    artifacts = []
    if digest:
        artifacts.append({
            "kind": "oci",
            "reference": f"ghcr.io/example/{implementation}:{version}",
            "availability": "available",
            "digest": digest,
        })
    else:
        artifacts.append({
            "kind": "oci",
            "reference": f"ghcr.io/example/{implementation}:{version}",
            "availability": "unknown",
        })
    verification = {
        scope: {
            "status": status,
            "default": default,
            "checked_at": "2026-09-18T00:00:00Z",
            "reason": "retained" if status != "unknown" else "unqualified",
            "evidence": ["run:retained"] if status == "confirmed" else [],
            "issues": [],
        },
        "mixed": {
            "status": "unknown",
            "default": False,
            "checked_at": "2026-09-18T00:00:00Z",
            "reason": "unqualified",
            "evidence": [],
            "issues": [],
        },
    }
    if implementation == "amaru" and status == "confirmed":
        verification[scope]["supporting_cardano_version"] = "11.1.2"
    return {
        "implementation": implementation,
        "version": version,
        "channel": "stable",
        "released_at": "2026-09-18T00:00:00Z",
        "source_revision": revision,
        "artifacts": artifacts,
        "verification": verification,
    }


def _catalog(releases):
    return {
        "schema_version": 1,
        "updated_at": "2026-09-18T00:00:00Z",
        "sources": {},
        "releases": releases,
        "compatibility_pairs": [],
    }


def _local_registration(identifier="local-amaru-1"):
    return {
        "registration_id": identifier,
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "local/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }


def test_operator_registrations_remain_separate_from_shared_catalog(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path)
    empty = ledger.read()
    registration = _local_registration()
    state = ledger.append_registration(
        registration, expected_revision=empty["revision"]
    )
    source = _catalog([
        _release(
            "amaru", "10.11.20260912", "1" * 40,
            digest="sha256:" + "c" * 64, status="confirmed", default=True,
        )
    ])

    overlay = load_operator_registrations(tmp_path)
    selected = load_operator_registration(registration["registration_id"], tmp_path)

    assert overlay == {
        "revision": state["revision"],
        "registrations": [registration],
    }
    assert selected == {"revision": state["revision"], "registration": registration}
    assert source["releases"] == [_release(
        "amaru", "10.11.20260912", "1" * 40,
        digest="sha256:" + "c" * 64, status="confirmed", default=True,
    )]
    with pytest.raises(ValueError, match="safe"):
        load_operator_registration("../escape", tmp_path)


def test_discovery_merge_adds_only_fully_identified_unknown_releases():
    digest = "sha256:" + "a" * 64
    source = _catalog([
        _release("cardano-node", "11.1.2", "1" * 40, digest=digest, status="confirmed", default=True),
    ])
    candidate = _catalog([
        _release("cardano-node", "11.1.2", "9" * 40, digest="sha256:" + "9" * 64),
        _release("cardano-node", "11.1.3", "2" * 40, digest="sha256:" + "2" * 64),
        _release("amaru", "10.12.0", "3" * 40),
    ])

    merged, discovered = merge_discovery_candidate(source, candidate)

    retained = next(item for item in merged["releases"] if item["version"] == "11.1.2")
    added = next(item for item in merged["releases"] if item["version"] == "11.1.3")
    assert retained["source_revision"] == "1" * 40
    assert retained["verification"] == source["releases"][0]["verification"]
    assert retained["artifacts"] == source["releases"][0]["artifacts"]
    assert added["verification"]["cardano-only"]["status"] == "unknown"
    assert added["verification"]["cardano-only"]["default"] is False
    assert not any(item["version"] == "10.12.0" for item in merged["releases"])
    assert discovered == [{"implementation": "cardano-node", "version": "11.1.3"}]


def test_effective_merge_refuses_unavailable_exact_platform_claims():
    digest = "sha256:" + "a" * 64
    source_release = _release(
        "cardano-node", "11.1.2", "1" * 40,
        digest=digest, status="confirmed", default=True,
    )
    source = _catalog([source_release])

    for availability in ("unknown", "unavailable"):
        candidate_release = _release(
            "cardano-node", "11.1.2", "1" * 40,
            digest=digest, status="confirmed", default=True,
        )
        candidate_release["artifacts"][0].update({
            "availability": availability,
            "public": True,
            "platforms": ["linux/amd64", "linux/arm64"],
        })
        candidate = _catalog([candidate_release])

        merged, discovered = merge_discovery_candidate(source, candidate)

        assert discovered == []
        assert merged["releases"][0]["source_revision"] == "1" * 40
        assert merged["releases"][0]["verification"] == source_release["verification"]
        assert merged["releases"][0]["artifacts"] == source_release["artifacts"]


def test_effective_catalog_uses_dedicated_shared_catalog_state(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "catalog.json"
    source = _catalog([
        _release(
            "cardano-node",
            "11.1.2",
            "1" * 40,
            digest="sha256:" + "a" * 64,
            status="confirmed",
            default=True,
        ),
    ])
    candidate = _catalog([
        _release(
            "cardano-node",
            "11.1.3",
            "2" * 40,
            digest="sha256:" + "b" * 64,
        ),
    ])
    source_path.write_text(json.dumps(source), encoding="utf-8")
    shared_state = tmp_path / "shared-state"
    candidate_root = shared_state / "version-catalog"
    candidate_root.mkdir(parents=True)
    (candidate_root / "discovery-candidate.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )
    monkeypatch.setenv(
        "ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(shared_state)
    )

    effective = load_effective_version_catalog(source_path)

    assert any(row["version"] == "11.1.3" for row in effective["releases"])


def test_refresh_enrichment_reaches_effective_default_without_changing_identity(tmp_path):
    source_path = tmp_path / "catalog.json"
    digest = "sha256:" + "a" * 64
    source_release = _release(
        "amaru",
        "10.11.20260912",
        "1" * 40,
        digest=digest,
        status="confirmed",
        default=True,
    )
    source_release["artifacts"][0]["reference"] = (
        "ghcr.io/pragma-org/amaru:v10.11.20260912"
    )
    support_release = _release(
        "cardano-node",
        "11.1.2",
        "2" * 40,
        digest="sha256:" + "b" * 64,
        status="confirmed",
        default=True,
    )
    support_release["artifacts"][0].update({
        "public": True,
        "platforms": ["linux/amd64", "linux/arm64"],
    })
    source = _catalog([source_release, support_release])
    source_path.write_text(json.dumps(source), encoding="utf-8")
    state_dir = tmp_path / "state"

    def fetch(url):
        if "cardano-node/releases" in url:
            return []
        if "pragma-org/amaru/releases" in url:
            return [{
                "tag_name": "v10.11.20260912",
                "published_at": "2026-09-12T15:30:25Z",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/pragma-org/amaru/releases/tag/v10.11.20260912",
            }]
        raise AssertionError(url)

    def resolve(implementation, tag, release):
        assert (implementation, tag) == ("amaru", "v10.11.20260912")
        return [{
            "kind": "oci",
            "reference": "ghcr.io/pragma-org/amaru:v10.11.20260912",
            "availability": "available",
            "digest": digest,
            "public": True,
            "platforms": ["linux/amd64", "linux/arm64"],
        }]

    status = perform_release_refresh(
        state_dir=state_dir,
        source_path=source_path,
        fetch_json=fetch,
        artifact_resolver=resolve,
        checked_at="2026-10-11T10:00:00Z",
        manual=True,
    )
    effective = load_effective_version_catalog(source_path, state_dir=state_dir)
    retained = next(
        row for row in effective["releases"]
        if row["implementation"] == "amaru"
    )

    assert status["state"] == "success"
    assert retained["source_revision"] == "1" * 40
    assert retained["verification"] == source_release["verification"]
    assert retained["artifacts"][0]["digest"] == digest
    assert retained["artifacts"][0]["public"] is True
    assert retained["artifacts"][0]["platforms"] == [
        "linux/amd64",
        "linux/arm64",
    ]
    persisted_source = json.loads(source_path.read_text())
    persisted_amaru = next(
        row for row in persisted_source["releases"]
        if row["implementation"] == "amaru"
    )
    assert "public" not in persisted_amaru["artifacts"][0]


def test_refresh_status_is_missing_then_stale_by_age(tmp_path):
    assert read_refresh_status(tmp_path)["state"] == "never"
    status = {
        "schema_version": 1,
        "state": "success",
        "last_attempt_at": "2026-09-18T00:00:00Z",
        "last_success_at": "2026-09-18T00:00:00Z",
        "source_status": {"cardano-node": "ok", "amaru": "ok"},
        "new_versions": [],
        "error": None,
    }
    state_root = tmp_path / "version-catalog"
    state_root.mkdir()
    (state_root / "refresh-status.json").write_text(json.dumps(status), encoding="utf-8")

    assert refresh_is_stale(
        read_refresh_status(tmp_path),
        max_age_seconds=3600,
        now=datetime(2026, 9, 18, 0, 30, tzinfo=timezone.utc),
    ) is False
    assert refresh_is_stale(
        read_refresh_status(tmp_path),
        max_age_seconds=3600,
        now=datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc),
    ) is True


def test_refresh_failure_is_retained_per_source_without_breaking_catalog(tmp_path):
    source_path = tmp_path / "catalog.json"
    source_path.write_text(json.dumps(_catalog([
        _release(
            "cardano-node", "11.1.2", "1" * 40,
            digest="sha256:" + "a" * 64, status="confirmed", default=True,
        ),
    ])), encoding="utf-8")

    def failing_fetch(url):
        if "cardano-node" in url:
            raise RuntimeError("rate limited")
        return []

    status = perform_release_refresh(
        state_dir=tmp_path / "state",
        source_path=source_path,
        fetch_json=failing_fetch,
        artifact_resolver=lambda *_: [],
        tag_revision_resolver=lambda *_: "2" * 40,
        checked_at="2026-09-18T03:00:00Z",
        manual=True,
    )

    assert status["state"] == "error"
    assert status["source_status"]["cardano-node"] == "error"
    assert status["source_status"]["amaru"] == "not-checked"
    assert "rate limited" in status["error"]
    assert not (tmp_path / "state/version-catalog/discovery-candidate.json").exists()


@pytest.mark.parametrize(
    ("implementation", "expected_repository", "unexpected_repository"),
    [
        ("amaru", "pragma-org/amaru", "IntersectMBO/cardano-node"),
        ("cardano-node", "IntersectMBO/cardano-node", "pragma-org/amaru"),
    ],
)
def test_manual_refresh_contacts_only_selected_implementation(
    tmp_path, implementation, expected_repository, unexpected_repository
):
    source_path = tmp_path / "catalog.json"
    source_path.write_text(
        json.dumps(
            _catalog(
                [
                    _release(
                        "amaru", "10.11.20260912", "1" * 40,
                        digest="sha256:" + "a" * 64,
                        status="confirmed", default=True,
                    ),
                    _release(
                        "cardano-node", "11.1.2", "2" * 40,
                        digest="sha256:" + "b" * 64,
                        status="confirmed", default=True,
                    ),
                ]
            )
        ),
        encoding="utf-8",
    )
    urls = []

    def fetch(url):
        urls.append(url)
        return []

    status = perform_release_refresh(
        implementation=implementation,
        state_dir=tmp_path / "state",
        source_path=source_path,
        fetch_json=fetch,
        artifact_resolver=lambda *_: [],
        checked_at="2026-10-13T12:00:00Z",
        manual=True,
    )

    assert status["state"] == "success"
    assert any(expected_repository in url for url in urls)
    assert all(unexpected_repository not in url for url in urls)
    assert status["implementations"][implementation]["last_success_at"] == (
        "2026-10-13T12:00:00Z"
    )


def test_alternating_source_refreshes_preserve_both_discoveries_and_evidence(tmp_path):
    source_path = tmp_path / "catalog.json"
    source = _catalog(
        [
            _release(
                "amaru", "10.11.20260912", "1" * 40,
                digest="sha256:" + "a" * 64, status="confirmed", default=True,
            ),
            _release(
                "cardano-node", "11.1.2", "2" * 40,
                digest="sha256:" + "b" * 64, status="confirmed", default=True,
            ),
        ]
    )
    source_path.write_text(json.dumps(source), encoding="utf-8")
    state_dir = tmp_path / "state"

    releases = {
        "amaru": {
            "tag_name": "v10.12.0",
            "published_at": "2026-10-12T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "html_url": "https://example.invalid/amaru/v10.12.0",
        },
        "cardano-node": {
            "tag_name": "11.2.0",
            "published_at": "2026-10-13T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "html_url": "https://example.invalid/cardano-node/11.2.0",
        },
    }

    def fetch(url):
        if "/pragma-org/amaru/" in url:
            return [releases["amaru"]]
        if "/IntersectMBO/cardano-node/" in url:
            return [releases["cardano-node"]]
        raise AssertionError(url)

    def resolve(implementation, tag, release):
        marker = "c" if implementation == "amaru" else "d"
        return [{
            "kind": "oci",
            "reference": f"ghcr.io/example/{implementation}:{tag}",
            "availability": "available",
            "digest": "sha256:" + marker * 64,
        }]

    first = perform_release_refresh(
        implementation="amaru", state_dir=state_dir, source_path=source_path,
        fetch_json=fetch, artifact_resolver=resolve,
        tag_revision_resolver=lambda repository, tag: "3" * 40,
        checked_at="2026-10-13T12:00:00Z", manual=True,
    )
    second = perform_release_refresh(
        implementation="cardano-node", state_dir=state_dir, source_path=source_path,
        fetch_json=fetch, artifact_resolver=resolve,
        tag_revision_resolver=lambda repository, tag: "4" * 40,
        checked_at="2026-10-13T13:00:00Z", manual=True,
    )
    effective = load_effective_version_catalog(source_path, state_dir=state_dir)

    identities = {
        (row["implementation"], row["version"]) for row in effective["releases"]
    }
    assert ("amaru", "10.12.0") in identities
    assert ("cardano-node", "11.2.0") in identities
    assert next(
        row for row in effective["releases"]
        if row["implementation"] == "amaru"
        and row["version"] == "10.11.20260912"
    )["verification"] == source["releases"][0]["verification"]
    assert first["implementations"]["amaru"]["last_success_at"] == "2026-10-13T12:00:00Z"
    assert second["implementations"]["amaru"]["last_success_at"] == "2026-10-13T12:00:00Z"
    assert second["implementations"]["cardano-node"]["last_success_at"] == "2026-10-13T13:00:00Z"


def test_source_failure_preserves_cached_candidate_and_other_source_receipt(tmp_path):
    source_path = tmp_path / "catalog.json"
    source_path.write_text(
        json.dumps(_catalog([
            _release(
                "cardano-node", "11.1.2", "2" * 40,
                digest="sha256:" + "b" * 64, status="confirmed", default=True,
            ),
        ])),
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    perform_release_refresh(
        implementation="cardano-node", state_dir=state_dir, source_path=source_path,
        fetch_json=lambda url: [], artifact_resolver=lambda *_: [],
        checked_at="2026-10-13T12:00:00Z", manual=True,
    )
    candidate = state_dir / "version-catalog/discovery-candidate.json"
    before = candidate.read_bytes()

    failed = perform_release_refresh(
        implementation="amaru", state_dir=state_dir, source_path=source_path,
        fetch_json=lambda url: (_ for _ in ()).throw(RuntimeError("rate limited")),
        artifact_resolver=lambda *_: [],
        checked_at="2026-10-13T13:00:00Z", manual=True,
    )

    assert candidate.read_bytes() == before
    assert failed["implementations"]["amaru"]["state"] == "error"
    assert failed["implementations"]["cardano-node"]["state"] == "success"
    assert failed["implementations"]["cardano-node"]["last_success_at"] == (
        "2026-10-13T12:00:00Z"
    )


def test_legacy_refresh_status_is_normalized_per_implementation(tmp_path):
    root = tmp_path / "version-catalog"
    root.mkdir()
    (root / "refresh-status.json").write_text(json.dumps({
        "schema_version": 1,
        "state": "error",
        "last_attempt_at": "2026-10-13T13:00:00Z",
        "last_success_at": "2026-10-13T12:00:00Z",
        "source_status": {"amaru": "error", "cardano-node": "ok"},
        "new_versions": [{"implementation": "cardano-node", "version": "11.2.0"}],
        "error": "rate limited",
    }), encoding="utf-8")

    status = read_refresh_status(tmp_path)

    assert status["implementations"]["amaru"]["state"] == "error"
    assert status["implementations"]["amaru"]["error"] == "rate limited"
    assert status["implementations"]["cardano-node"]["state"] == "success"
    assert status["implementations"]["cardano-node"]["new_versions"] == [
        {"implementation": "cardano-node", "version": "11.2.0"}
    ]


def test_background_refresh_lock_prevents_cross_source_double_start(
    tmp_path, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    calls = []

    def blocked_refresh(**kwargs):
        calls.append(kwargs["implementation"])
        entered.set()
        release.wait(timeout=2)
        completed.set()
        return {"state": "success"}

    monkeypatch.setattr(
        "profile_manager.version_discovery.perform_release_refresh", blocked_refresh
    )

    first = start_release_refresh(
        implementation="amaru", manual=True, state_dir=tmp_path
    )
    assert entered.wait(timeout=1)
    second = start_release_refresh(
        implementation="cardano-node", manual=True, state_dir=tmp_path
    )
    release.set()
    assert completed.wait(timeout=1)

    assert first["started"] is True
    assert second["started"] is False
    assert second["state"] == "running"
    assert calls == ["amaru"]
