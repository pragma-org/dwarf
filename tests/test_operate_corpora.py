from __future__ import annotations

import base64
import hashlib
import io
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tarfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jsonschema

from profile_manager import dashboard
from profile_manager.data.operate_corpora import (
    corpus_catalog_rows,
    corpus_detail,
    corpus_record_schema,
    deterministic_corpora_archive,
    dispatch_corpus_api_request,
    dispatch_corpus_mutating_request,
    repository_corpus_id,
)


def _write_repo_corpus(root: Path, name: str, files: dict[str, bytes]) -> Path:
    seeds = root / name / "seeds"
    seeds.mkdir(parents=True)
    for filename, body in files.items():
        (seeds / filename).write_bytes(body)
    return seeds


def _snapshot(root: Path) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    return [
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _mutation(path: str, payload: dict, *, runtime_root: Path, repo_root: Path):
    return dispatch_corpus_mutating_request(
        method="POST",
        path=path,
        body=json.dumps(payload).encode(),
        expected_token="secret",
        repository_root=repo_root,
        runtime_root=runtime_root,
    )


def test_discovers_every_explicit_corpus_root_and_exact_inventory(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"one.bin": b"same", "two.bin": b"same"})
    _write_repo_corpus(repo, "nested/beta", {"three.cbor": b"\x81\x00"})
    (repo / "alpha" / "seeds" / "._one.bin").write_bytes(b"noise")
    (repo / "alpha" / "seeds" / ".DS_Store").write_bytes(b"noise")
    (repo / "alpha" / "seeds" / "__pycache__").mkdir()
    (repo / "alpha" / "seeds" / "__pycache__" / "x.pyc").write_bytes(b"noise")

    rows = corpus_catalog_rows(repository_root=repo, runtime_root=runtime)

    assert [row["id"] for row in rows] == [
        repository_corpus_id("alpha"),
        repository_corpus_id("nested/beta"),
        "runtime--overlay",
    ]
    alpha = rows[0]
    assert alpha["input_count"] == 2
    assert alpha["size_bytes"] == 8
    assert alpha["duplicate_input_count"] == 1
    assert alpha["writable"] is False
    assert rows[-1]["writable"] is True
    assert rows[-1]["status"] == "empty"


def test_repository_inventory_accounts_for_all_shipped_seed_units():
    rows = [row for row in corpus_catalog_rows() if row["root_kind"] == "repository"]

    assert len(rows) == 20
    assert sum(row["input_count"] for row in rows) == 255
    cuddle = next(
        row
        for row in rows
        if row["id"] == repository_corpus_id("cuddle-generated/block-header")
    )
    assert cuddle["provenance"] == "cuddle 1.8.1.1"
    assert cuddle["grammar_ids"] == ["conway.cddl"]
    assert cuddle["source_campaign"] == "campaign-2026-07-19.md"
    assert cuddle["provenance_files"]
    assert all(path.startswith("dwarf/corpora/") for path in cuddle["provenance_files"])
    assert "/home/" not in json.dumps(cuddle["provenance_files"])


def test_symlinks_and_unsafe_names_never_escape_a_corpus(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    seeds = _write_repo_corpus(repo, "alpha", {"safe.bin": b"safe"})
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    (seeds / "escape.bin").symlink_to(outside)

    detail = corpus_detail(
        repository_corpus_id("alpha"), repository_root=repo, runtime_root=runtime
    )

    assert detail is not None
    assert [item["name"] for item in detail["inputs"]] == ["safe.bin"]
    assert any("symlink" in diagnostic.lower() for diagnostic in detail["diagnostics"])
    response = dispatch_corpus_api_request(
        f"/api/corpora/{repository_corpus_id('alpha')}/inputs/escape.bin/download",
        repository_root=repo,
        runtime_root=runtime,
    )
    assert response[0] == 404


def test_previews_are_bounded_and_structured_text_is_escaped(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(
        repo,
        "alpha",
        {
            "structured.json": b'{"value":"<script>alert(1)</script>"}',
            "binary.bin": bytes(range(128)),
            "encoded.cbor": b"\x82\x01\x02",
        },
    )

    detail = corpus_detail(
        repository_corpus_id("alpha"),
        repository_root=repo,
        runtime_root=runtime,
        preview_bytes=32,
    )
    by_name = {item["name"]: item for item in detail["inputs"]}

    assert by_name["structured.json"]["preview_kind"] == "text"
    assert "&lt;script&gt;" in by_name["structured.json"]["preview"]
    assert "<script>" not in by_name["structured.json"]["preview"]
    assert by_name["structured.json"]["preview_truncated"] is True
    assert by_name["binary.bin"]["preview_kind"] == "hex"
    assert len(by_name["binary.bin"]["preview"].split()) == 32
    assert by_name["binary.bin"]["preview_truncated"] is True
    assert by_name["encoded.cbor"]["preview_kind"] == "cbor-hex"
    assert by_name["encoded.cbor"]["preview"] == "82 01 02"


def test_metadata_diagnostics_and_schema_are_explicit(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    seeds = _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    (seeds.parent / "corpus.json").write_text('{"role": 7}', encoding="utf-8")

    row = corpus_catalog_rows(repository_root=repo, runtime_root=runtime)[0]

    assert row["metadata_status"] == "malformed"
    assert row["status"] == "diagnostic"
    assert row["diagnostics"]
    jsonschema.Draft202012Validator.check_schema(corpus_record_schema())
    jsonschema.validate(row["record"], corpus_record_schema())


def test_valid_metadata_controls_declared_role_and_retired_status(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    seeds = _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    (seeds.parent / "corpus.json").write_text(
        json.dumps(
            {
                "role": "regression",
                "provenance": "reviewed campaign",
                "status": "retired",
                "target_ids": ["missing-target"],
                "grammar_ids": ["missing-grammar"],
            }
        ),
        encoding="utf-8",
    )

    row = corpus_catalog_rows(repository_root=repo, runtime_root=runtime)[0]

    assert row["metadata_status"] == "valid"
    assert row["role"] == "regression"
    assert row["provenance"] == "reviewed campaign"
    assert row["status"] == "retired"
    assert {(item["catalog"], item["id"], item["resolved"]) for item in row["relationships"]} == {
        ("targets", "missing-target", False),
        ("grammars", "missing-grammar", False),
    }
    jsonschema.validate(row["record"], corpus_record_schema())


def test_relationships_come_only_from_explicit_source_references(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    scenarios = tmp_path / "scenarios"
    targets = tmp_path / "targets"
    _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    scenarios.mkdir()
    targets.mkdir()
    (scenarios / "uses-alpha.yaml").write_text(
        'params:\n  corpus: "/opt/dwarf/corpora/alpha/seeds"\n', encoding="utf-8"
    )
    (scenarios / "unrelated.yaml").write_text('id: "unrelated"\n', encoding="utf-8")
    (targets / "target-alpha.yaml").write_text(
        'seed_corpus: "dwarf/corpora/alpha/seeds"\n', encoding="utf-8"
    )

    row = corpus_catalog_rows(
        repository_root=repo,
        runtime_root=runtime,
        scenarios_dir=scenarios,
        targets_dir=targets,
    )[0]

    assert row["scenario_ids"] == ["uses-alpha"]
    assert row["target_ids"] == ["target-alpha"]
    assert {(item["catalog"], item["id"], item["resolved"]) for item in row["relationships"]} == {
        ("scenarios", "uses-alpha", True),
        ("targets", "target-alpha", True),
    }


def test_exact_download_and_deterministic_binary_export(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"raw.bin": b"\x00\xff\x10"})

    response = dispatch_corpus_api_request(
        f"/api/corpora/{repository_corpus_id('alpha')}/inputs/raw.bin/download",
        repository_root=repo,
        runtime_root=runtime,
    )
    assert response[0] == 200
    assert response[2] == b"\x00\xff\x10"
    assert response[3]["Content-Disposition"].endswith('filename="raw.bin"')
    assert dispatch_corpus_api_request(
        f"/api/corpora/{repository_corpus_id('alpha')}/inputs/..%2Foutside/download",
        repository_root=repo,
        runtime_root=runtime,
    )[0] == 400

    first = deterministic_corpora_archive(repository_root=repo, runtime_root=runtime)
    second = deterministic_corpora_archive(repository_root=repo, runtime_root=runtime)
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/corpora/alpha/seeds/raw.bin",
        ]
        assert archive.extractfile("dwarf/corpora/alpha/seeds/raw.bin").read() == b"\x00\xff\x10"
        manifest = json.loads(archive.extractfile("DWARF-EXPORT-MANIFEST.json").read())
        assert manifest["object_ids"] == [repository_corpus_id("alpha")]

    one = dispatch_corpus_api_request(
        f"/api/corpora/{repository_corpus_id('alpha')}/export",
        repository_root=repo,
        runtime_root=runtime,
    )
    assert one[0:2] == (200, "application/gzip")


def test_gets_are_read_only_and_mutations_require_token(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    before = _snapshot(tmp_path)

    corpus_catalog_rows(repository_root=repo, runtime_root=runtime)
    corpus_detail(repository_corpus_id("alpha"), repository_root=repo, runtime_root=runtime)
    deterministic_corpora_archive(repository_root=repo, runtime_root=runtime)
    denied = _mutation(
        "/api/corpora/runtime--overlay/actions",
        {"action": "import", "filename": "new.bin", "content_base64": "bmV3"},
        runtime_root=runtime,
        repo_root=repo,
    )

    assert denied[0] == 403
    assert dispatch_corpus_mutating_request(
        method="GET",
        path="/api/corpora/runtime--overlay/actions?token=secret",
        body=b"{}",
        expected_token="secret",
        repository_root=repo,
        runtime_root=runtime,
    )[0] == 405
    assert _snapshot(tmp_path) == before


def test_bad_uploads_never_write_and_duplicate_import_is_deterministic(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    before = _snapshot(runtime)
    bad = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "import", "filename": "../escape", "content_base64": "%%%"},
        runtime_root=runtime,
        repo_root=repo,
    )
    assert bad[0] == 422
    assert _snapshot(runtime) == before

    payload = {
        "action": "import",
        "filename": "seed.bin",
        "content_base64": base64.b64encode(b"payload").decode(),
    }
    first = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        payload,
        runtime_root=runtime,
        repo_root=repo,
    )
    second = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {**payload, "filename": "copy.bin"},
        runtime_root=runtime,
        repo_root=repo,
    )
    assert first[0] == 200
    assert json.loads(first[2])["result"] == "imported"
    assert second[0] == 200
    assert json.loads(second[2]) == {
        "ok": True,
        "result": "duplicate",
        "filename": "seed.bin",
        "sha256": hashlib.sha256(b"payload").hexdigest(),
    }
    assert _snapshot(runtime) == [
        ("seed.bin", hashlib.sha256(b"payload").hexdigest())
    ]


def test_dedupe_disable_and_remove_are_recoverable_and_runtime_only(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"repo.bin": b"fixed"})
    runtime.mkdir()
    (runtime / "a.bin").write_bytes(b"same")
    (runtime / "b.bin").write_bytes(b"same")

    immutable = _mutation(
        f"/api/corpora/{repository_corpus_id('alpha')}/actions?token=secret",
        {"action": "remove", "filename": "repo.bin"},
        runtime_root=runtime,
        repo_root=repo,
    )
    traversal = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "remove", "filename": "../outside"},
        runtime_root=runtime,
        repo_root=repo,
    )
    dedupe = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "deduplicate"},
        runtime_root=runtime,
        repo_root=repo,
    )
    disabled = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "disable"},
        runtime_root=runtime,
        repo_root=repo,
    )
    enabled = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "enable"},
        runtime_root=runtime,
        repo_root=repo,
    )
    removed = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "remove", "filename": "a.bin"},
        runtime_root=runtime,
        repo_root=repo,
    )

    assert immutable[0] == 403
    assert traversal[0] == 422
    assert json.loads(dedupe[2])["moved"] == ["b.bin"]
    assert disabled[0] == 200
    assert enabled[0] == 200 and not (runtime / ".disabled").exists()
    assert removed[0] == 200 and not (runtime / "a.bin").exists()
    trashed = [path for path in (runtime / ".trash").rglob("*") if path.is_file()]
    assert {path.name for path in trashed} >= {"a.bin", "b.bin"}
    assert (repo / "alpha" / "seeds" / "repo.bin").read_bytes() == b"fixed"


def test_promote_retained_testcase_copies_exact_bytes_to_runtime(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    state = tmp_path / "state"
    runs = tmp_path / "runs"
    artifact = runs / "run-a" / "outputs" / "minimized.cbor"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x82\x01\x02")
    (state / "testcases").mkdir(parents=True)
    (state / "testcases" / "tc-case.json").write_text(
        json.dumps(
            {
                "case_id": "tc-case",
                "source_run_id": "run-a",
                "source_artifact_path": "outputs/minimized.cbor",
                "classification": "queue",
                "replay_targets": ["amaru"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))

    response = _mutation(
        "/api/corpora/runtime--overlay/actions?token=secret",
        {"action": "promote", "case_id": "tc-case"},
        runtime_root=runtime,
        repo_root=repo,
    )

    payload = json.loads(response[2])
    assert response[0] == 200
    assert payload["result"] == "promoted"
    assert (runtime / payload["filename"]).read_bytes() == artifact.read_bytes()


def test_dashboard_routes_navigation_docs_and_examples_are_reconciled(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_CORPORA_DIR", str(repo))
    monkeypatch.setenv("ADA2_DWARF_CORPORA_DIR", str(runtime))
    from profile_manager.data import learn_api, sub_nav

    index = dashboard.render_route_html("/operate/corpora")
    detail = dashboard.render_route_html(
        f"/operate/corpora/{repository_corpus_id('alpha')}"
    )
    learn = dashboard.render_route_html("/learn/corpora")
    routes = {route for group in learn_api.html_route_groups() for route in group["routes"]}

    assert "Fuzz corpora" in index and repository_corpus_id("alpha") in index
    assert "one.bin" in detail and "bounded preview" in detail.lower()
    assert '<dl class="definition-summary corpus-summary">' in detail
    assert "<dt>Inputs</dt><dd>1</dd>" in detail
    for term in ("seed", "generated", "queue", "crash", "minimized", "regression", "promoted"):
        assert term in learn.lower()
    assert "not an arbitrary binary editor" in learn.lower()
    assert "/operate/corpora" in routes and "/operate/corpora/<id>" in routes
    assert "/learn/corpora" in routes
    assert any(item["url"] == "/operate/corpora" for item in sub_nav.OPERATE_SUB_NAV)
    assert any(item["url"] == "/learn/corpora" for item in sub_nav.LEARN_SUB_NAV)
    assert 'href="/operate/corpora"' in dashboard.render_route_html("/operate")
    assert 'href="/learn/corpora"' in dashboard.render_route_html("/learn")
    assert dashboard.render_route_html("/operate/corpora/not-found") is None


def test_dashboard_api_dispatch_and_visual_audit_cover_corpora(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "alpha", {"one.bin": b"one"})
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_CORPORA_DIR", str(repo))
    monkeypatch.setenv("ADA2_DWARF_CORPORA_DIR", str(runtime))

    response = dashboard.dispatch_api_request(
        f"/api/corpora/{repository_corpus_id('alpha')}/inputs/one.bin/download"
    )
    assert response[0] == 200 and response[2] == b"one"
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/dashboard_visual_audit.js").read_text(encoding="utf-8")
    assert repr("/operate/corpora") in source
    assert repr("/learn/corpora") in source
    assert "source.endsWith('/corpora')" in source


def test_live_http_corpus_management_is_post_only_token_gated_and_serialized(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_CORPORA_DIR", str(repo))
    monkeypatch.setenv("ADA2_DWARF_CORPORA_DIR", str(runtime))
    handler = dashboard.serve_dashboard_handler_factory("secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/api/corpora/runtime--overlay/actions"
    body = json.dumps(
        {
            "action": "import",
            "filename": "seed.bin",
            "content_base64": base64.b64encode(b"seed").decode(),
        }
    ).encode()
    try:
        try:
            urlopen(endpoint)
        except HTTPError as exc:
            assert exc.code == 405
        try:
            urlopen(Request(endpoint, data=body, method="POST"))
        except HTTPError as exc:
            assert exc.code == 403
        assert dashboard.try_acquire_mutating_lock()
        try:
            try:
                urlopen(Request(f"{endpoint}?token=secret", data=body, method="POST"))
            except HTTPError as exc:
                assert exc.code == 409
        finally:
            dashboard.release_mutating_lock()
        with urlopen(
            Request(f"{endpoint}?token=secret", data=body, method="POST")
        ) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert (runtime / "seed.bin").read_bytes() == b"seed"


def test_container_build_context_excludes_platform_and_cache_noise():
    root = Path(__file__).resolve().parents[1]
    patterns = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for pattern in ("**/._*", "**/.DS_Store", "**/__pycache__", "**/.pytest_cache"):
        assert pattern in patterns


def test_repository_ids_remain_unique_when_readable_path_slugs_collide(tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    _write_repo_corpus(repo, "a--b", {"one.bin": b"one"})
    _write_repo_corpus(repo, "a/b", {"two.bin": b"two"})

    ids = [
        row["id"]
        for row in corpus_catalog_rows(repository_root=repo, runtime_root=runtime)
    ]

    assert len(ids) == len(set(ids))
    assert repository_corpus_id("a--b") in ids
    assert repository_corpus_id("a/b") in ids
