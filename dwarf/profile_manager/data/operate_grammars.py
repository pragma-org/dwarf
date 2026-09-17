"""Containment-safe catalog and editor for generation grammar assets."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import jsonschema

from profile_manager.data.asset_catalog import (
    ExportSource,
    deterministic_export_archive,
    is_safe_asset_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_REQUEST_BYTES = 512 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TOKEN_LINE = re.compile(
    r'(?:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?"(?P<body>(?:\\.|[^"\\])*)"\s*'
)
_NOISE_NAMES = frozenset({".DS_Store", "__pycache__", ".pytest_cache", "README.md"})


def _repository_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_REPOSITORY_GRAMMARS_DIR", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "dwarf" / "grammars"


def _runtime_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_GRAMMARS_DIR", "").strip()
    if configured:
        return Path(configured)
    state = os.environ.get("ADA2_DWARF_STATE_DIR", "").strip()
    return (Path(state) if state else PROJECT_ROOT / "dwarf" / "state") / "grammars"


def _roots(
    *, repository_root: Path | None = None, runtime_root: Path | None = None
) -> tuple[Path, Path]:
    return Path(repository_root or _repository_root()), Path(runtime_root or _runtime_root())


@lru_cache(maxsize=1)
def generation_structure_schema() -> dict[str, Any]:
    path = PROJECT_ROOT / "dwarf" / "spec" / "v1" / "generation-structure.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _asset_id(root_kind: str, name: str) -> str:
    if root_kind not in {"repository", "runtime"} or not _safe_unit_name(name):
        raise ValueError(f"unsafe generation grammar name: {name!r}")
    prefix = "repo" if root_kind == "repository" else "runtime"
    digest = hashlib.sha256(f"{root_kind}:{name}".encode()).hexdigest()[:8]
    value = f"{prefix}--{name}--{digest}"
    if not is_safe_asset_id(value):
        raise ValueError(f"unsafe generation grammar id: {value!r}")
    return value


def repository_grammar_id(name: str) -> str:
    return _asset_id("repository", name)


def runtime_grammar_id(name: str) -> str:
    return _asset_id("runtime", name)


def _safe_unit_name(name: str) -> bool:
    return bool(
        _SAFE_NAME.fullmatch(name)
        and name not in {".", ".."}
        and not name.startswith(".")
        and not name.startswith("._")
        and not name.endswith(".pyc")
        and name not in _NOISE_NAMES
    )


def _decode_token_body(body: str) -> tuple[bytes | None, str | None]:
    output = bytearray()
    index = 0
    simple = {"n": 0x0A, "r": 0x0D, "t": 0x09, "0": 0x00, '"': 0x22, "\\": 0x5C}
    while index < len(body):
        char = body[index]
        if char != "\\":
            output.extend(char.encode("utf-8"))
            index += 1
            continue
        if index + 1 >= len(body):
            return None, "trailing escape"
        escaped = body[index + 1]
        if escaped == "x":
            digits = body[index + 2 : index + 4]
            if len(digits) != 2 or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
                return None, "\\x must be followed by two hexadecimal digits"
            output.append(int(digits, 16))
            index += 4
            continue
        if escaped not in simple:
            return None, f"unsupported escape \\{escaped}"
        output.append(simple[escaped])
        index += 2
    return bytes(output), None


def parse_mutation_dictionary(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse libFuzzer-style token lines with a finite parser, never evaluation."""

    tokens: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _TOKEN_LINE.fullmatch(stripped)
        if match is None:
            diagnostics.append(f"dict.txt line {line_number}: expected a quoted mutation token")
            continue
        body, error = _decode_token_body(match.group("body"))
        if error:
            diagnostics.append(f"dict.txt line {line_number}: {error}")
            continue
        assert body is not None
        tokens.append(
            {
                "name": match.group("name"),
                "literal": stripped,
                "bytes": body,
                "hex": " ".join(f"{byte:02x}" for byte in body),
                "size_bytes": len(body),
            }
        )
    return tokens, diagnostics


def _contained_file(unit: Path, name: str) -> tuple[Path | None, str | None]:
    candidate = unit / name
    if candidate.is_symlink():
        return None, "unsafe"
    if not candidate.exists():
        return None, "missing"
    try:
        resolved_unit = unit.resolve()
        resolved = candidate.resolve()
        resolved.relative_to(resolved_unit)
    except (OSError, ValueError):
        return None, "unsafe"
    if not resolved.is_file():
        return None, "unsafe"
    return resolved, None


def _read_structure(unit: Path) -> tuple[dict[str, Any] | None, str | None, str, list[str]]:
    path, state = _contained_file(unit, "structure.json")
    if path is None:
        return None, None, state or "missing", [f"structure.json is {state or 'missing'}"]
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, "malformed", [f"structure.json is not readable UTF-8: {exc}"]
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, raw, "malformed", [f"structure.json is malformed JSON: {exc}"]
    try:
        jsonschema.validate(loaded, generation_structure_schema())
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path) or "root"
        return loaded, raw, "invalid", [f"structure.json {where}: {exc.message}"]
    return loaded, raw, "valid", []


def _read_dictionary(unit: Path) -> tuple[list[dict[str, Any]], str | None, str, list[str]]:
    path, state = _contained_file(unit, "dict.txt")
    if path is None:
        return [], None, state or "missing", [f"dict.txt is {state or 'missing'}"]
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], None, "malformed", [f"dict.txt is not readable UTF-8: {exc}"]
    tokens, diagnostics = parse_mutation_dictionary(raw)
    return tokens, raw, "invalid" if diagnostics else "valid", diagnostics


def _units(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    return [
        path
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.is_symlink() and _safe_unit_name(path.name)
    ]


def _reference_sources(root: Path) -> list[tuple[str, str]]:
    if not root.is_dir() or root.is_symlink():
        return []
    rows: list[tuple[str, str]] = []
    for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"), *root.glob("*.json"))):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            rows.append((path.stem, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return rows


def _literal_references(sources: list[tuple[str, str]], fragment: str) -> list[str]:
    return sorted({source_id for source_id, source in sources if fragment in source})


def _corpus_reference_map(
    *,
    repository_root: Path | None,
    runtime_root: Path | None,
) -> dict[str, list[str]]:
    from profile_manager.data.operate_corpora import corpus_catalog_rows

    rows = corpus_catalog_rows(repository_root=repository_root, runtime_root=runtime_root)
    references: dict[str, list[str]] = {}
    for row in rows:
        for grammar_id in row.get("grammar_ids", []):
            references.setdefault(grammar_id, []).append(row["id"])
    return {
        grammar_id: sorted(set(corpus_ids))
        for grammar_id, corpus_ids in references.items()
    }


def _row(
    unit: Path,
    *,
    root_kind: str,
    scenario_sources: list[tuple[str, str]],
    target_sources: list[tuple[str, str]],
    corpus_ids: list[str],
) -> dict[str, Any]:
    structure, structure_raw, structure_status, structure_diagnostics = _read_structure(unit)
    tokens, dictionary_raw, dictionary_status, dictionary_diagnostics = _read_dictionary(unit)
    diagnostics = structure_diagnostics + dictionary_diagnostics
    name = unit.name
    if root_kind == "repository":
        source_path = f"dwarf/grammars/{name}"
        reference_fragment = source_path
        export_base = source_path
    else:
        source_path = str(unit)
        reference_fragment = str(unit)
        export_base = f"dwarf/state/grammars/{name}"
    modified = [
        path.stat().st_mtime
        for filename in ("structure.json", "dict.txt")
        if (path := _contained_file(unit, filename)[0]) is not None
    ]
    scenario_ids = _literal_references(scenario_sources, reference_fragment)
    target_ids = _literal_references(target_sources, reference_fragment)
    grammar_id = _asset_id(root_kind, name)
    relationships = [
        {
            "catalog": "scenarios",
            "id": scenario_id,
            "label": f"Scenario {scenario_id}",
            "url": f"/operate/scenarios/{scenario_id}",
            "relation": "used-by",
            "source_path": f"{source_path}#literal-reference",
            "resolved": True,
        }
        for scenario_id in scenario_ids
    ]
    relationships.extend(
        {
            "catalog": "targets",
            "id": target_id,
            "label": f"Target {target_id}",
            "url": f"/operate/targets/{target_id}",
            "relation": "consumed-by",
            "source_path": f"{source_path}#literal-reference",
            "resolved": True,
        }
        for target_id in target_ids
    )
    relationships.extend(
        {
            "catalog": "corpora",
            "id": corpus_id,
            "label": f"Corpus {corpus_id}",
            "url": f"/operate/corpora/{corpus_id}",
            "relation": "declared-by",
            "source_path": f"{source_path}#grammar-id={name}",
            "resolved": True,
        }
        for corpus_id in corpus_ids
    )
    return {
        "id": grammar_id,
        "label": name,
        "root_kind": root_kind,
        "writable": root_kind == "runtime",
        "status": "diagnostic" if diagnostics else "ready",
        "format": structure.get("format") if isinstance(structure, dict) else None,
        "target": structure.get("target") if isinstance(structure, dict) else None,
        "decoder_entrypoint": structure.get("decoder_entrypoint") if isinstance(structure, dict) else None,
        "structure": structure,
        "structure_pretty": json.dumps(structure, indent=2, ensure_ascii=False) if structure is not None else "",
        "structure_raw": structure_raw,
        "structure_status": structure_status,
        "dictionary_raw": dictionary_raw,
        "dictionary_status": dictionary_status,
        "tokens": tokens,
        "token_count": len(tokens),
        "diagnostics": diagnostics,
        "source_path": source_path,
        "unit_root": str(unit),
        "export_base": export_base,
        "scenario_ids": scenario_ids,
        "target_ids": target_ids,
        "corpus_ids": corpus_ids,
        "relationships": relationships,
        "latest_update_epoch": max(modified) if modified else None,
        "summary": f"{len(tokens)} mutation tokens · {structure_status} structure · {root_kind}",
    }


def grammar_catalog_rows(
    *,
    repository_root: Path | None = None,
    runtime_root: Path | None = None,
    scenarios_dir: Path | None = None,
    targets_dir: Path | None = None,
    corpus_repository_root: Path | None = None,
    corpus_runtime_root: Path | None = None,
) -> list[dict[str, Any]]:
    repository, runtime = _roots(repository_root=repository_root, runtime_root=runtime_root)
    scenario_sources = _reference_sources(Path(scenarios_dir or PROJECT_ROOT / "dwarf" / "scenarios"))
    target_sources = _reference_sources(Path(targets_dir or PROJECT_ROOT / "dwarf" / "targets" / "manifests"))
    corpus_references = _corpus_reference_map(
        repository_root=corpus_repository_root,
        runtime_root=corpus_runtime_root,
    )
    rows: list[dict[str, Any]] = []
    for root_kind, root in (("repository", repository), ("runtime", runtime)):
        for unit in _units(root):
            rows.append(
                _row(
                    unit,
                    root_kind=root_kind,
                    scenario_sources=scenario_sources,
                    target_sources=target_sources,
                    corpus_ids=corpus_references.get(unit.name, []),
                )
            )
    return sorted(rows, key=lambda row: (row["label"], row["root_kind"], row["id"]))


def grammar_detail(grammar_id: str, **kwargs) -> dict[str, Any] | None:
    if not is_safe_asset_id(grammar_id):
        return None
    return next((row for row in grammar_catalog_rows(**kwargs) if row["id"] == grammar_id), None)


def _row_entries(row: dict[str, Any]) -> list[ExportSource]:
    entries: list[ExportSource] = []
    unit = Path(row["unit_root"])
    for filename in ("structure.json", "dict.txt"):
        path, _ = _contained_file(unit, filename)
        if path is not None:
            export_path = f"{row['export_base']}/{filename}"
            entries.append(
                ExportSource(
                    object_id=row["id"],
                    source_path=export_path,
                    export_path=export_path,
                    body=path.read_bytes(),
                )
            )
    return entries


def deterministic_grammar_archive(*, grammar_id: str | None = None, **kwargs) -> bytes:
    rows = grammar_catalog_rows(**kwargs)
    if grammar_id is not None:
        rows = [row for row in rows if row["id"] == grammar_id]
    return deterministic_export_archive(
        "grammars", [entry for row in rows for entry in _row_entries(row)]
    )


def _attachment(filename: str) -> dict[str, str]:
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def dispatch_grammar_api_request(path: str, **kwargs):
    parts = urlsplit(path).path.strip("/").split("/")
    if parts == ["api", "grammars", "export"]:
        return 200, "application/gzip", deterministic_grammar_archive(**kwargs), _attachment("dwarf-grammars.tar.gz")
    if len(parts) == 4 and parts[:2] == ["api", "grammars"] and parts[3] == "export":
        grammar_id = unquote(parts[2])
        if not is_safe_asset_id(grammar_id):
            return 400, "text/plain; charset=utf-8", b"invalid grammar id\n"
        if grammar_detail(grammar_id, **kwargs) is None:
            return 404, "text/plain; charset=utf-8", b"not found\n"
        return 200, "application/gzip", deterministic_grammar_archive(grammar_id=grammar_id, **kwargs), _attachment(f"{grammar_id}.tar.gz")
    if len(parts) != 5 or parts[:2] != ["api", "grammars"] or parts[4] != "download":
        return None
    grammar_id, filename = unquote(parts[2]), unquote(parts[3])
    if not is_safe_asset_id(grammar_id) or filename not in {"structure.json", "dict.txt"}:
        return 400, "text/plain; charset=utf-8", b"invalid grammar source\n"
    row = grammar_detail(grammar_id, **kwargs)
    if row is None:
        return 404, "text/plain; charset=utf-8", b"not found\n"
    source, _ = _contained_file(Path(row["unit_root"]), filename)
    if source is None:
        return 404, "text/plain; charset=utf-8", b"not found\n"
    content_type = "application/json; charset=utf-8" if filename.endswith(".json") else "text/plain; charset=utf-8"
    return 200, content_type, source.read_bytes(), _attachment(filename)


def _json_response(status: int, payload: dict[str, Any]):
    return status, "application/json; charset=utf-8", json.dumps(payload, sort_keys=True).encode()


def _request_payload(body: bytes | None) -> tuple[dict[str, Any] | None, str | None]:
    if not body or len(body) > MAX_REQUEST_BYTES:
        return None, "request body is empty or too large"
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "request body must be JSON"
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        return None, "action is required"
    return payload, None


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dwarf-grammar-", delete=False) as stream:
        temp = Path(stream.name)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _clone(row: dict[str, Any], runtime_root: Path) -> tuple[int, dict[str, Any]]:
    if row["root_kind"] != "repository":
        return 422, {"ok": False, "error": "only shipped definitions can be cloned"}
    if runtime_root.is_symlink():
        return 409, {"ok": False, "error": "runtime grammar root is unsafe"}
    runtime_root.mkdir(parents=True, exist_ok=True)
    destination = runtime_root / row["label"]
    if destination.exists() or destination.is_symlink():
        return 409, {"ok": False, "error": "runtime clone already exists"}
    entries = _row_entries(row)
    if {Path(entry.export_path).name for entry in entries} != {"structure.json", "dict.txt"}:
        return 422, {"ok": False, "error": "both source files are required for cloning"}
    stage = Path(tempfile.mkdtemp(prefix=".dwarf-grammar-clone-", dir=runtime_root))
    try:
        for entry in entries:
            (stage / Path(entry.export_path).name).write_bytes(entry.body)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return 200, {
        "ok": True,
        "result": "cloned",
        "grammar_id": runtime_grammar_id(row["label"]),
        "url": f"/operate/grammars/{runtime_grammar_id(row['label'])}",
    }


def _save(row: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if row["root_kind"] != "runtime" or not row["writable"]:
        return 403, {"ok": False, "error": "shipped grammar definitions are read-only"}
    structure = payload.get("structure")
    dictionary = payload.get("dictionary")
    if not isinstance(structure, dict) or not isinstance(dictionary, str):
        return 422, {"ok": False, "error": "structure object and dictionary text are required"}
    try:
        jsonschema.validate(structure, generation_structure_schema())
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path) or "root"
        return 422, {"ok": False, "error": f"structure.json {where}: {exc.message}"}
    _, diagnostics = parse_mutation_dictionary(dictionary)
    if diagnostics:
        return 422, {"ok": False, "error": "; ".join(diagnostics)}
    unit = Path(row["unit_root"])
    if unit.is_symlink() or not unit.is_dir():
        return 409, {"ok": False, "error": "runtime grammar directory is unsafe"}
    _atomic_write(
        unit / "structure.json",
        (json.dumps(structure, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    _atomic_write(unit / "dict.txt", dictionary.encode("utf-8"))
    return 200, {"ok": True, "result": "saved", "grammar_id": row["id"]}


def dispatch_grammar_mutating_request(
    *,
    method: str,
    path: str,
    body: bytes | None,
    expected_token: str,
    repository_root: Path | None = None,
    runtime_root: Path | None = None,
):
    parsed = urlsplit(path)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "grammars"] or parts[3] != "actions":
        return None
    if method != "POST":
        return _json_response(405, {"ok": False, "error": "use POST"})
    token = (parse_qs(parsed.query, keep_blank_values=True).get("token") or [""])[0]
    if not token or not hmac.compare_digest(token, expected_token):
        return _json_response(403, {"ok": False, "error": "missing or invalid token"})
    grammar_id = unquote(parts[2])
    if not is_safe_asset_id(grammar_id):
        return _json_response(400, {"ok": False, "error": "invalid grammar id"})
    payload, error = _request_payload(body)
    if error:
        return _json_response(422, {"ok": False, "error": error})
    repository, runtime = _roots(repository_root=repository_root, runtime_root=runtime_root)
    row = grammar_detail(grammar_id, repository_root=repository, runtime_root=runtime)
    if row is None:
        return _json_response(404, {"ok": False, "error": "grammar not found"})
    if payload["action"] == "clone":
        status, result = _clone(row, runtime)
        return _json_response(status, result)
    if payload["action"] == "save":
        status, result = _save(row, payload)
        return _json_response(status, result)
    return _json_response(422, {"ok": False, "error": "unsupported action"})
