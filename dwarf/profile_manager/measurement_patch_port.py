"""Auto-ported ("derived") measurement patch manifests.

A committed manifest under ``dwarf/targets/<impl>/measurement-patches*/<rev>/``
is a hand-verified recipe for one exact source revision. When a developer
builds a revision that has none, DWARF can *derive* a manifest for it: the
newest committed patch series, re-pinned to the new revision's file
preimages after the patches were checked to apply. A derived manifest is
provisional — ``auto_ported: true``, ``validation.status``
``auto-ported-candidate`` — until a real run with the patched measurement
profile shows data on the patched taps (``auto-ported-proven``).

Derived manifests are never written into the repository by code. They live in
the runtime state (``derived-measurement-patches/<impl>/<rev>/``), and the
only path into ``dwarf/targets/`` is a human reviewing an exported bundle.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


class PortError(ValueError):
    """A derived recipe could not be selected, built or stored."""


IMPLEMENTATIONS = ("amaru", "cardano-node")
REPOSITORIES = {
    "amaru": "https://github.com/pragma-org/amaru.git",
    "cardano-node": "https://github.com/IntersectMBO/cardano-node.git",
}
_REVISION = re.compile(r"[0-9a-f]{40}")
_TARGETS = Path(__file__).resolve().parents[1] / "targets"


def select_base_recipe(implementation: str, *, targets_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """The newest committed patch series for ``implementation``: (manifest path, manifest)."""
    if implementation not in IMPLEMENTATIONS:
        raise PortError(f"unknown implementation: {implementation!r}")
    from profile_manager.antithesis_artifacts import recipe_rank

    root = Path(targets_root) if targets_root is not None else _TARGETS
    candidates: list[tuple[tuple[int, str, str], Path, dict[str, Any]]] = []
    for path in sorted((root / implementation).glob("measurement-patches*/*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        release = str((manifest.get("source") or {}).get("release", ""))
        candidates.append(((recipe_rank(manifest), release, str(path)), path, manifest))
    if not candidates:
        raise PortError(f"No committed patch series available to port for {implementation}")
    _, path, manifest = max(candidates, key=lambda item: item[0])
    return path, manifest


def derive_manifest(
    base: Mapping[str, Any], *, source_revision: str, release: str,
    preimages: Mapping[str, str], base_recipe_dir: str,
) -> dict[str, Any]:
    """``base`` re-pinned to ``source_revision``: same patches and patch-set digest,
    new preimage digests, marked auto-ported. ``base`` is not modified."""
    if not _REVISION.fullmatch(str(source_revision)):
        raise PortError("source revision must be a 40-character lowercase git revision")
    derived = copy.deepcopy(dict(base))
    source = dict(derived.get("source") or {})
    source["revision"] = source_revision
    source["release"] = release
    source.pop("tag", None)
    derived["source"] = source
    for item in derived.get("patches", []):
        for preimage in item.get("preimages", []):
            path = preimage.get("path")
            if path not in preimages:
                raise PortError(f"preimage missing for {path}")
            preimage["sha256"] = preimages[path]
    derived["auto_ported"] = True
    derived["derived_from"] = {
        "source_revision": base["source"]["revision"],
        "measurement_revision": base.get("measurement_revision", "v1"),
        "patch_set_sha256": base["patch_set_sha256"],
        "recipe_dir": base_recipe_dir,
    }
    validation = dict(derived.get("validation") or {})
    validation.update({
        "ported_from_source_revision": base["source"]["revision"],
        "status": "auto-ported-candidate",
        "compiled": False,
        "runtime_qualified": False,
    })
    derived["validation"] = validation
    return derived


def derived_recipe_root() -> Path:
    from profile_manager.config import storage_roots

    return Path(storage_roots().state) / "derived-measurement-patches"


def derived_manifest_path(implementation: str, source_revision: str) -> Path:
    return derived_recipe_root() / implementation / source_revision / "manifest.json"


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def save_derived_recipe(implementation: str, source_revision: str, manifest: Mapping[str, Any], port_result: Mapping[str, Any]) -> Path:
    """Persist a derived manifest and its port result; returns the recipe directory."""
    if implementation not in IMPLEMENTATIONS or not _REVISION.fullmatch(str(source_revision)):
        raise PortError("invalid derived recipe identity")
    if (not isinstance(manifest, Mapping) or manifest.get("auto_ported") is not True
            or (manifest.get("source") or {}).get("revision") != source_revision):
        raise PortError("only an auto-ported manifest for this exact revision can be saved")
    path = derived_manifest_path(implementation, source_revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, dict(manifest))
    _write_json(path.parent / "port-result.json", dict(port_result or {}))
    return path.parent


def load_derived_manifest(implementation: str, source_revision: str) -> dict[str, Any] | None:
    path = derived_manifest_path(implementation, source_revision)
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest if isinstance(manifest, dict) and manifest.get("auto_ported") is True else None


_README_FIRST_LINE = "Auto-ported DWARF measurement patch set — review before committing."


def export_derived_recipe(implementation: str, source_revision: str) -> dict[str, Any]:
    """Bundle a persisted derived recipe (manifest, port result, the base patch
    files it re-pins, destination, README) as a tar.gz for human review.
    Nothing is written under ``dwarf/targets/`` — that step is the reviewer's."""
    import hashlib
    import tarfile
    from profile_manager.config import storage_roots

    manifest = load_derived_manifest(implementation, source_revision)
    if manifest is None:
        raise PortError(f"No auto-ported recipe for {implementation} {source_revision[:12]}")
    recipe_dir = derived_manifest_path(implementation, source_revision).parent
    base_dir = _TARGETS / implementation / str(manifest["derived_from"]["recipe_dir"])
    destination = (f"dwarf/targets/{implementation}/measurement-patches-"
                   f"{manifest.get('measurement_revision', 'v1')}/{source_revision}/")
    validation = manifest.get("validation") or {}
    if validation.get("status") == "auto-ported-proven":
        proof = f"Proven by run {validation.get('proven_by_run')}: patched-only taps carried data."
    else:
        proof = "Not yet proven by a run."
    readme = "\n".join([
        _README_FIRST_LINE, proof, "",
        "1. Verify: PYTHONPATH=dwarf python3 -c 'from scripts.build_" + ("amaru" if implementation == "amaru" else "cardano")
        + "_measurement_target import verify_patch_set' against this manifest.json and patches/.",
        "2. Build the instrumented variant for this revision from the dashboard (Versions → Download & build).",
        "3. Run a measured scenario with the patched measurement profile and confirm the patched-only taps carry data.",
        "4. Copy manifest.json and patches/* to " + destination + " and remove the auto_ported and derived_from keys only after review.",
        "",
    ])
    members: list[tuple[str, bytes]] = [
        ("manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"),
        ("port-result.json", (recipe_dir / "port-result.json").read_bytes() if (recipe_dir / "port-result.json").is_file() else b"{}\n"),
    ]
    for item in manifest.get("patches", []):
        name = str(item["path"])
        file = base_dir / name
        if not file.is_file():
            raise PortError(f"base patch missing: {name}")
        content = file.read_bytes()
        if hashlib.sha256(content).hexdigest() != item.get("sha256"):
            raise PortError(f"base patch digest mismatch: {name}")
        members.append(("patches/" + name, content))
    members.append(("DESTINATION.txt", (destination + "\n").encode("utf-8")))
    members.append(("README.txt", readme.encode("utf-8")))
    bundles = Path(storage_roots().bundles)
    bundles.mkdir(parents=True, exist_ok=True)
    target = bundles / f"derived-recipe-{implementation}-{source_revision[:12]}.tar.gz"
    temporary = target.with_name(target.name + ".tmp")
    with tarfile.open(temporary, "w:gz") as tar:
        for name, content in members:
            import io
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(content))
    os.replace(temporary, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"path": str(target), "sha256": "sha256:" + digest, "destination": destination,
            "members": [name for name, _ in members]}
