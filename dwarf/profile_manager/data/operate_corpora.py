"""Explicit, containment-safe inventory and management for fuzz corpora."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import hmac
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
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
MAX_PREVIEW_BYTES = 256
MAX_IMPORT_BYTES = 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NOISE_NAMES = frozenset(
    {".DS_Store", "__pycache__", ".pytest_cache", ".disabled", ".trash", "corpus.json"}
)
_TEXT_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".txt", ".md", ".cddl", ".dict"})


def _repository_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_REPOSITORY_CORPORA_DIR", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "dwarf" / "corpora"


def _runtime_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_CORPORA_DIR", "").strip()
    if configured:
        return Path(configured)
    state = os.environ.get("ADA2_DWARF_STATE_DIR", "").strip()
    return (Path(state) if state else PROJECT_ROOT / "dwarf" / "state") / "corpora"


def _roots(
    *, repository_root: Path | None = None, runtime_root: Path | None = None
) -> tuple[Path, Path]:
    return Path(repository_root or _repository_root()), Path(runtime_root or _runtime_root())


@lru_cache(maxsize=1)
def corpus_record_schema() -> dict[str, Any]:
    schema_path = PROJECT_ROOT / "dwarf" / "spec" / "v1" / "corpus-record.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


_CORPUS_METADATA_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "role": {
            "type": "string",
            "enum": ["seed", "generated", "queue", "crash", "minimized", "regression", "promoted"],
        },
        "provenance": {"type": "string"},
        "source_campaign": {"type": "string"},
        "target_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "grammar_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "status": {"type": "string", "enum": ["active", "disabled", "retired"]},
    },
    "additionalProperties": False,
}


def _safe_name(name: str) -> bool:
    return bool(
        _SAFE_FILENAME.fullmatch(name)
        and name not in {".", ".."}
        and not name.startswith("._")
        and not name.endswith(".pyc")
        and name not in _NOISE_NAMES
    )


def repository_corpus_id(relative_path: str) -> str:
    """Return a readable, collision-resistant ID for one repository corpus unit."""
    pure = PurePosixPath(relative_path)
    if (
        not relative_path
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"unsafe repository corpus path: {relative_path!r}")
    readable = "--".join(pure.parts)
    digest = hashlib.sha256(pure.as_posix().encode("utf-8")).hexdigest()[:8]
    corpus_id = f"repo--{readable}--{digest}"
    if not is_safe_asset_id(corpus_id):
        raise ValueError(f"repository corpus path cannot form a safe id: {relative_path!r}")
    return corpus_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contained_file(root: Path, candidate: Path) -> Path | None:
    if candidate.is_symlink():
        return None
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _input_rows(root: Path, *, preview_bytes: int) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    if not root.exists():
        return rows, diagnostics
    if not root.is_dir() or root.is_symlink():
        return rows, [f"Corpus root is not a safe directory: {root}"]
    for candidate in sorted(root.iterdir(), key=lambda path: path.name):
        name = candidate.name
        if (
            name in _NOISE_NAMES
            or name.startswith("._")
            or name.startswith(".dwarf-corpus-")
            or name.endswith(".pyc")
        ):
            continue
        if candidate.is_symlink():
            diagnostics.append(f"Skipped symlink input: {name}")
            continue
        if candidate.is_dir():
            diagnostics.append(f"Skipped nested directory outside this corpus unit: {name}")
            continue
        if not _safe_name(name):
            diagnostics.append(f"Skipped unsafe input name: {name}")
            continue
        path = _contained_file(root, candidate)
        if path is None:
            diagnostics.append(f"Skipped input outside corpus root: {name}")
            continue
        size = path.stat().st_size
        digest = _sha256(path)
        with path.open("rb") as stream:
            prefix = stream.read(preview_bytes + 1)
        preview_body = prefix[:preview_bytes]
        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                text = preview_body.decode("utf-8", errors="strict")
                preview_kind = "text"
                preview = html.escape(text)
            except UnicodeDecodeError:
                preview_kind = "hex"
                preview = " ".join(f"{byte:02x}" for byte in preview_body)
        else:
            preview_kind = "cbor-hex" if path.suffix.lower() == ".cbor" else "hex"
            preview = " ".join(f"{byte:02x}" for byte in preview_body)
        rows.append(
            {
                "name": name,
                "path": str(path),
                "size_bytes": size,
                "sha256": digest,
                "preview_kind": preview_kind,
                "preview": preview,
                "preview_truncated": size > preview_bytes,
                "modified_at": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["sha256"]] = counts.get(row["sha256"], 0) + 1
    for row in rows:
        row["duplicate_count"] = counts[row["sha256"]]
        row["duplicate"] = counts[row["sha256"]] > 1
    return rows, diagnostics


def _read_metadata(path: Path) -> tuple[dict[str, Any], str, list[str], str | None]:
    if not path.is_file() or path.is_symlink():
        return {}, "absent", [], None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        jsonschema.validate(data, _CORPUS_METADATA_SCHEMA)
        return data, "valid", [], raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = None
        return {}, "malformed", [f"Invalid corpus.json metadata: {exc}"], raw


def _repo_units(repository_root: Path) -> list[tuple[str, str, Path, Path]]:
    units: list[tuple[str, str, Path, Path]] = []
    if not repository_root.is_dir() or repository_root.is_symlink():
        return units
    for seeds in sorted(repository_root.rglob("seeds")):
        if not seeds.is_dir() or seeds.is_symlink():
            continue
        try:
            relative_parent = seeds.parent.resolve().relative_to(repository_root.resolve())
        except (OSError, ValueError):
            continue
        if any(part in {"", ".", ".."} or part.startswith(".") for part in relative_parent.parts):
            continue
        try:
            corpus_id = repository_corpus_id(relative_parent.as_posix())
        except ValueError:
            continue
        units.append((corpus_id, relative_parent.as_posix(), seeds, seeds.parent / "corpus.json"))
    return units


def _reference_sources(root: Path) -> list[tuple[str, str]]:
    if not root.is_dir():
        return []
    sources: list[tuple[str, str]] = []
    for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"), *root.glob("*.json"))):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        sources.append((path.stem, source))
    return sources


def _reference_ids(sources: list[tuple[str, str]], fragment: str) -> list[str]:
    return sorted({source_id for source_id, source in sources if fragment in source})


def _repository_provenance(repository_root: Path, unit_root: Path) -> dict[str, Any]:
    """Read bounded, known provenance files from ancestors inside the corpus root."""
    files: list[str] = []
    manifest: dict[str, Any] = {}
    current = unit_root.parent
    root = repository_root.resolve()
    while True:
        try:
            current.resolve().relative_to(root)
        except (OSError, ValueError):
            break
        for candidate in sorted(current.glob("campaign-*.md")):
            if candidate.is_file() and not candidate.is_symlink():
                files.append(
                    f"dwarf/corpora/{candidate.resolve().relative_to(root).as_posix()}"
                )
        for name in ("README.md", "manifest.json"):
            candidate = current / name
            if candidate.is_file() and not candidate.is_symlink():
                files.append(
                    f"dwarf/corpora/{candidate.resolve().relative_to(root).as_posix()}"
                )
                if name == "manifest.json" and not manifest:
                    try:
                        loaded = json.loads(candidate.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            manifest = loaded
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        pass
        if current.resolve() == root:
            break
        current = current.parent
    generator = str(manifest.get("generator") or "")
    version = str(manifest.get("cuddle_version") or "")
    cddl_file = str(manifest.get("cddl_file") or "")
    campaign = next((Path(path).name for path in files if Path(path).name.startswith("campaign-")), "")
    return {
        "files": sorted(set(files)),
        "generator": " ".join(part for part in (generator, version) if part),
        "grammar_ids": [cddl_file] if cddl_file else [],
        "source_campaign": campaign,
    }


def _row(
    *,
    corpus_id: str,
    label: str,
    unit_root: Path,
    metadata_path: Path,
    root_kind: str,
    writable: bool,
    repository_root: Path,
    scenario_sources: list[tuple[str, str]],
    target_sources: list[tuple[str, str]],
    preview_bytes: int,
) -> dict[str, Any]:
    inputs, diagnostics = _input_rows(unit_root, preview_bytes=preview_bytes)
    metadata, metadata_status, metadata_diagnostics, raw_metadata = _read_metadata(metadata_path)
    diagnostics.extend(metadata_diagnostics)
    hashes: dict[str, int] = {}
    for item in inputs:
        hashes[item["sha256"]] = hashes.get(item["sha256"], 0) + 1
    duplicate_count = sum(count - 1 for count in hashes.values() if count > 1)
    provenance_files: list[str] = []
    inferred_provenance = ""
    inferred_campaign = ""
    inferred_grammars: list[str] = []
    if root_kind == "repository":
        relative = unit_root.resolve().relative_to(repository_root.resolve()).as_posix()
        source_path = f"dwarf/corpora/{relative}"
        reference_fragment = f"corpora/{relative}"
        export_base = f"dwarf/corpora/{relative}"
        provenance = _repository_provenance(repository_root, unit_root)
        provenance_files = provenance["files"]
        inferred_provenance = provenance["generator"]
        inferred_campaign = provenance["source_campaign"]
        inferred_grammars = provenance["grammar_ids"]
    else:
        source_path = str(unit_root)
        reference_fragment = str(unit_root)
        export_base = "dwarf/state/corpora"
    source_target_ids = set(_reference_ids(target_sources, reference_fragment))
    target_ids = sorted(set(metadata.get("target_ids", [])) | source_target_ids)
    scenario_ids = _reference_ids(scenario_sources, reference_fragment)
    grammar_ids = sorted(set(metadata.get("grammar_ids", [])) | set(inferred_grammars))
    disabled = (unit_root / ".disabled").is_file() if unit_root.is_dir() else False
    declared_status = metadata.get("status")
    status = (
        "disabled"
        if disabled or declared_status == "disabled"
        else "retired"
        if declared_status == "retired"
        else "diagnostic"
        if diagnostics
        else "ready"
        if inputs
        else "empty"
    )
    latest_update = max((item["modified_at"] for item in inputs), default=None)
    record = {
        "id": corpus_id,
        "label": label,
        "root_kind": root_kind,
        "writable": writable,
        "status": status,
        "input_count": len(inputs),
        "size_bytes": sum(item["size_bytes"] for item in inputs),
        "duplicate_input_count": duplicate_count,
        "metadata_status": metadata_status,
        "source_path": source_path,
        "target_ids": target_ids,
        "grammar_ids": grammar_ids,
        "scenario_ids": scenario_ids,
    }
    jsonschema.validate(record, corpus_record_schema())
    relationships = [
        {
            "catalog": "targets",
            "id": target_id,
            "label": f"Target {target_id}",
            "url": f"/operate/targets/{target_id}" if target_id in source_target_ids else None,
            "relation": "consumed-by",
            "source_path": f"{source_path}#target_ids",
            "resolved": target_id in source_target_ids,
        }
        for target_id in target_ids
    ]
    relationships.extend(
        {
            "catalog": "scenarios",
            "id": scenario_id,
            "label": f"Scenario {scenario_id}",
            "url": f"/operate/scenarios/{scenario_id}",
            "relation": "used-by",
            "source_path": f"{source_path}#scenario-reference",
            "resolved": True,
        }
        for scenario_id in scenario_ids
    )
    # corpus.json and campaign manifests use grammar names, while the grammar
    # catalog uses root-qualified IDs. Preserve the declaration without
    # manufacturing a URL from a name that may be ambiguous.
    relationships.extend(
        {
            "catalog": "grammars",
            "id": grammar_id,
            "label": f"Generation grammar {grammar_id}",
            "url": None,
            "relation": "generated-from",
            "source_path": f"{source_path}#grammar_ids",
            "resolved": False,
        }
        for grammar_id in grammar_ids
    )
    return {
        **record,
        "role": metadata.get("role") or ("seed" if root_kind == "repository" else "promoted"),
        "provenance": metadata.get("provenance") or inferred_provenance or ("shipped repository corpus" if root_kind == "repository" else "operator-managed runtime overlay"),
        "source_campaign": metadata.get("source_campaign") or inferred_campaign,
        "provenance_files": provenance_files,
        "latest_update": latest_update,
        "diagnostics": diagnostics,
        "inputs": inputs,
        "metadata": metadata,
        "raw_metadata": raw_metadata,
        "metadata_path": str(metadata_path),
        "unit_root": str(unit_root),
        "export_base": export_base,
        "record": record,
        "relationships": relationships,
        "grammar_relationships": [
            relationship
            for relationship in relationships
            if relationship["catalog"] == "grammars"
        ],
        "target_relationships": [
            relationship
            for relationship in relationships
            if relationship["catalog"] == "targets"
        ],
        "summary": f"{len(inputs)} inputs · {sum(item['size_bytes'] for item in inputs)} bytes · {root_kind}",
    }


def corpus_catalog_rows(
    *,
    repository_root: Path | None = None,
    runtime_root: Path | None = None,
    scenarios_dir: Path | None = None,
    targets_dir: Path | None = None,
    preview_bytes: int = MAX_PREVIEW_BYTES,
) -> list[dict[str, Any]]:
    repository, runtime = _roots(repository_root=repository_root, runtime_root=runtime_root)
    scenarios = Path(scenarios_dir or PROJECT_ROOT / "dwarf" / "scenarios")
    targets = Path(targets_dir or PROJECT_ROOT / "dwarf" / "targets" / "manifests")
    scenario_sources = _reference_sources(scenarios)
    target_sources = _reference_sources(targets)
    rows = [
        _row(
            corpus_id=corpus_id,
            label=label,
            unit_root=unit_root,
            metadata_path=metadata_path,
            root_kind="repository",
            writable=False,
            repository_root=repository,
            scenario_sources=scenario_sources,
            target_sources=target_sources,
            preview_bytes=max(1, min(int(preview_bytes), MAX_PREVIEW_BYTES)),
        )
        for corpus_id, label, unit_root, metadata_path in _repo_units(repository)
    ]
    rows.append(
        _row(
            corpus_id="runtime--overlay",
            label="Runtime overlay",
            unit_root=runtime,
            metadata_path=runtime / "corpus.json",
            root_kind="runtime",
            writable=True,
            repository_root=repository,
            scenario_sources=scenario_sources,
            target_sources=target_sources,
            preview_bytes=max(1, min(int(preview_bytes), MAX_PREVIEW_BYTES)),
        )
    )
    return sorted(rows, key=lambda row: row["id"])


def corpus_detail(corpus_id: str, **kwargs) -> dict[str, Any] | None:
    if not is_safe_asset_id(corpus_id):
        return None
    return next((row for row in corpus_catalog_rows(**kwargs) if row["id"] == corpus_id), None)


def deterministic_corpora_archive(*, corpus_id: str | None = None, **kwargs) -> bytes:
    sources: list[ExportSource] = []
    rows = corpus_catalog_rows(**kwargs)
    if corpus_id is not None:
        rows = [row for row in rows if row["id"] == corpus_id]
    for row in rows:
        for item in row["inputs"]:
            export_path = f"{row['export_base']}/{item['name']}"
            sources.append(
                ExportSource(
                    object_id=row["id"],
                    source_path=export_path,
                    export_path=export_path,
                    body=Path(item["path"]).read_bytes(),
                )
            )
        metadata_path = Path(row["metadata_path"])
        if metadata_path.is_file() and not metadata_path.is_symlink():
            export_path = f"{row['export_base']}/corpus.json"
            sources.append(
                ExportSource(
                    object_id=row["id"],
                    source_path=export_path,
                    export_path=export_path,
                    body=metadata_path.read_bytes(),
                )
            )
    return deterministic_export_archive("corpora", sources)


def _attachment(filename: str) -> dict[str, str]:
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def dispatch_corpus_api_request(path: str, **kwargs):
    parsed = urlsplit(path)
    encoded = parsed.path.strip("/").split("/")
    if encoded == ["api", "corpora", "export"]:
        return (
            200,
            "application/gzip",
            deterministic_corpora_archive(**kwargs),
            _attachment("dwarf-corpora.tar.gz"),
        )
    if len(encoded) == 4 and encoded[:2] == ["api", "corpora"] and encoded[3] == "export":
        corpus_id = unquote(encoded[2])
        if not is_safe_asset_id(corpus_id):
            return (400, "text/plain; charset=utf-8", b"invalid corpus id\n")
        if corpus_detail(corpus_id, **kwargs) is None:
            return (404, "text/plain; charset=utf-8", b"not found\n")
        return (
            200,
            "application/gzip",
            deterministic_corpora_archive(corpus_id=corpus_id, **kwargs),
            _attachment(f"{corpus_id}.tar.gz"),
        )
    if len(encoded) != 6 or encoded[:2] != ["api", "corpora"] or encoded[3] != "inputs" or encoded[5] != "download":
        return None
    corpus_id, filename = unquote(encoded[2]), unquote(encoded[4])
    if not is_safe_asset_id(corpus_id) or not _safe_name(filename):
        return (400, "text/plain; charset=utf-8", b"invalid corpus input\n")
    detail = corpus_detail(corpus_id, **kwargs)
    if detail is None:
        return (404, "text/plain; charset=utf-8", b"not found\n")
    item = next((candidate for candidate in detail["inputs"] if candidate["name"] == filename), None)
    if item is None:
        return (404, "text/plain; charset=utf-8", b"not found\n")
    return (
        200,
        "application/octet-stream",
        Path(item["path"]).read_bytes(),
        _attachment(filename),
    )


def _json_response(status: int, payload: dict[str, Any]):
    return status, "application/json; charset=utf-8", json.dumps(payload, sort_keys=True).encode("utf-8")


def _parse_action(body: bytes | None) -> tuple[dict[str, Any] | None, str | None]:
    if not body or len(body) > MAX_IMPORT_BYTES * 2:
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
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dwarf-corpus-", delete=False) as stream:
        temp_path = Path(stream.name)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _runtime_inputs(runtime_root: Path) -> list[dict[str, Any]]:
    return _input_rows(runtime_root, preview_bytes=1)[0]


def _import_bytes(runtime_root: Path, filename: str, body: bytes) -> tuple[int, dict[str, Any]]:
    if not _safe_name(filename):
        return 422, {"ok": False, "error": "unsafe filename"}
    if len(body) > MAX_IMPORT_BYTES:
        return 422, {"ok": False, "error": f"input exceeds {MAX_IMPORT_BYTES} bytes"}
    digest = hashlib.sha256(body).hexdigest()
    existing = sorted(_runtime_inputs(runtime_root), key=lambda item: item["name"])
    duplicate = next((item for item in existing if item["sha256"] == digest), None)
    if duplicate is not None:
        return 200, {"ok": True, "result": "duplicate", "filename": duplicate["name"], "sha256": digest}
    target = runtime_root / filename
    if target.exists() or target.is_symlink():
        return 409, {"ok": False, "error": "filename already exists with different bytes"}
    _atomic_write(target, body)
    return 200, {"ok": True, "result": "imported", "filename": filename, "sha256": digest}


def _trash(runtime_root: Path, item: dict[str, Any], reason: str) -> None:
    source = Path(item["path"])
    destination = runtime_root / ".trash" / reason / item["sha256"] / item["name"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_file() and _sha256(destination) == item["sha256"]:
            source.unlink()
            return
        raise FileExistsError(f"trash destination collision: {destination}")
    os.replace(source, destination)


def dispatch_corpus_mutating_request(
    *,
    method: str,
    path: str,
    body: bytes | None,
    expected_token: str,
    repository_root: Path | None = None,
    runtime_root: Path | None = None,
):
    parsed = urlsplit(path)
    encoded = parsed.path.strip("/").split("/")
    if len(encoded) != 4 or encoded[:2] != ["api", "corpora"] or encoded[3] != "actions":
        return None
    if method != "POST":
        return _json_response(405, {"ok": False, "error": "use POST"})
    token = (parse_qs(parsed.query, keep_blank_values=True).get("token") or [""])[0]
    if not token or not hmac.compare_digest(token, expected_token):
        return _json_response(403, {"ok": False, "error": "missing or invalid token"})
    corpus_id = unquote(encoded[2])
    if corpus_id != "runtime--overlay":
        return _json_response(403, {"ok": False, "error": "shipped corpora are read-only"})
    payload, error = _parse_action(body)
    if error:
        return _json_response(422, {"ok": False, "error": error})
    _, runtime = _roots(repository_root=repository_root, runtime_root=runtime_root)
    action = payload["action"]
    if action == "import":
        filename = payload.get("filename")
        encoded_body = payload.get("content_base64")
        if not isinstance(filename, str) or not _safe_name(filename) or not isinstance(encoded_body, str):
            return _json_response(422, {"ok": False, "error": "safe filename and base64 content are required"})
        try:
            decoded = base64.b64decode(encoded_body, validate=True)
        except (ValueError, binascii.Error):
            return _json_response(422, {"ok": False, "error": "invalid base64 content"})
        status, result = _import_bytes(runtime, filename, decoded)
        return _json_response(status, result)
    if action == "promote":
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not is_safe_asset_id(case_id):
            return _json_response(422, {"ok": False, "error": "safe testcase id is required"})
        from profile_manager.data.operate_testcases import testcase_artifact

        state, artifact = testcase_artifact(case_id)
        if state != "recorded" or artifact is None:
            return _json_response(404, {"ok": False, "error": "retained testcase artifact not found"})
        body_bytes = artifact.read_bytes()
        suffix = artifact.suffix if _safe_name(f"x{artifact.suffix}") else ".bin"
        filename = f"promoted-{case_id}-{hashlib.sha256(body_bytes).hexdigest()[:12]}{suffix}"
        status, result = _import_bytes(runtime, filename, body_bytes)
        if status == 200 and result.get("result") == "imported":
            result["result"] = "promoted"
        return _json_response(status, result)
    if action == "deduplicate":
        inputs = sorted(_runtime_inputs(runtime), key=lambda item: item["name"])
        by_hash: dict[str, list[dict[str, Any]]] = {}
        for item in inputs:
            by_hash.setdefault(item["sha256"], []).append(item)
        moved: list[str] = []
        for digest in sorted(by_hash):
            for item in by_hash[digest][1:]:
                _trash(runtime, item, "deduplicated")
                moved.append(item["name"])
        return _json_response(200, {"ok": True, "result": "deduplicated", "moved": moved})
    if action == "disable":
        _atomic_write(runtime / ".disabled", b"disabled\n")
        return _json_response(200, {"ok": True, "result": "disabled"})
    if action == "enable":
        marker = runtime / ".disabled"
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            return _json_response(409, {"ok": False, "error": "unsafe disable marker"})
        if marker.exists():
            marker.unlink()
        return _json_response(200, {"ok": True, "result": "enabled"})
    if action == "remove":
        filename = payload.get("filename")
        if not isinstance(filename, str) or not _safe_name(filename):
            return _json_response(422, {"ok": False, "error": "safe filename is required"})
        item = next((candidate for candidate in _runtime_inputs(runtime) if candidate["name"] == filename), None)
        if item is None:
            return _json_response(404, {"ok": False, "error": "input not found"})
        _trash(runtime, item, "removed")
        return _json_response(200, {"ok": True, "result": "removed", "filename": filename})
    return _json_response(422, {"ok": False, "error": "unsupported action"})
