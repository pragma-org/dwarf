from __future__ import annotations

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
from profile_manager.data.operate_grammars import (
    deterministic_grammar_archive,
    dispatch_grammar_api_request,
    dispatch_grammar_mutating_request,
    generation_structure_schema,
    grammar_catalog_rows,
    grammar_detail,
    parse_mutation_dictionary,
    repository_grammar_id,
)


ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "dwarf" / "grammars"


def _write_grammar(
    root: Path,
    name: str,
    *,
    structure: dict | str | None = None,
    dictionary: str | None = None,
) -> Path:
    unit = root / name
    unit.mkdir(parents=True)
    if structure is not None:
        raw = structure if isinstance(structure, str) else json.dumps(structure, indent=2)
        (unit / "structure.json").write_text(raw, encoding="utf-8")
    if dictionary is not None:
        (unit / "dict.txt").write_text(dictionary, encoding="utf-8")
    return unit


def _valid_structure(target: str = "target-alpha") -> dict:
    return {
        "format": "cbor",
        "target": target,
        "decoder_entrypoint": "decode(data)",
        "shape": {"type": "array", "notes": ["message envelope"]},
    }


def _snapshot(root: Path) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    return [
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _mutate(path: str, payload: dict, *, repository_root: Path, runtime_root: Path):
    return dispatch_grammar_mutating_request(
        method="POST",
        path=path,
        body=json.dumps(payload).encode(),
        expected_token="secret",
        repository_root=repository_root,
        runtime_root=runtime_root,
    )


def test_shipped_inventory_is_complete_unique_and_schema_valid():
    expected = {
        path.name
        for path in SHIPPED.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    rows = [row for row in grammar_catalog_rows() if row["root_kind"] == "repository"]

    assert {row["label"] for row in rows} == expected
    assert len(rows) == len(expected) == 5
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(row["status"] == "ready" for row in rows)
    assert all(row["structure_status"] == "valid" for row in rows)
    assert all(row["dictionary_status"] == "valid" for row in rows)
    assert all(row["token_count"] > 0 for row in rows)
    jsonschema.Draft202012Validator.check_schema(generation_structure_schema())
    for row in rows:
        jsonschema.validate(row["structure"], generation_structure_schema())


def test_dictionary_parser_is_finite_exact_and_never_evaluates_content(tmp_path):
    marker = tmp_path / "must-not-exist"
    text = (
        "# comments are ignored\n"
        'named="A\\x00\\n\\\"\\\\Z"\n'
        '"plain"\n'
        f'__import__("pathlib").Path("{marker}").touch()\n'
        '"\\x0g"\n'
    )

    tokens, diagnostics = parse_mutation_dictionary(text)

    assert [token["name"] for token in tokens] == ["named", None]
    assert tokens[0]["bytes"] == b'A\x00\n"\\Z'
    assert tokens[0]["hex"] == "41 00 0a 22 5c 5a"
    assert tokens[1]["bytes"] == b"plain"
    assert len(diagnostics) == 2
    assert not marker.exists()


def test_invalid_missing_and_symlink_sources_render_diagnostics(tmp_path):
    repository = tmp_path / "repository"
    runtime = tmp_path / "runtime"
    _write_grammar(repository, "missing-dict", structure=_valid_structure())
    _write_grammar(repository, "bad-json", structure="{", dictionary='"ok"\n')
    _write_grammar(
        repository,
        "bad-schema",
        structure={"format": "cbor"},
        dictionary='"ok"\n',
    )
    _write_grammar(
        repository,
        "bad-dict",
        structure=_valid_structure(),
        dictionary='"\\x0g"\n',
    )
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_valid_structure()), encoding="utf-8")
    linked = _write_grammar(repository, "linked", dictionary='"ok"\n')
    (linked / "structure.json").symlink_to(outside)
    (repository / "._noise").mkdir()

    rows = grammar_catalog_rows(repository_root=repository, runtime_root=runtime)

    assert {row["label"] for row in rows} == {
        "bad-dict",
        "bad-json",
        "bad-schema",
        "linked",
        "missing-dict",
    }
    assert all(row["status"] == "diagnostic" for row in rows)
    by_label = {row["label"]: row for row in rows}
    assert by_label["missing-dict"]["dictionary_status"] == "missing"
    assert by_label["bad-json"]["structure_status"] == "malformed"
    assert by_label["bad-schema"]["structure_status"] == "invalid"
    assert by_label["bad-dict"]["dictionary_status"] == "invalid"
    assert by_label["linked"]["structure_status"] == "unsafe"
    assert str(outside) not in json.dumps(by_label["linked"].get("structure"))


def test_relationships_require_literal_source_or_declared_metadata(tmp_path):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    scenarios = tmp_path / "scenarios"
    targets = tmp_path / "targets"
    corpora = tmp_path / "corpora"
    scenarios.mkdir()
    targets.mkdir()
    _write_grammar(repository, "alpha", structure=_valid_structure(), dictionary='"A"\n')
    (scenarios / "uses-alpha.yaml").write_text(
        'grammar: "dwarf/grammars/alpha"\n', encoding="utf-8"
    )
    (scenarios / "unrelated.yaml").write_text('id: "alpha-ish"\n', encoding="utf-8")
    (targets / "target-alpha.yaml").write_text(
        '{"id":"target-alpha","grammar":"dwarf/grammars/alpha"}', encoding="utf-8"
    )
    (targets / "unrelated.yaml").write_text(
        '{"id":"unrelated","notes":"alpha"}', encoding="utf-8"
    )
    seeds = corpora / "alpha-seeds" / "seeds"
    seeds.mkdir(parents=True)
    (seeds / "seed.bin").write_bytes(b"seed")
    (seeds.parent / "corpus.json").write_text(
        json.dumps({"grammar_ids": ["alpha"]}), encoding="utf-8"
    )

    row = grammar_catalog_rows(
        repository_root=repository,
        runtime_root=runtime,
        scenarios_dir=scenarios,
        targets_dir=targets,
        corpus_repository_root=corpora,
        corpus_runtime_root=tmp_path / "corpus-runtime",
    )[0]

    assert row["scenario_ids"] == ["uses-alpha"]
    assert row["target_ids"] == ["target-alpha"]
    assert len(row["corpus_ids"]) == 1
    assert "alpha-seeds" in row["corpus_ids"][0]
    assert {(item["catalog"], item["resolved"]) for item in row["relationships"]} == {
        ("scenarios", True),
        ("targets", True),
        ("corpora", True),
    }


def test_exact_download_deterministic_export_and_traversal_rejection(tmp_path):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    structure = _valid_structure()
    dictionary = '# exact\n"\\x82\\x01"\n'
    _write_grammar(repository, "alpha", structure=structure, dictionary=dictionary)
    grammar_id = repository_grammar_id("alpha")

    structure_response = dispatch_grammar_api_request(
        f"/api/grammars/{grammar_id}/structure.json/download",
        repository_root=repository,
        runtime_root=runtime,
    )
    dictionary_response = dispatch_grammar_api_request(
        f"/api/grammars/{grammar_id}/dict.txt/download",
        repository_root=repository,
        runtime_root=runtime,
    )
    assert structure_response[0] == 200
    assert json.loads(structure_response[2]) == structure
    assert dictionary_response[0] == 200
    assert dictionary_response[2] == dictionary.encode()
    assert dispatch_grammar_api_request(
        f"/api/grammars/{grammar_id}/..%2Foutside/download",
        repository_root=repository,
        runtime_root=runtime,
    )[0] == 400

    first = deterministic_grammar_archive(repository_root=repository, runtime_root=runtime)
    second = deterministic_grammar_archive(repository_root=repository, runtime_root=runtime)
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/grammars/alpha/dict.txt",
            "dwarf/grammars/alpha/structure.json",
        ]
        assert archive.extractfile("dwarf/grammars/alpha/dict.txt").read() == dictionary.encode()
        manifest = json.loads(archive.extractfile("DWARF-EXPORT-MANIFEST.json").read())
        assert manifest["object_ids"] == [grammar_id]


def test_clone_and_runtime_save_are_token_gated_validated_and_contained(tmp_path):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    original = _write_grammar(
        repository,
        "alpha",
        structure=_valid_structure(),
        dictionary='"A"\n',
    )
    grammar_id = repository_grammar_id("alpha")
    endpoint = f"/api/grammars/{grammar_id}/actions"
    before = _snapshot(tmp_path)

    denied = _mutate(
        endpoint,
        {"action": "clone"},
        repository_root=repository,
        runtime_root=runtime,
    )
    assert denied[0] == 403
    assert _snapshot(tmp_path) == before

    cloned = _mutate(
        f"{endpoint}?token=secret",
        {"action": "clone"},
        repository_root=repository,
        runtime_root=runtime,
    )
    payload = json.loads(cloned[2])
    assert cloned[0] == 200 and payload["result"] == "cloned"
    runtime_id = payload["grammar_id"]
    assert (runtime / "alpha" / "structure.json").read_bytes() == (
        original / "structure.json"
    ).read_bytes()
    assert (runtime / "alpha" / "dict.txt").read_bytes() == (
        original / "dict.txt"
    ).read_bytes()
    assert _mutate(
        f"{endpoint}?token=secret",
        {"action": "clone"},
        repository_root=repository,
        runtime_root=runtime,
    )[0] == 409

    updated = _valid_structure("target-beta")
    saved = _mutate(
        f"/api/grammars/{runtime_id}/actions?token=secret",
        {"action": "save", "structure": updated, "dictionary": 'beta="B\\x00"\n'},
        repository_root=repository,
        runtime_root=runtime,
    )
    assert saved[0] == 200
    assert json.loads((runtime / "alpha" / "structure.json").read_text()) == updated
    assert (runtime / "alpha" / "dict.txt").read_text() == 'beta="B\\x00"\n'

    saved_snapshot = _snapshot(runtime)
    invalid_structure = _mutate(
        f"/api/grammars/{runtime_id}/actions?token=secret",
        {"action": "save", "structure": {"format": "cbor"}, "dictionary": '"B"\n'},
        repository_root=repository,
        runtime_root=runtime,
    )
    invalid_dictionary = _mutate(
        f"/api/grammars/{runtime_id}/actions?token=secret",
        {"action": "save", "structure": updated, "dictionary": '"\\x0g"\n'},
        repository_root=repository,
        runtime_root=runtime,
    )
    immutable = _mutate(
        f"{endpoint}?token=secret",
        {"action": "save", "structure": updated, "dictionary": '"B"\n'},
        repository_root=repository,
        runtime_root=runtime,
    )
    assert invalid_structure[0] == 422
    assert invalid_dictionary[0] == 422
    assert immutable[0] == 403
    assert _snapshot(runtime) == saved_snapshot


def test_gets_are_read_only_and_mutation_method_is_post_only(tmp_path):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    _write_grammar(repository, "alpha", structure=_valid_structure(), dictionary='"A"\n')
    grammar_id = repository_grammar_id("alpha")
    before = _snapshot(tmp_path)

    grammar_catalog_rows(repository_root=repository, runtime_root=runtime)
    grammar_detail(grammar_id, repository_root=repository, runtime_root=runtime)
    deterministic_grammar_archive(repository_root=repository, runtime_root=runtime)
    wrong_method = dispatch_grammar_mutating_request(
        method="GET",
        path=f"/api/grammars/{grammar_id}/actions?token=secret",
        body=b"{}",
        expected_token="secret",
        repository_root=repository,
        runtime_root=runtime,
    )

    assert wrong_method[0] == 405
    assert _snapshot(tmp_path) == before


def test_views_navigation_docs_examples_and_visual_audit_are_reconciled(tmp_path, monkeypatch):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    _write_grammar(repository, "alpha", structure=_valid_structure(), dictionary='"A"\n')
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_GRAMMARS_DIR", str(repository))
    monkeypatch.setenv("ADA2_DWARF_GRAMMARS_DIR", str(runtime))
    from profile_manager.data import learn_api, sub_nav

    grammar_id = repository_grammar_id("alpha")
    index = dashboard.render_route_html("/operate/grammars")
    detail = dashboard.render_route_html(f"/operate/grammars/{grammar_id}", token="secret")
    learn = dashboard.render_route_html("/learn/grammars")
    routes = {route for group in learn_api.html_route_groups() for route in group["routes"]}

    assert "Generation grammars" in index and grammar_id in index
    assert "Mutation tokens" in detail and "Clone to runtime" in detail
    assert "Structured editor" not in detail
    assert "machine-readable generation assets" in learn.lower()
    assert "not the human glossary" in learn.lower()
    for term in ("structure.json", "dict.txt", "CDDL", "Cuddle", "AFL++", "AFLNet", "cargo-fuzz", "libFuzzer"):
        assert term.lower() in learn.lower()
    assert "not interchangeable" in learn.lower()
    assert "/operate/grammars" in routes and "/operate/grammars/<id>" in routes
    assert "/learn/grammars" in routes
    assert any(item["url"] == "/operate/grammars" for item in sub_nav.OPERATE_SUB_NAV)
    assert any(item["url"] == "/learn/grammars" for item in sub_nav.LEARN_SUB_NAV)
    assert 'href="/operate/grammars"' in dashboard.render_route_html("/operate")
    assert 'href="/learn/grammars"' in dashboard.render_route_html("/learn")
    assert dashboard.render_route_html("/operate/grammars/not-found") is None

    clone = _mutate(
        f"/api/grammars/{grammar_id}/actions?token=secret",
        {"action": "clone"},
        repository_root=repository,
        runtime_root=runtime,
    )
    runtime_id = json.loads(clone[2])["grammar_id"]
    editable = dashboard.render_route_html(f"/operate/grammars/{runtime_id}", token="secret")
    assert "Structured editor" in editable and "Raw files" in editable
    assert 'data-grammar-editor' in editable

    source = (ROOT / "tools" / "dashboard_visual_audit.js").read_text(encoding="utf-8")
    assert repr("/operate/grammars") in source
    assert repr("/learn/grammars") in source
    assert "source.endsWith('/grammars')" in source


def test_dashboard_api_dispatches_grammar_downloads(tmp_path, monkeypatch):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    _write_grammar(repository, "alpha", structure=_valid_structure(), dictionary='"A"\n')
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_GRAMMARS_DIR", str(repository))
    monkeypatch.setenv("ADA2_DWARF_GRAMMARS_DIR", str(runtime))

    grammar_id = repository_grammar_id("alpha")
    response = dashboard.dispatch_api_request(
        f"/api/grammars/{grammar_id}/dict.txt/download"
    )

    assert response[0] == 200 and response[2] == b'"A"\n'


def test_generation_asset_docs_state_exact_activation_boundary():
    readme = (ROOT / "dwarf" / "grammars" / "README.md").read_text(encoding="utf-8")
    learn = (ROOT / "dwarf" / "dashboard" / "templates" / "learn" / "grammars.j2").read_text(
        encoding="utf-8"
    )

    assert "auto-discovers `dict.txt` only" in readme
    assert "cargo_fuzz_campaign.py --dict-path" in readme
    assert "runtime_custom_mutator_template" in readme
    assert "runtime_aflpp_campaign" in readme
    assert "Cloning alone does not activate it" in learn
    assert "supported primitive <code>dict_path</code>" in learn


def test_live_http_grammar_mutations_are_post_only_token_gated_and_serialized(
    tmp_path, monkeypatch
):
    repository = tmp_path / "grammars"
    runtime = tmp_path / "runtime"
    _write_grammar(repository, "alpha", structure=_valid_structure(), dictionary='"A"\n')
    monkeypatch.setenv("ADA2_DWARF_REPOSITORY_GRAMMARS_DIR", str(repository))
    monkeypatch.setenv("ADA2_DWARF_GRAMMARS_DIR", str(runtime))
    handler = dashboard.serve_dashboard_handler_factory("secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    grammar_id = repository_grammar_id("alpha")
    endpoint = (
        f"http://127.0.0.1:{server.server_address[1]}"
        f"/api/grammars/{grammar_id}/actions"
    )
    body = json.dumps({"action": "clone"}).encode()
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

    assert (runtime / "alpha" / "structure.json").is_file()
    assert (runtime / "alpha" / "dict.txt").is_file()
