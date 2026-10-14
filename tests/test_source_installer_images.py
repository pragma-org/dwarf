"""Exercise real delivery scripts with Docker isolated at the CLI boundary."""

import fcntl
import os
from pathlib import Path
import subprocess
import time

import pytest

from controller.docker_runtime import DockerRuntime


ROOT = Path(__file__).resolve().parents[1]
AMARU = "ghcr.io/pragma-org/amaru@sha256:" + "a" * 64
CARDANO = "ghcr.io/intersectmbo/cardano-node@sha256:" + "b" * 64
AMARU_SUPPORT_CARDANO_DIGEST = (
    "sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c"
)
AMARU_DEFAULT_DIGEST = (
    "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"
)


def run_delivery(tmp_path, *, present=(), failure="", images=None, helper=False):
    calls = tmp_path / "calls"
    available = tmp_path / "available"
    available.write_text("\n".join(present) + "\n")
    env = dict(os.environ)
    env.update(
        DWARF_RUNTIME_ROOT=str(tmp_path / "runtime"),
        DWARF_CONTROLLER_TOKEN="test-token-" + "x" * 40,
        DWARF_INSTALLATION_ID="install-image-regression",
        DWARF_GOLDEN_IMAGES=images if images is not None else f"{AMARU},{CARDANO}",
        DWARF_IMAGE="test-framework:current",
        DWARF_CONTROLLER_IMAGE="test-controller:current",
        DWARF_MOOG_BOOTSTRAP="off",
        CALLS=str(calls), AVAILABLE=str(available), FAILURE=failure,
        COMMON=str(ROOT / "delivery/scripts/common.sh"),
        DEPLOY=str(ROOT / "delivery/scripts/deploy.sh"),
    )
    command = r'''
set -euo pipefail
docker() {
  printf '%s\n' "$*" >> "$CALLS"
  case "$1" in
    version) return 0 ;;
    compose) return 0 ;;
    image)
      case "${@: -1}" in test-framework:current|test-controller:current) return 0 ;; esac
      grep -Fxq -- "${@: -1}" "$AVAILABLE" ;;
    pull)
      [[ "$FAILURE" != pull ]] || return 73
      [[ "$FAILURE" == absent-after-pull ]] || printf '%s\n' "$2" >> "$AVAILABLE"
      return 0 ;;
    exec) cat >/dev/null; return 0 ;;
    *) return 91 ;;
  esac
}
export -f docker
'''
    command += '\nsource "$COMMON"\nensure_golden_images\n' if helper else '\nbash "$DEPLOY"\n'
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_fresh_deploy_pulls_missing_target_before_starting_containers(tmp_path):
    result, calls = run_delivery(tmp_path, present=[AMARU])
    assert result.returncode == 0, result.stderr
    assert f"pull {CARDANO}" in calls, "fresh install must supply the missing Cardano target"
    assert f"pull {AMARU}" not in calls
    up = next(i for i, call in enumerate(calls) if " up " in call)
    assert calls.index(f"pull {CARDANO}") < up
    assert calls.index(f"image inspect {CARDANO}", calls.index(f"pull {CARDANO}")) < up


def test_cached_targets_are_not_pulled_again(tmp_path):
    result, calls = run_delivery(tmp_path, present=[AMARU, CARDANO])
    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("pull ") for call in calls)


def test_source_install_declares_verified_golden_digests_to_dashboard():
    env = dict(os.environ)
    env.pop("DWARF_AVAILABLE_IMAGE_DIGESTS", None)
    env["DWARF_GOLDEN_IMAGES"] = f"{AMARU},{CARDANO}"

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT / "delivery/scripts/common.sh"}"; '
            'printf "%s" "$DWARF_AVAILABLE_IMAGE_DIGESTS"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"sha256:{'a' * 64},sha256:{'b' * 64}"


def test_default_source_install_includes_amaru_supporting_cardano_image():
    env = dict(os.environ)
    env.pop("DWARF_GOLDEN_IMAGES", None)
    env.pop("DWARF_AVAILABLE_IMAGE_DIGESTS", None)

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT / "delivery/scripts/common.sh"}"; '
            'printf "%s\n%s" "$DWARF_GOLDEN_IMAGES" "$DWARF_AVAILABLE_IMAGE_DIGESTS"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    images, digests = result.stdout.splitlines()
    assert f"ghcr.io/intersectmbo/cardano-node@{AMARU_SUPPORT_CARDANO_DIGEST}" in images.split(",")
    assert AMARU_SUPPORT_CARDANO_DIGEST in digests.split(",")


def test_default_source_install_approves_native_default_amaru_image(
    monkeypatch, tmp_path
):
    env = dict(os.environ)
    env.pop("DWARF_GOLDEN_IMAGES", None)
    env.pop("DWARF_AVAILABLE_IMAGE_DIGESTS", None)

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT / "delivery/scripts/common.sh"}"; '
            'printf "%s\n%s" "$DWARF_GOLDEN_IMAGES" "$DWARF_AVAILABLE_IMAGE_DIGESTS"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    images, digests = result.stdout.splitlines()
    default_image = f"ghcr.io/pragma-org/amaru@{AMARU_DEFAULT_DIGEST}"
    assert default_image in images.split(",")
    assert AMARU_DEFAULT_DIGEST in digests.split(",")
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", images)
    runtime = DockerRuntime(
        installation_id="install-default-amaru-regression",
        state_dir=tmp_path / "controller-state",
    )
    assert runtime._assert_approved_image(default_image) is None


def test_fresh_install_exposes_shared_measurement_target_registry_read_only(tmp_path):
    runtime_root = tmp_path / "runtime"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT / "delivery/scripts/common.sh"}"; ensure_runtime_dirs',
        ],
        env={**os.environ, "DWARF_RUNTIME_ROOT": str(runtime_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (runtime_root / "state" / "measurement-targets").is_dir()
    assert (runtime_root / "state" / "measurements").is_dir()
    assert (runtime_root / "state" / "measurement-profiles").is_dir()
    scenarios = runtime_root / "state" / "scenarios"
    assert scenarios.is_dir()
    assert os.access(scenarios, os.W_OK)
    profiles = runtime_root / "state" / "profiles"
    profile_lock = profiles / ".dwarf-catalog.lock"
    assert profiles.is_dir()
    assert os.access(profiles, os.W_OK)
    assert profile_lock.is_file()
    assert os.access(profile_lock, os.W_OK)
    assert os.access(runtime_root / "state" / "measurements", os.W_OK)
    assert os.access(runtime_root / "state" / "measurement-profiles", os.W_OK)
    version_catalog = runtime_root / "state" / "version-catalog"
    assert version_catalog.is_dir()
    assert os.access(version_catalog, os.W_OK)
    version_imports = runtime_root / "state" / "version-imports"
    assert version_imports.is_dir()
    assert os.access(version_imports, os.W_OK)
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text()
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/measurement-targets:"
        "/var/dwarf/controller-state/measurement-targets:ro"
    ) in compose
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/version-catalog:"
        "/var/dwarf/dashboard-state/version-catalog:ro"
    ) in compose
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/version-imports:"
        "/var/dwarf/version-imports:ro"
    ) in compose
    dashboard, controller = compose.split("\n  dwarf-controller:\n", 1)
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/version-imports:"
        "/var/dwarf/version-imports"
    ) in dashboard
    assert "/var/dwarf/version-imports:ro" in controller
    assert "ADA2_DWARF_MEASUREMENTS_DIR: /var/dwarf/state/measurements" in compose
    assert (
        "ADA2_DWARF_MEASUREMENT_PROFILES_DIR: "
        "/var/dwarf/state/measurement-profiles"
    ) in compose
    assert (
        "ADA2_DWARF_MEASUREMENTS_DIR: /var/dwarf/dashboard-state/measurements"
        in compose
    )
    assert (
        "ADA2_DWARF_MEASUREMENT_PROFILES_DIR: "
        "/var/dwarf/dashboard-state/measurement-profiles"
        in compose
    )
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/measurements:"
        "/var/dwarf/dashboard-state/measurements:ro"
    ) in compose
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/measurement-profiles:"
        "/var/dwarf/dashboard-state/measurement-profiles:ro"
    ) in compose
    assert (
        "ADA2_DWARF_SCENARIOS_DIR: /var/dwarf/dashboard-state/scenarios"
        in compose
    )
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/scenarios:"
        "/var/dwarf/dashboard-state/scenarios:ro"
    ) in compose
    assert (
        "${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state:"
        "/var/dwarf/dashboard-state:ro"
    ) not in compose
    assert "DWARF_HOST_OS: ${DWARF_HOST_OS:?" in compose
    assert "DWARF_HOST_ARCH: ${DWARF_HOST_ARCH:?" in compose
    assert (
        "DWARF_ARTIFACT_PACKAGE_AMARU_BASE: "
        "${DWARF_ARTIFACT_PACKAGE_AMARU_BASE:?"
    ) in compose
    assert (
        "DWARF_ARTIFACT_PACKAGE_CARDANO_BASE: "
        "${DWARF_ARTIFACT_PACKAGE_CARDANO_BASE:?"
    ) in compose


def test_normal_install_provisions_digest_pinned_artifact_packaging_bases():
    common = (ROOT / "delivery/scripts/common.sh").read_text()
    installer = (ROOT / "delivery/install.sh").read_text()

    assert "DWARF_ARTIFACT_PACKAGE_AMARU_BASE=${DWARF_ARTIFACT_PACKAGE_AMARU_BASE:-" in common
    assert "DWARF_ARTIFACT_PACKAGE_CARDANO_BASE=${DWARF_ARTIFACT_PACKAGE_CARDANO_BASE:-" in common
    assert "DWARF_ARTIFACT_PACKAGE_AMARU_BASE=%s" in installer
    assert "DWARF_ARTIFACT_PACKAGE_CARDANO_BASE=%s" in installer


@pytest.mark.parametrize("target_exists", [True, False])
def test_fresh_install_rejects_profile_catalog_lock_symlinks(tmp_path, target_exists):
    runtime_root = tmp_path / "runtime"
    profiles = runtime_root / "state" / "profiles"
    profiles.mkdir(parents=True)
    external = tmp_path / "external-lock"
    if target_exists:
        external.write_text("external-sentinel\n")
    os.symlink(external, profiles / ".dwarf-catalog.lock")

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT / "delivery/scripts/common.sh"}"; ensure_runtime_dirs',
        ],
        env={**os.environ, "DWARF_RUNTIME_ROOT": str(runtime_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    if target_exists:
        assert external.read_text() == "external-sentinel\n"
    else:
        assert not external.exists()


def test_profile_seeding_waits_for_catalog_lock_and_replaces_atomically(tmp_path):
    runtime_root = tmp_path / "runtime"
    env = {**os.environ, "DWARF_RUNTIME_ROOT": str(runtime_root)}
    common = ROOT / "delivery/scripts/common.sh"
    prepared = subprocess.run(
        ["bash", "-c", f'source "{common}"; ensure_runtime_dirs'],
        env=env,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0, prepared.stderr
    packaged = sorted((ROOT / "dwarf/profiles").glob("*/profile.yaml"))[0]
    target = runtime_root / "state" / "profiles" / packaged.parent.name / "profile.yaml"
    target.parent.mkdir()
    target.write_text("old-profile-bytes\n")
    lock_path = runtime_root / "state" / "profiles" / ".dwarf-catalog.lock"

    with lock_path.open("r+b") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        process = subprocess.Popen(
            ["bash", "-c", f'source "{common}"; seed_profiles'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.25)
        blocked = process.poll() is None
        assert target.read_text() == "old-profile-bytes\n"
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
    stdout, stderr = process.communicate(timeout=10)

    assert blocked, (stdout, stderr)
    assert process.returncode == 0, stderr
    assert target.read_bytes() == packaged.read_bytes()
    assert not list(target.parent.glob(".profile.yaml.*.tmp"))


@pytest.mark.parametrize("failure", ["pull", "absent-after-pull"])
def test_missing_target_failure_prevents_container_start(tmp_path, failure):
    result, calls = run_delivery(tmp_path, present=[AMARU], failure=failure)
    assert result.returncode != 0
    assert CARDANO in result.stderr
    assert not any(" up " in call for call in calls)


@pytest.mark.parametrize("images", [",", "ghcr.io/intersectmbo/cardano-node:latest", f"{AMARU},", f"{AMARU},not-pinned"])
def test_targets_must_all_be_digest_pinned_before_any_pull(tmp_path, images):
    result, calls = run_delivery(tmp_path, images=images, helper=True)
    assert result.returncode != 0
    assert "digest-pinned" in result.stderr
    assert not any(call.startswith("pull ") for call in calls)


def test_measurement_target_transfer_uses_installed_registry_without_minting_identity(
    tmp_path,
):
    runtime = tmp_path / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    (state / "installation-id").write_text("install-existing\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    python = fake_bin / "python3"
    python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >"$CALLS"\n'
    )
    python.chmod(0o755)
    script = ROOT / "delivery/scripts/transfer-measurement-target.sh"

    result = subprocess.run(
        ["bash", str(script), "import", "--bundle", str(tmp_path / "bundle")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "DWARF_RUNTIME_ROOT": str(runtime),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().strip() == (
        f"-m scripts.transfer_measurement_target import "
        f"--bundle {tmp_path / 'bundle'} "
        f"--registry-root {runtime / 'state' / 'measurement-targets'}"
    )
    assert (state / "installation-id").read_text() == "install-existing\n"
    assert not (state / "dashboard-token").exists()
    assert not (state / "controller-token").exists()

    export = subprocess.run(
        [
            "bash",
            str(script),
            "export",
            "--source-revision",
            "a" * 40,
            "--patch-set-sha256",
            "b" * 64,
            "--build-root",
            str(tmp_path / "qualified"),
            "--bundle",
            str(tmp_path / "export"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "DWARF_RUNTIME_ROOT": str(runtime),
        },
        capture_output=True,
        text=True,
    )
    assert export.returncode == 0, export.stderr
    assert calls.read_text().strip() == (
        f"-m scripts.transfer_measurement_target export "
        f"--source-revision {'a' * 40} --patch-set-sha256 {'b' * 64} "
        f"--build-root {tmp_path / 'qualified'} --bundle {tmp_path / 'export'} "
        f"--registry-root {runtime / 'state' / 'measurement-targets'} "
        f"--source-installation-id install-existing"
    )

    conformance = subprocess.run(
        [
            "bash",
            str(script),
            "export-conformance",
            "--implementation",
            "amaru",
            "--source-revision",
            "a" * 40,
            "--adapter-set-sha256",
            "b" * 64,
            "--bundle",
            str(tmp_path / "adapter-export"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "DWARF_RUNTIME_ROOT": str(runtime),
        },
        capture_output=True,
        text=True,
    )
    assert conformance.returncode == 0, conformance.stderr
    assert calls.read_text().strip() == (
        f"-m scripts.transfer_measurement_target export-conformance "
        f"--implementation amaru --source-revision {'a' * 40} "
        f"--adapter-set-sha256 {'b' * 64} --bundle {tmp_path / 'adapter-export'} "
        f"--registry-root {runtime / 'state' / 'measurement-targets'} "
        f"--package-root {ROOT / 'dwarf'} --source-installation-id install-existing"
    )
    conformance_import = subprocess.run(
        [
            "bash",
            str(script),
            "import-conformance",
            "--bundle",
            str(tmp_path / "adapter-export"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "DWARF_RUNTIME_ROOT": str(runtime),
        },
        capture_output=True,
        text=True,
    )
    assert conformance_import.returncode == 0, conformance_import.stderr
    assert calls.read_text().strip() == (
        f"-m scripts.transfer_measurement_target import-conformance "
        f"--bundle {tmp_path / 'adapter-export'} "
        f"--registry-root {runtime / 'state' / 'measurement-targets'} "
        f"--package-root {ROOT / 'dwarf'}"
    )


def test_measurement_target_transfer_refuses_unknown_action_and_missing_install(
    tmp_path,
):
    script = ROOT / "delivery/scripts/transfer-measurement-target.sh"
    runtime = tmp_path / "runtime"

    invalid = subprocess.run(
        ["bash", str(script), "publish"],
        env={**os.environ, "DWARF_RUNTIME_ROOT": str(runtime)},
        capture_output=True,
        text=True,
    )
    missing = subprocess.run(
        ["bash", str(script), "import", "--bundle", str(tmp_path / "bundle")],
        env={**os.environ, "DWARF_RUNTIME_ROOT": str(runtime)},
        capture_output=True,
        text=True,
    )

    assert invalid.returncode != 0
    assert "export-cbor-conformance" in invalid.stderr
    assert "import-cbor-conformance" in invalid.stderr
    assert missing.returncode != 0
    assert "installation-id" in missing.stderr
    assert not runtime.exists()


def test_measurement_target_transfer_allows_explicit_legacy_conformance_export_without_install(
    tmp_path,
):
    runtime = tmp_path / "legacy-runtime"
    registry = runtime / "state" / "measurement-targets"
    registry.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    python = fake_bin / "python3"
    python.write_text("#!/usr/bin/env bash\n" 'printf "%s\\n" "$*" >"$CALLS"\n')
    python.chmod(0o755)
    script = ROOT / "delivery/scripts/transfer-measurement-target.sh"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "export-conformance",
            "--legacy-qualified-source",
            "--implementation",
            "amaru",
            "--source-revision",
            "a" * 40,
            "--adapter-set-sha256",
            "b" * 64,
            "--bundle",
            str(tmp_path / "legacy-export"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "DWARF_RUNTIME_ROOT": str(runtime),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().strip() == (
        f"-m scripts.transfer_measurement_target export-conformance "
        f"--legacy-qualified-source --implementation amaru "
        f"--source-revision {'a' * 40} --adapter-set-sha256 {'b' * 64} "
        f"--bundle {tmp_path / 'legacy-export'} "
        f"--registry-root {registry} --package-root {ROOT / 'dwarf'}"
    )
    assert not (runtime / "state" / "installation-id").exists()
