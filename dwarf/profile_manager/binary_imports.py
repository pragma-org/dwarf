"""Bounded, inert intake for precompiled node binary imports.

This module may inspect bytes and persist them for the controller.  It must not
execute an uploaded program or invoke Docker.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import gzip
import os
import re
import shutil
import stat
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import parse_qs


class BinaryImportError(ValueError):
    """An uploaded artifact failed the bounded import contract."""


MAX_IMPORT_REQUEST_BYTES = 256 * 1024 * 1024 + 64 * 1024


@dataclass(frozen=True)
class ImportLimits:
    max_upload_bytes: int = 256 * 1024 * 1024
    max_executable_bytes: int = 256 * 1024 * 1024
    max_expanded_bytes: int = 512 * 1024 * 1024
    max_archive_members: int = 64
    max_archive_stream_bytes: int = 514 * 1024 * 1024
    max_archive_metadata_bytes: int = 1024 * 1024
    staging_lifetime_seconds: int = 60 * 60


@dataclass(frozen=True)
class UploadedBinary:
    implementation: str
    executable_name: str
    platform: str
    upload_sha256: str
    executable_sha256: str
    executable: bytes


@dataclass(frozen=True)
class ParsedBinaryImport:
    expected_revision: str
    version: str
    declared_source_revision: str | None
    artifact: UploadedBinary


_EXECUTABLE_NAMES = {"amaru": "amaru", "cardano-node": "cardano-node"}
_ARCHITECTURES = {
    "amd64": (62, "linux/amd64"),
    "x86_64": (62, "linux/amd64"),
    "arm64": (183, "linux/arm64"),
    "aarch64": (183, "linux/arm64"),
}
_ELF_MACHINES = {62: "linux/amd64", 183: "linux/arm64"}
_IMPORT_ID = re.compile(r"^import-[0-9a-f]{24}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_REVISION = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _archive_name(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith((".tar", ".tar.gz", ".tgz"))


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in {"", ".."} for part in path.parts)


def _archive_executable(
    payload: bytes,
    *,
    executable_name: str,
    limits: ImportLimits,
) -> bytes:
    archive_stream: io.BytesIO | tempfile.SpooledTemporaryFile[bytes]
    if payload.startswith(b"\x1f\x8b"):
        archive_stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        expanded_stream = 0
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as compressed:
                while True:
                    chunk = compressed.read(1024 * 1024)
                    if not chunk:
                        break
                    expanded_stream += len(chunk)
                    if expanded_stream > limits.max_archive_stream_bytes:
                        raise BinaryImportError("archive stream is too large")
                    archive_stream.write(chunk)
            archive_stream.seek(0)
        except (gzip.BadGzipFile, OSError, EOFError) as exc:
            archive_stream.close()
            raise BinaryImportError("uploaded archive is invalid") from exc
    else:
        if len(payload) > limits.max_archive_stream_bytes:
            raise BinaryImportError("archive stream is too large")
        archive_stream = io.BytesIO(payload)

    class BoundedArchiveReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            if size < 0 or size > limits.max_archive_metadata_bytes:
                raise BinaryImportError("archive metadata record is too large")
            return self.stream.read(size)

        def seek(self, *args):
            return self.stream.seek(*args)

        def tell(self):
            return self.stream.tell()

    try:
        archive = tarfile.open(fileobj=BoundedArchiveReader(archive_stream), mode="r:")
    except (tarfile.TarError, OSError) as exc:
        archive_stream.close()
        raise BinaryImportError("uploaded archive is invalid") from exc
    with archive_stream, archive:
        expanded = 0
        elf_matches: list[tarfile.TarInfo] = []
        member_count = 0
        # TarFile iteration calls next() lazily.  Enforce the count and the
        # declared expanded size before asking the parser to advance again or
        # reading any member body; getmembers() would defeat these ceilings.
        for member in archive:
            member_count += 1
            if member_count > limits.max_archive_members:
                raise BinaryImportError("uploaded archive has too many entries")
            if not _safe_member_name(member.name):
                raise BinaryImportError("unsafe archive member path")
            if not (member.isdir() or member.isfile()):
                raise BinaryImportError(
                    "archive may contain only regular files and directories"
                )
            expanded += member.size
            if expanded > limits.max_expanded_bytes:
                raise BinaryImportError("archive expanded content is too large")
            if member.isfile() and PurePosixPath(member.name).name == executable_name:
                prefix_stream = archive.extractfile(member)
                if prefix_stream is not None:
                    with prefix_stream:
                        if prefix_stream.read(4) == b"\x7fELF":
                            elf_matches.append(member)
        if len(elf_matches) != 1:
            raise BinaryImportError(
                f"archive must contain exactly one {executable_name} ELF executable"
            )
        selected = elf_matches[0]
        if selected.size > limits.max_executable_bytes:
            raise BinaryImportError("uploaded executable is too large")
        stream = archive.extractfile(selected)
        if stream is None:
            raise BinaryImportError("uploaded executable is unavailable")
        chunks = []
        executable_size = 0
        while True:
            chunk = stream.read(min(1024 * 1024, limits.max_executable_bytes + 1))
            if not chunk:
                break
            executable_size += len(chunk)
            if executable_size > limits.max_executable_bytes:
                raise BinaryImportError("uploaded executable is too large")
            chunks.append(chunk)
        return b"".join(chunks)


def _elf_platform(payload: bytes, *, host_arch: str) -> str:
    requested = _ARCHITECTURES.get(host_arch.lower())
    if requested is None:
        raise BinaryImportError("deployment architecture is unsupported")
    if (
        len(payload) < 20
        or payload[:4] != b"\x7fELF"
        or payload[4] != 2
        or payload[5] != 1
    ):
        raise BinaryImportError("uploaded executable is not a supported 64-bit Linux ELF")
    machine = int.from_bytes(payload[18:20], "little")
    platform = _ELF_MACHINES.get(machine)
    if platform is None:
        raise BinaryImportError("ELF architecture is unsupported")
    if machine != requested[0]:
        raise BinaryImportError("ELF architecture does not match deployment architecture")
    return platform


def extract_uploaded_binary(
    payload: bytes,
    *,
    filename: str,
    implementation: str,
    host_arch: str,
    limits: ImportLimits | None = None,
) -> UploadedBinary:
    """Validate upload bytes and return the one inert executable they contain."""
    selected_limits = limits or ImportLimits()
    executable_name = _EXECUTABLE_NAMES.get(implementation)
    if executable_name is None:
        raise BinaryImportError("implementation is unsupported")
    if len(payload) > selected_limits.max_upload_bytes:
        raise BinaryImportError("uploaded file is too large")
    executable = (
        _archive_executable(
            payload,
            executable_name=executable_name,
            limits=selected_limits,
        )
        if _archive_name(filename)
        else payload
    )
    if len(executable) > selected_limits.max_executable_bytes:
        raise BinaryImportError("uploaded executable is too large")
    platform = _elf_platform(executable, host_arch=host_arch)
    return UploadedBinary(
        implementation=implementation,
        executable_name=executable_name,
        platform=platform,
        upload_sha256=_sha256(payload),
        executable_sha256=_sha256(executable),
        executable=executable,
    )


def parse_binary_import_multipart(
    content_type: str,
    body: bytes,
    *,
    expected_token: str,
    host_arch: str,
    limits: ImportLimits | None = None,
) -> ParsedBinaryImport:
    """Authenticate and parse one strict bounded browser upload."""
    if len(body) > MAX_IMPORT_REQUEST_BYTES:
        raise BinaryImportError("binary import request is too large")
    from profile_manager.data.bundle_import import _extract_boundary

    boundary = _extract_boundary(content_type)
    if boundary is None:
        raise BinaryImportError("multipart boundary is invalid")
    separator = b"--" + boundary.encode("ascii")
    parts: dict[str, tuple[str | None, bytes]] = {}
    for raw_part in body.split(separator):
        if raw_part.startswith(b"\r\n"):
            raw_part = raw_part[2:]
        if raw_part in {b"", b"--", b"--\r\n"}:
            continue
        split = raw_part.find(b"\r\n\r\n")
        if split < 0 or split > 8192:
            raise BinaryImportError("multipart upload is invalid")
        headers = raw_part[:split].decode("utf-8", errors="replace")
        content = raw_part[split + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        field = re.search(r'(?:^|;)\s*name="([^"]+)"', headers)
        if field is None:
            raise BinaryImportError("multipart upload is invalid")
        name = field.group(1)
        if name in parts:
            raise BinaryImportError("multipart field is duplicated")
        filename_match = re.search(r'filename="([^"]*)"', headers)
        parts[name] = (
            filename_match.group(1) if filename_match is not None else None,
            content,
        )
    allowed = {
        "token",
        "expected_revision",
        "implementation",
        "version",
        "declared_source_revision",
        "artifact",
    }
    if set(parts) - allowed:
        raise BinaryImportError("multipart upload contains unknown fields")
    submitted_token = parts.get("token", (None, b""))[1]
    if not submitted_token or not hmac.compare_digest(
        submitted_token, expected_token.encode("utf-8")
    ):
        raise BinaryImportError("authentication failed")
    required = {"expected_revision", "implementation", "version", "artifact"}
    missing = sorted(required - set(parts))
    if missing:
        raise BinaryImportError(
            "missing required upload fields: " + ", ".join(missing)
        )

    def text_field(name: str) -> str:
        filename, value = parts[name]
        if filename is not None or len(value) > 512:
            raise BinaryImportError(f"{name} is invalid")
        try:
            return value.decode("utf-8")
        except UnicodeError as exc:
            raise BinaryImportError(f"{name} is invalid") from exc

    expected_revision = text_field("expected_revision")
    if not _REVISION.fullmatch(expected_revision):
        raise BinaryImportError("expected revision is invalid")
    implementation = text_field("implementation")
    version = text_field("version")
    source = (
        text_field("declared_source_revision")
        if "declared_source_revision" in parts
        else ""
    )
    artifact_filename, artifact_payload = parts["artifact"]
    if artifact_filename is None:
        raise BinaryImportError("artifact upload is invalid")
    artifact = extract_uploaded_binary(
        artifact_payload,
        filename=artifact_filename or "uploaded-binary",
        implementation=implementation,
        host_arch=host_arch,
        limits=limits,
    )
    return ParsedBinaryImport(
        expected_revision=expected_revision,
        version=version,
        declared_source_revision=source or None,
        artifact=artifact,
    )


def _write_private(directory_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _expiry(created_at: str, lifetime_seconds: int) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BinaryImportError("created_at is invalid") from exc
    if created.tzinfo is None:
        raise BinaryImportError("created_at is invalid")
    expires = created.astimezone(timezone.utc) + timedelta(seconds=lifetime_seconds)
    return expires.isoformat().replace("+00:00", "Z")


def stage_binary_import(
    root: str | os.PathLike[str],
    *,
    import_id: str,
    artifact: UploadedBinary,
    version: str,
    declared_source_revision: str | None,
    created_at: str,
    limits: ImportLimits | None = None,
) -> dict[str, object]:
    """Persist verified bytes and a path-free descriptor for controller pickup."""
    selected_limits = limits or ImportLimits()
    if not _IMPORT_ID.fullmatch(import_id):
        raise BinaryImportError("import id is invalid")
    if not _VERSION.fullmatch(version):
        raise BinaryImportError("version is invalid")
    if declared_source_revision is not None and not _SOURCE_REVISION.fullmatch(
        declared_source_revision
    ):
        raise BinaryImportError("source revision is invalid")
    staging_root = Path(root)
    try:
        if staging_root.exists() or staging_root.is_symlink():
            opened = staging_root.lstat()
            if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
                raise BinaryImportError("staging root is unsafe")
            if opened.st_uid not in {0, os.geteuid()}:
                raise BinaryImportError("staging root is unsafe")
        else:
            staging_root.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise BinaryImportError("staging root is unsafe") from exc

    root_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd: int | None = None
    import_fd: int | None = None
    import_root = staging_root / import_id
    try:
        root_fd = os.open(staging_root, root_flags)
        os.mkdir(import_id, 0o700, dir_fd=root_fd)
        import_fd = os.open(import_id, root_flags, dir_fd=root_fd)
        descriptor: dict[str, object] = {
            "schema_version": "v1",
            "import_id": import_id,
            "implementation": artifact.implementation,
            "version": version,
            "declared_source_revision": declared_source_revision,
            "platform": artifact.platform,
            "upload_sha256": artifact.upload_sha256,
            "executable_sha256": artifact.executable_sha256,
            "executable_size": len(artifact.executable),
            "created_at": created_at,
            "expires_at": _expiry(
                created_at, selected_limits.staging_lifetime_seconds
            ),
            "state": "staged",
        }
        _write_private(import_fd, "executable", artifact.executable)
        _write_private(
            import_fd,
            "request.json",
            json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
            + b"\n",
        )
        os.fsync(import_fd)
        os.fsync(root_fd)
        return descriptor
    except (FileExistsError, OSError) as exc:
        if import_root.exists() and import_root.is_dir() and not import_root.is_symlink():
            shutil.rmtree(import_root)
        raise BinaryImportError("cannot safely stage binary import") from exc
    finally:
        if import_fd is not None:
            os.close(import_fd)
        if root_fd is not None:
            os.close(root_fd)


def _default_staging_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_VERSION_IMPORTS_DIR", "").strip()
    if configured:
        return Path(configured)
    from profile_manager.config import storage_roots

    return storage_roots().state / "version-imports"


def _read_job(root: Path, import_id: str) -> dict[str, Any]:
    if not _IMPORT_ID.fullmatch(import_id):
        raise BinaryImportError("import id is invalid")
    directory = root / import_id
    path = directory / "request.json"
    try:
        directory_opened = directory.lstat()
        path_opened = path.lstat()
        if (
            stat.S_ISLNK(directory_opened.st_mode)
            or not stat.S_ISDIR(directory_opened.st_mode)
            or stat.S_ISLNK(path_opened.st_mode)
            or not stat.S_ISREG(path_opened.st_mode)
            or directory_opened.st_uid not in {0, os.geteuid()}
            or path_opened.st_uid not in {0, os.geteuid()}
            or path_opened.st_size > 32 * 1024
        ):
            raise BinaryImportError("binary import record is unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BinaryImportError("binary import record is unavailable") from exc
    if not isinstance(value, dict) or value.get("import_id") != import_id:
        raise BinaryImportError("binary import record is invalid")
    return value


def _write_job(root: Path, record: dict[str, Any]) -> None:
    import_id = str(record.get("import_id") or "")
    current = _read_job(root, import_id)
    if current.get("import_id") != import_id:
        raise BinaryImportError("binary import record is invalid")
    directory = root / import_id
    path = directory / "request.json"
    temporary = directory / f".request-{uuid.uuid4().hex}.tmp"
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cleanup_expired_binary_imports(
    staging_root: str | os.PathLike[str], *, now: datetime | None = None
) -> int:
    """Remove only expired staged bytes while retaining a bounded job receipt."""
    root = Path(staging_root)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cleaned = 0
    try:
        candidates = sorted(root.glob("import-*"))[:256]
    except OSError:
        return 0
    for candidate in candidates:
        try:
            record = _read_job(root, candidate.name)
            expires = datetime.fromisoformat(
                str(record.get("expires_at") or "").replace("Z", "+00:00")
            )
        except (BinaryImportError, TypeError, ValueError):
            continue
        if (
            expires.tzinfo is None
            or expires.astimezone(timezone.utc) > current
            or record.get("state") not in {"staged", "running", "submission-uncertain"}
        ):
            continue
        executable = candidate / "executable"
        try:
            if executable.is_symlink():
                continue
            executable.unlink(missing_ok=True)
            _write_job(
                root,
                {
                    **record,
                    "state": "expired",
                    "completed_at": current.isoformat().replace("+00:00", "Z"),
                    "failure": {
                        "code": "staged_artifact_expired",
                        "stage": "artifact_copy",
                        "message": "Staged artifact expired.",
                    },
                },
            )
            cleaned += 1
        except (BinaryImportError, OSError):
            continue
    return cleaned


def begin_binary_import(
    content_type: str,
    body: bytes,
    *,
    expected_token: str,
    host_arch: str,
    staging_root: str | os.PathLike[str] | None = None,
    ledger: Any = None,
    request_controller: Callable[..., dict[str, Any]] | None = None,
    import_id: str | None = None,
    execution_id: str | None = None,
    created_at: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Stage an authenticated upload and start one idempotent controller job."""
    parsed = parse_binary_import_multipart(
        content_type,
        body,
        expected_token=expected_token,
        host_arch=host_arch,
    )
    if ledger is None:
        from profile_manager.operator_version_decisions import (
            OperatorVersionDecisionLedger,
        )

        ledger = OperatorVersionDecisionLedger()
    state = ledger.read()
    if parsed.expected_revision != state["revision"]:
        raise BinaryImportError("operator version state revision is stale")
    selected_import_id = import_id or ("import-" + uuid.uuid4().hex[:24])
    selected_execution_id = execution_id or ("execution-" + uuid.uuid4().hex)
    selected_root = Path(staging_root) if staging_root is not None else _default_staging_root()
    cleanup_expired_binary_imports(selected_root)
    descriptor = stage_binary_import(
        selected_root,
        import_id=selected_import_id,
        artifact=parsed.artifact,
        version=parsed.version,
        declared_source_revision=parsed.declared_source_revision,
        created_at=created_at or _now(),
    )
    parameters: dict[str, Any] = {
        "import_id": selected_import_id,
        "execution_id": selected_execution_id,
        "implementation": parsed.artifact.implementation,
        "version": parsed.version,
        "platform": parsed.artifact.platform,
        "upload_sha256": "sha256:" + parsed.artifact.upload_sha256,
        "executable_sha256": "sha256:" + parsed.artifact.executable_sha256,
        "executable_size": len(parsed.artifact.executable),
    }
    if parsed.declared_source_revision is not None:
        parameters["declared_source_revision"] = parsed.declared_source_revision
    if request_controller is None:
        from profile_manager.topology_actions import controller_request

        request_controller = controller_request
    try:
        response = request_controller(
            "artifact.package",
            parameters,
            1800,
            idempotency_key="package-" + selected_import_id,
        )
    except BaseException:
        shutil.rmtree(selected_root / selected_import_id, ignore_errors=True)
        raise
    if response != {
        "status": "accepted",
        "execution_id": selected_execution_id,
    }:
        raise BinaryImportError("controller package response is invalid")
    _write_job(
        selected_root,
        {
            **descriptor,
            "state": "running",
            "execution_id": selected_execution_id,
            "expected_operator_revision": parsed.expected_revision,
        },
    )
    return 202, {
        "ok": True,
        "state": "running",
        "import_id": selected_import_id,
        "execution_id": selected_execution_id,
    }


def _status_form(body: bytes, expected_token: str) -> tuple[str, str]:
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    if set(form) != {"token", "import_id", "execution_id"}:
        raise BinaryImportError("binary import status request is invalid")
    token = (form.get("token") or [""])[0]
    if not token or not hmac.compare_digest(
        token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise BinaryImportError("authentication failed")
    import_id = (form["import_id"] or [""])[0]
    execution_id = (form["execution_id"] or [""])[0]
    if not _IMPORT_ID.fullmatch(import_id):
        raise BinaryImportError("import id is invalid")
    if not re.fullmatch(r"execution-[0-9a-f]{32}", execution_id):
        raise BinaryImportError("execution id is invalid")
    return import_id, execution_id


def _registration_from_result(
    record: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    digest = re.compile(r"^sha256:[0-9a-f]{64}$")
    expected_upload = "sha256:" + str(record.get("upload_sha256") or "")
    expected_executable = "sha256:" + str(record.get("executable_sha256") or "")
    if (
        result.get("status") != "packaged"
        or result.get("implementation") != record.get("implementation")
        or result.get("version") != record.get("version")
        or result.get("platform") != record.get("platform")
        or result.get("declared_source_revision")
        != record.get("declared_source_revision")
        or result.get("upload_sha256") != expected_upload
        or result.get("executable_sha256") != expected_executable
        or result.get("runtime_interface")
        != ("extracted-binary" if record.get("implementation") == "amaru" else None)
        or result.get("qualification") != "unverified"
        or result.get("compatibility") != "unknown-until-run"
        or result.get("provenance") != "operator-upload"
        or result.get("manifest_references") != []
        or not digest.fullmatch(str(result.get("config_image_id") or ""))
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            str(result.get("controller_installation_id") or ""),
        )
    ):
        raise BinaryImportError("controller package result identity is invalid")
    try:
        packaged_at = datetime.fromisoformat(
            str(result.get("packaged_at") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise BinaryImportError("controller package result identity is invalid") from exc
    if packaged_at.tzinfo is None:
        raise BinaryImportError("controller package result identity is invalid")
    import_id = str(record["import_id"])
    return {
        "registration_id": "registration-" + import_id.removeprefix("import-"),
        "implementation": result["implementation"],
        "version": result["version"],
        "image_reference": result["config_image_id"],
        "manifest_digest": None,
        "config_image_id": result["config_image_id"],
        "executable_digest": result["executable_sha256"],
        "platform": result["platform"],
        "runtime_interface": result["runtime_interface"],
        "controller_installation_id": result["controller_installation_id"],
        "inspected_at": result["packaged_at"],
        "provenance": {
            "source_revision": record.get("declared_source_revision"),
            "public": None,
            "qualification": "unverified",
        },
    }


def retained_import_executable_digest(
    registration: dict[str, Any],
    *,
    staging_root: str | os.PathLike[str] | None = None,
    request_controller: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """Recover an old import's digest from its retained, matched finalization record.

    This is a read-only compatibility bridge for registrations created before the
    ledger stored ``executable_digest``.  It does not infer identity from an image
    label or mutable version text.
    """
    registration_id = str(registration.get("registration_id") or "")
    if not registration_id.startswith("registration-"):
        raise BinaryImportError("registration has no retained import identity")
    import_id = "import-" + registration_id.removeprefix("registration-")
    if not _IMPORT_ID.fullmatch(import_id):
        raise BinaryImportError("registration has no retained import identity")
    selected_root = (
        Path(staging_root) if staging_root is not None else _default_staging_root()
    )
    record = _read_job(selected_root, import_id)
    if (
        record.get("state") != "registered"
        or record.get("registration_id") != registration_id
        or record.get("config_image_id") != registration.get("config_image_id")
        or record.get("implementation") != registration.get("implementation")
        or record.get("version") != registration.get("version")
        or record.get("platform") != registration.get("platform")
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(record.get("executable_sha256") or "")
        )
        or not isinstance(record.get("execution_id"), str)
        or not re.fullmatch(r"execution-[0-9a-f]{32}", record["execution_id"])
        or not isinstance(record.get("audit_receipt"), str)
        or not record["audit_receipt"].startswith("controller/controller-audit/")
        or not record["audit_receipt"].endswith(".json")
        or not isinstance(registration.get("controller_installation_id"), str)
        or not registration["controller_installation_id"]
    ):
        raise BinaryImportError("retained import identity differs from registration")
    if request_controller is None:
        from profile_manager.topology_actions import controller_request

        request_controller = controller_request
    try:
        status = request_controller(
            "execution.status", {"execution_id": record["execution_id"]}, 10
        )
    except Exception as exc:
        raise BinaryImportError(
            "retained import controller evidence is unavailable"
        ) from exc
    if (
        not isinstance(status, dict)
        or status.get("execution_id") != record["execution_id"]
        or status.get("state") != "applied"
        or status.get("audit_receipt") != record["audit_receipt"]
        or status.get("failure") is not None
        or not isinstance(status.get("result"), dict)
    ):
        raise BinaryImportError("retained import controller evidence is invalid")
    try:
        reconstructed = _registration_from_result(record, status["result"])
    except BinaryImportError as exc:
        raise BinaryImportError(
            "retained import controller evidence is invalid"
        ) from exc
    executable = reconstructed.pop("executable_digest")
    retained_executable = registration.get("executable_digest")
    registration_without_executable = {
        key: value
        for key, value in registration.items()
        if key != "executable_digest"
    }
    if (
        reconstructed != registration_without_executable
        or retained_executable is not None
        and retained_executable != executable
    ):
        raise BinaryImportError(
            "retained import controller evidence differs from registration"
        )
    return str(executable)


def poll_binary_import(
    body: bytes,
    *,
    expected_token: str,
    staging_root: str | os.PathLike[str] | None = None,
    ledger: Any = None,
    request_controller: Callable[..., dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Poll one package job and finalize its exact registration at most once."""
    import_id, execution_id = _status_form(body, expected_token)
    selected_root = Path(staging_root) if staging_root is not None else _default_staging_root()
    record = _read_job(selected_root, import_id)
    if record.get("execution_id") != execution_id:
        raise BinaryImportError("binary import execution identity differs")
    if record.get("state") == "registered":
        return 200, {
            "ok": True,
            "state": "registered",
            "import_id": import_id,
            "execution_id": execution_id,
            "registration_id": record["registration_id"],
            "config_image_id": record["config_image_id"],
            "compatibility": "unknown-until-run",
        }
    if request_controller is None:
        from profile_manager.topology_actions import controller_request

        request_controller = controller_request
    status = request_controller(
        "execution.status", {"execution_id": execution_id}, 10
    )
    if status.get("execution_id") != execution_id:
        raise BinaryImportError("controller package status identity is invalid")
    state = status.get("state")
    if state in {"running", "cancelling"}:
        return 200, {
            "ok": True,
            "state": state,
            "import_id": import_id,
            "execution_id": execution_id,
        }
    if state != "applied" or not isinstance(status.get("result"), dict):
        (selected_root / import_id / "executable").unlink(missing_ok=True)
        failure = status.get("failure") if isinstance(status.get("failure"), dict) else {}
        failed = {
            **record,
            "state": str(state or "failed"),
            "failure": {
                "code": str(failure.get("code") or "artifact_package_failed"),
                "stage": str(failure.get("stage") or "artifact_package"),
                "message": str(failure.get("message") or "Artifact packaging failed."),
            },
        }
        _write_job(selected_root, failed)
        return 200, {
            "ok": False,
            "state": failed["state"],
            "import_id": import_id,
            "execution_id": execution_id,
            "failure": failed["failure"],
        }
    registration = _registration_from_result(record, status["result"])
    if ledger is None:
        from profile_manager.operator_version_decisions import (
            OperatorVersionDecisionLedger,
        )

        ledger = OperatorVersionDecisionLedger()
    operator_state = ledger.read()
    existing = next(
        (
            item
            for item in operator_state["registrations"]
            if item["registration_id"] == registration["registration_id"]
        ),
        None,
    )
    if existing is not None and existing != registration:
        raise BinaryImportError("binary import registration identity differs")
    if existing is None:
        from profile_manager.operator_version_decisions import (
            StaleOperatorVersionState,
        )

        try:
            ledger.append_registration(
                registration, expected_revision=operator_state["revision"]
            )
        except StaleOperatorVersionState as exc:
            # Two status polls may race after the controller becomes terminal.
            # The losing poll is idempotent only when the winner retained this
            # exact immutable registration; every other ledger change remains
            # a conflict rather than an inferred success.
            concurrent_state = ledger.read()
            concurrent = next(
                (
                    item
                    for item in concurrent_state["registrations"]
                    if item["registration_id"]
                    == registration["registration_id"]
                ),
                None,
            )
            if concurrent != registration:
                raise BinaryImportError(
                    "binary import registration changed concurrently"
                ) from exc
    (selected_root / import_id / "executable").unlink(missing_ok=True)
    completed = {
        **record,
        "state": "registered",
        "registration_id": registration["registration_id"],
        "config_image_id": registration["config_image_id"],
        "completed_at": status.get("completed_at"),
        "audit_receipt": status.get("audit_receipt"),
    }
    _write_job(selected_root, completed)
    return 200, {
        "ok": True,
        "state": "registered",
        "import_id": import_id,
        "execution_id": execution_id,
        "registration_id": registration["registration_id"],
        "config_image_id": registration["config_image_id"],
        "compatibility": "unknown-until-run",
    }


def list_binary_import_jobs(
    staging_root: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """Return bounded, path-free job summaries for the Basic Versions view."""
    root = Path(staging_root) if staging_root is not None else _default_staging_root()
    try:
        candidates = sorted(root.glob("import-*"), reverse=True)[:20]
    except OSError:
        return []
    jobs: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            record = _read_job(root, candidate.name)
        except BinaryImportError:
            continue
        jobs.append(
            {
                key: record.get(key)
                for key in (
                    "import_id",
                    "execution_id",
                    "implementation",
                    "version",
                    "platform",
                    "state",
                    "created_at",
                    "completed_at",
                    "registration_id",
                    "config_image_id",
                    "failure",
                )
            }
        )
    return jobs
