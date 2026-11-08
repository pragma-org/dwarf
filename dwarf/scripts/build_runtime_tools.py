#!/usr/bin/env python3
"""Build the profile scenarios' Rust runtime tools on the host and record them.

The controller image has no Rust toolchain, so the tools are built once on the host against the
host's Amaru and pallas checkouts, with the toolchain pinned by ``targets/amaru/rust-toolchain.toml``.
Output goes under the host measurement-targets state, which the installed controller already
mounts read-only at ``/var/dwarf/controller-state/measurement-targets``:

    <output-root>/<key>/bin/<tool>      the binaries
    <output-root>/record.json           active key, source revisions, toolchain, sha256 per tool

``runtime_common.runtime_tool`` resolves a tool from that record and checks its digest. Extends
the host-build pattern of ``build_amaru_measurement_target.py``. Re-running with the same sources
writes the same key, so a rebuild never leaves a mixed set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SHIM_TOOLS = (
    "dwarf-amaru-runtime-blockfetch",
    "dwarf-amaru-runtime-blockfetch-stale-server",
    "dwarf-amaru-runtime-chainsync",
)
EXTRACTOR_TOOL = "cardano-debug-epoch-state-extractor"
DEFAULT_OUTPUT_ROOT = Path.home() / ".local" / "share" / "dwarf" / "state" / "measurement-targets" / "runtime-tools"
_EXTRACTOR_REL = Path("extractors") / EXTRACTOR_TOOL / "Cargo.toml"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _toolchain(dwarf_root: Path) -> str:
    for line in (dwarf_root / "targets" / "amaru" / "rust-toolchain.toml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("channel"):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("rust-toolchain.toml names no channel")


def _revision(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True, text=True,
                          capture_output=True).stdout.strip()


def build_plan(*, dwarf_root: Path, amaru_src: Path, pallas_src: Path, work_dir: Path, toolchain: str,
               cargo: str) -> dict:
    """The crate copy is placed so its ``../../../codebases/<repo>`` path dependencies resolve."""
    crate = work_dir / "dwarf" / "targets" / "amaru"
    shims = [cargo, f"+{toolchain}", "build", "--release", "--manifest-path", str(crate / "Cargo.toml")]
    for tool in SHIM_TOOLS:
        shims.extend(["--bin", tool])
    extractor = [cargo, f"+{toolchain}", "build", "--release", "--manifest-path", str(work_dir / "dwarf" / _EXTRACTOR_REL)]
    return {
        "crate": str(crate),
        "links": {str(work_dir / "codebases" / "amaru"): str(amaru_src), str(work_dir / "codebases" / "pallas"): str(pallas_src)},
        "commands": [shims, extractor],
        "artifacts": {
            **{tool: str(crate / "target" / "release" / tool) for tool in SHIM_TOOLS},
            EXTRACTOR_TOOL: str(work_dir / "dwarf" / "extractors" / EXTRACTOR_TOOL / "target" / "release" / EXTRACTOR_TOOL),
        },
    }


def write_record(*, output_root: Path, key: str, revisions: dict, toolchain: str) -> dict:
    tools = {}
    for tool in (*SHIM_TOOLS, EXTRACTOR_TOOL):
        path = output_root / key / "bin" / tool
        if not path.is_file():
            raise RuntimeError(f"built tool is missing: {path}")
        tools[tool] = {"path": f"{key}/bin/{tool}", "sha256": _sha256(path)}
    record = {"schema_version": 1, "key": key, "revisions": revisions, "toolchain": toolchain, "tools": tools}
    tmp = output_root / "record.json.tmp"
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, output_root / "record.json")
    return record


def build(*, dwarf_root: Path, amaru_src: Path, pallas_src: Path, output_root: Path, cargo: str) -> dict:
    toolchain = _toolchain(dwarf_root)
    revisions = {"amaru": _revision(amaru_src), "pallas": _revision(pallas_src)}
    try:
        revisions["dwarf"] = _revision(dwarf_root)
    except subprocess.CalledProcessError:
        revisions["dwarf"] = "unknown"
    with tempfile.TemporaryDirectory(prefix="dwarf-runtime-tools-") as tmp:
        work_dir = Path(tmp)
        plan = build_plan(dwarf_root=dwarf_root, amaru_src=amaru_src, pallas_src=pallas_src, work_dir=work_dir,
                          toolchain=toolchain, cargo=cargo)
        ignore = shutil.ignore_patterns("target", "coverage-targets", "measurement-patches*", "conformance-adapters")
        shutil.copytree(dwarf_root / "targets" / "amaru", plan["crate"], ignore=ignore)
        shutil.copytree(dwarf_root / "extractors" / EXTRACTOR_TOOL, work_dir / "dwarf" / "extractors" / EXTRACTOR_TOOL,
                        ignore=shutil.ignore_patterns("target"))
        for link, target in plan["links"].items():
            Path(link).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
        for command in plan["commands"]:
            subprocess.run(command, check=True)
        content = hashlib.sha256()
        for tool in sorted(plan["artifacts"]):
            content.update(tool.encode() + bytes.fromhex(_sha256(Path(plan["artifacts"][tool]))))
        key = f"{revisions['amaru'][:12]}-{revisions['pallas'][:12]}-{content.hexdigest()[:12]}"
        bin_dir = output_root / key / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for tool, artifact in plan["artifacts"].items():
            shutil.copy2(artifact, bin_dir / tool)
            (bin_dir / tool).chmod(0o755)
    return write_record(output_root=output_root, key=key, revisions=revisions, toolchain=toolchain)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dwarf-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--amaru-src", type=Path, required=True)
    parser.add_argument("--pallas-src", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cargo", default=shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo"))
    args = parser.parse_args(argv)
    args.output_root.mkdir(parents=True, exist_ok=True)
    record = build(dwarf_root=args.dwarf_root.resolve(), amaru_src=args.amaru_src.resolve(),
                   pallas_src=args.pallas_src.resolve(), output_root=args.output_root, cargo=args.cargo)
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
