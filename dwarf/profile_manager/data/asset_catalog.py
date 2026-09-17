"""Shared data contracts for repository-backed dashboard asset catalogs.

Catalog-specific modules own discovery and domain semantics.  This module only
normalizes the records they expose, enforces stable identifiers, and creates
deterministic source-only exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import tarfile
from typing import Any, Callable, Mapping, Sequence


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IGNORED_NAMES = frozenset({".DS_Store", "__pycache__", ".pytest_cache"})
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "secrets",
        "secrets.json",
    }
)
EXPORT_MANIFEST = "DWARF-EXPORT-MANIFEST.json"


class UnsafeAssetPathError(ValueError):
    """Raised when an exported source path could escape its archive."""


@dataclass(frozen=True)
class CatalogItem:
    """Normalized, immutable record rendered by the shared catalog views."""

    catalog: str
    item_id: str
    label: str
    source_path: str
    status: str
    facets: Mapping[str, tuple[str, ...]]
    summary: str
    raw_text: str
    export_path: str
    relationships: tuple[Mapping[str, Any], ...] = ()
    content_type: str = "text/plain; charset=utf-8"

    @property
    def source_bytes(self) -> bytes:
        return self.raw_text.encode("utf-8")


@dataclass(frozen=True)
class ExportSource:
    """One explicitly selected source file in a portable asset export."""

    object_id: str
    source_path: str
    export_path: str
    body: bytes


@dataclass(frozen=True)
class AssetEdge:
    """One source-proven relationship; unresolved targets stay explicit."""

    source: str
    target: str
    relation: str
    source_path: str
    resolved: bool


@dataclass(frozen=True)
class AssetGraph:
    nodes: frozenset[str]
    edges: tuple[AssetEdge, ...]

    def has_edge(self, source: str, target: str) -> bool:
        return any(edge.source == source and edge.target == target for edge in self.edges)

    def edge(self, source: str, target: str) -> AssetEdge:
        return next(
            edge
            for edge in self.edges
            if edge.source == source and edge.target == target
        )


@dataclass(frozen=True)
class AssetCatalog:
    """Definition of one explicitly allow-listed dashboard catalog."""

    slug: str
    label: str
    singular_label: str
    description: str
    active_sub: str
    load_items: Callable[[], Sequence[CatalogItem]]

    def items(self) -> tuple[CatalogItem, ...]:
        loaded = tuple(self.load_items())
        seen: set[str] = set()
        for item in loaded:
            if item.catalog != self.slug:
                raise ValueError(
                    f"asset {item.item_id!r} belongs to {item.catalog!r}, "
                    f"expected {self.slug!r}"
                )
            if not is_safe_asset_id(item.item_id):
                raise ValueError(f"unsafe asset id: {item.item_id!r}")
            if item.item_id in seen:
                raise ValueError(f"duplicate asset id: {item.item_id!r}")
            seen.add(item.item_id)
        return tuple(sorted(loaded, key=lambda item: item.item_id))

    def find(self, item_id: str) -> CatalogItem | None:
        if not is_safe_asset_id(item_id):
            return None
        return next((item for item in self.items() if item.item_id == item_id), None)


class AssetCatalogRegistry:
    """Small explicit registry; unregistered URL slugs are never filesystem input."""

    def __init__(self) -> None:
        self._catalogs: dict[str, AssetCatalog] = {}

    def register(self, catalog: AssetCatalog) -> None:
        if not is_safe_asset_id(catalog.slug):
            raise ValueError(f"unsafe catalog slug: {catalog.slug!r}")
        if catalog.slug in self._catalogs:
            raise ValueError(f"catalog already registered: {catalog.slug}")
        self._catalogs[catalog.slug] = catalog

    def get(self, slug: str) -> AssetCatalog | None:
        if not is_safe_asset_id(slug):
            return None
        return self._catalogs.get(slug)

    def slugs(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogs))


DEFAULT_REGISTRY = AssetCatalogRegistry()


def is_safe_asset_id(value: str) -> bool:
    """Return true only for one portable URL segment."""

    return bool(value and value not in {".", ".."} and _SAFE_ID.fullmatch(value))


def _archive_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or candidate.is_absolute()
        or (candidate.parts and candidate.parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise UnsafeAssetPathError(f"unsafe export path: {path!r}")
    return candidate


def _is_export_noise(path: PurePosixPath) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if (
            part in _IGNORED_NAMES
            or part.startswith("._")
            or part.endswith(".pyc")
            or lowered in _SENSITIVE_NAMES
            or lowered.startswith(".env.")
            or lowered.startswith("secret-")
            or lowered.startswith("credentials-")
            or lowered.endswith((".pem", ".private-key"))
        ):
            return True
    return False


def build_asset_graph(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> AssetGraph:
    """Build reciprocal edges only from explicit relationship declarations.

    Names, labels, paths, and similar strings are never used to guess a link.
    A relationship target may be absent; the edge remains visible with
    ``resolved=False`` instead of becoming a dangling dashboard hyperlink.
    """

    nodes = frozenset(
        f"{catalog}:{record['id']}"
        for catalog, catalog_records in records.items()
        for record in catalog_records
        if isinstance(record.get("id"), str) and record["id"]
    )
    edges: set[AssetEdge] = set()
    for catalog in sorted(records):
        for record in records[catalog]:
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                continue
            source = f"{catalog}:{record_id}"
            relationships = record.get("relationships") or []
            if not isinstance(relationships, (list, tuple)):
                continue
            for relationship in relationships:
                if not isinstance(relationship, Mapping):
                    continue
                target_catalog = relationship.get("catalog")
                target_id = relationship.get("id")
                if not isinstance(target_catalog, str) or not target_catalog:
                    continue
                if not isinstance(target_id, str) or not target_id:
                    continue
                target = f"{target_catalog}:{target_id}"
                relation = str(relationship.get("relation") or "references")
                source_path = str(
                    relationship.get("source_path") or record.get("source_path") or ""
                )
                resolved = target in nodes
                edges.add(AssetEdge(source, target, relation, source_path, resolved))
                edges.add(
                    AssetEdge(
                        target,
                        source,
                        "referenced-by",
                        source_path,
                        resolved,
                    )
                )
    return AssetGraph(
        nodes=nodes,
        edges=tuple(
            sorted(
                edges,
                key=lambda edge: (
                    edge.source,
                    edge.target,
                    edge.relation,
                    edge.source_path,
                ),
            )
        ),
    )


def _deterministic_generation_time(explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit
    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", "0")
    try:
        epoch = max(0, int(raw_epoch))
    except ValueError:
        epoch = 0
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def deterministic_export_archive(
    catalog: str,
    sources: Sequence[ExportSource],
    *,
    source_revision: str | None = None,
    generation_time: str | None = None,
) -> bytes:
    """Create one reproducible, source-only archive with a provenance manifest."""

    selected: list[tuple[str, bytes, ExportSource]] = []
    seen_paths: set[str] = set()
    for source in sources:
        path = _archive_path(source.export_path)
        if _is_export_noise(path):
            continue
        normalized = path.as_posix()
        if normalized == EXPORT_MANIFEST or normalized in seen_paths:
            raise ValueError(f"duplicate or reserved export path: {normalized}")
        seen_paths.add(normalized)
        selected.append((normalized, bytes(source.body), source))

    files = [
        {
            "object_id": source.object_id,
            "source_path": source.source_path,
            "export_path": path,
            "sha256": hashlib.sha256(body).hexdigest(),
            "size_bytes": len(body),
        }
        for path, body, source in sorted(selected, key=lambda item: item[0])
    ]
    manifest = {
        "schema_version": "dwarf-asset-export-v1",
        "catalog": catalog,
        "source_revision": source_revision
        if source_revision is not None
        else os.environ.get("DWARF_SOURCE_REVISION", "unknown"),
        "generated_at": _deterministic_generation_time(generation_time),
        "object_ids": sorted({record["object_id"] for record in files}),
        "objects": files,
        "files": files,
    }
    manifest_body = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    archive_entries = [(EXPORT_MANIFEST, manifest_body)] + [
        (path, body) for path, body, _source in selected
    ]

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, body in sorted(archive_entries):
            info = tarfile.TarInfo(path)
            info.size = len(body)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(body))

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        compressed.write(tar_buffer.getvalue())
    return output.getvalue()


def deterministic_asset_archive(
    catalog: AssetCatalog,
    *,
    item_id: str | None = None,
    source_revision: str | None = None,
    generation_time: str | None = None,
) -> bytes:
    """Return a reproducible tar.gz containing only catalog source records."""

    items = catalog.items()
    if item_id is not None:
        items = tuple(item for item in items if item.item_id == item_id)
    return deterministic_export_archive(
        catalog.slug,
        [
            ExportSource(
                object_id=item.item_id,
                source_path=item.source_path,
                export_path=item.export_path,
                body=item.source_bytes,
            )
            for item in items
        ],
        source_revision=source_revision,
        generation_time=generation_time,
    )


def catalog_archive_filename(catalog: AssetCatalog) -> str:
    return f"dwarf-{catalog.slug}.tar.gz"
