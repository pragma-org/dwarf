"""Narrow Docker CLI adapter for controller-owned local topology resources.

All command arguments are generated from validated controller values.  This
module never invokes a shell and never accepts argv, paths, mounts, commands,
environment blocks, container names, or Docker identifiers from an API caller.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from scripts.runtime_substrate_common import reconcile_amaru_runtime_metadata


class DockerRuntimeError(RuntimeError):
    """Docker could not safely satisfy a typed controller operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        stage: str | None = None,
        return_code: int | None = None,
        adapter_output: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = code
        self.failure_stage = stage
        self.return_code = return_code
        self.adapter_output = dict(adapter_output or {})


@dataclass(frozen=True)
class DockerResult:
    returncode: int
    stdout: str
    stderr: str


def _adapter_diagnostic(value: str) -> str:
    """Classify process output without retaining its raw text."""

    lowered = value.lower()
    if "exec format error" in lowered:
        return "adapter executable format is invalid"
    if "bad interpreter" in lowered or "cannot execute" in lowered:
        return "adapter executable could not start"
    if any(
        marker in lowered
        for marker in (
            "error while loading shared libraries",
            "dynamic linker",
            "loader",
        )
    ):
        return "adapter runtime loader or shared library is unavailable"
    if "permission denied" in lowered:
        return "adapter process permission denied"
    if "operation not permitted" in lowered:
        return "adapter process operation is not permitted"
    if "no such file" in lowered or "not found" in lowered:
        return "adapter executable or dependency is unavailable"
    return "[redacted]"


def _bounded_adapter_output(stdout: str, stderr: str) -> dict[str, list[str]]:
    """Retain only bounded, actionable process output without credential material."""

    def safe_lines(value: str) -> list[str]:
        lines: list[str] = []
        for raw in value[:4096].splitlines()[:8]:
            line = " ".join(raw.replace("\x00", " ").split())[:256]
            lines.append(_adapter_diagnostic(line))
        return lines

    return {"stdout": safe_lines(stdout), "stderr": safe_lines(stderr)}


_INSTALLATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$|^[a-z0-9]$")
_TRANSFER_SOURCE_INSTALLATION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_MANAGED = "io.dwarf.managed"
_INSTALLATION = "io.dwarf.installation"
_TOPOLOGY = "io.dwarf.topology"
_COMPONENT = "io.dwarf.component"
_SAFE_RUNTIME_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]$|^[a-z0-9]$")
_IMAGE_CONFIG_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_LOOKUP = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9][a-z0-9._-]*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)
_REPO_DIGEST = re.compile(
    r"^[a-z0-9][a-z0-9._:-]*(?:/[a-z0-9][a-z0-9._-]*)*"
    r"@sha256:[0-9a-f]{64}$"
)
_CATALOG_REVISION = re.compile(r"^[0-9a-f]{64}$")
_AMARU_HARDENING_EXCEPTION_SERVICES = {
    "configurator",
    "tracer",
    "tracer-sidecar",
    "log-tailer",
    "sidecar",
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    "amaru-consumer",
    "amaru-consumer-seed",
    "amaru-relay-1",
    "amaru-relay-2",
    "amaru-target-extract",
    "plutus-v2-activate",
}
_AMARU_HARDENING_EXCEPTION = "proven-relay-bootstrap-writable-rootfs"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _atomic_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _immutable_image_identity(image: str) -> str:
    """Return repository@digest while ignoring an optional human-readable tag."""

    if _IMAGE_CONFIG_ID.fullmatch(str(image)):
        return str(image)
    repository, separator, digest = str(image).partition("@")
    if separator != "@" or not digest.startswith("sha256:"):
        raise DockerRuntimeError("target image is not digest-pinned")
    last_slash = repository.rfind("/")
    last_colon = repository.rfind(":")
    if last_colon > last_slash:
        repository = repository[:last_colon]
    return f"{repository}@{digest}"


def _published_ports_are_loopback_only(service: dict[str, Any]) -> bool:
    for port in service.get("ports") or []:
        if isinstance(port, dict):
            target = str(port.get("target") or "")
            published = port.get("published")
            host_ip = str(port.get("host_ip") or "")
            if target == "8787" or str(published or "") == "8787":
                return False
            if published is None or host_ip not in {"127.0.0.1", "::1"}:
                return False
            continue
        value = str(port)
        if not value:
            return False
        if value.startswith("127.0.0.1:") or value.startswith("[::1]:"):
            fields = value.removesuffix("/tcp").removesuffix("/udp").rsplit(":", 2)
            if len(fields) < 3 or "8787" in fields[-2:]:
                return False
            continue
        return False
    return True


class DockerRuntime:
    """Manage only resources bearing this installation's complete label set."""

    def __init__(
        self,
        *,
        installation_id: str,
        docker_binary: str = "docker",
        runner: Callable[[Sequence[str], int, str | None], DockerResult] | None = None,
        state_dir: str | Path | None = None,
        host_state_dir: str | Path | None = None,
        launch_root: str | Path | None = None,
        version_imports_dir: str | Path | None = None,
        adapter_runner: Callable[[Sequence[str], int, str | None], DockerResult] | None = None,
    ) -> None:
        if not _INSTALLATION_ID.fullmatch(installation_id):
            raise ValueError("installation_id is not safe")
        self.installation_id = installation_id
        self.docker_binary = docker_binary
        self.state_dir = Path(
            state_dir or os.environ.get("ADA2_DWARF_STATE_DIR", "/var/dwarf/state")
        )
        self.host_state_dir = Path(
            host_state_dir
            or os.environ.get("DWARF_CONTROLLER_HOST_STATE_DIR")
            or self.state_dir
        )
        self.launch_root = Path(
            launch_root
            or os.environ.get("ADA2_DWARF_LAUNCH_ROOT")
            or "/var/dwarf/launches"
        )
        self.version_imports_dir = Path(
            version_imports_dir
            or os.environ.get("ADA2_DWARF_VERSION_IMPORTS_DIR")
            or "/var/dwarf/version-imports"
        )
        if not self.host_state_dir.is_absolute() or ".." in self.host_state_dir.parts:
            raise ValueError("host_state_dir must be an absolute normalized host path")
        self._external_runner = runner
        self._external_adapter_runner = adapter_runner
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._process_lock = threading.Lock()
        self.run_attachments_dir = (
            self.state_dir / "controller" / "run-network-attachments"
        )

    def _approved_image_identities(self) -> set[str]:
        return {
            _immutable_image_identity(item.strip())
            for item in os.environ.get("DWARF_GOLDEN_IMAGES", "").split(",")
            if item.strip()
        }

    @staticmethod
    def _runtime_interface_from_oci_config(
        item: dict[str, Any], *, implementation: str
    ) -> str | None:
        """Classify only an immutable, directly observed executable contract."""

        if implementation != "amaru":
            return None
        config = item.get("Config")
        if not isinstance(config, dict):
            return None
        if config.get("Entrypoint") == ["/usr/local/bin/amaru"] and config.get(
            "Cmd"
        ) == ["run"]:
            return "extracted-binary"
        return None

    def inspect_image(
        self, image: str, *, implementation: str
    ) -> dict[str, Any]:
        """Inspect one bounded lookup and return only immutable identity fields."""
        if (
            not isinstance(image, str)
            or len(image) > 512
            or image.startswith("-")
            or "://" in image
            or ".." in image
            or "//" in image
            or not (_IMAGE_CONFIG_ID.fullmatch(image) or _IMAGE_LOOKUP.fullmatch(image))
        ):
            raise DockerRuntimeError("image lookup is unsafe")
        inspected = self._json(
            self._run(("image", "inspect", image), timeout=20), "image inspect"
        )
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise DockerRuntimeError("Docker image inspection is ambiguous")
        item = inspected[0]
        config_id = item.get("Id")
        if not isinstance(config_id, str) or not _IMAGE_CONFIG_ID.fullmatch(config_id):
            raise DockerRuntimeError("inspected image config identity is invalid")
        repo_digests = item.get("RepoDigests") or []
        if not isinstance(repo_digests, list) or any(
            not isinstance(value, str) or not _REPO_DIGEST.fullmatch(value)
            for value in repo_digests
        ):
            raise DockerRuntimeError("inspected image manifest references are invalid")
        manifests = sorted(set(repo_digests))
        if _IMAGE_CONFIG_ID.fullmatch(image):
            if config_id != image:
                raise DockerRuntimeError("inspected image config identity differs")
        elif "@sha256:" in image:
            requested = _immutable_image_identity(image)
            if requested not in {
                _immutable_image_identity(reference) for reference in manifests
            }:
                raise DockerRuntimeError("inspected image manifest identity differs")
        operating_system = item.get("Os")
        architecture = item.get("Architecture")
        if operating_system != "linux" or architecture not in {"amd64", "arm64"}:
            raise DockerRuntimeError("inspected image platform is unsupported")
        return {
            "config_image_id": config_id,
            "manifest_references": manifests,
            "platform": f"{operating_system}/{architecture}",
            "runtime_interface": self._runtime_interface_from_oci_config(
                item, implementation=implementation
            ),
        }

    @staticmethod
    def _read_private_regular(
        directory_fd: int, name: str, *, maximum: int
    ) -> tuple[bytes, os.stat_result]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = None
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid not in {0, os.geteuid()}
                or opened.st_size > maximum
            ):
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read(maximum + 1)
            after = os.fstat(descriptor)
            if (
                len(payload) > maximum
                or (opened.st_dev, opened.st_ino, opened.st_size)
                != (after.st_dev, after.st_ino, after.st_size)
            ):
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                )
            return payload, opened
        except OSError as exc:
            raise DockerRuntimeError(
                "staged artifact is unavailable",
                code="staged_artifact_unavailable",
                stage="artifact_copy",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _verified_staged_artifact(
        self, parameters: dict[str, Any]
    ) -> tuple[bytes, dict[str, Any]]:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
        root_fd = import_fd = None
        try:
            root_fd = os.open(self.version_imports_dir, directory_flags)
            import_fd = os.open(parameters["import_id"], directory_flags, dir_fd=root_fd)
            opened = os.fstat(import_fd)
            if not stat.S_ISDIR(opened.st_mode) or opened.st_uid not in {0, os.geteuid()}:
                raise DockerRuntimeError(
                    "staged artifact is unavailable",
                    code="staged_artifact_unavailable",
                    stage="artifact_copy",
                )
            descriptor_bytes, _ = self._read_private_regular(
                import_fd, "request.json", maximum=16 * 1024
            )
            try:
                descriptor = json.loads(descriptor_bytes.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                ) from exc
            if not isinstance(descriptor, dict):
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                )
            expected = {
                "import_id": parameters["import_id"],
                "implementation": parameters["implementation"],
                "version": parameters["version"],
                "platform": parameters["platform"],
                "upload_sha256": parameters["upload_sha256"].removeprefix("sha256:"),
                "executable_sha256": parameters["executable_sha256"].removeprefix(
                    "sha256:"
                ),
                "executable_size": parameters["executable_size"],
                "declared_source_revision": parameters.get(
                    "declared_source_revision"
                ),
            }
            if any(descriptor.get(key) != value for key, value in expected.items()):
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                )
            expires_at = descriptor.get("expires_at")
            try:
                expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError as exc:
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                ) from exc
            if expires <= datetime.now(timezone.utc):
                raise DockerRuntimeError(
                    "staged artifact expired",
                    code="staged_artifact_expired",
                    stage="artifact_copy",
                )
            executable, opened_file = self._read_private_regular(
                import_fd, "executable", maximum=256 * 1024 * 1024
            )
            digest = hashlib.sha256(executable).hexdigest()
            if (
                opened_file.st_size != parameters["executable_size"]
                or digest != expected["executable_sha256"]
            ):
                raise DockerRuntimeError(
                    "staged artifact identity changed",
                    code="staged_artifact_invalid",
                    stage="artifact_copy",
                )
            return executable, descriptor
        except DockerRuntimeError:
            raise
        except OSError as exc:
            raise DockerRuntimeError(
                "staged artifact is unavailable",
                code="staged_artifact_unavailable",
                stage="artifact_copy",
            ) from exc
        finally:
            if import_fd is not None:
                os.close(import_fd)
            if root_fd is not None:
                os.close(root_fd)

    @staticmethod
    def _verify_copied_elf_platform(payload: bytes, expected_platform: str) -> None:
        if (
            len(payload) < 20
            or payload[:4] != b"\x7fELF"
            or payload[4] != 2
            or payload[5] != 1
        ):
            raise DockerRuntimeError(
                "copied artifact is not a supported 64-bit ELF",
                code="artifact_elf_invalid",
                stage="artifact_copy",
            )
        observed = {62: "linux/amd64", 183: "linux/arm64"}.get(
            int.from_bytes(payload[18:20], "little")
        )
        if observed != expected_platform:
            raise DockerRuntimeError(
                "copied artifact ELF platform differs from the controller platform",
                code="artifact_platform_mismatch",
                stage="artifact_copy",
            )

    @staticmethod
    def _artifact_dockerfile(
        *, base_image: str, implementation: str, version: str,
        executable_sha256: str, declared_source_revision: str | None,
    ) -> str:
        executable = "amaru" if implementation == "amaru" else "cardano-node"
        lines = [
            f"FROM {base_image}",
            f"COPY executable /usr/local/bin/{executable}",
            f'LABEL org.dwarf.local-artifact.executable-sha256="{executable_sha256}"',
            f'LABEL org.dwarf.local-artifact.version="{version}"',
            'LABEL org.dwarf.local-artifact.qualification="unverified"',
        ]
        if declared_source_revision is not None:
            lines.append(
                f'LABEL org.opencontainers.image.revision="{declared_source_revision}"'
            )
        if implementation == "amaru":
            lines.append("USER amaru")
        lines.extend(
            [f'ENTRYPOINT ["/usr/local/bin/{executable}"]', 'CMD ["run"]', ""]
        )
        return "\n".join(lines)

    @staticmethod
    def _artifact_package_tag(
        implementation: str, execution_id: str, executable_sha256: str
    ) -> str:
        tag_prefix = (
            "dwarf/local-amaru-import"
            if implementation == "amaru"
            else "dwarf/local-cardano-node-import"
        )
        return (
            tag_prefix
            + ":"
            + execution_id.removeprefix("execution-")
            + "-"
            + executable_sha256.removeprefix("sha256:")[:12]
        )

    def _cleanup_artifact_package_resources(
        self,
        *,
        tag: str,
        probe_name: str,
        work_root: Path,
        remove_image: bool,
    ) -> None:
        failed = False
        try:
            container = self._run(
                ("container", "rm", "--force", probe_name),
                timeout=15,
                execution_id=None,
            )
            if container.returncode != 0:
                retained = self._run(
                    ("container", "inspect", probe_name),
                    timeout=10,
                    execution_id=None,
                )
                failed = retained.returncode == 0
        except BaseException:
            failed = True
        if remove_image:
            try:
                image = self._run(
                    ("image", "rm", tag),
                    timeout=30,
                    execution_id=None,
                )
                if image.returncode != 0:
                    retained = self._run(
                        ("image", "inspect", tag),
                        timeout=10,
                        execution_id=None,
                    )
                    failed = failed or retained.returncode == 0
            except BaseException:
                failed = True
        try:
            shutil.rmtree(work_root)
        except FileNotFoundError:
            pass
        except OSError:
            failed = True
        if work_root.exists() or work_root.is_symlink():
            failed = True
        if failed:
            raise DockerRuntimeError(
                "artifact package cleanup could not be verified",
                code="artifact_cleanup_failed",
                stage="artifact_cleanup",
            )

    def cleanup_artifact_package(
        self, parameters: dict[str, Any], execution_id: str
    ) -> None:
        """Reconcile only one interrupted package job's execution-owned resources."""
        tag = self._artifact_package_tag(
            str(parameters["implementation"]),
            execution_id,
            str(parameters["executable_sha256"]),
        )
        self._cleanup_artifact_package_resources(
            tag=tag,
            probe_name=f"dwarf-package-probe-{execution_id.removeprefix('execution-')}",
            work_root=(
                self.state_dir / "controller" / "artifact-packages" / execution_id
            ),
            remove_image=True,
        )

    def package_artifact(
        self,
        parameters: dict[str, Any],
        execution_id: str,
        *,
        timeout: int,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        """Package and probe one verified staged binary without host execution."""
        implementation = parameters["implementation"]
        if implementation not in {"amaru", "cardano-node"}:
            raise DockerRuntimeError(
                "artifact implementation is unavailable",
                code="artifact_implementation_unavailable",
                stage="artifact_package",
            )
        expected_arch = os.environ.get("DWARF_HOST_ARCH", "").strip().lower()
        expected_platform = {
            "amd64": "linux/amd64",
            "x86_64": "linux/amd64",
            "arm64": "linux/arm64",
            "aarch64": "linux/arm64",
        }.get(expected_arch)
        if expected_platform != parameters["platform"]:
            raise DockerRuntimeError(
                "uploaded artifact platform differs from the controller platform",
                code="artifact_platform_mismatch",
                stage="artifact_copy",
            )
        executable, _descriptor = self._verified_staged_artifact(parameters)
        self._verify_copied_elf_platform(executable, parameters["platform"])
        if cancel_event.is_set():
            return {"status": "cancelled"}

        implementation_label = "Amaru" if implementation == "amaru" else "Cardano"
        base_environment = (
            "DWARF_ARTIFACT_PACKAGE_AMARU_BASE"
            if implementation == "amaru"
            else "DWARF_ARTIFACT_PACKAGE_CARDANO_BASE"
        )
        base_image = os.environ.get(base_environment, "").strip()
        if not base_image or "@sha256:" not in base_image:
            raise DockerRuntimeError(
                f"{implementation_label} packaging base is unavailable",
                code="artifact_base_unavailable",
                stage="artifact_package",
            )
        if _immutable_image_identity(base_image) not in self._approved_image_identities():
            raise DockerRuntimeError(
                f"{implementation_label} packaging base is unavailable",
                code="artifact_base_unavailable",
                stage="artifact_package",
            )
        base = self.inspect_image(base_image, implementation=implementation)
        if base["platform"] != parameters["platform"]:
            raise DockerRuntimeError(
                f"{implementation_label} packaging base platform differs",
                code="artifact_base_platform_mismatch",
                stage="artifact_package",
            )

        work_root = self.state_dir / "controller" / "artifact-packages" / execution_id
        if work_root.exists() or work_root.is_symlink():
            raise DockerRuntimeError("artifact package workspace already exists")
        work_root.mkdir(parents=True, mode=0o700)
        probe_name = f"dwarf-package-probe-{execution_id.removeprefix('execution-')}"
        tag = self._artifact_package_tag(
            implementation, execution_id, parameters["executable_sha256"]
        )
        keep_image = False
        try:
            executable_path = work_root / "executable"
            with executable_path.open("xb") as stream:
                stream.write(executable)
                stream.flush()
                os.fsync(stream.fileno())
            # Docker's legacy builder preserves context file modes but rejects
            # COPY --chmod. Make the already-verified bytes executable without
            # introducing a build-time RUN step or toolchain dependency.
            os.chmod(executable_path, 0o555)
            dockerfile = self._artifact_dockerfile(
                base_image=base_image,
                implementation=implementation,
                version=parameters["version"],
                executable_sha256=parameters["executable_sha256"],
                declared_source_revision=parameters.get("declared_source_revision"),
            )
            (work_root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
            build = self._run(
                (
                    "build",
                    "--pull=false",
                    "--network=none",
                    "--no-cache",
                    "--tag",
                    tag,
                    str(work_root),
                ),
                timeout=max(1, timeout - 35),
                execution_id=execution_id,
            )
            if cancel_event.is_set():
                return {"status": "cancelled"}
            if build.returncode != 0:
                raise DockerRuntimeError(
                    "local artifact image packaging failed",
                    code="artifact_build_failed",
                    stage="artifact_package",
                )
            inspected = self.inspect_image(tag, implementation=implementation)
            expected_interface = "extracted-binary" if implementation == "amaru" else None
            if (
                inspected["platform"] != parameters["platform"]
                or inspected["runtime_interface"] != expected_interface
            ):
                raise DockerRuntimeError(
                    f"packaged {implementation_label} interface is invalid",
                    code="artifact_interface_invalid",
                    stage="artifact_probe",
                )
            try:
                probe = self._run(
                    (
                    "run",
                    "--rm",
                    "--name",
                    probe_name,
                    "--network=none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "64",
                    "--memory",
                    "256m",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,nodev,size=16m",
                    inspected["config_image_id"],
                    "--version",
                    ),
                    timeout=min(30, max(1, timeout)),
                    execution_id=execution_id,
                    output_limit=64 * 1024,
                )
            except DockerRuntimeError as exc:
                if str(exc) == "Docker operation output exceeded limit":
                    raise DockerRuntimeError(
                        "packaged artifact probe output exceeded limit",
                        code="artifact_probe_output_exceeded",
                        stage="artifact_probe",
                    ) from exc
                raise
            if cancel_event.is_set():
                return {"status": "cancelled"}
            if probe.returncode != 0:
                raise DockerRuntimeError(
                    f"packaged {implementation_label} executable smoke failed",
                    code="artifact_probe_failed",
                    stage="artifact_probe",
                )
            try:
                interface_probe = self._run(
                    (
                    "run",
                    "--rm",
                    "--name",
                    probe_name,
                    "--network=none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "64",
                    "--memory",
                    "256m",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,nodev,size=16m",
                    inspected["config_image_id"],
                    "run",
                    "--help",
                    ),
                    timeout=min(30, max(1, timeout)),
                    execution_id=execution_id,
                    output_limit=64 * 1024,
                )
            except DockerRuntimeError as exc:
                if str(exc) == "Docker operation output exceeded limit":
                    raise DockerRuntimeError(
                        "packaged artifact probe output exceeded limit",
                        code="artifact_probe_output_exceeded",
                        stage="artifact_probe",
                    ) from exc
                raise
            if cancel_event.is_set():
                return {"status": "cancelled"}
            if interface_probe.returncode != 0:
                raise DockerRuntimeError(
                    f"packaged {implementation_label} run interface smoke failed",
                    code="artifact_interface_probe_failed",
                    stage="artifact_probe",
                )
            keep_image = True
            return {
                "status": "packaged",
                "implementation": implementation,
                "version": parameters["version"],
                "config_image_id": inspected["config_image_id"],
                "manifest_references": inspected["manifest_references"],
                "platform": inspected["platform"],
                "runtime_interface": inspected["runtime_interface"],
                "upload_sha256": parameters["upload_sha256"],
                "executable_sha256": parameters["executable_sha256"],
                "declared_source_revision": parameters.get("declared_source_revision"),
                "provenance": "operator-upload",
                "qualification": "unverified",
                "compatibility": "unknown-until-run",
                "controller_installation_id": self.installation_id,
                "packaged_at": _utc_now(),
                "probe": {
                    "kind": "isolated-executable-and-run-help-smoke",
                    "status": "passed",
                },
            }
        finally:
            self._cleanup_artifact_package_resources(
                tag=tag,
                probe_name=probe_name,
                work_root=work_root,
                remove_image=not keep_image,
            )

    def _qualified_local_target(
        self, image: str, qualified_target: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve exactly one profile-selected local target from the shared registry."""

        from profile_manager.measurement_targets import (
            MeasurementTargetError,
            resolve_patched_amaru_target,
            resolve_patched_cardano_target,
        )

        implementation = str(qualified_target.get("impl") or "")
        if qualified_target.get("target_mode") != "patched" or implementation not in {
            "amaru",
            "cardano-node",
        }:
            raise DockerRuntimeError("qualified local target identity is invalid")
        profile = SimpleNamespace(
            amaru_version=str(qualified_target.get("version") or ""),
            cardano_version=str(qualified_target.get("version") or ""),
            measurement_patch_revision=str(
                qualified_target.get("source_revision") or ""
            ),
            measurement_patch_set_sha256=str(
                qualified_target.get("patch_set_sha256") or ""
            ),
        )
        resolver = (
            resolve_patched_amaru_target
            if implementation == "amaru"
            else resolve_patched_cardano_target
        )
        try:
            record = resolver(
                profile, registry_root=self.state_dir / "measurement-targets"
            )
        except (MeasurementTargetError, OSError, TypeError, ValueError) as exc:
            raise DockerRuntimeError(
                "qualified local target record is unavailable or invalid"
            ) from exc
        expected = {
            "implementation": implementation,
            "mode": "patched",
            "version": qualified_target.get("version"),
            "source_revision": qualified_target.get("source_revision"),
            "patch_set_sha256": qualified_target.get("patch_set_sha256"),
            "image_digest": qualified_target.get("image_digest"),
            "executable_digest": qualified_target.get("executable_digest"),
            "build_result_sha256": qualified_target.get("build_result_sha256"),
            "runtime_probe_log_sha256": qualified_target.get(
                "runtime_probe_log_sha256"
            ),
            "runtime_probe_image": qualified_target.get("runtime_probe_image"),
        }
        runtime_reference = str(
            record.get("runtime_image_reference") or record.get("image_reference") or ""
        )
        if image != runtime_reference or any(
            record.get(key) != value for key, value in expected.items()
        ):
            raise DockerRuntimeError(
                "qualified local target differs from the profile-resolved identity"
            )
        if record.get("runtime_image_reference") is not None:
            imported_expected = {
                "image_reference": qualified_target.get("source_image_reference"),
                "image_manifest_digest": qualified_target.get(
                    "image_manifest_digest"
                ),
                "image_config_digest": qualified_target.get("image_config_digest"),
                "qualification_image_id": qualified_target.get(
                    "qualification_image_id"
                ),
                "transfer_source_installation_id": qualified_target.get(
                    "transfer_source_installation_id"
                ),
                "transfer_manifest_sha256": qualified_target.get(
                    "transfer_manifest_sha256"
                ),
            }
            source_installation_id = qualified_target.get(
                "transfer_source_installation_id"
            )
            if not isinstance(
                source_installation_id, str
            ) or not _TRANSFER_SOURCE_INSTALLATION_ID.fullmatch(
                source_installation_id
            ):
                raise DockerRuntimeError(
                    "qualified local target imported provenance is invalid"
                )
            if any(
                record.get(key) != value
                for key, value in imported_expected.items()
            ):
                raise DockerRuntimeError(
                    "qualified local target differs from the imported provenance"
                )
        return record

    def _assert_approved_image(
        self,
        image: str,
        *,
        qualified_target: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if qualified_target is not None:
            record = self._qualified_local_target(image, qualified_target)
            self._verify_qualified_local_image(image, record)
            return record
        if _IMAGE_CONFIG_ID.fullmatch(image):
            raise DockerRuntimeError(
                "bare image config identities require qualified imported provenance"
            )
        approved = self._approved_image_identities()
        if approved and _immutable_image_identity(image) in approved:
            return None
        raise DockerRuntimeError("target is not an approved golden image")

    def _verify_qualified_local_image(
        self, image: str, target_record: dict[str, Any]
    ) -> None:
        present = self._run(("image", "inspect", image), timeout=20)
        if present.returncode != 0:
            raise DockerRuntimeError("qualified local target image is unavailable")
        try:
            inspected = json.loads(present.stdout or "null")
        except json.JSONDecodeError as exc:
            raise DockerRuntimeError(
                "qualified local target image inspection is invalid"
            ) from exc
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise DockerRuntimeError(
                "qualified local target image inspection is ambiguous"
            )
        item = inspected[0]
        host_os = os.environ.get("DWARF_HOST_OS", "").strip().lower()
        host_arch = os.environ.get("DWARF_HOST_ARCH", "").strip().lower()
        if host_os not in {"darwin", "linux"} or host_arch not in {
            "amd64",
            "arm64",
        }:
            raise DockerRuntimeError(
                "qualified local target host platform is unavailable"
            )
        runtime_reference = target_record.get("runtime_image_reference")
        if runtime_reference is not None:
            identity_matches = bool(
                image == runtime_reference
                and runtime_reference == target_record.get("image_config_digest")
                and runtime_reference == target_record.get("image_digest")
                and item.get("Id") == runtime_reference
            )
        else:
            repo_digests = item.get("RepoDigests")
            identity_matches = bool(
                item.get("Id") == target_record.get("image_digest")
                and isinstance(repo_digests, list)
                and target_record.get("image_reference") in repo_digests
            )
        if (
            not identity_matches
            or item.get("Os") != "linux"
            or item.get("Architecture") != host_arch
        ):
            raise DockerRuntimeError(
                "qualified local target image identity or platform differs"
            )

    def _verify_qualified_runtime_probe_image(
        self, image: str, target_record: dict[str, Any]
    ) -> None:
        if target_record.get("runtime_probe_image") != image:
            raise DockerRuntimeError(
                "qualified local target runtime probe differs from the registry"
            )
        present = self._run(("image", "inspect", image), timeout=20)
        if present.returncode != 0:
            raise DockerRuntimeError(
                "qualified local target runtime probe image is unavailable"
            )
        try:
            inspected = json.loads(present.stdout or "null")
        except json.JSONDecodeError as exc:
            raise DockerRuntimeError(
                "qualified local target runtime probe inspection is invalid"
            ) from exc
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise DockerRuntimeError(
                "qualified local target runtime probe inspection is ambiguous"
            )
        item = inspected[0]
        repo_digests = item.get("RepoDigests")
        host_arch = os.environ.get("DWARF_HOST_ARCH", "").strip().lower()
        if (
            "@sha256:" not in image
            or not isinstance(repo_digests, list)
            or image not in repo_digests
            or item.get("Os") != "linux"
            or host_arch not in {"amd64", "arm64"}
            or item.get("Architecture") != host_arch
        ):
            raise DockerRuntimeError(
                "qualified local target runtime probe identity or platform differs"
            )

    def _assert_node_images(self, node: dict[str, Any]) -> None:
        registration = node.get("operator_registration")
        if isinstance(registration, dict):
            from profile_manager.deployment_versions import (
                DeploymentVersionGateError,
                operator_registration_release,
            )

            try:
                release = operator_registration_release(registration)
            except DeploymentVersionGateError as exc:
                raise DockerRuntimeError(
                    "operator-registered image provenance is invalid"
                ) from exc
            image = str(node.get("image") or "")
            if (
                image != registration.get("config_image_id")
                or node.get("impl") != release.get("implementation")
                or node.get("version") != release.get("version")
            ):
                raise DockerRuntimeError(
                    "operator-registered image differs from the selected identity"
                )
            inspected = self.inspect_image(
                image, implementation=str(node.get("impl") or "")
            )
            manifest = registration.get("manifest_digest")
            reference = registration.get("image_reference")
            if (
                inspected.get("config_image_id") != image
                or inspected.get("platform") != registration.get("platform")
                or inspected.get("runtime_interface")
                != registration.get("runtime_interface")
                or (
                    manifest is not None
                    and (
                        not isinstance(reference, str)
                        or not reference.endswith("@" + str(manifest))
                        or reference not in inspected.get("manifest_references", [])
                    )
                )
            ):
                raise DockerRuntimeError(
                    "operator-registered image identity or platform differs"
                )
            return
        qualified_target = (
            node if node.get("target_mode") == "patched" else None
        )
        target_record = self._assert_approved_image(
            str(node.get("image") or ""),
            qualified_target=qualified_target,
        )
        probe_image = node.get("runtime_probe_image")
        if not probe_image:
            return
        if target_record is not None:
            self._verify_qualified_runtime_probe_image(
                str(probe_image), target_record
            )
        else:
            self._assert_approved_image(str(probe_image))

    @staticmethod
    def _assert_operator_registration_state(substrate: dict[str, Any]) -> None:
        """Revalidate the exact operator overlay immediately before mutation."""

        registration_id = substrate.get("operator_registration_id")
        overlay_revision = substrate.get("operator_overlay_revision")
        catalog = substrate.get("catalog_snapshot")
        registered_nodes = [
            node
            for node in substrate.get("nodes") or []
            if isinstance(node, dict)
            and isinstance(node.get("operator_registration"), dict)
        ]
        if registration_id is None and overlay_revision is None and not registered_nodes:
            return
        if (
            not isinstance(registration_id, str)
            or not registration_id
            or not isinstance(overlay_revision, str)
            or not _IMAGE_CONFIG_ID.fullmatch(overlay_revision)
            or not isinstance(catalog, dict)
            or catalog.get("operator_registration_id") != registration_id
            or catalog.get("operator_overlay_revision") != overlay_revision
            or not registered_nodes
        ):
            raise DockerRuntimeError(
                "operator registration state differs from the selected identity"
            )
        from profile_manager.version_discovery import load_operator_registration

        try:
            selected = load_operator_registration(registration_id)
        except ValueError as exc:
            raise DockerRuntimeError(
                "operator registration state differs from the selected identity"
            ) from exc
        registration = selected.get("registration")
        if (
            selected.get("revision") != overlay_revision
            or not isinstance(registration, dict)
            or any(node.get("operator_registration") != registration for node in registered_nodes)
        ):
            raise DockerRuntimeError(
                "operator registration state differs from the selected identity"
            )

    def prepare_topology(self, topology: dict[str, Any]) -> dict[str, Any]:
        """Resolve a thin client identity into a complete server-owned deployment."""

        from profile_manager.deployment_versions import (
            build_deployment_version_preview,
            version_catalog_revision,
        )
        from profile_manager.profiles import (
            desired_topology_for_profile,
            find_profile,
            versioned_substrate_for_profile,
        )
        from profile_manager.version_discovery import load_effective_version_catalog

        topology_id = str(topology.get("topology_id") or "")
        catalog_revision = topology.get("catalog_revision")
        if catalog_revision is not None and (
            not isinstance(catalog_revision, str)
            or not _CATALOG_REVISION.fullmatch(catalog_revision)
        ):
            raise DockerRuntimeError("requested catalog revision is invalid")
        controller_catalog = load_effective_version_catalog()
        controller_catalog_revision = version_catalog_revision(
            catalog=controller_catalog
        )
        if (
            catalog_revision is not None
            and catalog_revision != controller_catalog_revision
        ):
            raise DockerRuntimeError(
                "requested catalog revision differs from the controller catalog"
            )
        try:
            profile = find_profile(topology_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise DockerRuntimeError("topology is not a catalog-owned profile") from exc
        try:
            profile_data = asdict(profile)
            operator_registration_id = topology.get("operator_registration_id")
            operator_overlay_revision = topology.get("operator_overlay_revision")
            operator_implementation = None
            if operator_registration_id is not None:
                from profile_manager.version_discovery import load_operator_registration

                operator_record = load_operator_registration(
                    str(operator_registration_id)
                )
                operator_implementation = operator_record["registration"].get(
                    "implementation"
                )
            requested_versions: dict[str, str] = {}
            for implementation, count, field in (
                ("cardano-node", profile.node_count, "cardano_version"),
                ("amaru", profile.amaru_node_count, "amaru_version"),
            ):
                if count < 1:
                    continue
                if implementation == operator_implementation:
                    continue
                versions = {
                    str(node.get("version") or "")
                    for node in (topology.get("nodes") or [])
                    if isinstance(node, dict)
                    and node.get("implementation") == implementation
                    and node.get("version")
                }
                if len(versions) > 1:
                    raise ValueError(
                        f"requested {implementation} nodes do not share one exact version"
                    )
                if len(versions) == 1:
                    requested_versions[field] = versions.pop()
            if requested_versions:
                profile_data.update(requested_versions)
                profile_data["version_policy"] = "exact"
                profile_data["compatibility_pair"] = None
            preview = build_deployment_version_preview(
                profile_data,
                catalog=controller_catalog,
                catalog_revision=catalog_revision or controller_catalog_revision,
                operator_registration_id=operator_registration_id,
            )
            if operator_registration_id is not None and (
                preview.get("operator_overlay_revision") != operator_overlay_revision
            ):
                raise ValueError("operator registration revision differs")
            if preview.get("blocked"):
                raise DockerRuntimeError(
                    "requested catalog resolution is blocked"
                )
            desired = desired_topology_for_profile(profile, preview)
        except (KeyError, TypeError, ValueError) as exc:
            raise DockerRuntimeError(
                "requested identity differs from the catalog-resolved topology"
            ) from exc
        expected = {
            "topology_id": desired["topology_id"],
            "network_magic": desired["network_magic"],
            "nodes": [
                {
                    key: node[key]
                    for key in ("id", "implementation", "version", "image")
                }
                for node in desired["nodes"]
            ],
        }
        if catalog_revision is not None:
            expected["catalog_revision"] = catalog_revision
        if preview.get("operator_registration_id") is not None:
            expected["operator_registration_id"] = preview["operator_registration_id"]
            expected["operator_overlay_revision"] = preview[
                "operator_overlay_revision"
            ]
        if topology != expected:
            raise DockerRuntimeError(
                "requested identity differs from the catalog-resolved topology"
            )
        substrate = versioned_substrate_for_profile(profile, preview)
        if substrate.get("compose_mode") != "docker" or substrate.get("host_strategy") == "explicit":
            raise DockerRuntimeError("only single-machine Docker topology adapters are supported")
        for node in substrate.get("nodes") or []:
            self._assert_node_images(node)
        config_source = substrate.get("config_source_dir")
        if config_source and not Path(str(config_source)).is_dir():
            # Installed containers do not inherit host-specific /opt paths.
            # Public network assets are downloaded by the adapter instead.
            if substrate.get("network") not in {"mainnet", "preprod", "preview"}:
                raise DockerRuntimeError("configured topology assets are unavailable")
            substrate["config_source_dir"] = None
        substrate["controller_ownership"] = {
            "installation_id": self.installation_id,
            "topology_id": topology_id,
        }
        substrate["docker_host_runtime_root"] = str(
            self.host_state_dir
            / "controller"
            / "topology-runtimes"
            / topology_id
            / "current"
        )
        adapter = (
            "runtime_amaru_control_substrate.py"
            if substrate.get("deployment_adapter") == "amaru-control"
            else "runtime_compose_substrate.py"
        )
        return {
            "schema_version": "v1",
            "adapter": adapter,
            "compose_project": f"dwarf-profile-{self.installation_id}-{topology_id}",
            "substrate": substrate,
        }

    def run_launch(
        self,
        *,
        launch_id: str,
        plan_digest: str,
        execution_id: str,
        timeout: int,
        cancel_event: threading.Event | None = None,
        request_controller=None,
    ) -> dict[str, Any]:
        """Execute one immutable dashboard launch inside the controller boundary."""

        from profile_manager.config import storage_roots
        from profile_manager.launch_store import (
            LaunchStoreError,
            expected_launch_artifacts,
            load_launch,
            materialize_launch_snapshot,
        )
        from profile_manager.run_plan import (
            RunPlanError,
            digest_run_plan,
            resolve_run_plan,
        )

        cancellation = cancel_event or threading.Event()
        if cancellation.is_set():
            return {"status": "cancelled", "run_id": None, "exit_code": 130}

        try:
            launch = load_launch(launch_id, root=self.launch_root)
            request = launch["plan"].get("request")
            capability_context = None
            registration_id = (
                request.get("operator_registration_id")
                if isinstance(request, dict)
                else None
            )
            if registration_id is not None:
                from profile_manager.deployment_versions import (
                    operator_registration_capability_context_from_observation,
                )

                versions = launch["plan"].get("versions") or {}
                registrations = [
                    release.get("operator_registration")
                    for group in ("resolved", "supporting")
                    for release in (versions.get(group) or {}).values()
                    if isinstance(release, dict)
                    and isinstance(release.get("operator_registration"), dict)
                ]
                if (
                    versions.get("operator_registration_id") != registration_id
                    or len(registrations) != 1
                    or registrations[0].get("registration_id") != registration_id
                ):
                    raise ValueError("frozen operator registration identity is invalid")
                registration = registrations[0]
                inspected = self.inspect_image(
                    registration["image_reference"],
                    implementation=registration["implementation"],
                )
                source_revision = (registration.get("provenance") or {}).get(
                    "source_revision"
                )
                capability_context = (
                    operator_registration_capability_context_from_observation(
                        registration,
                        {
                            "schema_version": "v1",
                            "implementation": registration["implementation"],
                            "version": registration["version"],
                            "requested_image": registration["image_reference"],
                            **inspected,
                            "declared_source_revision": source_revision,
                            "installation_id": self.installation_id,
                        },
                    )
                )
            resolved = resolve_run_plan(
                request,
                capability_context=capability_context,
                request_controller=request_controller,
            )
        except (LaunchStoreError, RunPlanError, OSError, ValueError) as exc:
            raise DockerRuntimeError("launch inputs are invalid or stale") from exc
        current_digest = digest_run_plan(resolved)
        if current_digest != plan_digest:
            raise DockerRuntimeError("launch plan digest does not match the active catalog")
        if digest_run_plan(launch["plan"]) != current_digest:
            raise DockerRuntimeError("launch plan does not match the active catalog")
        expected = expected_launch_artifacts(resolved)
        for name, body in expected.items():
            path = launch["directory"] / name
            try:
                observed = path.read_bytes()
            except OSError as exc:
                raise DockerRuntimeError(
                    "launch inputs do not match the active catalog"
                ) from exc
            if observed != body:
                raise DockerRuntimeError(
                    "launch inputs do not match the active catalog"
                )

        preflight = None
        preflight_path = launch["directory"] / "preflight.json"
        if preflight_path.exists():
            if preflight_path.is_symlink() or not preflight_path.is_file():
                raise DockerRuntimeError("launch preflight input is invalid")
            try:
                preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DockerRuntimeError("launch preflight input is invalid") from exc
            if not isinstance(preflight, dict) or preflight.get("state") != "ready":
                raise DockerRuntimeError("launch preflight is not ready")

        private_launch_root = (
            self.state_dir / "controller" / "run-launches" / execution_id
        )
        try:
            private_launch = materialize_launch_snapshot(
                resolved,
                launch_id=launch_id,
                root=private_launch_root,
                preflight=preflight,
            )
        except (LaunchStoreError, OSError, ValueError) as exc:
            raise DockerRuntimeError(
                "controller could not materialize trusted launch inputs"
            ) from exc

        command = (
            shutil.which("env") or "/usr/bin/env",
            f"ADA2_DWARF_LAUNCH_ROOT={private_launch_root}",
            f"ADA2_DWARF_LAUNCH_ID={launch_id}",
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "cardano-profile"),
            "scenario",
            "run",
            str(private_launch["path"] / "scenario.yaml"),
            "--origin",
            "dashboard",
        )
        profile_id = str((resolved.get("profile") or {}).get("id") or "")
        attachment = (
            self._attach_run_network(
                profile_id, execution_id, cancel_event=cancellation
            )
            if profile_id
            else None
        )
        if profile_id and attachment is None:
            return {"status": "cancelled", "run_id": None, "exit_code": 130}
        try:
            if cancellation.is_set():
                return {"status": "cancelled", "run_id": None, "exit_code": 130}
            attached_command = command
            if attachment is not None:
                attached_command = (
                    command[0],
                    f"ADA2_DWARF_RUN_NETWORK={attachment['network_name']}",
                    *command[1:],
                )
            result = self._run_adapter(
                attached_command, timeout, execution_id, cancel_event=cancellation
            )
            if cancellation.is_set() and result.returncode != 0:
                return {"status": "cancelled", "run_id": None, "exit_code": 130}
        finally:
            if attachment is not None:
                self._detach_run_network(attachment, state="detached")
        match = re.search(r"(?m)^run_id:\s*([A-Za-z0-9._-]+)\s*$", result.stdout)
        if match is None:
            raise DockerRuntimeError(
                "scenario runner returned no canonical run identity",
                code="run_adapter_identity_missing",
                stage="adapter_execution",
                return_code=int(result.returncode),
                adapter_output=_bounded_adapter_output(result.stdout, result.stderr),
            )
        run_id = match.group(1)
        manifest_path = storage_roots().runs / run_id / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DockerRuntimeError("scenario runner produced no readable manifest") from exc
        if manifest.get("run_id") != run_id:
            raise DockerRuntimeError("scenario runner manifest identity is inconsistent")
        execution = manifest.get("execution") or {}
        return {
            "status": "completed",
            "run_id": run_id,
            "exit_code": int(result.returncode),
            "exit_status": manifest.get("exit_status"),
            "security_verdict": execution.get("security_verdict"),
            "manifest_path": str(manifest_path),
        }

    def _attachment_path(self, execution_id: str) -> Path:
        if not _SAFE_RUNTIME_ID.fullmatch(execution_id):
            raise DockerRuntimeError("run execution identity is invalid")
        return self.run_attachments_dir / f"{execution_id}.json"

    def _write_run_attachment(self, record: dict[str, Any]) -> None:
        _atomic_private_json(
            self._attachment_path(str(record.get("execution_id") or "")), record
        )

    def resolve_run_network(self, profile_id: str) -> str:
        """Return the one owned topology network shared by the profile nodes."""

        from profile_manager.profiles import runtime_root

        metadata_path = runtime_root(profile_id) / "runtime.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DockerRuntimeError("managed topology runtime metadata is unavailable") from exc
        if metadata.get("profile_id") != profile_id:
            raise DockerRuntimeError("managed topology runtime identity is inconsistent")
        nodes = metadata.get("nodes")
        containers: list[dict[str, Any]] = []
        declared_networks: set[str] | None = None
        if metadata.get("lifecycle") == "cardano_amaru_relay_bootstrap_control":
            compose_project = str(metadata.get("compose_project") or "")
            if not compose_project:
                raise DockerRuntimeError("managed Amaru topology project is missing")
            config = self._verify_retained_runtime(
                runtime_root=metadata_path.parent,
                topology_id=profile_id,
                compose_project=compose_project,
                enforce_golden=True,
            )
            substrate = config.get("substrate") or {}
            selected_nodes = substrate.get("nodes")
            mapping = config.get("controller_node_services")
            if not isinstance(selected_nodes, list) or not isinstance(mapping, dict):
                raise DockerRuntimeError("managed Amaru topology mapping is invalid")
            mapped_services = [
                str(mapping.get(str(node.get("id") or "")) or "")
                for node in selected_nodes
                if isinstance(node, dict)
            ]
            if (
                not mapped_services
                or len(mapped_services) != len(selected_nodes)
                or any(not service for service in mapped_services)
                or len(set(mapped_services)) != len(mapped_services)
            ):
                raise DockerRuntimeError("managed Amaru topology mapping is invalid")
            shape = substrate.get("actual_topology") or {}
            network_keys = shape.get("network_names")
            if not isinstance(network_keys, list) or not network_keys:
                raise DockerRuntimeError("managed Amaru topology networks are invalid")
            declared_networks = {
                f"{compose_project}-{str(network_key)}" for network_key in network_keys
            }
            if len(declared_networks) != len(network_keys):
                raise DockerRuntimeError("managed Amaru topology networks are ambiguous")
            for service in mapped_services:
                container_name = f"{compose_project}-{service}-1"
                container = self._inspect_optional("container", container_name)
                if container is None:
                    raise DockerRuntimeError("managed Amaru topology service is absent")
                if not self._project_service_owned(
                    container,
                    topology_id=profile_id,
                    compose_project=compose_project,
                    expected_roles={service: "node"},
                ):
                    raise DockerRuntimeError(
                        "managed Amaru topology service ownership is invalid"
                    )
                containers.append(container)
        elif isinstance(nodes, list) and nodes:
            container_name = str(nodes[0].get("container_name") or "")
            if not container_name:
                raise DockerRuntimeError("managed topology node identity is missing")
            container = self._assert_owned_container(container_name, profile_id)
            if container is None:
                raise DockerRuntimeError("managed topology node is absent")
            containers.append(container)
        else:
            raise DockerRuntimeError("managed topology runtime has no nodes")

        common_owned_networks: set[str] | None = None
        for container in containers:
            network_map = (container.get("NetworkSettings") or {}).get("Networks") or {}
            if not isinstance(network_map, dict):
                raise DockerRuntimeError("managed topology node networks are invalid")
            container_owned_networks = set()
            for name in sorted(network_map):
                network = self._inspect_optional("network", str(name))
                if network is not None and self._network_owned(network, profile_id):
                    if declared_networks is not None and str(name) not in declared_networks:
                        raise DockerRuntimeError(
                            "managed Amaru topology network is not retained"
                        )
                    container_owned_networks.add(str(name))
            if not container_owned_networks:
                raise DockerRuntimeError(
                    "managed topology must expose exactly one owned run network"
                )
            if common_owned_networks is None:
                common_owned_networks = container_owned_networks
            else:
                common_owned_networks.intersection_update(container_owned_networks)
        if common_owned_networks is None or len(common_owned_networks) != 1:
            raise DockerRuntimeError(
                "managed topology must expose exactly one owned run network"
            )
        return next(iter(common_owned_networks))

    def _attach_run_network(
        self,
        profile_id: str,
        execution_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any] | None:
        """Join the one owned topology network needed by a profile-bound run."""

        controller_id = str(os.environ.get("HOSTNAME") or "")
        if not controller_id or not _INSTALLATION_ID.fullmatch(controller_id):
            raise DockerRuntimeError("controller container identity is unavailable")
        network_name = self.resolve_run_network(profile_id)
        attachment = {
            "schema_version": "v1",
            "execution_id": execution_id,
            "installation_id": self.installation_id,
            "profile_id": profile_id,
            "network_name": network_name,
            "controller_id": controller_id,
            "state": "attaching",
            "created_at": _utc_now(),
            "completed_at": None,
        }
        self._write_run_attachment(attachment)
        connected = self._run(
            ("network", "connect", network_name, controller_id),
            timeout=30,
            execution_id=execution_id,
        )
        if connected.returncode != 0:
            failed = {
                **attachment,
                "state": "attach_failed",
                "completed_at": _utc_now(),
            }
            self._write_run_attachment(failed)
            try:
                self._reconcile_run_attachment(failed)
            except DockerRuntimeError as exc:
                raise DockerRuntimeError(
                    "controller network join failed and membership could not be reconciled"
                ) from exc
            if cancel_event is not None and cancel_event.is_set():
                return None
            raise DockerRuntimeError("controller could not join the managed topology network")
        attachment = {**attachment, "state": "attached"}
        self._write_run_attachment(attachment)
        return attachment

    def _detach_run_network(
        self, attachment: dict[str, Any], *, state: str
    ) -> dict[str, Any]:
        network_name = str(attachment["network_name"])
        controller_id = str(attachment["controller_id"])
        detached = self._run(
            ("network", "disconnect", network_name, controller_id),
            timeout=30,
        )
        detail = str(detached.stderr or "").strip().lower()
        already_absent = "not connected" in detail or "is not connected to network" in detail
        terminal = {
            **attachment,
            "state": (
                state if detached.returncode == 0 or already_absent else "detach_failed"
            ),
            "completed_at": _utc_now(),
        }
        self._write_run_attachment(terminal)
        if detached.returncode != 0 and not already_absent:
            raise DockerRuntimeError(
                "controller could not leave the managed topology network"
            )
        return terminal

    @staticmethod
    def _network_has_controller(
        network: dict[str, Any], controller_id: str
    ) -> bool:
        if "Containers" not in network or not isinstance(
            network["Containers"], dict
        ):
            raise DockerRuntimeError("managed topology network membership is invalid")
        containers = network["Containers"]
        matches = []
        for container_id, details in containers.items():
            name = details.get("Name") if isinstance(details, dict) else None
            if (
                str(container_id) == controller_id
                or str(container_id).startswith(controller_id)
                or name == controller_id
            ):
                matches.append(str(container_id))
        if len(matches) > 1:
            raise DockerRuntimeError("managed topology controller membership is ambiguous")
        return len(matches) == 1

    def _reconcile_run_attachment(
        self, record: dict[str, Any]
    ) -> dict[str, Any]:
        profile_id = str(record["profile_id"])
        network_name = str(record["network_name"])
        controller_id = str(record["controller_id"])
        network = self._inspect_optional("network", network_name)
        if network is not None and not self._network_owned(network, profile_id):
            raise DockerRuntimeError("run network attachment ownership changed")
        if network is not None and self._network_has_controller(network, controller_id):
            return self._detach_run_network(record, state="reconciled")
        terminal = {**record, "state": "reconciled", "completed_at": _utc_now()}
        self._write_run_attachment(terminal)
        return terminal

    def reconcile_run_attachments(self) -> list[dict[str, str]]:
        """Remove crash-left controller memberships from exact owned networks."""

        reconciled: list[dict[str, str]] = []
        for path in sorted(self.run_attachments_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DockerRuntimeError("run network attachment record is invalid") from exc
            execution_id = str(record.get("execution_id") or "")
            profile_id = str(record.get("profile_id") or "")
            network_name = str(record.get("network_name") or "")
            controller_id = str(record.get("controller_id") or "")
            if (
                record.get("schema_version") != "v1"
                or path.stem != execution_id
                or not _SAFE_RUNTIME_ID.fullmatch(execution_id)
                or not _SAFE_RUNTIME_ID.fullmatch(profile_id)
                or not _SAFE_RUNTIME_ID.fullmatch(network_name)
                or not _INSTALLATION_ID.fullmatch(controller_id)
                or record.get("installation_id") != self.installation_id
            ):
                raise DockerRuntimeError("run network attachment identity is invalid")
            if record.get("state") in {"detached", "reconciled"}:
                continue
            self._reconcile_run_attachment(record)
            reconciled.append(
                {
                    "execution_id": execution_id,
                    "network_name": network_name,
                    "state": "reconciled",
                }
            )
        return reconciled

    def _run(
        self,
        arguments: Sequence[str],
        timeout: int = 30,
        execution_id: str | None = None,
        *,
        output_limit: int | None = None,
    ) -> DockerResult:
        argv = [self.docker_binary, *arguments]
        if self._external_runner is not None:
            result = self._external_runner(tuple(argv), timeout, execution_id)
            if output_limit is not None and (
                len(result.stdout.encode("utf-8", errors="replace"))
                + len(result.stderr.encode("utf-8", errors="replace"))
                > output_limit
            ):
                raise DockerRuntimeError("Docker operation output exceeded limit")
            return result
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=output_limit is None,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise DockerRuntimeError("Docker CLI is unavailable") from exc
        if execution_id is not None:
            with self._process_lock:
                self._processes[execution_id] = process
        try:
            if output_limit is None:
                stdout, stderr = process.communicate(timeout=timeout)
            else:
                assert process.stdout is not None and process.stderr is not None
                selected = selectors.DefaultSelector()
                selected.register(process.stdout, selectors.EVENT_READ, "stdout")
                selected.register(process.stderr, selectors.EVENT_READ, "stderr")
                chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
                total = 0
                deadline = time.monotonic() + timeout
                try:
                    while selected.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, timeout)
                        events = selected.select(min(0.1, remaining))
                        for key, _mask in events:
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selected.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > output_limit:
                                if process.poll() is None:
                                    try:
                                        os.killpg(process.pid, signal.SIGKILL)
                                    except (ProcessLookupError, PermissionError):
                                        process.kill()
                                process.wait(timeout=1)
                                raise DockerRuntimeError(
                                    "Docker operation output exceeded limit"
                                )
                            chunks[key.data].append(chunk)
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                finally:
                    selected.close()
                stdout = b"".join(chunks["stdout"]).decode(
                    "utf-8", errors="replace"
                )
                stderr = b"".join(chunks["stderr"]).decode(
                    "utf-8", errors="replace"
                )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            self._stop_process_group(process.pid)
            raise DockerRuntimeError("Docker operation timed out") from exc
        finally:
            if execution_id is not None:
                with self._process_lock:
                    self._processes.pop(execution_id, None)
        return DockerResult(process.returncode, stdout, stderr)

    @staticmethod
    def _json(result: DockerResult, operation: str) -> Any:
        if result.returncode != 0:
            raise DockerRuntimeError(f"Docker {operation} failed")
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as exc:
            raise DockerRuntimeError(f"Docker {operation} returned invalid JSON") from exc

    def health(self) -> dict[str, Any]:
        socket_path = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        result = self._run(("version", "--format", "{{json .Server}}"), timeout=10)
        compose = self._run(("compose", "version", "--format", "json"), timeout=10)
        configured_images = [
            item.strip()
            for item in os.environ.get("DWARF_GOLDEN_IMAGES", "").split(",")
            if item.strip()
        ]
        missing_images = [
            image
            for image in configured_images
            if self._run(("image", "inspect", image), timeout=20).returncode != 0
        ]
        return {
            "docker": "ok" if result.returncode == 0 else "error",
            "compose": "ok" if compose.returncode == 0 else "error",
            "socket": "ok" if socket_path.startswith("unix://") else "configured",
            "golden_images": "error" if not configured_images or missing_images else "ok",
            "golden_images_configured": len(configured_images),
            "golden_images_missing": missing_images,
        }

    def _all_containers(self) -> list[dict[str, Any]]:
        identifiers = self._run(("ps", "-aq"), timeout=15)
        if identifiers.returncode != 0:
            raise DockerRuntimeError("Docker inventory failed")
        ids = [line.strip() for line in identifiers.stdout.splitlines() if line.strip()]
        if not ids:
            return []
        inspected = self._json(self._run(("inspect", *ids), timeout=30), "inspect")
        if not isinstance(inspected, list):
            raise DockerRuntimeError("Docker inspect returned a non-list")
        return [item for item in inspected if isinstance(item, dict)]

    def _owned(self, item: dict[str, Any], *, topology_id: str | None = None) -> bool:
        labels = ((item.get("Config") or {}).get("Labels") or {})
        if not isinstance(labels, dict):
            return False
        expected = {
            _MANAGED: "true",
            _INSTALLATION: self.installation_id,
            _COMPONENT: "node",
        }
        if topology_id is not None:
            expected[_TOPOLOGY] = topology_id
        return all(labels.get(key) == value for key, value in expected.items())

    def _owned_runtime_resource(
        self, item: dict[str, Any], *, topology_id: str | None = None
    ) -> bool:
        """Recognize every owned node/support service without widening node APIs."""
        labels = ((item.get("Config") or {}).get("Labels") or {})
        if not isinstance(labels, dict):
            return False
        if labels.get(_COMPONENT) not in {"node", "support"}:
            return False
        expected = {
            _MANAGED: "true",
            _INSTALLATION: self.installation_id,
        }
        if topology_id is not None:
            expected[_TOPOLOGY] = topology_id
        return all(labels.get(key) == value for key, value in expected.items())

    def _owned_auxiliary_resources(
        self, kind: str, component: str
    ) -> list[dict[str, Any]]:
        """Return every labelled owned network/volume, rejecting ambiguous rows."""
        listed = self._run(
            (
                kind,
                "ls",
                "--quiet",
                "--filter",
                f"label={_MANAGED}=true",
                "--filter",
                f"label={_INSTALLATION}={self.installation_id}",
            ),
            timeout=30,
        )
        if listed.returncode != 0:
            raise DockerRuntimeError(f"Docker owned {kind} inventory failed")
        identifiers = [
            line.strip() for line in listed.stdout.splitlines() if line.strip()
        ]
        rows = []
        for identifier in identifiers:
            item = self._inspect_optional(kind, identifier)
            if item is None:
                raise DockerRuntimeError(f"owned {kind} inventory changed during inspect")
            labels = item.get("Labels") or {}
            topology_id = labels.get(_TOPOLOGY) if isinstance(labels, dict) else None
            if (
                not isinstance(labels, dict)
                or labels.get(_MANAGED) != "true"
                or labels.get(_INSTALLATION) != self.installation_id
                or labels.get(_COMPONENT) != component
                or not isinstance(topology_id, str)
                or not topology_id
            ):
                raise DockerRuntimeError(
                    f"owned {kind} inventory has invalid ownership labels"
                )
            name = str(item.get("Name") or "")
            resource_id = str(item.get("Id") or name)
            if not name or not resource_id:
                raise DockerRuntimeError(f"owned {kind} inventory identity is invalid")
            rows.append(
                {
                    "id": resource_id,
                    "name": name,
                    "image": "",
                    "state": "present",
                    "node_id": "",
                    "implementation": "",
                    "version": "",
                    "topology_id": topology_id,
                    "component": component,
                    "ownership": "managed",
                    "mutation_allowed": True,
                }
            )
        return rows

    def _project_service_owned(
        self,
        item: dict[str, Any],
        *,
        topology_id: str,
        compose_project: str,
        expected_roles: dict[str, str] | None = None,
    ) -> bool:
        """Validate one retained adapter service without widening node inventory."""

        labels = ((item.get("Config") or {}).get("Labels") or {})
        if not isinstance(labels, dict):
            return False
        service = str(labels.get("com.docker.compose.service") or "")
        role = str(labels.get(_COMPONENT) or "")
        name = str(item.get("Name") or "").lstrip("/")
        valid = bool(
            service
            and role in {"node", "support"}
            and labels.get(_MANAGED) == "true"
            and labels.get(_INSTALLATION) == self.installation_id
            and labels.get(_TOPOLOGY) == topology_id
            and labels.get("com.docker.compose.project") == compose_project
            and name == f"{compose_project}-{service}-1"
        )
        if not valid or expected_roles is None:
            return valid
        return expected_roles.get(service) == role

    def _project_runtime_containers(
        self,
        *,
        topology_id: str,
        compose_project: str,
        containers: list[dict[str, Any]] | None = None,
        expected_roles: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return an exact owned node/support Compose scope or fail closed."""

        matching = []
        for item in containers if containers is not None else self._all_containers():
            name = str(item.get("Name") or "").lstrip("/")
            labels = ((item.get("Config") or {}).get("Labels") or {})
            relevant = bool(
                name.startswith(compose_project + "-")
                or (
                    isinstance(labels, dict)
                    and labels.get(_INSTALLATION) == self.installation_id
                    and labels.get(_TOPOLOGY) == topology_id
                )
            )
            if not relevant:
                continue
            if not self._project_service_owned(
                item,
                topology_id=topology_id,
                compose_project=compose_project,
                expected_roles=expected_roles,
            ):
                raise DockerRuntimeError(
                    "refusing to mutate an unmanaged container collision"
                )
            matching.append(item)
        return matching

    def _current_containers_for_prior_scope(
        self,
        *,
        topology_id: str,
        before: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        projects = {
            str(((item.get("Config") or {}).get("Labels") or {}).get(
                "com.docker.compose.project"
            ) or "")
            for item in before
        }
        projects.discard("")
        if not projects:
            return [
                item
                for item in self._all_containers()
                if self._owned(item, topology_id=topology_id)
            ]
        if len(projects) != 1:
            raise DockerRuntimeError("topology project ownership is ambiguous")
        compose_project = next(iter(projects))
        prior_roles = {
            str(labels.get("com.docker.compose.service") or ""): str(
                labels.get(_COMPONENT) or ""
            )
            for item in before
            if isinstance(
                labels := ((item.get("Config") or {}).get("Labels") or {}), dict
            )
        }
        if "" in prior_roles or len(prior_roles) != len(before):
            raise DockerRuntimeError("topology service ownership is ambiguous")
        expected_roles = self._retained_project_roles(
            topology_id=topology_id,
            compose_project=compose_project,
        )
        if any(expected_roles.get(service) != role for service, role in prior_roles.items()):
            raise DockerRuntimeError("topology service role changed before mutation")
        return self._project_runtime_containers(
            topology_id=topology_id,
            compose_project=compose_project,
            expected_roles=expected_roles,
        )

    @staticmethod
    def _container_row(item: dict[str, Any]) -> dict[str, Any]:
        config = item.get("Config") or {}
        labels = config.get("Labels") or {}
        state = item.get("State") or {}
        return {
            "id": str(item.get("Id") or ""),
            "name": str(item.get("Name") or "").lstrip("/"),
            "image": str(config.get("Image") or ""),
            "state": str(state.get("Status") or "unknown"),
            "node_id": str(labels.get("io.dwarf.node_id") or ""),
            "implementation": str(labels.get("io.dwarf.implementation") or ""),
            "version": str(labels.get("io.dwarf.version") or ""),
            "topology_id": str(labels.get("io.dwarf.topology") or ""),
        }

    @staticmethod
    def _visible_unmanaged_node(item: dict[str, Any]) -> bool:
        config = item.get("Config") or {}
        labels = config.get("Labels") or {}
        if labels.get("io.dwarf.component") == "node":
            return True
        if labels.get("ada2.managed") == "dwarf" or labels.get("ada2.profile"):
            return True
        identity = " ".join(
            (
                str(item.get("Name") or ""),
                str(config.get("Image") or ""),
            )
        ).lower()
        return "cardano-node" in identity or "amaru" in identity

    def inventory(self) -> dict[str, Any]:
        containers = self._all_containers()
        for item in containers:
            labels = ((item.get("Config") or {}).get("Labels") or {})
            if not isinstance(labels, dict):
                continue
            if not (
                labels.get(_MANAGED) == "true"
                and labels.get(_INSTALLATION) == self.installation_id
                and labels.get(_COMPONENT) in {"node", "support"}
            ):
                continue
            topology_id = labels.get(_TOPOLOGY)
            resource_id = item.get("Id")
            resource_name = item.get("Name")
            if (
                not isinstance(topology_id, str)
                or not topology_id
                or not isinstance(resource_id, str)
                or not resource_id
                or not isinstance(resource_name, str)
                or not resource_name.lstrip("/")
            ):
                raise DockerRuntimeError(
                    "owned container inventory has invalid ownership identity"
                )
        managed = [
            {
                **self._container_row(item),
                "ownership": "managed",
                "mutation_allowed": True,
            }
            for item in containers
            if self._owned(item)
        ]
        unmanaged_all = [
            item for item in containers if not self._owned_runtime_resource(item)
        ]
        owned_resources = []
        for item in containers:
            if not self._owned_runtime_resource(item):
                continue
            labels = ((item.get("Config") or {}).get("Labels") or {})
            owned_resources.append(
                {
                    **self._container_row(item),
                    "component": str(labels.get(_COMPONENT) or ""),
                    "ownership": "managed",
                    "mutation_allowed": True,
                }
            )
        owned_resources.extend(self._owned_auxiliary_resources("network", "network"))
        owned_resources.extend(self._owned_auxiliary_resources("volume", "volume"))
        unmanaged = [
            {
                **self._container_row(item),
                "ownership": "unmanaged",
                "mutation_allowed": False,
            }
            for item in unmanaged_all
            if self._visible_unmanaged_node(item)
        ]
        return {
            "installation_id": self.installation_id,
            "managed": sorted(managed, key=lambda item: (item["name"], item["id"])),
            "managed_count": len(managed),
            "owned_resources": sorted(
                owned_resources, key=lambda item: (item["name"], item["id"])
            ),
            "owned_resource_count": len(owned_resources),
            "unmanaged": sorted(unmanaged, key=lambda item: (item["name"], item["id"])),
            "unmanaged_count": len(unmanaged_all),
            "visible_unmanaged_count": len(unmanaged),
            "unmanaged_total_count": len(unmanaged_all),
        }

    def snapshot(self, topology_id: str) -> dict[str, Any]:
        resources = [
            self._container_row(item)
            for item in self._all_containers()
            if self._owned(item, topology_id=topology_id)
        ]
        compose_project = f"dwarf-profile-{self.installation_id}-{topology_id}"
        adapter = "runtime_compose_substrate.py"
        config_path = self._runtime_parent(topology_id) / "current" / "versioned-deployment.json"
        if config_path.is_file():
            try:
                retained = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DockerRuntimeError("retained deployment specification is invalid") from exc
            if isinstance(retained, dict):
                compose_project = str(retained.get("compose_project") or compose_project)
                adapter = str(retained.get("adapter") or adapter)
        networks = []
        for network_name in self._adapter_network_names(adapter, compose_project):
            network = self._inspect_optional("network", network_name)
            if network is None:
                continue
            labels = network.get("Labels") or {}
            networks.append(
                {
                    "id": str(network.get("Id") or ""),
                    "name": str(network.get("Name") or ""),
                    "installation_id": str(labels.get(_INSTALLATION) or ""),
                    "topology_id": str(labels.get(_TOPOLOGY) or ""),
                    "ownership": (
                        "managed"
                        if self._network_owned(network, topology_id)
                        else "unmanaged"
                    ),
                }
            )
        return {
            "topology_id": topology_id,
            "resources": sorted(resources, key=lambda item: (item["name"], item["id"])),
            "networks": networks,
        }

    def topology_health(self, topology_id: str) -> dict[str, Any]:
        """Return current controller-owned node and chain evidence for one topology."""

        current = self._runtime_parent(topology_id) / "current"
        containers = self._all_containers()
        owned = [
            item
            for item in containers
            if self._owned(item, topology_id=topology_id)
        ]
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not current.is_dir() and not owned:
            return {
                "schema_version": "v1",
                "topology_id": topology_id,
                "checked_at": checked_at,
                "state": "absent",
                "reason_code": "no_managed_resources",
                "persisted_deployment": None,
                "nodes": [],
            }
        try:
            config_path = current / "versioned-deployment.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            substrate = config.get("substrate") if isinstance(config, dict) else None
            compose_project = str(config.get("compose_project") or "")
            if not isinstance(substrate, dict) or not compose_project:
                raise DockerRuntimeError("retained deployment specification is invalid")
            self._verify_retained_runtime(
                runtime_root=current,
                topology_id=topology_id,
                compose_project=compose_project,
                enforce_golden=True,
            )
            if config.get("adapter") == "runtime_amaru_control_substrate.py":
                owned = self._project_runtime_containers(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    containers=containers,
                )
                return self._amaru_control_health(
                    runtime_root=current,
                    config=config,
                    topology_id=topology_id,
                    owned=owned,
                    checked_at=checked_at,
                )
            report = json.loads(
                (current / "evidence" / "compose-report.json").read_text(
                    encoding="utf-8"
                )
            )
            network_magic = int(report["network_magic"])
        except (DockerRuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
            return {
                "schema_version": "v1",
                "topology_id": topology_id,
                "checked_at": checked_at,
                "state": "unknown",
                "reason_code": "retained_runtime_unavailable",
                "detail": str(exc),
                "persisted_deployment": None,
                "nodes": [],
            }

        by_node_id = {
            str(((item.get("Config") or {}).get("Labels") or {}).get("io.dwarf.node_id") or ""): item
            for item in owned
        }
        node_results: list[dict[str, Any]] = []
        for node in substrate.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "")
            implementation = str(node.get("impl") or node.get("implementation") or "")
            item = by_node_id.get(node_id)
            state = str(((item or {}).get("State") or {}).get("Status") or "missing")
            name = str((item or {}).get("Name") or "").lstrip("/")
            bindings = ((item or {}).get("NetworkSettings") or {}).get("Ports") or {}
            published = [
                binding
                for values in bindings.values()
                for binding in (values or [])
                if isinstance(binding, dict)
            ]
            loopback = bool(published) and all(
                str(binding.get("HostIp") or "") in {"127.0.0.1", "::1"}
                for binding in published
            )
            result: dict[str, Any] = {
                "id": node_id,
                "implementation": implementation,
                "container_name": name,
                "container_state": state,
                "listener_ready": loopback,
                "loopback_only": loopback,
            }
            if implementation == "cardano-node" and state == "running" and name:
                queried = self._run(
                    (
                        "exec",
                        name,
                        "cardano-cli",
                        "query",
                        "tip",
                        "--testnet-magic",
                        str(network_magic),
                        "--socket-path",
                        f"/env/socket/{node_id}/sock",
                    ),
                    timeout=20,
                )
                try:
                    tip = json.loads(queried.stdout) if queried.returncode == 0 else {}
                except (TypeError, json.JSONDecodeError):
                    tip = {}
                if not isinstance(tip, dict):
                    tip = {}
                try:
                    slot_value = tip.get("slot")
                    block_value = tip.get("block")
                    sync_value = tip.get("syncProgress")
                    sync_progress = float(str(sync_value))
                    chain_ready = (
                        slot_value is not None
                        and block_value is not None
                        and int(str(slot_value)) > 0
                        and int(str(block_value)) >= 1
                        and bool(str(tip.get("hash") or ""))
                    )
                    synchronized = sync_progress >= 99.0
                except (TypeError, ValueError):
                    chain_ready = False
                    synchronized = False
                result.update(
                    {
                        "chain_ready": chain_ready,
                        "synchronized": synchronized,
                        "tip": tip,
                        "query_error": "" if queried.returncode == 0 else queried.stderr.strip(),
                    }
                )
            elif implementation == "amaru":
                # Current Amaru chain progress is not yet available through
                # this controller sample.  Never equate a running process with
                # semantic chain health.
                result["chain_ready"] = False
                result["semantic_evidence"] = "unsupported"
            else:
                result["chain_ready"] = False
            result["healthy"] = bool(
                state == "running"
                and result["listener_ready"]
                and result["chain_ready"]
                and result.get("synchronized") is True
            )
            node_results.append(result)

        tip_hashes = {
            str((node.get("tip") or {}).get("hash") or "")
            for node in node_results
            if isinstance(node.get("tip"), dict)
        }
        converged = bool(node_results) and len(tip_hashes) == 1 and "" not in tip_hashes
        healthy = converged and all(node["healthy"] for node in node_results)
        if healthy:
            reason_code = "node_and_chain_healthy"
        elif (
            node_results
            and not converged
            and all(node.get("chain_ready") for node in node_results)
        ):
            reason_code = "node_chain_not_converged"
        elif any(
            node.get("chain_ready") and node.get("synchronized") is not True
            for node in node_results
        ):
            reason_code = "node_chain_not_synchronized"
        else:
            reason_code = "node_or_chain_unhealthy"
        return {
            "schema_version": "v1",
            "topology_id": topology_id,
            "checked_at": checked_at,
            "state": "healthy" if healthy else "unhealthy",
            "reason_code": reason_code,
            "network_magic": network_magic,
            "expected_nodes": len(node_results),
            "converged": converged,
            "persisted_deployment": config,
            "nodes": node_results,
        }

    def _amaru_control_health(
        self,
        *,
        runtime_root: Path,
        config: dict[str, Any],
        topology_id: str,
        owned: list[dict[str, Any]],
        checked_at: str,
    ) -> dict[str, Any]:
        network_magic = (config.get("substrate") or {}).get("network_magic")
        if isinstance(network_magic, bool) or network_magic != 42:
            raise DockerRuntimeError(
                "retained Amaru lifecycle requires exact network magic 42"
            )
        report, fresh_report_path = self._fresh_amaru_control_report(
            runtime_root, config
        )
        compose_file = self._compose_file(runtime_root)
        if compose_file is None:
            raise DockerRuntimeError("retained Amaru compose model is unavailable")
        try:
            compose = json.loads(compose_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("retained Amaru compose model is invalid") from exc
        services = compose.get("services") or {}
        project = str(config["compose_project"])
        by_name = {
            str(item.get("Name") or "").lstrip("/"): item for item in owned
        }
        expected_names = {
            f"{project}-{service_name}-1" for service_name in services
        }
        expected_roles = {
            str(service_name): str(
                ((service.get("labels") or {}).get(_COMPONENT) or "")
            )
            for service_name, service in services.items()
            if isinstance(service, dict)
        }
        exact_inventory = bool(
            set(by_name) == expected_names
            and len(expected_roles) == len(services)
            and all(
                self._project_service_owned(
                    item,
                    topology_id=topology_id,
                    compose_project=project,
                    expected_roles=expected_roles,
                )
                for item in by_name.values()
            )
        )
        required_running = {
            f"{project}-{service_name}-1"
            for service_name, service in services.items()
            if isinstance(service, dict)
            and str(service.get("restart") or "") in {"always", "unless-stopped"}
        }
        required_running.update(
            f"{project}-{service_name}-1"
            for service_name in (config.get("controller_node_services") or {}).values()
        )
        running = exact_inventory and all(
            str(((by_name.get(name) or {}).get("State") or {}).get("Status") or "")
            == "running"
            for name in required_running
        )
        no_restarts = exact_inventory and all(
            int(item.get("RestartCount") or 0) == 0 for item in by_name.values()
        )
        semantic = bool(
            report.get("passed") is True
            and report.get("retained") is True
            and (report.get("identity") or {}).get("matched") is True
            and (report.get("observation") or {}).get("state") == "healthy"
            and all(
                (report.get("gates") or {}).get(name) is True
                for name in (report.get("required_gates") or [])
            )
        )
        node_by_id = {
            str(node.get("id") or ""): node
            for node in ((config.get("substrate") or {}).get("nodes") or [])
            if isinstance(node, dict)
        }
        report_identities = (report.get("identity") or {}).get("services") or {}
        nodes = []
        exact_runtime_identities = True
        for node_id, service_name in (config.get("controller_node_services") or {}).items():
            name = f"{project}-{service_name}-1"
            item = by_name.get(name) or {}
            state = str((item.get("State") or {}).get("Status") or "missing")
            node = node_by_id.get(node_id) or {}
            service = services.get(service_name) or {}
            identity = report_identities.get(service_name) or {}
            target_image = str(node.get("image") or "")
            runtime_image = str(service.get("image") or "")
            running_image = str((item.get("Config") or {}).get("Image") or "")
            running_image_id = str(item.get("Image") or "")
            target_image_id = str(identity.get("expected_image_id") or "")
            artifact_image_id = str(identity.get("artifact_image_id") or "")
            runtime_image_id = str(
                identity.get("wrapper_expected_image_id")
                or identity.get("expected_image_id")
                or ""
            )
            try:
                live_runtime_matches = bool(
                    running_image
                    and _immutable_image_identity(running_image)
                    == _immutable_image_identity(runtime_image)
                    and _IMAGE_CONFIG_ID.fullmatch(running_image_id)
                    and running_image_id == runtime_image_id
                )
                wrapper_image = identity.get("wrapper_expected_image")
                if wrapper_image:
                    report_runtime_matches = bool(
                        _immutable_image_identity(str(wrapper_image))
                        == _immutable_image_identity(runtime_image)
                        and identity.get("running_image_id") == runtime_image_id
                    )
                else:
                    report_runtime_matches = bool(
                        _immutable_image_identity(runtime_image)
                        == _immutable_image_identity(target_image)
                        and identity.get("running_image_id") == target_image_id
                    )
            except DockerRuntimeError:
                target_image_id = ""
                runtime_image_id = ""
                live_runtime_matches = False
                report_runtime_matches = False
            provenance_matches = bool(
                identity.get("matched") is True
                and identity.get("wrapper_matched") is True
                and identity.get("expected_image") == target_image
                and _IMAGE_CONFIG_ID.fullmatch(target_image_id)
                and artifact_image_id == target_image_id
                and _IMAGE_CONFIG_ID.fullmatch(runtime_image_id)
                and report_runtime_matches
                and live_runtime_matches
            )
            exact_runtime_identities = exact_runtime_identities and provenance_matches
            node_healthy = (
                semantic
                and running
                and state == "running"
                and provenance_matches
            )
            nodes.append(
                {
                    "id": node_id,
                    "implementation": str(node.get("impl") or ""),
                    "version": str(node.get("version") or ""),
                    "image": target_image,
                    "service": service_name,
                    "container_name": name,
                    "container_state": state,
                    "runtime_image": runtime_image,
                    "runtime_image_id": runtime_image_id,
                    "runtime_container_image_id": running_image_id,
                    "target_image_id": target_image_id,
                    "artifact_image_id": artifact_image_id,
                    "wrapper_matched": identity.get("wrapper_matched") is True,
                    "listener_ready": node_healthy,
                    "chain_ready": node_healthy,
                    "synchronized": node_healthy,
                    "healthy": node_healthy,
                    "semantic_evidence": "fresh-relay-bootstrap-gates",
                }
            )
        healthy = (
            semantic
            and running
            and no_restarts
            and exact_runtime_identities
            and bool(nodes)
        )
        config_path = runtime_root / "versioned-deployment.json"
        raw_observation = (report.get("observation") or {}).get("observation") or {}
        required_gates = report.get("required_gates") or []
        return {
            "schema_version": "v1",
            "topology_id": topology_id,
            "checked_at": checked_at,
            "state": "healthy" if healthy else "unhealthy",
            "reason_code": (
                "node_and_chain_healthy"
                if healthy
                else (
                    "amaru_control_runtime_identity_mismatch"
                    if not exact_runtime_identities
                    else "amaru_control_runtime_or_semantic_gate_unhealthy"
                )
            ),
            "network_magic": network_magic,
            "expected_nodes": len(nodes),
            "converged": semantic,
            "semantic_health": {
                "schema_version": report.get("schema_version"),
                "profile_id": report.get("profile_id"),
                "completed_at": report.get("observed_at"),
                "fresh_observation": report.get("fresh_observation"),
                "retained": report.get("retained"),
                "passed": report.get("passed"),
                "classification": report.get("classification"),
                "required_gates": required_gates,
                "failed_gates": report.get("failed_gates"),
                "gates": {
                    name: (report.get("gates") or {}).get(name)
                    for name in required_gates
                },
                "identity_matched": (report.get("identity") or {}).get("matched"),
                "sample_count": len(raw_observation.get("samples") or []),
            },
            "persisted_deployment": config,
            "retained_evidence": {
                "profile_id": str(config.get("profile_id") or ""),
                "deployment_config_sha256": self._file_sha256(config_path),
                "compose_sha256": self._file_sha256(compose_file),
                "semantic_report_sha256": self._file_sha256(fresh_report_path),
            },
            "semantic_report": str(
                fresh_report_path
            ),
            "nodes": nodes,
        }

    def _fresh_amaru_control_report(
        self, runtime_root: Path, config: dict[str, Any]
    ) -> tuple[dict[str, Any], Path]:
        adapter = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "runtime_amaru_control_substrate.py"
        )
        config_path = runtime_root / "versioned-deployment.json"
        output_path = (
            runtime_root
            / "evidence"
            / f"controller-health-{time.time_ns()}-{threading.get_ident()}.json"
        )
        result = self._run_adapter(
            (
                sys.executable,
                str(adapter),
                "--config",
                str(config_path),
                "--verify-retained",
                "--health-output",
                str(output_path),
            ),
            timeout=120,
            execution_id=None,
        )
        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("fresh Amaru semantic observation failed") from exc
        node_by_id = {
            str(node.get("id") or ""): node
            for node in ((config.get("substrate") or {}).get("nodes") or [])
            if isinstance(node, dict)
        }
        expected_selected = [
            {
                "id": node_id,
                "service": service_name,
                "implementation": str(node_by_id.get(node_id, {}).get("impl") or ""),
                "version": str(node_by_id.get(node_id, {}).get("version") or ""),
                "image": str(node_by_id.get(node_id, {}).get("image") or ""),
                "supporting": node_by_id.get(node_id, {}).get("supporting") is True,
            }
            for node_id, service_name in (
                config.get("controller_node_services") or {}
            ).items()
        ]
        declared_shape = (config.get("substrate") or {}).get("actual_topology")
        if (
            not isinstance(report, dict)
            or report.get("fresh_observation") is not True
            or report.get("profile_id") != config.get("profile_id")
            or report.get("project") != config.get("compose_project")
            or report.get("selected_nodes") != expected_selected
            or report.get("actual_topology") != declared_shape
            or result.returncode not in {0, 2}
        ):
            raise DockerRuntimeError("fresh Amaru semantic observation is invalid")
        return report, output_path

    def _resource_name(self, topology_id: str, node_id: str | None = None) -> str:
        base = f"dwarf-{self.installation_id}-{topology_id}"
        return f"{base}-{node_id}" if node_id else base

    def _inspect_optional(self, kind: str, name: str) -> dict[str, Any] | None:
        result = self._run((kind, "inspect", name), timeout=15)
        if result.returncode != 0:
            detail = str(result.stderr or "").strip().lower()
            missing = detail == "not found"
            if kind == "container":
                missing = missing or "no such object" in detail or "no such container" in detail
            elif kind == "network":
                missing = missing or "no such network" in detail or (
                    detail.startswith("error response from daemon: network ")
                    and detail.endswith(" not found")
                )
            elif kind == "volume":
                missing = missing or "no such volume" in detail
            if missing:
                return None
            raise DockerRuntimeError(f"Docker {kind} inspect failed")
        parsed = self._json(result, f"{kind} inspect")
        if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
            raise DockerRuntimeError(f"Docker {kind} inspect was ambiguous")
        return parsed[0]

    def _assert_owned_container(self, name: str, topology_id: str) -> dict[str, Any] | None:
        item = self._inspect_optional("container", name)
        if item is not None and not self._owned(item, topology_id=topology_id):
            raise DockerRuntimeError("refusing to mutate an unmanaged container collision")
        return item

    def _network_owned(self, item: dict[str, Any], topology_id: str) -> bool:
        labels = item.get("Labels") or {}
        return (
            isinstance(labels, dict)
            and labels.get(_MANAGED) == "true"
            and labels.get(_INSTALLATION) == self.installation_id
            and labels.get(_TOPOLOGY) == topology_id
            and labels.get(_COMPONENT) == "network"
        )

    def _volume_owned(
        self,
        item: dict[str, Any],
        *,
        topology_id: str,
        compose_project: str,
        volume_key: str,
    ) -> bool:
        labels = item.get("Labels") or {}
        return (
            isinstance(labels, dict)
            and labels.get(_MANAGED) == "true"
            and labels.get(_INSTALLATION) == self.installation_id
            and labels.get(_TOPOLOGY) == topology_id
            and labels.get(_COMPONENT) == "volume"
            and labels.get("com.docker.compose.project") == compose_project
            and labels.get("com.docker.compose.volume") == volume_key
            and item.get("Name") == f"{compose_project}_{volume_key}"
        )

    def _ensure_network(self, topology_id: str, execution_id: str) -> tuple[str, bool]:
        name = self._resource_name(topology_id)
        existing = self._inspect_optional("network", name)
        if existing is not None:
            if not self._network_owned(existing, topology_id):
                raise DockerRuntimeError("refusing to use an unmanaged network collision")
            return name, False
        result = self._run(
            (
                "network",
                "create",
                "--label",
                f"{_MANAGED}=true",
                "--label",
                f"{_INSTALLATION}={self.installation_id}",
                "--label",
                f"{_TOPOLOGY}={topology_id}",
                "--label",
                f"{_COMPONENT}=network",
                name,
            ),
            timeout=30,
            execution_id=execution_id,
        )
        if result.returncode != 0:
            raise DockerRuntimeError("Docker network creation failed")
        return name, True

    def _ensure_image(
        self,
        image: str,
        execution_id: str,
        *,
        qualified_target: dict[str, Any] | None = None,
    ) -> None:
        target_record = self._assert_approved_image(
            image, qualified_target=qualified_target
        )
        if target_record is not None:
            return
        present = self._run(("image", "inspect", image), timeout=20)
        if present.returncode == 0:
            return
        pulled = self._run(("pull", image), timeout=1800, execution_id=execution_id)
        if pulled.returncode != 0:
            raise DockerRuntimeError("Docker digest-pinned image pull failed")
        if self._run(("image", "inspect", image), timeout=20).returncode != 0:
            raise DockerRuntimeError("pulled image is not available under the requested digest")

    def _run_adapter(
        self,
        arguments: Sequence[str],
        timeout: int,
        execution_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> DockerResult:
        argv = tuple(str(item) for item in arguments)
        if cancel_event is not None and cancel_event.is_set():
            return DockerResult(130, "", "scenario execution cancelled before start")
        if self._external_adapter_runner is not None:
            return self._external_adapter_runner(argv, timeout, execution_id)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise DockerRuntimeError("deployment adapter is unavailable") from exc
        with self._process_lock:
            self._processes[execution_id] = process
        if cancel_event is not None and cancel_event.is_set():
            self._stop_process_group(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            self._stop_process_group(process.pid)
            raise DockerRuntimeError("deployment adapter timed out") from exc
        finally:
            # The adapter invokes Docker Compose and other subprocesses.  Do
            # not let an orphaned grandchild outlive the runtime worker and
            # therefore the controller's mutation locks.
            self._stop_process_group(process.pid)
            with self._process_lock:
                self._processes.pop(execution_id, None)
        return DockerResult(process.returncode, stdout, stderr)

    @staticmethod
    def _process_group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _stop_process_group(cls, process_group: int) -> None:
        if not cls._process_group_exists(process_group):
            return
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 1.0
        while cls._process_group_exists(process_group) and time.monotonic() < deadline:
            time.sleep(0.02)
        if cls._process_group_exists(process_group):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
        deadline = time.monotonic() + 5.0
        while cls._process_group_exists(process_group) and time.monotonic() < deadline:
            time.sleep(0.02)
        if cls._process_group_exists(process_group):
            raise DockerRuntimeError("runtime process group did not terminate")

    def _runtime_parent(self, topology_id: str) -> Path:
        return self.state_dir / "controller" / "topology-runtimes" / topology_id

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)

    @staticmethod
    def _compose_file(runtime_root: Path) -> Path | None:
        for name in ("docker-compose.yml", "docker-compose.json"):
            candidate = runtime_root / name
            if candidate.is_file():
                return candidate
        return None

    def _retained_project_roles(
        self, *, topology_id: str, compose_project: str
    ) -> dict[str, str]:
        runtime_root = self._runtime_parent(topology_id) / "current"
        compose_file = self._compose_file(runtime_root)
        if compose_file is None:
            raise DockerRuntimeError("retained Compose ownership model is unavailable")
        try:
            compose = json.loads(compose_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("retained Compose ownership model is invalid") from exc
        services = compose.get("services") if isinstance(compose, dict) else None
        roles = {
            str(service): str(((spec.get("labels") or {}).get(_COMPONENT) or ""))
            for service, spec in (services or {}).items()
            if isinstance(spec, dict)
        }
        if (
            not isinstance(services, dict)
            or not services
            or len(roles) != len(services)
            or any(role not in {"node", "support"} for role in roles.values())
        ):
            raise DockerRuntimeError("retained Compose service roles are invalid")
        return roles

    def _compose(
        self,
        runtime_root: Path,
        compose_project: str,
        arguments: Sequence[str],
        execution_id: str,
        *,
        timeout: int = 600,
    ) -> DockerResult:
        compose_file = self._compose_file(runtime_root)
        if compose_file is None:
            return DockerResult(1, "", "compose file is missing")
        return self._run(
            (
                "compose",
                "--project-name",
                compose_project,
                "--file",
                str(compose_file),
                *arguments,
            ),
            timeout=timeout,
            execution_id=execution_id,
        )

    @staticmethod
    def _semantic_report_name(config: dict[str, Any]) -> str:
        if config.get("adapter") == "runtime_amaru_control_substrate.py":
            return "deployment-report.json"
        return "compose-report.json"

    def _semantic_report(
        self, runtime_root: Path, config: dict[str, Any]
    ) -> dict[str, Any]:
        path = runtime_root / "evidence" / self._semantic_report_name(config)
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError(
                "deployment adapter did not emit a semantic report"
            ) from exc
        if not isinstance(report, dict):
            raise DockerRuntimeError("deployment adapter semantic report is invalid")
        return report

    @staticmethod
    def _structured_adapter_failure(result: DockerResult) -> DockerRuntimeError | None:
        """Map only reviewed adapter codes to fixed controller-domain failures."""

        if result.returncode == 0:
            return None
        safe_failures = {
            (
                "compose_project_not_fresh",
                "preflight",
            ): "Amaru deployment preflight found retained project resources",
            (
                "runtime_assets_exist",
                "preflight",
            ): "Amaru deployment preflight found retained runtime assets",
            (
                "required_runtime_asset_missing",
                "asset_staging",
            ): "Amaru deployment runtime assets are unavailable",
            (
                "runtime_asset_permission_denied",
                "asset_staging",
            ): "Amaru deployment runtime assets are not writable",
        }
        for line in reversed(result.stdout.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            failure = payload.get("failure") if isinstance(payload, dict) else None
            if not isinstance(failure, dict):
                continue
            code = str(failure.get("code") or "")
            stage = str(failure.get("stage") or "")
            message = safe_failures.get((code, stage))
            if message is not None:
                return DockerRuntimeError(
                    message,
                    code=code,
                    stage="adapter_" + stage,
                )
        return None

    def _write_runtime_integrity(
        self,
        *,
        runtime_root: Path,
        topology_id: str,
        compose_project: str,
    ) -> None:
        compose_file = self._compose_file(runtime_root)
        config_path = runtime_root / "versioned-deployment.json"
        if compose_file is None or not config_path.is_file():
            raise DockerRuntimeError("deployment adapter did not retain its complete specification")
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("deployment specification is unavailable") from exc
        report_name = self._semantic_report_name(config)
        report_path = runtime_root / "evidence" / report_name
        if not report_path.is_file():
            raise DockerRuntimeError("deployment adapter did not retain its semantic report")
        integrity = {
            "schema_version": "v1",
            "installation_id": self.installation_id,
            "topology_id": topology_id,
            "compose_project": compose_project,
            "compose_file": compose_file.name,
            "compose_sha256": self._file_sha256(compose_file),
            "deployment_config_sha256": self._file_sha256(config_path),
            "semantic_report": report_name,
            "semantic_report_sha256": self._file_sha256(report_path),
        }
        runtime_metadata = runtime_root / "runtime.json"
        if runtime_metadata.is_file():
            integrity["runtime_metadata_sha256"] = self._file_sha256(runtime_metadata)
        self._write_json(
            runtime_root / "controller-integrity.json",
            integrity,
        )

    def _verify_retained_runtime(
        self,
        *,
        runtime_root: Path,
        topology_id: str,
        compose_project: str,
        enforce_golden: bool,
    ) -> dict[str, Any]:
        """Verify the controller-private retained spec before any Compose use."""

        integrity_path = runtime_root / "controller-integrity.json"
        config_path = runtime_root / "versioned-deployment.json"
        try:
            integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("retained runtime integrity record is unavailable") from exc
        expected_integrity = {
            "schema_version": "v1",
            "installation_id": self.installation_id,
            "topology_id": topology_id,
            "compose_project": compose_project,
        }
        if not isinstance(integrity, dict) or any(
            integrity.get(key) != value for key, value in expected_integrity.items()
        ):
            raise DockerRuntimeError("retained runtime integrity identity is invalid")
        compose_name = integrity.get("compose_file")
        if compose_name not in {"docker-compose.yml", "docker-compose.json"}:
            raise DockerRuntimeError("retained runtime compose identity is invalid")
        compose_file = runtime_root / str(compose_name)
        try:
            if integrity.get("compose_sha256") != self._file_sha256(compose_file):
                raise DockerRuntimeError("retained runtime compose integrity check failed")
            if integrity.get("deployment_config_sha256") != self._file_sha256(config_path):
                raise DockerRuntimeError("retained deployment specification integrity check failed")
            report_name = self._semantic_report_name(config)
            report_path = runtime_root / "evidence" / report_name
            if (
                integrity.get("semantic_report") != report_name
                or integrity.get("semantic_report_sha256")
                != self._file_sha256(report_path)
            ):
                raise DockerRuntimeError("retained semantic report integrity check failed")
            runtime_metadata = runtime_root / "runtime.json"
            if integrity.get("runtime_metadata_sha256") is not None and (
                not runtime_metadata.is_file()
                or integrity.get("runtime_metadata_sha256")
                != self._file_sha256(runtime_metadata)
            ):
                raise DockerRuntimeError("retained runtime metadata integrity check failed")
            compose = json.loads(compose_file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DockerRuntimeError("retained runtime files are unavailable") from exc
        except ValueError as exc:
            raise DockerRuntimeError("retained runtime compose is invalid") from exc
        substrate = config.get("substrate") if isinstance(config, dict) else None
        ownership = substrate.get("controller_ownership") if isinstance(substrate, dict) else None
        if (
            not isinstance(substrate, dict)
            or config.get("compose_project") != compose_project
            or ownership
            != {"installation_id": self.installation_id, "topology_id": topology_id}
        ):
            raise DockerRuntimeError("retained deployment ownership is invalid")
        host_root = str(substrate.get("docker_host_runtime_root") or "")
        expected_host_root = str(
            self.host_state_dir
            / "controller"
            / "topology-runtimes"
            / topology_id
            / "current"
        )
        if (
            host_root != expected_host_root
            or not host_root.startswith("/")
            or ".." in Path(host_root).parts
        ):
            raise DockerRuntimeError("retained runtime host path is invalid")
        if config.get("adapter") == "runtime_amaru_control_substrate.py":
            try:
                runtime_metadata = json.loads(
                    (runtime_root / "runtime.json").read_text(encoding="utf-8")
                )
                runtime_metadata = reconcile_amaru_runtime_metadata(
                    runtime_metadata, config
                )
            except (OSError, ValueError) as exc:
                raise DockerRuntimeError(
                    "retained Amaru runtime metadata is unavailable"
                ) from exc
            self._verify_amaru_control_retained(
                config=config,
                compose=compose,
                report=self._semantic_report(runtime_root, config),
                runtime_metadata=runtime_metadata,
                topology_id=topology_id,
                compose_project=compose_project,
                enforce_golden=enforce_golden,
            )
            return config
        nodes = substrate.get("nodes")
        services = compose.get("services") if isinstance(compose, dict) else None
        if not isinstance(nodes, list) or not isinstance(services, dict):
            raise DockerRuntimeError("retained runtime service specification is invalid")
        expected_ids = {str(node.get("id") or "") for node in nodes if isinstance(node, dict)}
        if not expected_ids or set(services) != expected_ids:
            raise DockerRuntimeError("retained runtime service identities are invalid")
        for node in nodes:
            if not isinstance(node, dict):
                raise DockerRuntimeError("retained runtime node is invalid")
            node_id = str(node.get("id") or "")
            service = services.get(node_id)
            image = str(node.get("image") or "")
            if not isinstance(service, dict) or _immutable_image_identity(
                str(service.get("image") or "")
            ) != _immutable_image_identity(image):
                raise DockerRuntimeError("retained runtime image identity is invalid")
            if enforce_golden:
                self._assert_node_images(node)
            labels = service.get("labels")
            required_labels = {
                _MANAGED: "true",
                _INSTALLATION: self.installation_id,
                _TOPOLOGY: topology_id,
                _COMPONENT: "node",
                "io.dwarf.node_id": node_id,
            }
            if not isinstance(labels, dict) or any(
                labels.get(key) != value for key, value in required_labels.items()
            ):
                raise DockerRuntimeError("retained runtime ownership labels are invalid")
            expected_volumes = {f"{host_root}/env:/env", f"{host_root}/logs:/logs"}
            if str(node.get("impl") or "") == "amaru":
                expected_volumes.add(f"{host_root}/amaru:/amaru")
            if set(service.get("volumes") or []) != expected_volumes:
                raise DockerRuntimeError("retained runtime mounts are invalid")
            if (
                service.get("container_name") != f"{compose_project}-{node_id}-1"
                or service.get("read_only") is not True
                or service.get("cap_drop") != ["ALL"]
                or service.get("security_opt") != ["no-new-privileges:true"]
                or service.get("privileged") is not None
                or service.get("network_mode") is not None
                or any(str(port).split(":")[-2:-1] == ["8787"] for port in service.get("ports") or [])
            ):
                raise DockerRuntimeError("retained runtime container policy is invalid")
        network = ((compose.get("networks") or {}).get("default") or {})
        network_labels = network.get("labels") if isinstance(network, dict) else None
        if not isinstance(network_labels, dict) or any(
            network_labels.get(key) != value
            for key, value in {
                _MANAGED: "true",
                _INSTALLATION: self.installation_id,
                _TOPOLOGY: topology_id,
                _COMPONENT: "network",
            }.items()
        ):
            raise DockerRuntimeError("retained runtime network policy is invalid")
        return config

    def _verify_amaru_control_retained(
        self,
        *,
        config: dict[str, Any],
        compose: dict[str, Any],
        report: dict[str, Any],
        runtime_metadata: dict[str, Any],
        topology_id: str,
        compose_project: str,
        enforce_golden: bool,
    ) -> None:
        substrate = config.get("substrate") or {}
        nodes = substrate.get("nodes")
        services = compose.get("services") if isinstance(compose, dict) else None
        networks = compose.get("networks") if isinstance(compose, dict) else None
        mapping = config.get("controller_node_services")
        if (
            substrate.get("deployment_adapter") != "amaru-control"
            or config.get("lifecycle") != "cardano_amaru_relay_bootstrap_control"
            or not isinstance(nodes, list)
            or not isinstance(services, dict)
            or not services
            or not isinstance(networks, dict)
            or not networks
            or not isinstance(mapping, dict)
            or not isinstance(runtime_metadata, dict)
        ):
            raise DockerRuntimeError("retained Amaru control specification is invalid")
        node_by_id = {
            str(node.get("id") or ""): node
            for node in nodes
            if isinstance(node, dict)
        }
        if not node_by_id or set(mapping) != set(node_by_id) or len(set(mapping.values())) != len(mapping):
            raise DockerRuntimeError("retained Amaru logical node mapping is invalid")
        declared_shape = substrate.get("actual_topology")
        if (
            not isinstance(declared_shape, dict)
            or set(declared_shape.get("all_services") or ()) != set(services)
            or set(declared_shape.get("network_names") or ()) != set(networks)
        ):
            raise DockerRuntimeError("retained Amaru physical shape is invalid")
        expected_host_root = str(
            self.host_state_dir
            / "controller"
            / "topology-runtimes"
            / topology_id
            / "current"
        )
        if substrate.get("docker_host_runtime_root") != expected_host_root:
            raise DockerRuntimeError("retained runtime host path is invalid")
        selected_identities = set()
        for node in node_by_id.values():
            image = str(node.get("image") or "")
            selected_identities.add(_immutable_image_identity(image))
            if enforce_golden:
                self._assert_node_images(node)
        service_identities = set()
        host_root = str(substrate.get("docker_host_runtime_root") or "")
        for service_name, service in services.items():
            if not isinstance(service, dict):
                raise DockerRuntimeError("retained Amaru service specification is invalid")
            service_identities.add(
                _immutable_image_identity(str(service.get("image") or ""))
            )
            labels = service.get("labels")
            required = {
                _MANAGED: "true",
                _INSTALLATION: self.installation_id,
                _TOPOLOGY: topology_id,
            }
            if not isinstance(labels, dict) or any(
                labels.get(key) != value for key, value in required.items()
            ):
                raise DockerRuntimeError("retained Amaru service ownership is invalid")
            if labels.get(_COMPONENT) not in {"node", "support"}:
                raise DockerRuntimeError("retained Amaru service role is invalid")
            hardened = bool(
                service.get("read_only") is True
                and service.get("cap_drop") == ["ALL"]
                and "no-new-privileges:true" in (service.get("security_opt") or [])
            )
            exception = labels.get("io.dwarf.hardening.exception")
            if not hardened and (
                service_name not in _AMARU_HARDENING_EXCEPTION_SERVICES
                or exception != _AMARU_HARDENING_EXCEPTION
            ):
                raise DockerRuntimeError(
                    "retained Amaru service hardening policy is invalid"
                )
            if (
                service.get("privileged") is not None
                or service.get("network_mode") == "host"
                or not _published_ports_are_loopback_only(service)
            ):
                raise DockerRuntimeError("retained Amaru container policy is invalid")
            for volume in service.get("volumes") or []:
                if isinstance(volume, dict) and volume.get("type") == "bind":
                    source = str(volume.get("source") or "")
                    if not Path(source).is_absolute():
                        raise DockerRuntimeError(
                            "retained Amaru bind source is relative"
                        )
                elif isinstance(volume, str):
                    source = volume.partition(":")[0]
                    if (
                        not Path(source).is_absolute()
                        and (
                            source.startswith(".")
                            or "/" in source
                            or "\\" in source
                        )
                    ):
                        raise DockerRuntimeError(
                            "retained Amaru bind source is relative"
                        )
                else:
                    continue
                if source.startswith("/") and not (
                    source == host_root or source.startswith(host_root.rstrip("/") + "/")
                ):
                    raise DockerRuntimeError("retained Amaru bind source is outside its host root")
        if not selected_identities.issubset(service_identities):
            raise DockerRuntimeError("retained Amaru selected image identity is missing")
        for node_id, service_name in mapping.items():
            service = services.get(str(service_name))
            labels = service.get("labels") if isinstance(service, dict) else None
            node = node_by_id[node_id]
            expected = {
                _COMPONENT: "node",
                "io.dwarf.node_id": node_id,
                "io.dwarf.implementation": str(node.get("impl") or ""),
                "io.dwarf.version": str(node.get("version") or ""),
            }
            if not isinstance(labels, dict) or any(
                labels.get(key) != value for key, value in expected.items()
            ):
                raise DockerRuntimeError("retained Amaru node identity is invalid")
            identity = ((report.get("identity") or {}).get("services") or {}).get(
                str(service_name)
            )
            service_image = str(service.get("image") or "") if isinstance(service, dict) else ""
            target_image = str(node.get("image") or "")
            wrapper_image = identity.get("wrapper_expected_image") if isinstance(identity, dict) else None
            target_image_id = str(identity.get("expected_image_id") or "") if isinstance(identity, dict) else ""
            artifact_image_id = str(identity.get("artifact_image_id") or "") if isinstance(identity, dict) else ""
            runtime_image_id = str(
                identity.get("wrapper_expected_image_id")
                or identity.get("expected_image_id")
                or ""
            ) if isinstance(identity, dict) else ""
            try:
                runtime_matches = bool(
                    isinstance(identity, dict)
                    and identity.get("wrapper_matched") is True
                    and _IMAGE_CONFIG_ID.fullmatch(target_image_id)
                    and artifact_image_id == target_image_id
                    and _IMAGE_CONFIG_ID.fullmatch(runtime_image_id)
                    and (
                        (
                            wrapper_image
                            and _immutable_image_identity(str(wrapper_image))
                            == _immutable_image_identity(service_image)
                            and identity.get("running_image_id") == runtime_image_id
                        )
                        or (
                            not wrapper_image
                            and _immutable_image_identity(service_image)
                            == _immutable_image_identity(target_image)
                            and identity.get("running_image_id") == target_image_id
                        )
                    )
                )
            except DockerRuntimeError:
                runtime_matches = False
            if (
                not isinstance(identity, dict)
                or identity.get("matched") is not True
                or identity.get("expected_image") != node.get("image")
                or not runtime_matches
            ):
                raise DockerRuntimeError(
                    "retained Amaru per-node image identity is invalid"
                )
        for network_name, network in networks.items():
            labels = network.get("labels") if isinstance(network, dict) else None
            required = {
                _MANAGED: "true",
                _INSTALLATION: self.installation_id,
                _TOPOLOGY: topology_id,
                _COMPONENT: "network",
            }
            if (
                not isinstance(labels, dict)
                or any(labels.get(key) != value for key, value in required.items())
                or str(network.get("name") or "")
                != f"{compose_project}-{network_name}"
            ):
                raise DockerRuntimeError("retained Amaru network ownership is invalid")
        volumes = compose.get("volumes")
        if not isinstance(volumes, dict) or not volumes:
            raise DockerRuntimeError("retained Amaru volume specification is invalid")
        for volume in volumes.values():
            labels = volume.get("labels") if isinstance(volume, dict) else None
            if (
                not isinstance(volume, dict)
                or volume.get("external") is True
                or volume.get("name") is not None
                or not isinstance(labels, dict)
                or any(
                    labels.get(key) != value
                    for key, value in {
                        _MANAGED: "true",
                        _INSTALLATION: self.installation_id,
                        _TOPOLOGY: topology_id,
                        _COMPONENT: "volume",
                    }.items()
                )
            ):
                raise DockerRuntimeError("retained Amaru volume ownership is invalid")
        observation = report.get("observation") or {}
        required_gates = report.get("required_gates")
        gates = report.get("gates")
        expected_selected = [
            {
                "id": node_id,
                "service": service_name,
                "implementation": str(node_by_id[node_id].get("impl") or ""),
                "version": str(node_by_id[node_id].get("version") or ""),
                "image": str(node_by_id[node_id].get("image") or ""),
                "supporting": node_by_id[node_id].get("supporting") is True,
            }
            for node_id, service_name in mapping.items()
        ]
        runtime_shape = runtime_metadata.get("actual_topology") or {}
        if (
            report.get("passed") is not True
            or report.get("retained") is not True
            or report.get("profile_id") != config.get("profile_id")
            or report.get("scope") != config.get("scope")
            or report.get("project") != compose_project
            or (report.get("identity") or {}).get("matched") is not True
            or observation.get("state") != "healthy"
            or not isinstance(required_gates, list)
            or not required_gates
            or not isinstance(gates, dict)
            or any(gates.get(name) is not True for name in required_gates)
            or report.get("selected_nodes") != expected_selected
            or report.get("actual_topology") != declared_shape
            or isinstance(substrate.get("network_magic"), bool)
            or substrate.get("network_magic") != 42
            or runtime_metadata.get("profile_id") != config.get("profile_id")
            or isinstance(runtime_metadata.get("network_magic"), bool)
            or runtime_metadata.get("network_magic")
            != substrate.get("network_magic")
            or runtime_metadata.get("scope") != config.get("scope")
            or runtime_metadata.get("compose_project") != compose_project
            or ((runtime_metadata.get("actual_topology") or {}).get("selected_nodes"))
            != expected_selected
            or (
                isinstance(declared_shape, dict)
                and any(runtime_shape.get(key) != value for key, value in declared_shape.items())
            )
            or (runtime_metadata.get("images") or {}).get("cardano-node")
            != config.get("cardano_image")
            or (runtime_metadata.get("images") or {}).get("amaru")
            != config.get("amaru_image")
        ):
            raise DockerRuntimeError("retained Amaru semantic readiness is invalid")

    def _deployment_config(
        self, deployment: dict[str, Any], runtime_root: Path
    ) -> dict[str, Any]:
        adapter = str(deployment.get("adapter") or "")
        if adapter not in {
            "runtime_compose_substrate.py",
            "runtime_amaru_control_substrate.py",
        }:
            raise DockerRuntimeError("unsupported local deployment adapter")
        substrate = deployment.get("substrate")
        compose_project = str(deployment.get("compose_project") or "")
        if not isinstance(substrate, dict) or not compose_project.startswith("dwarf-profile-"):
            raise DockerRuntimeError("retained deployment specification is invalid")
        common = {
            "schema_version": "v1",
            "adapter": adapter,
            "substrate": substrate,
            "output_dir": str(runtime_root / "evidence"),
            "runtime_root": str(runtime_root),
            "compose_project": compose_project,
        }
        if adapter == "runtime_compose_substrate.py":
            return {**common, "healthy_timeout_seconds": 300}
        if substrate.get("deployment_adapter") != "amaru-control" or substrate.get(
            "scope"
        ) not in {"amaru-only", "mixed"}:
            raise DockerRuntimeError("Amaru control adapter does not match the selected profile")
        network_magic = substrate.get("network_magic")
        if isinstance(network_magic, bool) or network_magic != 42:
            raise DockerRuntimeError(
                "Amaru control lifecycle requires exact network magic 42"
            )
        if substrate.get("peer_sharing") is not False:
            raise DockerRuntimeError(
                "Amaru control lifecycle requires peer_sharing=false",
                code="amaru_peer_sharing_contract_invalid",
                stage="adapter_preflight",
            )
        ownership = substrate.get("controller_ownership")
        if (
            not isinstance(ownership, dict)
            or ownership.get("installation_id") != self.installation_id
            or not str(ownership.get("topology_id") or "")
        ):
            raise DockerRuntimeError("Amaru control ownership identity is invalid")
        expected_host_root = str(
            self.host_state_dir
            / "controller"
            / "topology-runtimes"
            / str(ownership["topology_id"])
            / "current"
        )
        if substrate.get("docker_host_runtime_root") != expected_host_root:
            raise DockerRuntimeError("Amaru control runtime host path is invalid")
        from profile_manager.profiles import amaru_control_topology_shape

        try:
            actual_topology = amaru_control_topology_shape(substrate)
        except (KeyError, TypeError, ValueError) as exc:
            raise DockerRuntimeError(str(exc)) from exc
        if substrate.get("actual_topology") != actual_topology:
            raise DockerRuntimeError(
                "Amaru control physical topology differs from the catalog plan"
            )
        nodes = substrate.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise DockerRuntimeError("Amaru control adapter requires exact selected nodes")
        cardano = [node for node in nodes if isinstance(node, dict) and node.get("impl") == "cardano-node"]
        amaru = [node for node in nodes if isinstance(node, dict) and node.get("impl") == "amaru"]
        selected_cardano = [node for node in cardano if node.get("supporting") is not True]
        support_cardano = [node for node in cardano if node.get("supporting") is True]
        if len(selected_cardano) > 2:
            raise DockerRuntimeError(
                "proven Amaru lifecycle cannot honor selected Cardano node count"
            )
        if not amaru or len(amaru) > 2:
            raise DockerRuntimeError(
                "proven Amaru lifecycle cannot honor selected Amaru node count"
            )
        if substrate.get("scope") == "amaru-only" and len(support_cardano) != 1:
            raise DockerRuntimeError(
                "proven Amaru lifecycle requires exactly one catalog-selected Cardano support node"
            )
        if substrate.get("scope") == "mixed" and support_cardano:
            raise DockerRuntimeError("mixed topology cannot contain implicit support nodes")
        target_count = len(selected_cardano) + len(amaru)
        if int(substrate.get("target_node_count") or -1) != target_count or int(
            substrate.get("support_node_count") or 0
        ) != len(support_cardano):
            raise DockerRuntimeError("selected node counts differ from the retained topology")
        for group, label in ((cardano, "Cardano"), (amaru, "Amaru")):
            identities = {
                (str(node.get("version") or ""), str(node.get("image") or ""))
                for node in group
            }
            if len(identities) != 1 or not all(all(item) for item in identities):
                raise DockerRuntimeError(
                    f"proven Amaru lifecycle requires one exact {label} version/image identity"
                )
        cardano_identity = next(iter({(node["version"], node["image"]) for node in cardano}))
        amaru_identity = next(iter({(node["version"], node["image"]) for node in amaru}))
        node_services = dict(actual_topology["selected_node_services"])
        target = amaru[0]
        measurement_identity = {
            key: target[key]
            for key in (
                "version",
                "source_revision",
                "target_mode",
                "image",
                "image_digest",
                "executable_digest",
                "patch_set_sha256",
                "build_result_sha256",
                "runtime_probe_image",
                "runtime_probe_log_sha256",
                "source_image_reference",
                "image_manifest_digest",
                "image_config_digest",
                "qualification_image_id",
                "transfer_source_installation_id",
                "transfer_manifest_sha256",
            )
            if key in target
        }
        config = {
            **common,
            "profile_id": str(substrate.get("profile_id") or ""),
            "scope": str(substrate["scope"]),
            "lifecycle": "cardano_amaru_relay_bootstrap_control",
            "supporting_cardano_version": str(cardano_identity[0]),
            "cardano_image": str(cardano_identity[1]),
            "amaru_version": str(amaru_identity[0]),
            "amaru_image": str(amaru_identity[1]),
            "measurement_target_mode": str(target.get("target_mode") or "stock"),
            "amaru_json_traces": bool(substrate.get("amaru_json_traces", False)),
            "amaru_runtime_interface": (
                "extracted-binary"
                if target.get("target_mode") == "patched"
                else target.get("amaru_runtime_interface")
            ),
            "measurement_target_identity": measurement_identity,
            "controller_node_services": node_services,
            "peer_sharing": False,
            "healthy_timeout_seconds": 1800,
        }
        experimental_protocols = substrate.get("cardano_experimental_protocols")
        if experimental_protocols is None:
            from scripts.qualify_node_versions import (
                cardano_experimental_protocols_policy,
            )

            experimental_protocols = cardano_experimental_protocols_policy(
                str(substrate["scope"]), str(cardano_identity[0])
            )
        if experimental_protocols is not None:
            config["cardano_experimental_protocols"] = bool(experimental_protocols)
        if substrate.get("kes_genesis_override"):
            config["kes_genesis_override"] = dict(substrate["kes_genesis_override"])
        if substrate.get("plutus_v2_genesis"):
            config.update(
                {
                    "plutus_v2_genesis": True,
                    "plutus_v2_cost_model_path": substrate.get(
                        "plutus_v2_cost_model_path"
                    ),
                    "plutus_v2_cost_model_sha256": substrate.get(
                        "plutus_v2_cost_model_sha256"
                    ),
                }
            )
        return config

    def _invoke_deployment(
        self,
        deployment: dict[str, Any],
        runtime_root: Path,
        execution_id: str,
    ) -> dict[str, Any]:
        config = self._deployment_config(deployment, runtime_root)
        self._assert_operator_registration_state(config["substrate"])
        for node in (config["substrate"].get("nodes") or []):
            qualified_target = (
                node if node.get("target_mode") == "patched" else None
            )
            image = str(node.get("image") or "")
            if isinstance(node.get("operator_registration"), dict):
                self._assert_node_images(node)
            else:
                self._ensure_image(
                    image,
                    execution_id,
                    qualified_target=qualified_target,
                )
            probe_image = node.get("runtime_probe_image")
            if not probe_image:
                continue
            if qualified_target is not None:
                target_record = self._qualified_local_target(
                    image, qualified_target
                )
                self._verify_qualified_runtime_probe_image(
                    str(probe_image), target_record
                )
            else:
                self._ensure_image(str(probe_image), execution_id)
        config_path = runtime_root / "versioned-deployment.json"
        self._write_json(config_path, config)
        adapter = Path(__file__).resolve().parents[1] / "scripts" / str(deployment["adapter"])
        if not adapter.is_file():
            raise DockerRuntimeError("retained deployment adapter is unavailable")
        result = self._run_adapter(
            (sys.executable, str(adapter), "--config", str(config_path)),
            timeout=2100,
            execution_id=execution_id,
        )
        structured_failure = (
            self._structured_adapter_failure(result)
            if config.get("adapter") == "runtime_amaru_control_substrate.py"
            else None
        )
        if structured_failure is not None:
            raise structured_failure
        report = self._semantic_report(runtime_root, config)
        if config.get("adapter") == "runtime_amaru_control_substrate.py":
            semantically_ready = (
                report.get("passed") is True and report.get("retained") is True
            )
        else:
            semantically_ready = report.get("healthy") is True
        if result.returncode != 0 or not semantically_ready:
            raise DockerRuntimeError("deployment adapter did not prove semantic readiness")
        ownership = config["substrate"].get("controller_ownership") or {}
        self._write_runtime_integrity(
            runtime_root=runtime_root,
            topology_id=str(ownership.get("topology_id") or ""),
            compose_project=str(config["compose_project"]),
        )
        self._verify_retained_runtime(
            runtime_root=runtime_root,
            topology_id=str(ownership.get("topology_id") or ""),
            compose_project=str(config["compose_project"]),
            enforce_golden=True,
        )
        if config.get("adapter") == "runtime_amaru_control_substrate.py":
            report = {
                **report,
                "nodes": [
                    {
                        "id": node_id,
                        "container_name": f"{config['compose_project']}-{service_name}-1",
                    }
                    for node_id, service_name in config["controller_node_services"].items()
                ],
            }
        return report

    @staticmethod
    def _adapter_network_names(adapter: str, compose_project: str) -> list[str]:
        if adapter == "runtime_amaru_control_substrate.py":
            return [
                compose_project + "-default",
                compose_project + "-amaru-consumer-net",
            ]
        return [compose_project + "-net"]

    def _retained_volume_specs(
        self,
        *,
        runtime_root: Path,
        topology_id: str,
        compose_project: str,
    ) -> dict[str, str]:
        compose_file = self._compose_file(runtime_root)
        if compose_file is None:
            raise DockerRuntimeError("retained volume specification is unavailable")
        try:
            compose = json.loads(compose_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("retained volume specification is invalid") from exc
        volumes = compose.get("volumes")
        if not isinstance(volumes, dict) or not volumes:
            raise DockerRuntimeError("retained Amaru runtime has no private volumes")
        required_labels = {
            _MANAGED: "true",
            _INSTALLATION: self.installation_id,
            _TOPOLOGY: topology_id,
            _COMPONENT: "volume",
        }
        specs: dict[str, str] = {}
        for raw_key, definition in volumes.items():
            key = str(raw_key or "")
            if not _SAFE_RUNTIME_ID.fullmatch(key) or not isinstance(definition, dict):
                raise DockerRuntimeError("retained runtime volume identity is invalid")
            if definition.get("external") is True or definition.get("name") is not None:
                raise DockerRuntimeError(
                    "retained runtime volume is external, shared, or custom-named"
                )
            labels = definition.get("labels")
            if not isinstance(labels, dict) or any(
                labels.get(label) != value for label, value in required_labels.items()
            ):
                raise DockerRuntimeError("retained runtime volume ownership is invalid")
            specs[f"{compose_project}_{key}"] = key
        return specs

    def _project_volume_names(self, compose_project: str) -> set[str]:
        result = self._run(
            (
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={compose_project}",
            ),
            timeout=30,
        )
        if result.returncode != 0:
            raise DockerRuntimeError("Docker project volume inventory failed")
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _project_volume_preflight(
        self,
        *,
        topology_id: str,
        compose_project: str,
        specs: dict[str, str],
        require_complete: bool,
    ) -> dict[str, dict[str, Any]]:
        listed = self._project_volume_names(compose_project)
        if listed - set(specs):
            raise DockerRuntimeError(
                "refusing to mutate unexpected project-scoped volumes"
            )
        found: dict[str, dict[str, Any]] = {}
        for name, key in specs.items():
            volume = self._inspect_optional("volume", name)
            if volume is None:
                if require_complete:
                    raise DockerRuntimeError("retained runtime volume is missing")
                continue
            if not self._volume_owned(
                volume,
                topology_id=topology_id,
                compose_project=compose_project,
                volume_key=key,
            ):
                raise DockerRuntimeError(
                    "refusing to mutate an unmanaged or shared volume collision"
                )
            found[name] = volume
        if listed != set(found):
            raise DockerRuntimeError("project volume inventory is inconsistent")
        return found

    @staticmethod
    def _directory_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(root).as_posix()
            stat = path.lstat()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(
                f"{stat.st_mode}:{stat.st_uid}:{stat.st_gid}:{stat.st_size}".encode(
                    "ascii"
                )
                + b"\0"
            )
            if path.is_symlink():
                digest.update(os.readlink(path).encode("utf-8"))
            elif path.is_file():
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
        return "sha256:" + digest.hexdigest()

    def _volume_transfer_container(
        self,
        *,
        volume_name: str,
        topology_id: str,
        image: str,
        writable: bool,
        execution_id: str,
    ) -> str:
        suffix = hashlib.sha256(
            f"{volume_name}:{execution_id}:{writable}".encode("utf-8")
        ).hexdigest()[:20]
        name = f"dwarf-volume-transfer-{suffix}"
        mount = f"type=volume,source={volume_name},target=/volume"
        if not writable:
            mount += ",readonly"
        created = self._run(
            (
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--label",
                f"{_MANAGED}=true",
                "--label",
                f"{_INSTALLATION}={self.installation_id}",
                "--label",
                f"{_TOPOLOGY}={topology_id}",
                "--label",
                f"{_COMPONENT}=volume-transfer",
                "--mount",
                mount,
                "--entrypoint",
                "/bin/true",
                image,
            ),
            timeout=60,
            execution_id=execution_id,
        )
        if created.returncode != 0:
            raise DockerRuntimeError("owned volume transfer container creation failed")
        return name

    def _copy_volume_to_directory(
        self,
        *,
        volume_name: str,
        destination: Path,
        topology_id: str,
        image: str,
        execution_id: str,
    ) -> str:
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        helper = self._volume_transfer_container(
            volume_name=volume_name,
            topology_id=topology_id,
            image=image,
            writable=False,
            execution_id=execution_id,
        )
        try:
            copied = self._run(
                ("cp", "--archive", f"{helper}:/volume/.", str(destination)),
                timeout=1800,
                execution_id=execution_id,
            )
            if copied.returncode != 0:
                raise DockerRuntimeError("owned volume snapshot failed")
        finally:
            removed = self._run(
                ("container", "rm", "--force", helper),
                timeout=60,
                execution_id=execution_id,
            )
            if removed.returncode != 0:
                raise DockerRuntimeError("owned volume transfer cleanup failed")
        return self._directory_digest(destination)

    def _snapshot_owned_volumes(
        self,
        *,
        parent: Path,
        topology_id: str,
        compose_project: str,
        specs: dict[str, str],
        image: str,
        execution_id: str,
    ) -> Path:
        self._project_volume_preflight(
            topology_id=topology_id,
            compose_project=compose_project,
            specs=specs,
            require_complete=True,
        )
        snapshot = parent / (
            ".volume-rollback-"
            + hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:20]
        )
        snapshot.mkdir(parents=True, exist_ok=False, mode=0o700)
        manifest: dict[str, Any] = {
            "schema_version": "v1",
            "installation_id": self.installation_id,
            "topology_id": topology_id,
            "compose_project": compose_project,
            "volumes": {},
        }
        try:
            for index, (name, key) in enumerate(sorted(specs.items())):
                directory = snapshot / f"volume-{index:03d}"
                content_sha256 = self._copy_volume_to_directory(
                    volume_name=name,
                    destination=directory,
                    topology_id=topology_id,
                    image=image,
                    execution_id=execution_id + f"-snapshot-{index}",
                )
                manifest["volumes"][name] = {
                    "key": key,
                    "directory": directory.name,
                    "content_sha256": content_sha256,
                }
            self._write_json(snapshot / "manifest.json", manifest)
            return snapshot
        except BaseException:
            shutil.rmtree(snapshot, ignore_errors=True)
            raise

    def _restore_owned_volumes(
        self,
        *,
        snapshot: Path,
        topology_id: str,
        compose_project: str,
        specs: dict[str, str],
        image: str,
        execution_id: str,
    ) -> None:
        try:
            manifest = json.loads((snapshot / "manifest.json").read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError("owned volume rollback manifest is invalid") from exc
        expected_identity = {
            "schema_version": "v1",
            "installation_id": self.installation_id,
            "topology_id": topology_id,
            "compose_project": compose_project,
        }
        if any(manifest.get(key) != value for key, value in expected_identity.items()):
            raise DockerRuntimeError("owned volume rollback identity is invalid")
        records = manifest.get("volumes")
        if not isinstance(records, dict) or set(records) != set(specs):
            raise DockerRuntimeError("owned volume rollback set is invalid")
        for index, (name, key) in enumerate(sorted(specs.items())):
            record = records[name]
            source = snapshot / str(record.get("directory") or "")
            if (
                not isinstance(record, dict)
                or record.get("key") != key
                or record.get("directory") != f"volume-{index:03d}"
                or not source.is_dir()
                or self._directory_digest(source) != record.get("content_sha256")
            ):
                raise DockerRuntimeError("owned volume rollback content is invalid")
            existing = self._inspect_optional("volume", name)
            if existing is not None:
                if not self._volume_owned(
                    existing,
                    topology_id=topology_id,
                    compose_project=compose_project,
                    volume_key=key,
                ):
                    raise DockerRuntimeError("owned volume rollback collision is invalid")
                comparison = snapshot / f".existing-{index:03d}"
                try:
                    existing_digest = self._copy_volume_to_directory(
                        volume_name=name,
                        destination=comparison,
                        topology_id=topology_id,
                        image=image,
                        execution_id=execution_id + f"-compare-{index}",
                    )
                finally:
                    shutil.rmtree(comparison, ignore_errors=True)
                if existing_digest == record.get("content_sha256"):
                    continue
                removed = self._run(
                    ("volume", "rm", name),
                    timeout=60,
                    execution_id=execution_id,
                )
                if removed.returncode != 0:
                    raise DockerRuntimeError("owned volume rollback could not replace volume")
            created = self._run(
                (
                    "volume",
                    "create",
                    "--label",
                    f"{_MANAGED}=true",
                    "--label",
                    f"{_INSTALLATION}={self.installation_id}",
                    "--label",
                    f"{_TOPOLOGY}={topology_id}",
                    "--label",
                    f"{_COMPONENT}=volume",
                    "--label",
                    f"com.docker.compose.project={compose_project}",
                    "--label",
                    f"com.docker.compose.volume={key}",
                    name,
                ),
                timeout=60,
                execution_id=execution_id,
            )
            if created.returncode != 0 or created.stdout.strip() != name:
                raise DockerRuntimeError("owned volume rollback creation failed")
            helper = self._volume_transfer_container(
                volume_name=name,
                topology_id=topology_id,
                image=image,
                writable=True,
                execution_id=execution_id + f"-restore-{index}",
            )
            try:
                copied = self._run(
                    ("cp", "--archive", str(source) + "/.", f"{helper}:/volume"),
                    timeout=1800,
                    execution_id=execution_id + f"-restore-{index}",
                )
                if copied.returncode != 0:
                    raise DockerRuntimeError("owned volume rollback copy failed")
            finally:
                removed = self._run(
                    ("container", "rm", "--force", helper),
                    timeout=60,
                    execution_id=execution_id + f"-restore-{index}",
                )
                if removed.returncode != 0:
                    raise DockerRuntimeError("owned volume transfer cleanup failed")
            verification = snapshot / f".verified-{index:03d}"
            try:
                restored_digest = self._copy_volume_to_directory(
                    volume_name=name,
                    destination=verification,
                    topology_id=topology_id,
                    image=image,
                    execution_id=execution_id + f"-verify-{index}",
                )
            finally:
                shutil.rmtree(verification, ignore_errors=True)
            if restored_digest != record.get("content_sha256"):
                raise DockerRuntimeError("owned volume rollback verification failed")
        self._project_volume_preflight(
            topology_id=topology_id,
            compose_project=compose_project,
            specs=specs,
            require_complete=True,
        )

    def _project_preflight(
        self,
        topology_id: str,
        compose_project: str,
        adapter: str,
        expected_roles: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if adapter == "runtime_amaru_control_substrate.py":
            matching = self._project_runtime_containers(
                topology_id=topology_id,
                compose_project=compose_project,
                expected_roles=expected_roles,
            )
        else:
            matching = []
            for item in self._all_containers():
                name = str(item.get("Name") or "").lstrip("/")
                labels = ((item.get("Config") or {}).get("Labels") or {})
                if (
                    name.startswith(compose_project + "-")
                    or labels.get(_TOPOLOGY) == topology_id
                ):
                    if not self._owned(item, topology_id=topology_id):
                        raise DockerRuntimeError(
                            "refusing to mutate an unmanaged container collision"
                        )
                    matching.append(item)
        found = []
        for name in self._adapter_network_names(adapter, compose_project):
            network = self._inspect_optional("network", name)
            if network is not None and not self._network_owned(network, topology_id):
                raise DockerRuntimeError("refusing to mutate an unmanaged network collision")
            if network is not None:
                found.append(network)
        return matching, found[0] if found else None

    def _restore_runtime(
        self,
        *,
        backup: Path,
        current: Path,
        topology_id: str,
        compose_project: str,
        before: list[dict[str, Any]],
        network_before: dict[str, Any] | None,
        execution_id: str,
    ) -> None:
        if current.exists():
            failed = current.with_name(f"failed-{execution_id}")
            if failed.exists():
                shutil.rmtree(failed)
            current.rename(failed)
        backup.rename(current)
        restored = self._compose(
            current, compose_project, ("up", "-d"), execution_id + "-rollback"
        )
        if restored.returncode != 0:
            raise DockerRuntimeError("topology mutation and rollback both failed")
        for item in before:
            if str((item.get("State") or {}).get("Status")) == "running":
                continue
            name = str(item.get("Name") or "").lstrip("/")
            stopped = self._run(
                ("stop", name), timeout=60, execution_id=execution_id + "-rollback"
            )
            if stopped.returncode != 0:
                raise DockerRuntimeError("topology rollback could not restore stopped state")
        self._restore_network_state(
            topology_id=topology_id,
            compose_project=compose_project,
            before=network_before,
            execution_id=execution_id,
        )

    def _restore_network_state(
        self,
        *,
        topology_id: str,
        compose_project: str,
        before: dict[str, Any] | None,
        execution_id: str,
    ) -> None:
        prior_name = str((before or {}).get("Name") or "")
        if before is not None and prior_name == compose_project + "-default":
            for name in self._adapter_network_names(
                "runtime_amaru_control_substrate.py", compose_project
            ):
                try:
                    network = self._inspect_optional("network", name)
                except DockerRuntimeError as exc:
                    raise DockerRuntimeError(
                        "topology mutation and rollback both failed"
                    ) from exc
                if network is None or not self._network_owned(network, topology_id):
                    raise DockerRuntimeError(
                        "topology mutation and rollback both failed"
                    )
            return
        name = prior_name or compose_project + "-net"
        try:
            network = self._inspect_optional("network", name)
        except DockerRuntimeError as exc:
            raise DockerRuntimeError("topology mutation and rollback both failed") from exc
        if before is not None:
            if network is None or not self._network_owned(network, topology_id):
                raise DockerRuntimeError("topology mutation and rollback both failed")
            return
        if network is None:
            return
        if not self._network_owned(network, topology_id):
            raise DockerRuntimeError("topology mutation and rollback both failed")
        removed = self._run(
            ("network", "rm", name),
            timeout=60,
            execution_id=execution_id + "-rollback",
        )
        if removed.returncode != 0:
            raise DockerRuntimeError("topology mutation and rollback both failed")
        try:
            remaining = self._inspect_optional("network", name)
        except DockerRuntimeError as exc:
            raise DockerRuntimeError("topology mutation and rollback both failed") from exc
        if remaining is not None:
            raise DockerRuntimeError("topology mutation and rollback both failed")

    def _restore_container_states(
        self,
        *,
        topology_id: str,
        before: list[dict[str, Any]],
        execution_id: str,
    ) -> None:
        expected = {
            str(item.get("Name") or "").lstrip("/"): str(
                (item.get("State") or {}).get("Status") or "unknown"
            )
            for item in before
        }
        current = self._current_containers_for_prior_scope(
            topology_id=topology_id,
            before=before,
        )
        current_names = {str(item.get("Name") or "").lstrip("/") for item in current}
        for name in sorted(current_names - set(expected)):
            removed = self._run(
                ("container", "rm", "--force", name),
                timeout=60,
                execution_id=execution_id + "-rollback",
            )
            if removed.returncode != 0:
                raise DockerRuntimeError("topology rollback could not remove a repair-created container")
        if set(expected) - current_names:
            raise DockerRuntimeError("topology rollback could not restore an original container")
        for name, prior_state in expected.items():
            verb = "start" if prior_state == "running" else "stop"
            restored = self._run(
                (verb, name), timeout=60, execution_id=execution_id + "-rollback"
            )
            if restored.returncode != 0:
                raise DockerRuntimeError("topology rollback could not restore container state")

    def _quiesce_owned_containers(
        self,
        *,
        topology_id: str,
        before: list[dict[str, Any]],
        execution_id: str,
    ) -> None:
        expected = {
            str(item.get("Name") or "").lstrip("/"): str(
                (item.get("State") or {}).get("Status") or "unknown"
            )
            for item in before
        }
        current = self._current_containers_for_prior_scope(
            topology_id=topology_id,
            before=before,
        )
        by_name = {str(item.get("Name") or "").lstrip("/"): item for item in current}
        if not expected or set(by_name) != set(expected):
            raise DockerRuntimeError(
                "topology inventory changed before volume quiescence"
            )
        try:
            for name, prior_state in expected.items():
                if prior_state != "running":
                    continue
                stopped = self._run(
                    ("stop", "--time", "60", name),
                    timeout=90,
                    execution_id=execution_id + "-quiesce",
                )
                if stopped.returncode != 0:
                    raise DockerRuntimeError(
                        "topology volume writer quiescence failed"
                    )
            observed = self._current_containers_for_prior_scope(
                topology_id=topology_id,
                before=before,
            )
            observed_by_name = {
                str(item.get("Name") or "").lstrip("/"): item for item in observed
            }
            if set(observed_by_name) != set(expected) or any(
                str((item.get("State") or {}).get("Status") or "") == "running"
                for item in observed_by_name.values()
            ):
                raise DockerRuntimeError(
                    "topology volume writers did not reach a quiescent state"
                )
        except BaseException:
            self._restore_container_states(
                topology_id=topology_id,
                before=before,
                execution_id=execution_id,
            )
            raise

    def _verify_running_postcondition(
        self,
        *,
        topology_id: str,
        compose_project: str,
        config: dict[str, Any],
    ) -> list[str]:
        if config.get("adapter") == "runtime_amaru_control_substrate.py":
            health = self.topology_health(topology_id)
            if health.get("state") != "healthy":
                raise DockerRuntimeError(
                    "Amaru topology repair did not restore semantic readiness"
                )
            return sorted(
                str(node.get("container_name") or "")
                for node in health.get("nodes") or []
            )
        nodes = (config.get("substrate") or {}).get("nodes") or []
        expected = {
            f"{compose_project}-{str(node.get('id') or '')}-1" for node in nodes
        }
        current = [
            item
            for item in self._all_containers()
            if self._owned(item, topology_id=topology_id)
        ]
        by_name = {str(item.get("Name") or "").lstrip("/"): item for item in current}
        if set(by_name) != expected or any(
            str((item.get("State") or {}).get("Status") or "") != "running"
            for item in by_name.values()
        ):
            raise DockerRuntimeError("topology mutation did not reach the exact running postcondition")
        network = self._inspect_optional("network", compose_project + "-net")
        if network is None or not self._network_owned(network, topology_id):
            raise DockerRuntimeError("topology mutation did not retain its owned network")
        return sorted(expected)

    def _verify_absent_postcondition(
        self,
        *,
        topology_id: str,
        compose_project: str,
        config: dict[str, Any] | None = None,
        volume_specs: dict[str, str] | None = None,
    ) -> None:
        adapter = str((config or {}).get("adapter") or "runtime_compose_substrate.py")
        if adapter == "runtime_amaru_control_substrate.py":
            remaining = self._project_runtime_containers(
                topology_id=topology_id,
                compose_project=compose_project,
            )
        else:
            remaining = [
                item
                for item in self._all_containers()
                if self._owned(item, topology_id=topology_id)
            ]
        if remaining:
            raise DockerRuntimeError("topology removal left managed containers behind")
        for network_name in self._adapter_network_names(adapter, compose_project):
            if self._inspect_optional("network", network_name) is not None:
                raise DockerRuntimeError("topology removal left its managed network behind")
        if volume_specs is not None:
            if self._project_volume_names(compose_project):
                raise DockerRuntimeError("topology removal left project volumes behind")
            for volume_name in volume_specs:
                if self._inspect_optional("volume", volume_name) is not None:
                    raise DockerRuntimeError("topology removal left its managed volume behind")

    @staticmethod
    def _cancelled(topology_id: str) -> dict[str, Any]:
        return {"status": "cancelled", "topology_id": topology_id, "containers": []}

    def _apply_deployment(
        self, plan: dict[str, Any], execution_id: str, cancel_event: threading.Event
    ) -> dict[str, Any]:
        action = str(plan["action"])
        topology_id = str(plan["topology_id"])
        deployment = plan.get("deployment")
        current = self._runtime_parent(topology_id) / "current"
        parent = current.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        compose_project = (
            str((deployment or {}).get("compose_project") or "")
            or f"dwarf-profile-{self.installation_id}-{topology_id}"
        )
        adapter = str(
            ((deployment or {}).get("adapter") if isinstance(deployment, dict) else "")
            or "runtime_compose_substrate.py"
        )
        retained_config: dict[str, Any] | None = None
        retained_roles: dict[str, str] | None = None
        volume_specs: dict[str, str] = {}
        volume_snapshot: Path | None = None
        volume_image = ""

        def discard_volume_snapshot() -> None:
            nonlocal volume_snapshot
            if volume_snapshot is not None:
                shutil.rmtree(volume_snapshot)
                volume_snapshot = None

        if current.exists():
            retained_config = self._verify_retained_runtime(
                runtime_root=current,
                topology_id=topology_id,
                compose_project=compose_project,
                enforce_golden=action != "remove",
            )
            adapter = str(retained_config.get("adapter") or adapter)
            if adapter == "runtime_amaru_control_substrate.py":
                retained_roles = self._retained_project_roles(
                    topology_id=topology_id,
                    compose_project=compose_project,
                )
                volume_specs = self._retained_volume_specs(
                    runtime_root=current,
                    topology_id=topology_id,
                    compose_project=compose_project,
                )
                self._project_volume_preflight(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    specs=volume_specs,
                    require_complete=True,
                )
                volume_image = str(retained_config.get("cardano_image") or "")
        before, network_before = self._project_preflight(
            topology_id,
            compose_project,
            adapter,
            retained_roles,
        )
        if cancel_event.is_set():
            return self._cancelled(topology_id)
        if not current.exists() and before:
            raise DockerRuntimeError("managed topology has no retained controller runtime")
        if (
            action == "repair"
            and isinstance(deployment, dict)
            and retained_config is not None
            and retained_config.get("substrate") != deployment.get("substrate")
        ):
            from profile_manager.profiles import deployment_substrates_reusable

            if not deployment_substrates_reusable(
                retained_config.get("substrate"), deployment.get("substrate")
            ):
                raise DockerRuntimeError(
                    "retained topology differs from the current catalog plan"
                )
        if action in {"deploy", "repair", "redeploy"} and isinstance(
            deployment, dict
        ):
            substrate = deployment.get("substrate")
            if not isinstance(substrate, dict):
                raise DockerRuntimeError("deployment substrate is invalid")
            self._assert_operator_registration_state(substrate)
            for node in substrate.get("nodes") or []:
                if isinstance(node, dict) and isinstance(
                    node.get("operator_registration"), dict
                ):
                    self._assert_node_images(node)
        if action == "remove":
            if not before and not current.exists():
                if (
                    adapter == "runtime_amaru_control_substrate.py"
                    and self._project_volume_names(compose_project)
                ):
                    raise DockerRuntimeError(
                        "refusing to remove project volumes without a retained specification"
                    )
                if network_before is not None:
                    for network_name in self._adapter_network_names(
                        adapter, compose_project
                    ):
                        network = self._inspect_optional("network", network_name)
                        if network is None:
                            continue
                        if not self._network_owned(network, topology_id):
                            raise DockerRuntimeError(
                                "refusing to remove an unmanaged network collision"
                            )
                        removed = self._run(
                            ("network", "rm", network_name),
                            timeout=60,
                            execution_id=execution_id,
                        )
                        if removed.returncode != 0:
                            raise DockerRuntimeError(
                                "Docker topology network removal failed"
                            )
                    self._verify_absent_postcondition(
                        topology_id=topology_id,
                        compose_project=compose_project,
                        config={"adapter": adapter},
                    )
                return {"status": "applied", "topology_id": topology_id, "containers": []}
            if volume_specs:
                self._quiesce_owned_containers(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                if cancel_event.is_set():
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    return self._cancelled(topology_id)
                try:
                    volume_snapshot = self._snapshot_owned_volumes(
                        parent=parent,
                        topology_id=topology_id,
                        compose_project=compose_project,
                        specs=volume_specs,
                        image=volume_image,
                        execution_id=execution_id,
                    )
                except BaseException:
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    raise
                if cancel_event.is_set():
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    discard_volume_snapshot()
                    return self._cancelled(topology_id)
            down_arguments = (
                ("down", "--volumes", "--remove-orphans")
                if volume_specs
                else ("down", "--remove-orphans")
            )
            try:
                down = self._compose(
                    current, compose_project, down_arguments, execution_id
                )
            except BaseException:
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology removal and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError(
                        "topology removal and rollback both failed"
                    )
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                raise
            if down.returncode != 0 or cancel_event.is_set():
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology removal and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError("topology removal and rollback both failed")
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                if cancel_event.is_set():
                    return self._cancelled(topology_id)
                raise DockerRuntimeError("Docker Compose topology removal failed")
            try:
                self._verify_absent_postcondition(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    config=retained_config,
                    volume_specs=volume_specs or None,
                )
            except DockerRuntimeError:
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology removal and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError("topology removal and rollback both failed")
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                raise
            if cancel_event.is_set():
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology cancellation and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError("topology cancellation and rollback both failed")
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                return self._cancelled(topology_id)
            archive = parent / f"removed-{execution_id}"
            if current.exists():
                current.rename(archive)
            discard_volume_snapshot()
            return {
                "status": "applied",
                "topology_id": topology_id,
                "containers": [],
                "archived_runtime": str(archive) if archive.exists() else None,
            }
        if not isinstance(deployment, dict):
            raise DockerRuntimeError("plan has no complete server-owned deployment")
        if action == "deploy" and (before or current.exists()):
            raise DockerRuntimeError("topology is not absent; create a repair or redeploy plan")
        if action == "repair":
            if not current.exists():
                raise DockerRuntimeError("retained runtime is unavailable for repair")
            repaired = self._compose(
                current, compose_project, ("up", "-d"), execution_id
            )
            if repaired.returncode != 0 or cancel_event.is_set():
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                if cancel_event.is_set():
                    return self._cancelled(topology_id)
                raise DockerRuntimeError("Docker Compose repair failed")
            assert retained_config is not None
            try:
                containers = self._verify_running_postcondition(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    config=retained_config,
                )
            except DockerRuntimeError:
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                raise
            if cancel_event.is_set():
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                return self._cancelled(topology_id)
            return {
                "status": "applied",
                "topology_id": topology_id,
                "containers": containers,
                "semantic_health": None,
            }
        backup = parent / f"rollback-{execution_id}"
        if action == "redeploy":
            if not current.exists():
                raise DockerRuntimeError("retained runtime is unavailable for redeploy")
            if volume_specs:
                self._quiesce_owned_containers(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                if cancel_event.is_set():
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    return self._cancelled(topology_id)
                try:
                    volume_snapshot = self._snapshot_owned_volumes(
                        parent=parent,
                        topology_id=topology_id,
                        compose_project=compose_project,
                        specs=volume_specs,
                        image=volume_image,
                        execution_id=execution_id,
                    )
                except BaseException:
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    raise
                if cancel_event.is_set():
                    self._restore_container_states(
                        topology_id=topology_id,
                        before=before,
                        execution_id=execution_id,
                    )
                    discard_volume_snapshot()
                    return self._cancelled(topology_id)
            down_arguments = (
                ("down", "--volumes", "--remove-orphans")
                if volume_specs
                else ("down", "--remove-orphans")
            )
            try:
                down = self._compose(
                    current, compose_project, down_arguments, execution_id
                )
            except BaseException:
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology redeploy teardown and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError(
                        "topology redeploy teardown and rollback both failed"
                    )
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                raise
            if down.returncode != 0 or cancel_event.is_set():
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology redeploy teardown and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError("topology redeploy teardown and rollback both failed")
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                if cancel_event.is_set():
                    return self._cancelled(topology_id)
                raise DockerRuntimeError("Docker Compose redeploy teardown failed")
            try:
                self._verify_absent_postcondition(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    config=retained_config,
                    volume_specs=volume_specs or None,
                )
            except DockerRuntimeError:
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology redeploy teardown and rollback both failed"
                        ) from exc
                restored = self._compose(
                    current,
                    compose_project,
                    ("up", "-d"),
                    execution_id + "-rollback",
                )
                if restored.returncode != 0:
                    raise DockerRuntimeError(
                        "topology redeploy teardown and rollback both failed"
                    )
                self._restore_container_states(
                    topology_id=topology_id,
                    before=before,
                    execution_id=execution_id,
                )
                self._restore_network_state(
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
                raise
            current.rename(backup)
        current.mkdir(parents=True, exist_ok=False)
        try:
            if cancel_event.is_set():
                raise InterruptedError("topology action cancelled")
            report = self._invoke_deployment(deployment, current, execution_id)
            if cancel_event.is_set():
                raise InterruptedError("topology action cancelled")
        except BaseException:
            self._compose(
                current, compose_project, ("down", "--volumes", "--remove-orphans"), execution_id
            )
            if backup.exists():
                if volume_snapshot is not None:
                    try:
                        self._restore_owned_volumes(
                            snapshot=volume_snapshot,
                            topology_id=topology_id,
                            compose_project=compose_project,
                            specs=volume_specs,
                            image=volume_image,
                            execution_id=execution_id + "-rollback",
                        )
                    except DockerRuntimeError as exc:
                        raise DockerRuntimeError(
                            "topology mutation and rollback both failed"
                        ) from exc
                self._restore_runtime(
                    backup=backup,
                    current=current,
                    topology_id=topology_id,
                    compose_project=compose_project,
                    before=before,
                    network_before=network_before,
                    execution_id=execution_id,
                )
                discard_volume_snapshot()
            else:
                try:
                    self._verify_absent_postcondition(
                        topology_id=topology_id,
                        compose_project=compose_project,
                        config=self._deployment_config(deployment, current),
                        volume_specs=volume_specs or None,
                    )
                except DockerRuntimeError as exc:
                    raise DockerRuntimeError(
                        "topology mutation and cleanup both failed"
                    ) from exc
                if current.exists():
                    failed = parent / f"failed-{execution_id}"
                    if failed.exists():
                        shutil.rmtree(failed)
                    current.rename(failed)
            if cancel_event.is_set():
                return {"status": "cancelled", "topology_id": topology_id, "containers": []}
            raise
        if backup.exists():
            archive = parent / f"replaced-{execution_id}"
            backup.rename(archive)
        discard_volume_snapshot()
        return {
            "status": "applied",
            "topology_id": topology_id,
            "containers": [
                str(item.get("container_name") or item.get("id") or "")
                for item in (report.get("nodes") or [])
            ],
            "semantic_health": True,
            "semantic_report": str(current / "evidence" / "compose-report.json"),
        }

    def _remove_topology(self, topology_id: str, execution_id: str) -> None:
        current = [
            item
            for item in self._all_containers()
            if self._owned(item, topology_id=topology_id)
        ]
        for item in current:
            name = str(item.get("Name") or "").lstrip("/")
            if not name:
                raise DockerRuntimeError("owned container has no name")
            self._assert_owned_container(name, topology_id)
            removed = self._run(
                ("container", "rm", "--force", name),
                timeout=60,
                execution_id=execution_id,
            )
            if removed.returncode != 0:
                raise DockerRuntimeError("Docker container removal failed")
        network_name = self._resource_name(topology_id)
        network = self._inspect_optional("network", network_name)
        if network is not None:
            if not self._network_owned(network, topology_id):
                raise DockerRuntimeError("refusing to remove an unmanaged network collision")
            removed = self._run(
                ("network", "rm", network_name), timeout=30, execution_id=execution_id
            )
            if removed.returncode != 0:
                raise DockerRuntimeError("Docker network removal failed")

    def _restore_topology(
        self, topology: dict[str, Any], execution_id: str
    ) -> None:
        """Restore a previously exported owned topology after a failed mutation."""
        try:
            result = self.apply(
                {
                    "action": "deploy",
                    "topology_id": topology["topology_id"],
                    "topology": topology,
                },
                execution_id + "-rollback",
                threading.Event(),
            )
        except BaseException as exc:
            raise DockerRuntimeError("topology mutation and rollback both failed") from exc
        if result.get("status") != "applied":
            raise DockerRuntimeError("topology mutation and rollback both failed")

    def apply(
        self, plan: dict[str, Any], execution_id: str, cancel_event: threading.Event
    ) -> dict[str, Any]:
        if plan.get("deployment") is not None or plan.get("action") == "remove":
            return self._apply_deployment(plan, execution_id, cancel_event)
        raise DockerRuntimeError("plan has no complete server-owned deployment")

    def cancel(self, execution_id: str) -> None:
        with self._process_lock:
            process = self._processes.get(execution_id)
            process_group = process.pid if process is not None else None
        if process_group is not None:
            self._stop_process_group(process_group)

    def logs(self, **parameters: Any) -> dict[str, Any]:
        kind = parameters["resource_kind"]
        resource_id = parameters["resource_id"]
        maximum = parameters["max_lines"]
        if kind == "topology":
            names = [item["name"] for item in self.snapshot(resource_id)["resources"]]
        elif kind == "controller" and resource_id == "controller":
            names = [os.environ.get("HOSTNAME", "")]
        else:
            raise DockerRuntimeError("log resource is not managed by this controller")
        lines: list[str] = []
        for name in names:
            if not name:
                continue
            result = self._run(("logs", "--tail", str(maximum), name), timeout=30)
            if result.returncode != 0:
                raise DockerRuntimeError("Docker log read failed")
            lines.extend(f"{name}: {line}" for line in result.stdout.splitlines())
        selected = lines[-maximum:]
        return {
            "resource_kind": kind,
            "resource_id": resource_id,
            "lines": selected,
            "next_cursor": None,
            "truncated": len(lines) > len(selected),
        }

    def export_topology(self, topology_id: str) -> dict[str, Any]:
        from profile_manager.deployment_versions import build_deployment_version_preview
        from profile_manager.profiles import find_profile
        from profile_manager.topology_export import (
            TopologyExportError,
            build_exact_deployed_snapshot,
        )

        current = self._runtime_parent(topology_id) / "current"
        try:
            config_path = current / "versioned-deployment.json"
            retained = json.loads(config_path.read_text(encoding="utf-8"))
            compose_project = str(retained.get("compose_project") or "")
            if not compose_project:
                raise DockerRuntimeError("retained deployment has no Compose identity")
            retained = self._verify_retained_runtime(
                runtime_root=current,
                topology_id=topology_id,
                compose_project=compose_project,
                enforce_golden=True,
            )
            profile = find_profile(topology_id)
            preview = build_deployment_version_preview(asdict(profile))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            if isinstance(exc, DockerRuntimeError):
                raise
            raise DockerRuntimeError("exact retained deployment is unavailable") from exc

        owned = [
            item
            for item in self._all_containers()
            if self._owned(item, topology_id=topology_id)
        ]
        if not owned:
            raise DockerRuntimeError("managed topology is absent")
        observed_nodes = []
        for item in owned:
            row = self._container_row(item)
            if not row["node_id"] or not row["implementation"] or not row["version"]:
                raise DockerRuntimeError("managed node lacks exact identity labels")
            observed_nodes.append(
                {
                    "id": row["node_id"],
                    "implementation": row["implementation"],
                    "version": row["version"],
                    "image": row["image"],
                    "state": row["state"],
                    "container_name": row["name"],
                }
            )
        semantic_health = self.topology_health(topology_id)
        semantic_state = str(semantic_health.get("state") or "unknown")
        reconciliation_state = (
            "converged"
            if semantic_state in {"healthy", "catching_up"}
            else semantic_state
        )
        try:
            return build_exact_deployed_snapshot(
                profile=profile,
                version_preview=preview,
                retained_deployment=retained,
                observed_nodes=observed_nodes,
                reconciliation_state=reconciliation_state,
                exported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                semantic_health=semantic_health,
            )
        except TopologyExportError as exc:
            raise DockerRuntimeError(str(exc)) from exc
