import copy
import json

from scripts.refresh_version_catalog import (
    refresh_version_catalog,
    resolve_oci_artifacts,
    write_catalog_candidate,
)


def _existing_catalog() -> dict:
    return {
        "schema_version": 1,
        "updated_at": "2026-09-10T00:00:00Z",
        "sources": {},
        "releases": [
            {
                "implementation": "cardano-node",
                "version": "11.1.1",
                "channel": "stable",
                "released_at": "2026-09-10T00:00:00Z",
                "source_revision": "1" * 40,
                "artifacts": [],
                "verification": {
                    "cardano-only": {
                        "status": "confirmed",
                        "default": True,
                        "checked_at": "2026-09-11T00:00:00Z",
                        "reason": "Qualified locally.",
                        "evidence": ["run:old"],
                        "issues": [],
                    }
                },
            },
            {
                "implementation": "amaru",
                "version": "10.11.20260901",
                "channel": "stable",
                "released_at": "2026-09-01T00:00:00Z",
                "source_revision": "2" * 40,
                "artifacts": [],
                "verification": {
                    "amaru-only": {
                        "status": "blocked",
                        "default": False,
                        "checked_at": "2026-09-11T00:00:00Z",
                        "reason": "Known bootstrap blocker.",
                        "evidence": ["run:amaru-old"],
                        "issues": ["https://example.test/issue/1"],
                    }
                },
            },
        ],
        "compatibility_pairs": [],
    }


def _fake_fetch(url: str):
    if url.endswith("IntersectMBO/cardano-node/releases?per_page=100"):
        return [
            {
                "tag_name": "11.1.2",
                "published_at": "2026-09-17T00:00:00Z",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/IntersectMBO/cardano-node/releases/tag/11.1.2",
            },
            {
                "tag_name": "11.1.1",
                "published_at": "2026-09-10T00:00:00Z",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/IntersectMBO/cardano-node/releases/tag/11.1.1",
            },
            {"tag_name": "draft", "draft": True, "prerelease": False},
        ]
    if url.endswith("pragma-org/amaru/releases?per_page=100"):
        return [
            {
                "tag_name": "v10.11.20260912",
                "published_at": "2026-09-12T00:00:00Z",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/pragma-org/amaru/releases/tag/v10.11.20260912",
            },
            {
                "tag_name": "v10.12.0-rc1",
                "published_at": "2026-09-15T00:00:00Z",
                "draft": False,
                "prerelease": True,
                "html_url": "https://github.com/pragma-org/amaru/releases/tag/v10.12.0-rc1",
            },
        ]
    if "/commits/" in url:
        ref = url.rsplit("/", 1)[-1]
        return {"sha": (ref.encode().hex() + "0" * 40)[:40]}
    raise AssertionError(f"unexpected URL {url}")


def _artifact_resolver(implementation: str, tag: str, release: dict):
    if implementation == "cardano-node" and tag == "11.1.2":
        return [
            {
                "kind": "oci",
                "reference": "ghcr.io/example/cardano-node:11.1.2",
                "availability": "available",
                "digest": "sha256:" + "a" * 64,
            }
        ]
    return []


def test_refresh_discovers_releases_without_promoting_runtime_status():
    original = _existing_catalog()

    refreshed = refresh_version_catalog(
        original,
        fetch_json=_fake_fetch,
        artifact_resolver=_artifact_resolver,
        checked_at="2026-09-17T22:00:00Z",
    )

    cardano = [r for r in refreshed["releases"] if r["implementation"] == "cardano-node"]
    amaru = [r for r in refreshed["releases"] if r["implementation"] == "amaru"]
    assert [r["version"] for r in cardano[:2]] == ["11.1.2", "11.1.1"]
    assert cardano[0]["verification"]["cardano-only"]["status"] == "unknown"
    assert cardano[0]["verification"]["cardano-only"]["default"] is False
    assert cardano[0]["artifacts"][0]["digest"] == "sha256:" + "a" * 64
    assert amaru[0]["version"] == "10.12.0-rc1"
    assert amaru[0]["channel"] == "prerelease"
    assert original == _existing_catalog(), "refresh must not mutate its input"


def test_refresh_preserves_human_verification_and_default_records():
    refreshed = refresh_version_catalog(
        _existing_catalog(),
        fetch_json=_fake_fetch,
        artifact_resolver=_artifact_resolver,
        checked_at="2026-09-17T22:00:00Z",
    )

    prior = next(
        r for r in refreshed["releases"]
        if r["implementation"] == "cardano-node" and r["version"] == "11.1.1"
    )
    amaru_prior = next(
        r for r in refreshed["releases"]
        if r["implementation"] == "amaru" and r["version"] == "10.11.20260901"
    )
    assert prior["verification"]["cardano-only"]["status"] == "confirmed"
    assert prior["verification"]["cardano-only"]["evidence"] == ["run:old"]
    assert prior["verification"]["cardano-only"]["default"] is True
    assert amaru_prior["verification"]["amaru-only"]["status"] == "blocked"
    assert amaru_prior["verification"]["amaru-only"]["default"] is False
    assert amaru_prior["verification"]["amaru-only"]["issues"] == ["https://example.test/issue/1"]


def test_refresh_preserves_artifact_bound_to_confirmed_runtime_evidence():
    catalog = _existing_catalog()
    prior = catalog["releases"][0]
    prior["artifacts"] = [{
        "kind": "oci",
        "reference": "ghcr.io/example/cardano-node@sha256:" + "1" * 64,
        "availability": "available",
        "digest": "sha256:" + "1" * 64,
    }]

    def changed_registry_artifact(implementation, tag, release):
        return [{
            "kind": "oci",
            "reference": f"ghcr.io/example/{implementation}:{tag}",
            "availability": "available",
            "digest": "sha256:" + "9" * 64,
        }]

    refreshed = refresh_version_catalog(
        catalog,
        fetch_json=_fake_fetch,
        artifact_resolver=changed_registry_artifact,
        checked_at="2026-09-17T22:00:00Z",
    )

    retained = next(
        release for release in refreshed["releases"]
        if release["implementation"] == "cardano-node" and release["version"] == "11.1.1"
    )
    assert retained["artifacts"] == prior["artifacts"]


def test_refresh_is_deterministic_for_the_same_inputs():
    kwargs = {
        "fetch_json": _fake_fetch,
        "artifact_resolver": _artifact_resolver,
        "checked_at": "2026-09-17T22:00:00Z",
    }

    first = refresh_version_catalog(copy.deepcopy(_existing_catalog()), **kwargs)
    second = refresh_version_catalog(copy.deepcopy(_existing_catalog()), **kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_refresh_can_bound_registry_lookups_to_newest_candidates():
    calls = []

    def resolver(implementation, tag, release):
        calls.append((implementation, tag))
        return []

    refresh_version_catalog(
        _existing_catalog(),
        fetch_json=_fake_fetch,
        artifact_resolver=resolver,
        artifact_limit_per_implementation=1,
        checked_at="2026-09-17T22:00:00Z",
    )

    assert calls == [
        ("cardano-node", "11.1.2"),
        ("amaru", "v10.12.0-rc1"),
    ]


def test_refresh_uses_batched_tag_revision_resolver_when_provided():
    fetched = []

    def releases_only(url):
        fetched.append(url)
        if "/releases?" in url:
            return _fake_fetch(url)
        raise AssertionError("per-tag commit API must not be called")

    def revisions(repository, tag):
        return (repository + tag).encode().hex()[:40].ljust(40, "0")

    refreshed = refresh_version_catalog(
        _existing_catalog(),
        fetch_json=releases_only,
        artifact_resolver=lambda *_: [],
        tag_revision_resolver=revisions,
        checked_at="2026-09-17T22:00:00Z",
    )

    assert len(fetched) == 2
    assert all("/releases?" in url for url in fetched)
    newest = next(
        release for release in refreshed["releases"]
        if release["implementation"] == "cardano-node" and release["version"] == "11.1.2"
    )
    assert len(newest["source_revision"]) == 40


def test_amaru_artifact_resolution_preserves_the_official_v_prefixed_tag(monkeypatch):
    commands = []

    class Result:
        returncode = 0
        stdout = json.dumps({"digest": "sha256:" + "b" * 64})

    def run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr("scripts.refresh_version_catalog.subprocess.run", run)

    artifacts = resolve_oci_artifacts(
        "amaru",
        "v10.11.20260912",
        {"tag_name": "v10.11.20260912"},
    )

    assert commands[0][4] == "ghcr.io/pragma-org/amaru:v10.11.20260912"
    assert artifacts[0]["reference"] == "ghcr.io/pragma-org/amaru:v10.11.20260912"
    assert artifacts[0]["availability"] == "available"


def test_write_candidate_does_not_overwrite_source_catalog(tmp_path):
    source = tmp_path / "catalog.json"
    output = tmp_path / "candidate.json"
    source.write_text(json.dumps(_existing_catalog()) + "\n", encoding="utf-8")
    candidate = copy.deepcopy(_existing_catalog())
    candidate["updated_at"] = "2026-09-17T22:00:00Z"

    written = write_catalog_candidate(candidate, output_path=output, source_path=source)

    assert written == output
    assert json.loads(source.read_text(encoding="utf-8"))["updated_at"] == "2026-09-10T00:00:00Z"
    assert json.loads(output.read_text(encoding="utf-8"))["updated_at"] == "2026-09-17T22:00:00Z"


def test_write_candidate_refuses_source_path(tmp_path):
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(_existing_catalog()) + "\n", encoding="utf-8")

    try:
        write_catalog_candidate(_existing_catalog(), output_path=source, source_path=source)
    except ValueError as exc:
        assert "refuses to overwrite" in str(exc)
    else:
        raise AssertionError("candidate writer overwrote its source")
