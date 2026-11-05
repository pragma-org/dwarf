#!/usr/bin/env python3
"""Port-check driver: can the newest committed patch series be applied to a new
source revision?

Runs inside the builder container as ``python3 /work/port.py`` with only the
job directory mounted (stdlib only). Input: ``/work/request.json``
(``implementation``, ``source_revision``, ``repository``, optional
``candidates``) and the candidate base recipes — ``/work/candidates/<i>/``
(manifest.json + patch files), tried in order, or a single ``/work/manifest.json``
with its patches. The source is fetched once; the first candidate whose patches
all apply is ported. Output: ``/work/output/port-result.json`` — ``status``
``ported`` or ``failed``, the winning ``candidate_index``, every ``attempts[]``
with its per-patch checks, the new revision's preimage digests and the reason.
It never builds anything and never writes outside ``/work``.
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


# The single source of the CI-matrix rule: the bare GHC series (e.g. '9.6') must be
# quoted inside a `ghc: [...]` list. The controller injects this same template into
# the Cardano compile driver, so the port stage can never say "ok" where the compile
# gate would refuse.
GHC_SERIES_TEMPLATE = r"(?m)^\s*ghc:\s*\[[^\n]*[\"']{series}[\"']"


def ghc_series_pattern(series: str) -> str:
    return GHC_SERIES_TEMPLATE.replace("{series}", re.escape(series))


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
    if re.search(ghc_series_pattern(series), workflow.read_text(encoding="utf-8", errors="replace")):
        return {"status": "ok", "ghc_series": series, "detail": ""}
    return {"status": "failed", "ghc_series": series,
            "detail": f"GHC series {series} is not declared in .github/workflows/haskell.yml"}


def _candidates(work: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Candidate recipe directories in try order: candidates/<i>/ or the job root."""
    root = work / "candidates"
    if root.is_dir():
        dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: int(d.name))
        return [(d, json.loads((d / "manifest.json").read_text(encoding="utf-8"))) for d in dirs]
    return [(work, json.loads((work / "manifest.json").read_text(encoding="utf-8")))]


def port(work: Path) -> dict[str, Any]:
    work = Path(work)
    output = work / "output"
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 2, "status": "failed", "implementation": None, "source_revision": None,
        "candidate_index": None, "attempts": [],
        "base_source_revision": None, "base_patch_set_sha256": None, "measurement_revision": None,
        "checks": [], "preimages": {}, "preimages_changed": [], "toolchain_check": None, "failure": None,
    }
    try:
        request = json.loads((work / "request.json").read_text(encoding="utf-8"))
        result.update(implementation=request["implementation"], source_revision=request["source_revision"])
        candidates = _candidates(work)
        if not candidates:
            raise PortCheckError("no candidate patch series supplied")
        ensure_git()
        source = work / "source"
        fetch_exact_source(str(request["repository"]), str(request["source_revision"]), source)
        chosen = None
        for index, (directory, manifest) in enumerate(candidates):
            checks = check_patches(source, directory, manifest)
            attempt = {"index": index, "base_source_revision": manifest["source"]["revision"],
                       "base_release": str(manifest.get("source", {}).get("release", "")),
                       "measurement_revision": manifest.get("measurement_revision", "v1"),
                       "patch_set_sha256": manifest.get("patch_set_sha256"),
                       "status": "failed" if any(c["status"] == "failed" for c in checks) else "ported",
                       "checks": checks}
            result["attempts"].append(attempt)
            if attempt["status"] == "ported":
                chosen = (index, directory, manifest, checks)
                break
        if chosen is None:
            result["checks"] = result["attempts"][-1]["checks"]   # the last attempt's rejects, for single-candidate readers
            raise PortCheckError(f"no committed patch series applies to {request['source_revision'][:12]} "
                                 f"({len(candidates)} tried): " + "; ".join(
                                     f"{a['base_release'] or a['base_source_revision'][:12]}/{a['measurement_revision']}: "
                                     + next((c["detail"].splitlines()[0] if c["detail"] else c["path"] for c in a["checks"] if c["status"] == "failed"), "")
                                     for a in result["attempts"])[:1500])
        index, directory, manifest, checks = chosen
        result.update(
            candidate_index=index, checks=checks,
            base_source_revision=manifest["source"]["revision"],
            base_patch_set_sha256=manifest.get("patch_set_sha256"),
            measurement_revision=manifest.get("measurement_revision", "v1"),
        )
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
