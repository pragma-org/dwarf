"""Declare reproducible candidate build lanes without executing candidate code.

These are unresolved build plans, not executors or readiness records. Existing
exact-release builders remain unchanged: their revision checks must not be
bypassed to compile an intake tree. A future executor must copy and revalidate
the frozen source, isolate its build hooks, resolve every prerequisite, and
retain the output receipt described here before reporting an artifact as built.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import tomllib

from profile_manager.testplease_candidates import (
    CandidateIntakeError, CandidateSnapshot, discover_candidates,
)
from profile_manager.candidate_build_environments import (
    BuildEnvironmentError, resolve_build_environment,
)
from scripts import build_amaru_measurement_target as amaru_patch
from scripts import build_cardano_measurement_target as cardano_patch
from scripts import build_amaru_coverage_target as amaru_coverage
from scripts import build_cardano_coverage_target as cardano_coverage


TRUSTED_ROOT = Path(__file__).resolve().parents[2]
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_RUST = re.compile(r"(?:[0-9]+\.[0-9]+\.[0-9]+|nightly-[0-9]{4}-[0-9]{2}-[0-9]{2})\Z")
_OUTPUTS = ("executable_sha256", "image_sha256", "build_result_sha256")
_ENVELOPE = (
    "source_tree_digest", "repository_commit", "plan_digest", "toolchain",
    "compiler_flags", "harness_digest", "executable_digest", "image_digest",
    "corpus_digest", "raw_profile_digests", "coverage_report_digest",
)
# These are audited asset identities, not accepted candidate-version defaults.
_PATCHES = {
    "amaru": (
        "dwarf/targets/amaru/measurement-patches-nanoseconds-v3/b159172f25a9c389f82f20bca4f15e3032791638/manifest.json",
        "dwarf/targets/amaru/measurement-patches-nanoseconds-v3/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json",
        "dwarf/targets/amaru/measurement-patches-nanoseconds-v2/d3a6dafcced78f5809a96619e883cf04911d2bdc/manifest.json",
    ),
    "cardano-node": (
        "dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json",
    ),
}
_COVERAGE = {
    "amaru": "dwarf/targets/amaru/coverage-targets/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json",
    "cardano-node": "dwarf/targets/cardano-node/coverage-targets/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json",
}


class CandidateBuildError(ValueError):
    """An identity or required trusted build input cannot be established."""


@dataclass(frozen=True)
class BuildInput:
    origin: str  # trusted, source, or dependency (not yet downloaded/verified)
    path: str
    sha256: str


@dataclass(frozen=True)
class CandidateBuildPlan:
    candidate: CandidateSnapshot
    variant: str
    recipe_digest: str
    commands: tuple[tuple[str, ...], ...]
    command_workdirs: tuple[str, ...]
    command_environments: tuple[tuple[tuple[str, str], ...], ...]
    environment: tuple[tuple[str, str], ...]
    toolchain: tuple[tuple[str, str], ...]
    compiler_flags: tuple[str, ...]
    inputs: tuple[BuildInput, ...]
    unresolved: tuple[str, ...]
    binary: str
    # Cabal output paths are resolved by this exact argv in {source}; compiler
    # and build-directory selection must match the corresponding build command.
    binary_query: tuple[str, ...] = ()
    patch_set_digest: str | None = None
    harness_digest: str | None = None
    asset_reference_revision: str | None = None
    base_image: str | None = None
    build_container_image: str | None = None
    build_environment_digest: str | None = None
    dependency_closure_digest: str | None = None
    build_environment_blockers: tuple[str, ...] = ()
    performance_authority: str = "unestablished"
    coverage_scope: str | None = None
    coverage_envelope_fields: tuple[str, ...] = ()
    staging_inputs: tuple[BuildInput, ...] = ()
    staging_directory: str | None = None
    generated_inputs: tuple[tuple[str, str], ...] = ()
    required_runtime_outputs: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = _OUTPUTS
    # Receipt identity must bind all these plan inputs; hashes must be verified
    # against retained artifacts, never copied from candidate-supplied metadata.
    receipt_identity_fields: tuple[str, ...] = (
        "plan_digest", "repository_commit", "source_tree_digest", "variant",
        "recipe_digest", "toolchain", "build_container_image", "base_image",
        "commands", "command_workdirs", "command_environments", "environment",
        "inputs", "patch_set_digest", "harness_digest", "compiler_flags",
        "generated_inputs", "staging_inputs", "staging_directory", "binary", "binary_query",
        "build_environment_digest", "dependency_closure_digest", "build_environment_blockers",
    )
    plan_digest: str = ""

    @property
    def source_tree_digest(self) -> str:
        return self.candidate.source_tree_digest

    @property
    def repository_commit(self) -> str:
        return self.candidate.repository_commit

    @property
    def status(self) -> str:
        return "unresolved"

    @property
    def ready(self) -> bool:
        return False

    @property
    def executable_digest(self) -> None:
        return None

    @property
    def image_digest(self) -> None:
        return None

    @property
    def coverage_envelope_digest(self) -> None:
        return None


def _digest(body: object) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(plan: CandidateBuildPlan) -> str:
    body = asdict(plan)
    body.pop("plan_digest")
    body["candidate"].pop("source_root")
    return _digest(body)


def _input(origin: str, root: Path, relative: str) -> BuildInput:
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise CandidateBuildError(f"missing or unsafe {origin} build input: {relative}")
    return BuildInput(origin, relative, amaru_patch.sha256_file(path))


def _source(candidate: CandidateSnapshot) -> None:
    if not isinstance(candidate, CandidateSnapshot) or not _HEX.fullmatch(candidate.source_tree_digest):
        raise CandidateBuildError("invalid source snapshot identity")
    try:
        expected = candidate.source_root.parents[3]
        records = discover_candidates(expected, repository_commit=candidate.repository_commit)
    except (CandidateIntakeError, IndexError, OSError) as exc:
        raise CandidateBuildError(str(exc)) from exc
    actual = next((item for item in records if item.source_root == candidate.source_root), None)
    if actual != candidate:
        raise CandidateBuildError("frozen source snapshot changed or identity does not match intake")


def _assets(implementation: str):
    """Verify all built-in patches before selecting by content compatibility."""
    helper = amaru_patch if implementation == "amaru" else cardano_patch
    verified = []
    for relative in _PATCHES[implementation]:
        manifest_input = _input("trusted", TRUSTED_ROOT, relative)
        path = TRUSTED_ROOT / relative
        try:
            manifest = helper.load_manifest(path)
        except (RuntimeError, OSError, ValueError) as exc:
            raise CandidateBuildError(f"trusted patch validation failed: {exc}") from exc
        inputs = [manifest_input]
        for row in manifest["patches"]:
            inputs.append(_input("trusted", TRUSTED_ROOT, (path.parent / row["path"]).relative_to(TRUSTED_ROOT).as_posix()))
        try:
            helper.verify_patch_set(path.parent, manifest)
        except (RuntimeError, OSError, ValueError) as exc:
            raise CandidateBuildError(f"trusted patch validation failed: {exc}") from exc
        verified.append((manifest, tuple(inputs)))
    return verified


def _toolchain(candidate: CandidateSnapshot):
    root = candidate.source_root
    unresolved = []
    inputs = []
    if candidate.implementation == "amaru":
        relative = "rust-toolchain.toml"
        channel = None
        if (root / relative).is_file():
            inputs.append(_input("source", root, relative))
            try:
                value = tomllib.loads((root / relative).read_text()).get("toolchain", {}).get("channel")
                if isinstance(value, str) and _RUST.fullmatch(value):
                    channel = value
            except (tomllib.TOMLDecodeError, UnicodeError, AttributeError):
                pass
        if channel is None:
            unresolved.append("exact Rust toolchain is unresolved; mutable channels are not identities")
        tools = (("rust", channel),) if channel else ()
        tools += (("cargo-zigbuild", "0.23.4"), ("zig", "0.15.2"), ("target", "x86_64-unknown-linux-musl"))
    else:
        text = (root / "cabal.project").read_text()
        versions = re.findall(r"(?m)^\s*with-compiler\s*:\s*ghc-([0-9]+\.[0-9]+\.[0-9]+)\s*$", text)
        tools = (("ghc", versions[0]),) if len(versions) == 1 else ()
        if not tools:
            unresolved.append("exact GHC toolchain is unresolved")
        tools += (("cabal", "3.16.0.0"),)
    return tools, tuple(inputs), tuple(unresolved)


def _compatible_patch(candidate, assets):
    for manifest, inputs in assets:
        if candidate.implementation == "amaru":
            try:
                for patch in manifest["patches"]:
                    amaru_patch.verify_preimages(candidate.source_root, patch["preimages"])
            except (amaru_patch.BuildContractError, OSError):
                continue
            return manifest, inputs
        # Cardano patches apply to external dependencies, not cardano-node files.
        # A matching freeze only selects a recipe; archive, solver-plan and
        # dependency-preimage checks remain required before its execution.
        freeze = candidate.source_root / "cabal.project.freeze"
        if not freeze.is_file():
            continue
        text = freeze.read_text()
        if all(re.search(r"(?<![\w.-])(?:any\.)?" + re.escape(row["package"]) +
                         r"\s*==\s*" + re.escape(row["version"]) + r"(?![\w.])", text)
               for row in manifest["dependencies"]):
            return manifest, inputs
    return None, ()


def plan_candidate_builds(candidate: CandidateSnapshot, *, require_patch: bool = False) -> tuple[CandidateBuildPlan, ...]:
    """Declare three lanes from the identical intake snapshot, never run them.

    No source release/version is selected or restricted. ``require_patch`` is a
    fail-closed request for an available content-compatible trusted recipe.
    Without one the patched lane remains explicit and unresolved. Compiler,
    tool and image identities below are requirements, not observed tool probes.
    """
    _source(candidate)
    implementation = candidate.implementation
    assets = _assets(implementation)
    manifest, patch_inputs = _compatible_patch(candidate, assets)
    if require_patch and manifest is None:
        raise CandidateBuildError("no compatible trusted patch for frozen source/dependency inputs")
    tools, tool_inputs, tool_reasons = _toolchain(candidate)
    source_inputs = list(tool_inputs)
    for relative in (("Cargo.toml", "Cargo.lock", "crates/amaru/Cargo.toml") if implementation == "amaru" else
                     ("cabal.project", "cabal.project.freeze", "cardano-node/cardano-node.cabal")):
        if (candidate.source_root / relative).is_file():
            source_inputs.append(_input("source", candidate.source_root, relative))
    lock = "Cargo.lock" if implementation == "amaru" else "cabal.project.freeze"
    reasons = [*tool_reasons]
    if not (candidate.source_root / lock).is_file():
        reasons.append(f"dependency lock input missing: {lock}")
    reasons.extend((
        "observed compiler/tool identities require verification inside the isolated environment",
        "isolated frozen-source copy and candidate build-hook containment require verification",
        "executable, image and build-result artifact digests have not been produced or verified",
    ))
    code = ["dwarf/profile_manager/candidate_builds.py", "dwarf/profile_manager/testplease_candidates.py",
            "dwarf/profile_manager/candidate_build_environments.py",
            "dwarf/spec/v1/candidate-build-environment.schema.json",
            "dwarf/spec/v1/candidate-dependency-closure.schema.json",
            f"dwarf/scripts/build_{'amaru' if implementation == 'amaru' else 'cardano'}_measurement_target.py"]
    trusted_inputs = [_input("trusted", TRUSTED_ROOT, relative) for relative in code]
    tool_map = dict(tools)
    if implementation == "amaru":
        compiler = ("cargo", "+" + tool_map["rust"]) if "rust" in tool_map else ("cargo",)
        stock_command = (*compiler, "zigbuild", "--locked", "--release", "-p", "amaru", "--bin", "amaru", "--target", "x86_64-unknown-linux-musl")
        environment = (("CARGO_INCREMENTAL", "0"), ("CARGO_TERM_COLOR", "never"), ("CARGO_ZIGBUILD_ZIG_COMMAND", "python-zig"))
        binary = "target/x86_64-unknown-linux-musl/release/amaru"
    else:
        compiler = ("-w", "ghc-" + tool_map["ghc"]) if "ghc" in tool_map else ()
        stock_command = ("cabal", "build", "--builddir=dist-newstyle-dwarf-candidate", *compiler, "exe:cardano-node")
        environment = (("LANG", "C.UTF-8"),)
        binary = "cabal:list-bin:exe:cardano-node"
    plans = []
    for variant in ("stock", "patched", "compiler-coverage"):
        inputs = [*trusted_inputs, *source_inputs]
        lane_reasons = list(reasons)
        commands = (stock_command,)
        command_workdirs = ("{source}",)
        command_environments = ((),)
        flags = ()
        lane_tools = tools
        lane_environment = environment
        kwargs = {}
        if implementation == "cardano-node":
            kwargs["binary_query"] = ("cabal", "list-bin", *stock_command[2:])
        if variant == "patched":
            if manifest is None:
                lane_reasons.append("no content-compatible trusted patch; source or dependency preimages require a reviewed patch")
            else:
                inputs.extend(patch_inputs)
                for patch in manifest["patches"]:
                    origin = "source" if implementation == "amaru" else patch["root"]
                    inputs.extend(BuildInput(origin, row["path"], row["sha256"]) for row in patch["preimages"])
                inputs.extend(BuildInput("dependency", row["archive"], row["sha256"]) for row in manifest.get("dependencies", ()))
                kwargs.update(patch_set_digest=manifest["patch_set_sha256"], asset_reference_revision=manifest["source"]["revision"], base_image=manifest["base_image"])
                patch_commands = []
                workdirs = []
                for patch, patch_input in zip(manifest["patches"], patch_inputs[1:]):
                    patch_path = "{trusted}/" + patch_input.path
                    patch_commands.extend((("git", "apply", "--check", "--whitespace=error-all", patch_path),
                                           ("git", "apply", "--whitespace=error-all", patch_path)))
                    workdir = "{source}"
                    if implementation == "cardano-node":
                        workdir += "/vendor/" + patch["root"].removeprefix("dependency:")
                    workdirs.extend((workdir, workdir))
                commands = (*patch_commands, stock_command)
                command_workdirs = (*workdirs, "{source}")
                command_environments = tuple(
                    (("GIT_CEILING_DIRECTORIES", "{source}/vendor"),) if implementation == "cardano-node" else ()
                    for _ in patch_commands
                ) + ((),)
                lane_reasons.append("selected patch requires exact application checks in the isolated source/dependency copy")
                if implementation == "cardano-node":
                    lane_reasons.append("dependency solver plan, archive hashes and patch preimages require verification")
                    project = "packages: " + " ".join(
                        f"vendor/{row['package']}-{row['version']}" for row in manifest["dependencies"]
                    ) + "\n"
                    kwargs["generated_inputs"] = (("cabal.project.local", project),)
                expected = manifest["toolchain"]
                if (implementation == "amaru" and tool_map.get("rust") != expected) or (implementation == "cardano-node" and tool_map.get("ghc") != expected["ghc"]):
                    if require_patch:
                        raise CandidateBuildError("required patch is incompatible with source toolchain")
                    lane_reasons.append("source toolchain is incompatible with selected patch recipe reference toolchain")
        if variant == "compiler-coverage":
            coverage_path = _COVERAGE[implementation]
            path = TRUSTED_ROOT / coverage_path
            helper = amaru_coverage if implementation == "amaru" else cardano_coverage
            try:
                inputs.append(_input("trusted", TRUSTED_ROOT, coverage_path))
                coverage = helper.load_manifest(path)
                harness_inputs = tuple(_input("trusted", TRUSTED_ROOT, (path.parent / row["path"]).relative_to(TRUSTED_ROOT).as_posix())
                                       for row in coverage["harness_files"])
                verified = helper.verify_harness_set(path.parent, coverage)
            except (RuntimeError, OSError, ValueError) as exc:
                raise CandidateBuildError(f"trusted coverage validation failed: {exc}") from exc
            inputs.append(_input("trusted", TRUSTED_ROOT, f"dwarf/scripts/build_{'amaru' if implementation == 'amaru' else 'cardano'}_coverage_target.py"))
            inputs.extend(harness_inputs)
            inputs.extend(BuildInput("dependency", row["archive"], row["sha256"]) for row in coverage.get("dependencies", ()))
            kwargs.update(harness_digest=verified["coverage_harness_sha256"], asset_reference_revision=coverage["source"]["revision"],
                          performance_authority="non-authoritative", coverage_envelope_fields=_ENVELOPE,
                          staging_inputs=harness_inputs, required_runtime_outputs=("coverage_envelope_sha256",))
            if implementation == "amaru":
                flags = ("-Cinstrument-coverage",)
                lane_tools += (("cargo-fuzz", coverage["cargo_fuzz_version"]),)
                lane_environment = (*environment, ("RUSTFLAGS", "-Cinstrument-coverage"))
                commands = ((*compiler, "fuzz", "build", "--fuzz-dir", "dwarf-coverage-fuzz", "plutus_data"),)
                kwargs.update(binary="cargo-fuzz:plutus_data", coverage_scope=coverage["production_entrypoint"], staging_directory="dwarf-coverage-fuzz")
                if not (candidate.source_root / "crates/amaru-kernel/Cargo.toml").is_file():
                    lane_reasons.append("coverage harness requires crates/amaru-kernel/Cargo.toml")
            else:
                flags = tuple(coverage["build"]["ghc_options"])
                commands = (("cabal", "build", *compiler, "--enable-coverage", "exe:cardano-node-production-handshake-coverage"),)
                kwargs.update(binary="cabal:list-bin:exe:cardano-node-production-handshake-coverage", coverage_scope=coverage["production_entrypoints"], staging_directory="dwarf-coverage-harness")
                kwargs["binary_query"] = ("cabal", "list-bin", *compiler, "exe:cardano-node-production-handshake-coverage")
                vendor = " ".join(f"vendor/{row['package']}-{row['version']}" for row in coverage["dependencies"])
                project = f"packages: dwarf-coverage-harness {vendor}\n" + "".join(
                    f"package {package}\n  ghc-options: {' '.join(flags)}\n"
                    for package in coverage["build"]["instrumented_packages"]
                )
                kwargs["generated_inputs"] = (("cabal.project.local", project),)
            lane_reasons.append("harness production API compatibility, isolated staging and coverage envelope require verification")
        for relative, _ in kwargs.get("generated_inputs", ()):
            if (candidate.source_root / relative).exists():
                lane_reasons.append(f"{relative} already exists; refusing to replace a candidate input")
        recipe_digest = _digest([asdict(row) for row in inputs if row.origin == "trusted"])
        plan = CandidateBuildPlan(candidate=candidate, variant=variant, recipe_digest=recipe_digest,
                                  commands=commands, command_workdirs=command_workdirs,
                                  command_environments=command_environments,
                                  environment=lane_environment, toolchain=lane_tools,
                                  compiler_flags=flags, inputs=tuple(inputs), unresolved=tuple(lane_reasons),
                                  **dict({"binary": binary}, **kwargs))
        try:
            build_environment = resolve_build_environment(plan, trusted_root=TRUSTED_ROOT)
        except BuildEnvironmentError as exc:
            raise CandidateBuildError(str(exc)) from exc
        # Environment selection never consults candidate metadata or commands.
        # The recipe digest excludes environment inputs to avoid a hash cycle;
        # the complete input set and both manifests are bound by plan_digest.
        plan = replace(
            plan, build_environment_digest=build_environment.manifest_digest,
            dependency_closure_digest=build_environment.closure_digest,
            build_container_image=build_environment.build_image,
            base_image=build_environment.runtime_base_image or plan.base_image,
            build_environment_blockers=build_environment.blockers,
            inputs=(*plan.inputs, *(BuildInput("trusted", path, digest)
                                   for path, digest in build_environment.inputs)),
            unresolved=(*plan.unresolved, *build_environment.blockers),
        )
        plans.append(replace(plan, plan_digest=_identity(plan)))
    # Detect source mutation during the read-only planning pass as well.
    _source(candidate)
    return tuple(plans)


def validate_candidate_build_plan(plan: CandidateBuildPlan) -> None:
    """Recheck frozen source, built-in recipe, patch and harness identities.

    Successful validation means an unchanged declaration, never a built/ready
    artifact. No caller-provided command, digest override or status is accepted.
    """
    if _identity(plan) != plan.plan_digest:
        raise CandidateBuildError("candidate build plan digest mismatch")
    _source(plan.candidate)
    for row in plan.inputs:
        if row.origin in ("trusted", "source"):
            root = TRUSTED_ROOT if row.origin == "trusted" else plan.candidate.source_root
            if _input(row.origin, root, row.path) != row:
                raise CandidateBuildError(f"{row.origin} build input changed: {row.path}")
    expected = next((item for item in plan_candidate_builds(plan.candidate) if item.variant == plan.variant), None)
    if plan != expected:
        raise CandidateBuildError("candidate build plan is not the current built-in recipe")
