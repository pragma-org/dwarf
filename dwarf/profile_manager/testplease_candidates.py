"""Identify ordinary source submissions without running any candidate code.

The caller supplies the trusted repository commit; candidate identity additionally
binds every on-disk entry, including ignored/untracked files and build support.
Callers must keep the source checkout stable while scanning and consume the same
frozen content later. A snapshot is an immutable identity record, not a file copy.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib


_IMPLEMENTATIONS = ("amaru", "cardano-node")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class CandidateIntakeError(ValueError):
    """A source submission cannot safely and unambiguously be identified."""


@dataclass(frozen=True)
class CandidateSnapshot:
    implementation: str
    candidate_name: str
    repository_commit: str
    source_tree_digest: str
    source_root: Path


def _directory(path: Path) -> bool:
    """Check control directories without following directory aliases."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise CandidateIntakeError(f"symlink is not allowed for intake directories: {path}")
    if not stat.S_ISDIR(mode):
        raise CandidateIntakeError(f"expected candidate directory: {path}")
    return True


def _validate_symlink(root: Path, path: Path) -> None:
    """Resolve link components only while they remain inside the source root.

    Do not traverse an external directory even to inspect another symlink there.
    Directory links are validated but never recursively scanned.
    """
    pending = deque(path.relative_to(root).parts)
    current = root
    hops = 0
    while pending:
        part = pending.popleft()
        if part == "..":
            if current == root:
                raise CandidateIntakeError(f"symlink escapes candidate root: {path}")
            current = current.parent
            continue
        if part == ".":
            continue
        current = current / part
        try:
            mode = current.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise CandidateIntakeError(f"dangling symlink in candidate: {path}") from exc
        if stat.S_ISLNK(mode):
            hops += 1
            if hops > 40:
                raise CandidateIntakeError(f"cyclic or excessive symlink chain: {path}")
            target_text = os.readlink(current)
            target = Path(target_text)
            current = current.parent
            if target.is_absolute():
                if not target.is_relative_to(root):
                    raise CandidateIntakeError(f"symlink escapes candidate root: {path}")
                target = target.relative_to(root)
                current = root
            target_parts = list(target.parts)
            # Path drops trailing '/' and '/.'. Both require the final target
            # to be a directory, including when it resolves through more links.
            if target_text.endswith(("/", "/.")):
                target_parts.append(".")
            pending.extendleft(reversed(target_parts))
        elif pending and not stat.S_ISDIR(mode):
            raise CandidateIntakeError(f"non-directory component in symlink target: {path}")


def _tree_entries(root: Path) -> dict[str, int]:
    entries: dict[str, int] = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            entries[relative] = mode
            if stat.S_ISLNK(mode):
                _validate_symlink(root, path)
            elif stat.S_ISDIR(mode):
                visit(path)
            elif not stat.S_ISREG(mode):
                raise CandidateIntakeError(f"unsupported file type in candidate: {path}")

    visit(root)
    return entries


def _layouts(root: Path, entries: dict[str, int], prefix: str = "") -> tuple[str, ...]:
    """Recognize the Cargo package and Cabal source surfaces used by builders.

    This validates source structure, not buildability or manifest dependency
    semantics. Versions and toolchains belong to subsequent run selection.
    """
    def regular(name: str) -> bool:
        return stat.S_ISREG(entries.get(prefix + name, 0))

    def text(name: str) -> str:
        return (root / (prefix + name)).read_text(encoding="utf-8")

    found = []
    if all(regular(name) for name in ("Cargo.toml", "crates/amaru/Cargo.toml")):
        try:
            workspace = tomllib.loads(text("Cargo.toml"))
            package = tomllib.loads(text("crates/amaru/Cargo.toml"))
            binary_paths = ["src/main.rs"]
            for binary in package.get("bin", []):
                if isinstance(binary, dict) and binary.get("name") == "amaru" and isinstance(binary.get("path"), str):
                    binary_paths.append(binary["path"])
            has_binary = any(
                not Path(path).is_absolute()
                and ".." not in Path(path).parts
                and regular("crates/amaru/" + Path(path).as_posix())
                for path in binary_paths
            )
            if (isinstance(workspace.get("workspace"), dict)
                    and package.get("package", {}).get("name") == "amaru"
                    and has_binary):
                found.append("amaru")
        except (tomllib.TOMLDecodeError, UnicodeError, AttributeError, TypeError):
            pass
    if regular("cabal.project") and regular("cardano-node/cardano-node.cabal"):
        source_prefix = prefix + "cardano-node/src/"
        if any(name.startswith(source_prefix) and name.endswith(".hs") and stat.S_ISREG(mode)
               for name, mode in entries.items()):
            try:
                if re.search(r"(?mi)^name\s*:\s*cardano-node\s*(?:--[^\n]*)?$", text("cardano-node/cardano-node.cabal")):
                    found.append("cardano-node")
            except UnicodeError:
                pass
    return tuple(found)


def _tree_digest(root: Path, entries: dict[str, int]) -> str:
    digest = hashlib.sha256(b"dwarf-testplease-source-tree-v1\0")
    for relative, mode in sorted(entries.items()):
        path = root / relative
        record: list[object] = [relative]
        if stat.S_ISLNK(mode):
            record.extend(["symlink", os.readlink(path)])
        elif stat.S_ISDIR(mode):
            record.append("directory")
        else:
            content = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    content.update(block)
            record.extend(["file", bool(mode & 0o111), size, content.hexdigest()])
        encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def discover_candidates(repository_root: Path, *, repository_commit: str) -> tuple[CandidateSnapshot, ...]:
    """Scan ``dwarf/testplease/<implementation>/<candidate>/`` deterministically.

    ``repository_commit`` must be the full lowercase 40-hex identity obtained by
    the trusted caller (for example GITHUB_SHA), not candidate-supplied metadata.
    An absent or empty intake returns (). Any invalid submission rejects the
    scan with CandidateIntakeError; no partial result is returned.
    """
    if not isinstance(repository_commit, str) or not _COMMIT.fullmatch(repository_commit):
        raise CandidateIntakeError("repository commit must be a full lowercase 40-hex Git object ID")
    repository_root = Path(repository_root).resolve()
    intake = repository_root / "dwarf" / "testplease"
    try:
        if not _directory(repository_root / "dwarf") or not _directory(intake):
            return ()
        for entry in intake.iterdir():
            if entry.name in _IMPLEMENTATIONS:
                continue
            if entry.name == "README.md" and stat.S_ISREG(entry.lstat().st_mode):
                continue
            raise CandidateIntakeError(f"unsupported intake entry: {entry}")
        snapshots = []
        for implementation in _IMPLEMENTATIONS:
            parent = intake / implementation
            if not _directory(parent):
                continue
            for source in sorted(parent.iterdir(), key=lambda item: item.name):
                if source.name == ".gitkeep" and stat.S_ISREG(source.lstat().st_mode):
                    continue
                if not _NAME.fullmatch(source.name):
                    raise CandidateIntakeError(f"unsafe candidate name: {source.name!r}")
                if not _directory(source):
                    raise CandidateIntakeError(f"expected candidate directory: {source}")
                entries = _tree_entries(source)
                layouts = _layouts(source, entries)
                if len(layouts) > 1:
                    raise CandidateIntakeError(f"ambiguous layout for {implementation}/{source.name}")
                for name, mode in entries.items():
                    if stat.S_ISDIR(mode) and _layouts(source, entries, name + "/"):
                        raise CandidateIntakeError(f"nested candidate root in {implementation}/{source.name}: {name}")
                if layouts != (implementation,):
                    raise CandidateIntakeError(f"unsupported layout for {implementation}/{source.name}: expected ordinary upstream {implementation} source")
                snapshots.append(CandidateSnapshot(
                    implementation=implementation,
                    candidate_name=source.name,
                    repository_commit=repository_commit,
                    source_tree_digest=_tree_digest(source, entries),
                    source_root=source,
                ))
        return tuple(snapshots)
    except OSError as exc:
        raise CandidateIntakeError(f"cannot inspect source intake: {exc}") from exc
