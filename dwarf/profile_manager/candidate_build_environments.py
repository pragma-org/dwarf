"""Validate trusted retained build inputs without running or acquiring anything.

Only the fixed DWARF-owned catalog path is consulted. These contracts describe
prerequisites for a future backend; validating a manifest is never an observed
build, an observed toolchain probe, or artifact/runtime readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

from jsonschema import Draft202012Validator


_SCHEMAS = ("candidate-build-environment", "candidate-dependency-closure")
_RECIPE_FIELDS = (
    "variant", "commands", "command_workdirs", "command_environments", "environment",
    "toolchain", "compiler_flags", "binary", "binary_query", "patch_set_digest",
    "harness_digest", "generated_inputs", "staging_directory", "required_outputs",
    "required_runtime_outputs", "coverage_scope", "coverage_envelope_fields",
)


class BuildEnvironmentError(ValueError):
    """A retained trusted build input is missing, unsafe or inconsistent."""


@dataclass(frozen=True)
class BuildEnvironmentResolution:
    manifest_digest: str | None = None
    closure_digest: str | None = None
    build_image: str | None = None
    runtime_base_image: str | None = None
    inputs: tuple[tuple[str, str], ...] = ()
    blockers: tuple[str, ...] = ()
    # Immutable bytes-equivalent data from the same verified manifest read.
    manifest_json: str | None = None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def environment_recipe_digest(plan):
    """Bind effective trusted commands, generated projects and output contracts.

    File input identities, including the validation code, are already frozen in
    plan.recipe_digest. Keep this binding independent of environment inputs to
    avoid a manifest/plan digest cycle.
    """
    return _digest({"recipe_digest": plan.recipe_digest,
                    **{name: getattr(plan, name) for name in _RECIPE_FIELDS}})


def _path(root, relative):
    value = PurePosixPath(relative)
    if (value.is_absolute() or not value.parts or str(value) != relative
            or ".." in value.parts or "\\" in relative or "\x00" in relative):
        raise BuildEnvironmentError(f"unsafe trusted build input path: {relative}")
    path = root
    for component in value.parts:
        path = path / component
        if path.is_symlink():
            raise BuildEnvironmentError(f"symlink in trusted build input: {relative}")
    return path


def _retained(root, relative, expected=None, *, document=False, destination=None,
              max_bytes=None):
    path = _path(root, relative)
    try:
        digest = hashlib.sha256()
        payload = bytearray()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise BuildEnvironmentError(f"trusted build input is not regular: {relative}")
            if max_bytes is not None and before.st_size > max_bytes:
                raise BuildEnvironmentError(f"trusted build input exceeds copy limit: {relative}")
            if document and before.st_size > 1024 * 1024:
                raise BuildEnvironmentError(f"trusted build document exceeds limit: {relative}")
            copied = None
            try:
                if destination is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    copied = destination.open("xb")
                size = 0
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if max_bytes is not None and size > max_bytes:
                        raise BuildEnvironmentError(f"trusted build input exceeds copy limit: {relative}")
                    digest.update(block)
                    if copied is not None:
                        copied.write(block)
                    if document:
                        payload.extend(block)
                        if len(payload) > 1024 * 1024:
                            raise BuildEnvironmentError(f"trusted build document exceeds limit: {relative}")
            finally:
                if copied is not None:
                    copied.close()
                    destination.chmod(0o400)
            after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, key) != getattr(after, key) for key in fields):
                raise BuildEnvironmentError(f"trusted build input changed during read: {relative}")
        actual = digest.hexdigest()
        if expected is not None and actual != expected:
            raise BuildEnvironmentError(f"trusted build input digest mismatch: {relative}")
        # Parse precisely the bytes hashed above, never a second pathname read.
        body = json.loads(payload) if document else None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildEnvironmentError(f"missing or unreadable trusted build input: {relative}") from exc
    return actual, body


def _validate(document, root, name):
    relative = f"dwarf/spec/v1/{name}.schema.json"
    digest, schema = _retained(root, relative, document=True)
    errors = list(Draft202012Validator(schema).iter_errors(document))
    if errors:
        error = errors[0]
        raise BuildEnvironmentError(f"{name} schema violation at {list(error.absolute_path)}: {error.message}")
    return relative, digest


def resolve_build_environment(plan, *, trusted_root):
    """Read a checked-in closure, or identify its exact missing catalog path.

    No candidate path, candidate metadata, environment variable, network client
    or subprocess can select/resolve the builder. Empty catalogs remain blocked.
    Retained offline evidence is checked as data; live verification is separate.
    """
    root = Path(trusted_root).resolve()
    relative = (f"dwarf/build-environments/v1/{plan.candidate.implementation}/"
                f"{plan.source_tree_digest}/{plan.variant}.json")
    if not _path(root, relative).exists():
        return BuildEnvironmentResolution(blockers=(f"trusted-build-environment-missing:{relative}",))
    manifest_digest, environment = _retained(root, relative, document=True)
    inputs = [(relative, manifest_digest), _validate(environment, root, _SCHEMAS[0])]
    recipe = environment_recipe_digest(plan)
    compiler = "rust" if plan.candidate.implementation == "amaru" else "ghc"
    if compiler not in dict(plan.toolchain):
        raise BuildEnvironmentError("trusted environment requires an exact source compiler identity")
    for key, expected in (("implementation", plan.candidate.implementation), ("variant", plan.variant),
                          ("source_tree_digest", plan.source_tree_digest), ("recipe_digest", recipe),
                          ("toolchain", dict(plan.toolchain))):
        if environment[key] != expected:
            raise BuildEnvironmentError(f"trusted build environment {key} mismatch")
    probes = [probe["tool"] for probe in environment["toolchain_probes"]]
    if len(probes) != len(set(probes)) or set(probes) != set(dict(plan.toolchain)):
        raise BuildEnvironmentError("trusted build environment toolchain probe set mismatch")
    if plan.base_image is not None and environment["runtime_base_image"] != plan.base_image:
        raise BuildEnvironmentError("trusted build environment runtime base differs from trusted patch")
    packaging = environment["packaging"]
    method = "copy-static-elf-v1" if plan.candidate.implementation == "amaru" else "copy-elf-runtime-closure-v1"
    if packaging["method"] != method or packaging["entrypoint"] != [packaging["executable_path"]]:
        raise BuildEnvironmentError("trusted runtime packaging contract mismatch")
    reference = environment["dependency_closure"]
    closure_digest, closure = _retained(root, reference["path"], reference["sha256"], document=True)
    inputs.extend(((reference["path"], closure_digest), _validate(closure, root, _SCHEMAS[1])))
    for key, expected in (("source_tree_digest", plan.source_tree_digest), ("recipe_digest", recipe),
                          ("build_image", environment["build_image"]),
                          ("format", "cargo-vendor-v1" if plan.candidate.implementation == "amaru" else "cabal-store-v1")):
        if closure[key] != expected:
            raise BuildEnvironmentError(f"trusted dependency closure {key} mismatch")
    locks = {row["path"]: row["sha256"] for row in closure["locks"]}
    expected_locks = {row.path: row.sha256 for row in plan.inputs if row.origin == "source"
                      and row.path in {"Cargo.lock", "cabal.project", "cabal.project.freeze"}}
    required_lock = "Cargo.lock" if plan.candidate.implementation == "amaru" else "cabal.project.freeze"
    if locks != expected_locks or len(locks) != len(closure["locks"]) or required_lock not in locks:
        raise BuildEnvironmentError("trusted dependency closure source lock identity mismatch")
    roles = {row["role"] for row in closure["files"]}
    required_roles = {"package-manager-config", "solver-plan", "native-dependencies"}
    required_roles |= ({"cargo-vendor-archive"} if plan.candidate.implementation == "amaru" else
                       {"cabal-store-archive", "repository-index", "runtime-root"})
    if plan.variant == "compiler-coverage":
        required_roles.add("coverage-tools")
    if missing := required_roles - roles:
        raise BuildEnvironmentError(f"trusted dependency closure missing retained roles: {','.join(sorted(missing))}")
    paths = [row["path"] for row in closure["files"]]
    if len(paths) != len(set(paths)):
        raise BuildEnvironmentError("duplicate trusted dependency closure paths")
    for dependency in (row for row in plan.inputs if row.origin == "dependency"):
        if not any(row["role"] == "dependency-archive" and PurePosixPath(row["path"]).name == dependency.path
                   and row["sha256"] == dependency.sha256 for row in closure["files"]):
            raise BuildEnvironmentError(f"trusted dependency closure missing archive: {dependency.path}")
    for row in closure["files"]:
        actual, _ = _retained(root, row["path"], row["sha256"])
        inputs.append((row["path"], actual))
    verification = closure["offline_verification"]
    closure_inputs = {key: value for key, value in closure.items() if key != "offline_verification"}
    if (verification["inputs_sha256"] != _digest(closure_inputs)
            or verification["observed_toolchain"] != dict(plan.toolchain)):
        raise BuildEnvironmentError("trusted offline verification identity/toolchain mismatch")
    log = verification["log"]
    actual, _ = _retained(root, log["path"], log["sha256"])
    inputs.append((log["path"], actual))
    return BuildEnvironmentResolution(manifest_digest, closure_digest, environment["build_image"],
                                      environment["runtime_base_image"], tuple(inputs),
                                      manifest_json=json.dumps(environment, sort_keys=True, separators=(",", ":")))
