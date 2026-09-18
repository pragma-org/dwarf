import json
from datetime import datetime, timezone

from profile_manager.version_discovery import (
    merge_discovery_candidate,
    perform_release_refresh,
    read_refresh_status,
    refresh_is_stale,
)


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
    assert retained["verification"]["cardano-only"]["status"] == "confirmed"
    assert added["verification"]["cardano-only"]["status"] == "unknown"
    assert added["verification"]["cardano-only"]["default"] is False
    assert not any(item["version"] == "10.12.0" for item in merged["releases"])
    assert discovered == [{"implementation": "cardano-node", "version": "11.1.3"}]


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
