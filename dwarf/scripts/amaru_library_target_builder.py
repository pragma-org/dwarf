#!/usr/bin/env python3
"""Build one packaged DWARF shim against one exact Amaru source commit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib


TRANSFORM_VERSION = "amaru-library-target-v2"
_TARGET = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_TOOLCHAIN = re.compile(r"^(?:nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]+\.[0-9]+\.[0-9]+)$")
_SUPPORTED_TARGETS = frozenset(
    {
        "amaru-cbor-decode-auxiliary-data",
        "amaru-cbor-decode-block",
        "amaru-cbor-decode-block-header",
        "amaru-cbor-decode-certificate",
        "amaru-cbor-decode-tx-body",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 7200,
    unprivileged: bool = False,
) -> None:
    print("+ " + " ".join(command), flush=True)

    def drop_privileges() -> None:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)

    subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        check=True,
        timeout=timeout,
        preexec_fn=drop_privileges if unprivileged else None,
    )


def _request(root: Path) -> dict:
    value = json.loads((root / "request.json").read_text(encoding="utf-8"))
    fields = {
        "schema_version",
        "transform_version",
        "source_repository",
        "source_revision",
        "target_id",
        "target_source_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("builder request fields are invalid")
    if (
        value["schema_version"] != "dwarf.library-target-builder-request.v1"
        or value["transform_version"] != TRANSFORM_VERSION
        or value["source_repository"] != "https://github.com/pragma-org/amaru.git"
        or not _REVISION.fullmatch(str(value["source_revision"]))
        or not _TARGET.fullmatch(str(value["target_id"]))
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(value["target_source_sha256"])
        )
        or value["target_id"] not in _SUPPORTED_TARGETS
    ):
        raise ValueError("builder request identity is invalid")
    return value


def main() -> int:
    root = Path("/work")
    request = _request(root)
    target_source = root / "target.rs"
    if target_source.is_symlink() or not target_source.is_file():
        raise ValueError("packaged target source is missing")
    if _sha256(target_source) != request["target_source_sha256"]:
        raise ValueError("packaged target source digest differs")

    _run(["apt-get", "update"], timeout=1200)
    _run(
        [
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            "git",
            "curl",
            "ca-certificates",
            "build-essential",
            "pkg-config",
            "clang",
            "libclang-dev",
            "cmake",
            "protobuf-compiler",
        ],
        timeout=1200,
    )
    source = root / "source"
    source.mkdir(mode=0o755)
    _run(["git", "init", str(source)])
    _run(
        [
            "git",
            "-C",
            str(source),
            "fetch",
            "--depth=1",
            request["source_repository"],
            request["source_revision"],
        ],
        timeout=1200,
    )
    _run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    if commit != request["source_revision"] or not _REVISION.fullmatch(tree):
        raise ValueError("checked-out Amaru source identity differs")

    source_toolchain_path = source / "rust-toolchain.toml"
    source_lock_path = source / "Cargo.lock"
    if source_toolchain_path.is_symlink() or not source_toolchain_path.is_file():
        raise ValueError("selected Amaru source has no pinned Rust toolchain")
    if source_lock_path.is_symlink() or not source_lock_path.is_file():
        raise ValueError("selected Amaru source has no Cargo lock")
    toolchain = tomllib.loads(source_toolchain_path.read_text(encoding="utf-8"))[
        "toolchain"
    ]["channel"]
    if not isinstance(toolchain, str) or not _TOOLCHAIN.fullmatch(toolchain):
        raise ValueError("selected Amaru Rust toolchain is invalid")
    upstream_lock_sha256 = _sha256(source_lock_path)
    shutil.copyfile(source_lock_path, root / "upstream-Cargo.lock")

    crate = source / "crates" / "dwarf-library-target"
    (crate / "src").mkdir(parents=True)
    shutil.copyfile(target_source, crate / "src" / "main.rs")
    (crate / "Cargo.toml").write_text(
        "\n".join(
            (
                "[package]",
                'name = "dwarf-amaru-library-target"',
                'version = "0.1.0"',
                "edition.workspace = true",
                "publish = false",
                "",
                "[dependencies]",
                "amaru-kernel.workspace = true",
                "",
            )
        ),
        encoding="utf-8",
    )

    home = root / "home"
    home.mkdir(mode=0o755)
    for base in (source, home):
        for directory, _dirs, files in os.walk(base):
            os.chown(directory, 65534, 65534)
            for name in files:
                os.chown(Path(directory) / name, 65534, 65534)
    environment = {
        "PATH": "/work/home/.cargo/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "CARGO_HOME": str(home / ".cargo"),
        "RUSTUP_HOME": str(home / ".rustup"),
        "CARGO_INCREMENTAL": "0",
        "CARGO_TERM_COLOR": "never",
        "CARGO_BUILD_JOBS": "2",
    }
    installer = root / "rustup-init.sh"
    _run(
        [
            "curl",
            "--fail",
            "--proto",
            "=https",
            "--tlsv1.2",
            "https://sh.rustup.rs",
            "-o",
            str(installer),
        ],
        timeout=300,
    )
    os.chown(installer, 65534, 65534)
    _run(
        [
            "sh",
            str(installer),
            "-y",
            "--profile",
            "minimal",
            "--default-toolchain",
            toolchain,
        ],
        env=environment,
        timeout=1200,
        unprivileged=True,
    )
    lock_text = source_lock_path.read_text(encoding="utf-8")
    if 'name = "dwarf-amaru-library-target"' in lock_text:
        raise ValueError("selected Amaru lock already contains the DWARF target package")
    source_lock_path.write_text(
        lock_text.rstrip()
        + '\n\n[[package]]\nname = "dwarf-amaru-library-target"\nversion = "0.1.0"\ndependencies = [\n "amaru-kernel",\n]\n',
        encoding="utf-8",
    )
    effective_lock_sha256 = _sha256(source_lock_path)
    command = [
        "cargo",
        f"+{toolchain}",
        "build",
        "--locked",
        "--release",
        "-p",
        "dwarf-amaru-library-target",
    ]
    _run(
        command,
        cwd=source,
        env=environment,
        timeout=7200,
        unprivileged=True,
    )
    binary = source / "target" / "release" / "dwarf-amaru-library-target"
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("library target executable was not produced")
    smoke = subprocess.run(
        [str(binary)], input=b"", capture_output=True, check=False, timeout=10
    )
    if smoke.returncode != 1 or not smoke.stdout.startswith(b"ERR "):
        raise ValueError("library target executable failed the clean-error smoke contract")
    shutil.copyfile(binary, root / "executable")
    (root / "executable").chmod(0o555)
    shutil.copyfile(source_lock_path, root / "effective-Cargo.lock")
    rustc_version = subprocess.check_output(
        ["rustc", f"+{toolchain}", "--version", "--verbose"],
        text=True,
        env=environment,
    ).strip()
    cargo_version = subprocess.check_output(
        ["cargo", f"+{toolchain}", "--version", "--verbose"],
        text=True,
        env=environment,
    ).strip()
    result = {
        "schema_version": "dwarf.library-target-builder-result.v1",
        "transform_version": TRANSFORM_VERSION,
        "source_commit": commit,
        "source_tree": tree,
        "target_id": request["target_id"],
        "toolchain": toolchain,
        "rustc_version": rustc_version,
        "cargo_version": cargo_version,
        "upstream_lock_sha256": upstream_lock_sha256,
        "effective_lock_sha256": effective_lock_sha256,
        "build_command": command,
        "binary_sha256": _sha256(root / "executable"),
        "smoke": {
            "return_code": smoke.returncode,
            "stdout": smoke.stdout.decode("utf-8", errors="replace")[:256],
            "stderr": smoke.stderr.decode("utf-8", errors="replace")[:256],
        },
    }
    (root / "result.json").write_text(
        json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
