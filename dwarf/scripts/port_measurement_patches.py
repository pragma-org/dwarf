#!/usr/bin/env python3
"""Port-check driver: can the newest committed patch series be applied to a new
source revision?

Runs inside the builder container as ``python3 /work/port.py`` with only the
job directory mounted (stdlib only). Input: ``/work/request.json``
(``implementation``, ``source_revision``, ``repository``), ``/work/manifest.json``
(the *base* manifest) and the base patch files next to it. Output:
``/work/output/port-result.json`` — ``status`` ``ported`` or ``failed`` with the
per-patch checks, the new revision's preimage digests and the reason. It never
builds anything and never writes outside ``/work``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class PortCheckError(RuntimeError):
    """The patch series cannot be ported to this revision (honest, not fatal)."""


def _run(arguments: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2048:]
        raise PortCheckError(f"{' '.join(arguments[:3])} failed: {detail}")
    return completed.stdout.strip()


def ensure_git() -> None:
    """The amaru build driver installs git the same way inside the builder image."""
    if shutil.which("git") is not None:
        return
    _run(["apt-get", "update"])
    _run(["apt-get", "install", "-y", "--no-install-recommends", "git", "ca-certificates"])


def fetch_exact_source(repository: str, revision: str, destination: Path) -> str:
    """``git init`` + ``fetch --depth=1 <repository> <revision>`` + detached checkout,
    exactly as the amaru build driver obtains its source; returns the checked-out
    revision and refuses anything but the requested one."""
    destination.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q", str(destination)])
    _run(["git", "-C", str(destination), "fetch", "-q", "--depth=1", repository, revision])
    _run(["git", "-C", str(destination), "checkout", "-q", "--detach", "FETCH_HEAD"])
    actual = _run(["git", "-C", str(destination), "rev-parse", "HEAD"])
    if actual != revision:
        raise PortCheckError(f"fetched revision {actual} differs from requested {revision}")
    return actual


def check_patches(source: Path, patches_root: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Strict ``git apply --check --whitespace=error-all`` per patch, the same
    check the build drivers use. Dependency-rooted patches (cardano) apply to
    pinned archives that are identical for every node revision: ``inherited``."""
    results: list[dict[str, str]] = []
    for item in manifest["patches"]:
        path = str(item["path"])
        if str(item.get("root", "")).startswith("dependency:"):
            results.append({"path": path, "status": "inherited", "detail": ""})
            continue
        completed = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", str(patches_root / path)],
            cwd=source, text=True, capture_output=True,
        )
        if completed.returncode == 0:
            results.append({"path": path, "status": "ok", "detail": ""})
        else:
            results.append({"path": path, "status": "failed",
                            "detail": (completed.stderr or completed.stdout).strip()[-2048:]})
    return results


def collect_preimages(source: Path, manifest: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Digest every preimage path at the new revision (inherited patches keep the
    base digests). Returns (path -> sha256, paths whose digest differs from base)."""
    preimages: dict[str, str] = {}
    changed: set[str] = set()
    for item in manifest["patches"]:
        inherited = str(item.get("root", "")).startswith("dependency:")
        for preimage in item.get("preimages", []):
            path = str(preimage["path"])
            if inherited:
                preimages[path] = str(preimage.get("sha256", ""))
                continue
            file = source / path
            if not file.is_file():
                raise PortCheckError(f"preimage path missing: {path}")
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            preimages[path] = digest
            if digest != preimage.get("sha256"):
                changed.add(path)
    return preimages, sorted(changed)


def check_cardano_toolchain(source: Path, manifest: dict[str, Any]) -> dict[str, str]:
    """The new revision's CI matrix must name the recipe's GHC series (the same
    rule the Cardano build driver applies before compiling)."""
    toolchain = manifest.get("toolchain")
    ghc = str(toolchain.get("ghc", "")) if isinstance(toolchain, dict) else ""
    series = ".".join(ghc.split(".")[:2]) if ghc else ""
    if not series:
        return {"status": "failed", "ghc_series": "", "detail": "recipe declares no GHC version"}
    workflow = source / ".github" / "workflows" / "haskell.yml"
    if not workflow.is_file():
        return {"status": "failed", "ghc_series": series, "detail": "no .github/workflows/haskell.yml at this revision"}
    pattern = r"(?m)^\s*ghc:\s*\[[^\n]*[\"']" + re.escape(series) + r"(?:\.\d+)?[\"']"
    if re.search(pattern, workflow.read_text(encoding="utf-8", errors="replace")):
        return {"status": "ok", "ghc_series": series, "detail": ""}
    return {"status": "failed", "ghc_series": series,
            "detail": f"GHC series {series} is not declared in .github/workflows/haskell.yml"}


def port(work: Path) -> dict[str, Any]:
    work = Path(work)
    output = work / "output"
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1, "status": "failed", "implementation": None, "source_revision": None,
        "base_source_revision": None, "base_patch_set_sha256": None, "measurement_revision": None,
        "checks": [], "preimages": {}, "preimages_changed": [], "toolchain_check": None, "failure": None,
    }
    try:
        request = json.loads((work / "request.json").read_text(encoding="utf-8"))
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        result.update(
            implementation=request["implementation"], source_revision=request["source_revision"],
            base_source_revision=manifest["source"]["revision"],
            base_patch_set_sha256=manifest.get("patch_set_sha256"),
            measurement_revision=manifest.get("measurement_revision", "v1"),
        )
        ensure_git()
        source = work / "source"
        fetch_exact_source(str(request["repository"]), str(request["source_revision"]), source)
        result["checks"] = check_patches(source, work, manifest)
        failed = [check for check in result["checks"] if check["status"] == "failed"]
        if failed:
            raise PortCheckError(f"patch did not apply: {failed[0]['path']}: {failed[0]['detail'][:500]}")
        preimages, changed = collect_preimages(source, manifest)
        result["preimages"] = preimages
        result["preimages_changed"] = changed
        if request["implementation"] == "cardano-node":
            result["toolchain_check"] = check_cardano_toolchain(source, manifest)
            if result["toolchain_check"]["status"] != "ok":
                raise PortCheckError(result["toolchain_check"]["detail"])
        result["status"] = "ported"
    except (PortCheckError, OSError, ValueError, KeyError, TypeError) as exc:
        result["status"] = "failed"
        result["failure"] = str(exc)[:2048] or exc.__class__.__name__
    (output / "port-result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="/work")
    arguments = parser.parse_args(argv)
    result = port(Path(arguments.work))
    print(json.dumps({"status": result["status"], "failure": result["failure"]}))
    return 0 if result["implementation"] is not None else 2


if __name__ == "__main__":
    sys.exit(main())
