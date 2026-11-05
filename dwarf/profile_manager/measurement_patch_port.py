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
from typing import Any, Mapping, Sequence


class PortError(ValueError):
    """A derived recipe could not be selected, built or stored."""


IMPLEMENTATIONS = ("amaru", "cardano-node")
REPOSITORIES = {
    "amaru": "https://github.com/pragma-org/amaru.git",
    "cardano-node": "https://github.com/IntersectMBO/cardano-node.git",
}
_REVISION = re.compile(r"[0-9a-f]{40}")
_TARGETS = Path(__file__).resolve().parents[1] / "targets"


def _release_key(release: str) -> tuple:
    """Version-aware ordering of a release label: each dot-part compares by its
    leading integer, then its suffix (``30-gd3a6dafc`` < ``20261029``); a part
    with no leading integer sorts below every numeric one."""
    key = []
    for part in str(release).split("."):
        match = re.match(r"(\d+)(.*)", part)
        key.append((1, int(match.group(1)), match.group(2)) if match else (0, 0, part))
    return tuple(key)


def select_base_recipes(implementation: str, *, targets_root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """Every committed patch series for ``implementation`` in the order a port
    should try them: nearest base first — newest base *release* first, and for
    the same base the highest series rank. (The newest series is not the
    nearest base: a ``-v4`` series may only exist for an old revision while an
    adjacent revision carries a ``-v3`` recipe that applies cleanly.)"""
    if implementation not in IMPLEMENTATIONS:
        raise PortError(f"unknown implementation: {implementation!r}")
    from profile_manager.antithesis_artifacts import recipe_rank

    root = Path(targets_root) if targets_root is not None else _TARGETS
    candidates: list[tuple[tuple, Path, dict[str, Any]]] = []
    for path in sorted((root / implementation).glob("measurement-patches*/*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        release = str((manifest.get("source") or {}).get("release", ""))
        candidates.append(((_release_key(release), recipe_rank(manifest), str(path)), path, manifest))
    if not candidates:
        raise PortError(f"No committed patch series available to port for {implementation}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(path, manifest) for _, path, manifest in candidates]


def select_base_recipe(implementation: str, *, targets_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """The first candidate a port tries: (manifest path, manifest)."""
    return select_base_recipes(implementation, targets_root=targets_root)[0]


def derive_manifest(
    base: Mapping[str, Any], *, source_revision: str, release: str,
    preimages: Mapping[str, str] | Sequence[Mapping[str, Any]], base_recipe_dir: str,
) -> dict[str, Any]:
    """``base`` re-pinned to ``source_revision``: same patches and patch-set digest,
    new preimage digests, marked auto-ported. ``base`` is not modified.

    ``preimages`` is the port driver's ``patch_preimages`` — one
    ``{"path": <patch>, "preimages": {<file>: <sha256>}}`` per patch — so a file
    several patches touch keeps a distinct digest per patch (F24). A flat
    ``{<file>: <sha256>}`` is accepted for single-patch series."""
    if not _REVISION.fullmatch(str(source_revision)):
        raise PortError("source revision must be a 40-character lowercase git revision")
    if isinstance(preimages, Mapping):
        per_patch: dict[str, Mapping[str, str]] | None = None
        flat: Mapping[str, str] = preimages
    else:
        per_patch = {str(entry.get("path")): dict(entry.get("preimages") or {}) for entry in preimages}
        flat = {}
    derived = copy.deepcopy(dict(base))
    source = dict(derived.get("source") or {})
    source["revision"] = source_revision
    source["release"] = release
    source.pop("tag", None)
    derived["source"] = source
    for item in derived.get("patches", []):
        digests = per_patch.get(str(item.get("path")), {}) if per_patch is not None else flat
        for preimage in item.get("preimages", []):
            path = preimage.get("path")
            if path not in digests:
                raise PortError(f"preimage missing for {path}")
            preimage["sha256"] = digests[path]
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
    """Derived recipes live beside the operator version ledger (``<version-catalog
    state>/version-catalog/derived-measurement-patches``). That directory is the
    one piece of dashboard state the controller also mounts, so the process that
    plans a topology can resolve a registration's auto-ported recipe (F22)."""
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

    return Path(OperatorVersionDecisionLedger().root) / "derived-measurement-patches"


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
    from profile_manager import antithesis_cardano_bridge as bridge
    for item in manifest.get("patches", []):
        name = str(item["path"])
        if Path(name).name == bridge.PATCH_NAME and item.get("sha256") == bridge.CORRECTION_HASH:
            # The cardano composite's import correction lives in code, not on disk
            # (exactly as the build's _copy_recipe materialises it).
            content = bridge.CORRECTION.encode("utf-8")
        else:
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


def patched_only_concepts(implementation: str, *, catalog_index: dict[str, Any] | None = None) -> list[str]:
    """Concepts the catalog binds for mode ``patched`` but not for ``stock``: the
    taps only an instrumented build can carry, hence the proof surface for a port."""
    from profile_manager.data.measurement_compare import _catalog_index
    from profile_manager.tap_coverage import _bindings_for

    index = catalog_index or _catalog_index()
    patched = {concept for concept, _, _ in _bindings_for(index, implementation, "patched")}
    stock = {concept for concept, _, _ in _bindings_for(index, implementation, "stock")}
    return sorted(patched - stock)


def proof_from_report(report: Mapping[str, Any], implementation: str) -> dict[str, Any]:
    """Did this run carry data on the patched-only taps? Proven only for a run
    whose target identity says ``patched``, with at least one patched-only
    concept covered and none *missing* — present in the report without a value —
    the same rule DWARF's tap-coverage gate applies. Patched-only concepts whose
    raw ids never appear in the report are listed as ``absent`` (a patch series
    need not emit every bound concept on every revision) and do not block proof."""
    from profile_manager.tap_coverage import coverage_report, report_identity

    only = patched_only_concepts(implementation)
    try:
        _, mode = report_identity(dict(report))
    except ValueError:
        mode = ""
    metrics = report.get("measurements") if isinstance(report, Mapping) else None
    result = coverage_report(metrics if isinstance(metrics, dict) else {}, implementation, "patched")
    covered = sorted(concept for concept in result["covered"] if concept in only)
    excepted = sorted(row["concept"] for row in result["excepted"] if row["concept"] in only)
    missing = sorted(row["concept"] for row in result["missing"] if row["concept"] in only)
    absent = sorted(set(only) - set(covered) - set(excepted) - set(missing))
    return {"proven": mode == "patched" and not missing and bool(covered),
            "patched_only": only, "covered": covered, "excepted": excepted, "missing": missing, "absent": absent}


def mark_proven(implementation: str, source_revision: str, *, run_id: str, proof: Mapping[str, Any]) -> dict[str, Any]:
    """Promote a persisted derived recipe to ``auto-ported-proven``. The first
    proving run is kept; later runs do not overwrite it."""
    manifest = load_derived_manifest(implementation, source_revision)
    if manifest is None:
        raise PortError(f"No auto-ported recipe for {implementation} {source_revision[:12]}")
    validation = dict(manifest.get("validation") or {})
    if validation.get("status") != "auto-ported-proven":
        validation.update({
            "status": "auto-ported-proven", "runtime_qualified": True, "proven_by_run": str(run_id),
            "proof": {"covered": list(proof.get("covered") or []), "excepted": list(proof.get("excepted") or [])},
        })
        manifest["validation"] = validation
        _write_json(derived_manifest_path(implementation, source_revision), manifest)
    return manifest


def record_run_proof(run_dir: Path, run_id: str, exit_status: object) -> dict[str, Any] | None:
    """Prove an auto-ported recipe from one retained run, if the run can.

    Returns the proof when the run's target is an auto-ported instrumented
    registration, the run passed, and its patched-only taps carried data;
    ``None`` otherwise. Marks the derived recipe proven (first run wins) and
    retains one ``auto_port_proofs`` ledger row per proving run. Idempotent:
    calling it again for the same run changes nothing. Advisory: it raises
    nothing a caller must handle beyond :class:`PortError` for a missing recipe
    — callers that cannot write the version-catalog state (the controller's
    read-only view) simply fail to persist and the dashboard records it later.
    """
    if exit_status != "pass":
        return None
    run_dir = Path(run_dir)
    report_path = run_dir / "measurements" / "report.json"
    if not report_path.is_file():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    identity = report.get("target_identity") if isinstance(report, dict) else None
    registration_id = identity.get("operator_registration_id") if isinstance(identity, dict) else None
    if not isinstance(registration_id, str) or not registration_id:
        return None
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
    from profile_manager.version_discovery import load_operator_registration

    registration = load_operator_registration(registration_id)["registration"]
    build = registration.get("source_build") or {}
    if build.get("auto_ported") is not True:
        return None
    implementation = str(registration.get("implementation") or "")
    revision = str((registration.get("provenance") or {}).get("source_revision") or "")
    proof = proof_from_report(report, implementation)
    if not proof["proven"]:
        return proof
    mark_proven(implementation, revision, run_id=run_id, proof=proof)
    ledger = OperatorVersionDecisionLedger()
    state = ledger.read()
    if any(row.get("run_id") == run_id for row in state.get("auto_port_proofs", [])):
        return proof
    from datetime import datetime, timezone
    import hashlib

    ledger.append_auto_port_proof({
        "proof_id": "auto-port-proof-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32],
        "registration_id": registration_id, "implementation": implementation, "source_revision": revision,
        "run_id": run_id, "covered": proof["covered"], "excepted": proof["excepted"],
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }, expected_revision=state["revision"])
    return proof
