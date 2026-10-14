"""Source-checkout uninstall must stop the existing stack without minting identity."""

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TOKEN = "dashboard-secret-" + "d" * 48
CONTROLLER_TOKEN = "controller-secret-" + "c" * 48
INSTALLATION_ID = "install-0123456789abcdef"


def persist_identity(runtime, *, installation=True, tokens=True):
    state = runtime / "state"
    state.mkdir(parents=True)
    if tokens:
        for name, value in (
            ("dashboard-token", DASHBOARD_TOKEN),
            ("controller-token", CONTROLLER_TOKEN),
        ):
            (state / name).write_text(value + "\n", encoding="utf-8")
            (state / name).chmod(0o600)
    if installation:
        (state / "installation-id").write_text(INSTALLATION_ID + "\n", encoding="utf-8")
        (state / "installation-id").chmod(0o600)
    return state


def stage_source_checkout(tmp_path, *, env_text=None):
    checkout = tmp_path / "checkout"
    scripts = checkout / "delivery" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("common.sh", "uninstall.sh"):
        shutil.copy2(ROOT / "delivery" / "scripts" / name, scripts / name)
    shutil.copy2(
        ROOT / "delivery" / "docker-compose.dwarf.yml",
        checkout / "delivery" / "docker-compose.dwarf.yml",
    )
    if env_text is not None:
        (checkout / ".env").write_text(env_text, encoding="utf-8")
    return checkout


def run_uninstall(tmp_path, runtime, *, checkout=None, **overrides):
    checkout = checkout or stage_source_checkout(tmp_path)
    calls = tmp_path / "calls"
    install_root = tmp_path / "release-install-that-does-not-exist"
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "ADA2_DWARF_TOKEN",
            "DWARF_CONTROLLER_TOKEN",
            "DWARF_INSTALLATION_ID",
            "DWARF_COMPOSE_PROJECT_NAME",
        }
    }
    env.update(
        DWARF_INSTALL_ROOT=str(install_root),
        DWARF_RUNTIME_ROOT=str(runtime),
        CALLS=str(calls),
        EXPECTED_DASHBOARD_TOKEN=DASHBOARD_TOKEN,
        EXPECTED_CONTROLLER_TOKEN=CONTROLLER_TOKEN,
        UNINSTALL=str(checkout / "delivery" / "scripts" / "uninstall.sh"),
    )
    env.update(overrides)
    command = r'''
set -euo pipefail
docker() {
  if [[ "$1" == version ]]; then
    return 0
  fi
  if [[ "$1" == compose && "${2:-}" == version ]]; then
    return 0
  fi
  if [[ "$1" == compose ]]; then
    [[ "${DWARF_INSTALLATION_ID:-}" == "install-0123456789abcdef" ]] || {
      echo "required installation identity is missing or wrong" >&2
      return 81
    }
    [[ "${ADA2_DWARF_TOKEN:-}" == "$EXPECTED_DASHBOARD_TOKEN" ]] || {
      echo "required dashboard identity is missing or wrong" >&2
      return 82
    }
    [[ "${DWARF_CONTROLLER_TOKEN:-}" == "$EXPECTED_CONTROLLER_TOKEN" ]] || {
      echo "required controller identity is missing or wrong" >&2
      return 83
    }
    printf '%s|project=%s\n' "${*:2}" "${DWARF_COMPOSE_PROJECT_NAME:-}" >> "$CALLS"
    return 0
  fi
  if [[ "$1" == image && "$2" == rm ]]; then
    printf 'image-rm %s\n' "$3" >> "$CALLS"
    return 0
  fi
  return 91
}
export -f docker
bash "$UNINSTALL"
'''
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
    return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def assert_no_credentials(result):
    output = result.stdout + result.stderr
    assert DASHBOARD_TOKEN not in output
    assert CONTROLLER_TOKEN not in output


def test_source_uninstall_without_root_env_hydrates_persisted_identity(tmp_path):
    runtime = tmp_path / "runtime"
    persist_identity(runtime)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert "down --remove-orphans|project=" in calls[0]
    assert "--volumes" not in calls[0]
    assert "--env-file" not in calls[0]
    assert "preserving all runtime data" in result.stdout
    assert_no_credentials(result)


def test_source_uninstall_uses_optional_root_env_without_overriding_hydrated_identity(tmp_path):
    runtime = tmp_path / "runtime"
    persist_identity(runtime)
    checkout = stage_source_checkout(
        tmp_path,
        env_text=(
            "DWARF_COMPOSE_PROJECT_NAME=dwarf-source-test\n"
            "DWARF_INSTALLATION_ID=\n"
            "ADA2_DWARF_TOKEN=\n"
            "DWARF_CONTROLLER_TOKEN=\n"
        ),
    )

    result, calls = run_uninstall(tmp_path, runtime, checkout=checkout)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert f"--env-file {checkout / '.env'}" in calls[0]
    assert calls[0].endswith("|project=dwarf-source-test")
    assert os.environ.get("DWARF_INSTALLATION_ID") is None
    assert_no_credentials(result)


def test_source_uninstall_refuses_missing_identity_without_creating_it(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime, installation=False)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert "persisted identity is missing" in result.stderr
    assert not (state / "installation-id").exists()
    assert_no_credentials(result)


def test_source_uninstall_refuses_missing_tokens_without_creating_them(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime, tokens=False)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert not (state / "dashboard-token").exists()
    assert not (state / "controller-token").exists()


def test_source_uninstall_refuses_insecure_token_without_changing_mode(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)
    dashboard_token = state / "dashboard-token"
    dashboard_token.chmod(0o644)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert "permissions are not 0600" in result.stderr
    assert dashboard_token.stat().st_mode & 0o777 == 0o644
    assert_no_credentials(result)


def test_source_uninstall_validates_insecure_persisted_token_even_when_env_matches(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)
    dashboard_token = state / "dashboard-token"
    dashboard_token.chmod(0o644)

    result, calls = run_uninstall(
        tmp_path,
        runtime,
        ADA2_DWARF_TOKEN=DASHBOARD_TOKEN,
    )

    assert result.returncode != 0
    assert calls == []
    assert "permissions are not 0600" in result.stderr
    assert dashboard_token.stat().st_mode & 0o777 == 0o644
    assert_no_credentials(result)


def test_source_uninstall_refuses_conflicting_caller_token_before_compose(tmp_path):
    runtime = tmp_path / "runtime"
    persist_identity(runtime)

    result, calls = run_uninstall(
        tmp_path,
        runtime,
        ADA2_DWARF_TOKEN="different-dashboard-secret-" + "x" * 48,
    )

    assert result.returncode != 0
    assert calls == []
    assert "dashboard credential conflicts with persisted identity" in result.stderr
    assert_no_credentials(result)


def test_source_uninstall_preserves_caller_project_over_conflicting_root_env(tmp_path):
    runtime = tmp_path / "runtime"
    persist_identity(runtime)
    checkout = stage_source_checkout(
        tmp_path,
        env_text="DWARF_COMPOSE_PROJECT_NAME=wrong-env-project\n",
    )

    result, calls = run_uninstall(
        tmp_path,
        runtime,
        checkout=checkout,
        DWARF_COMPOSE_PROJECT_NAME="caller-project",
    )

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert calls[0].endswith("|project=caller-project")
    assert_no_credentials(result)


def test_source_uninstall_refuses_conflicting_root_env_token_before_compose(tmp_path):
    runtime = tmp_path / "runtime"
    persist_identity(runtime)
    checkout = stage_source_checkout(
        tmp_path,
        env_text="ADA2_DWARF_TOKEN=different-dashboard-secret-" + "x" * 48 + "\n",
    )

    result, calls = run_uninstall(tmp_path, runtime, checkout=checkout)

    assert result.returncode != 0
    assert calls == []
    assert "dashboard credential conflicts with persisted identity" in result.stderr
    assert_no_credentials(result)


def test_source_uninstall_refuses_insecure_installation_id_without_changing_mode(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)
    installation_file = state / "installation-id"
    installation_file.chmod(0o644)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert "permissions are not 0600" in result.stderr
    assert installation_file.stat().st_mode & 0o777 == 0o644
    assert_no_credentials(result)


def test_source_uninstall_refuses_symlink_installation_id(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)
    installation_file = state / "installation-id"
    target = tmp_path / "outside-installation-id"
    target.write_text(INSTALLATION_ID + "\n", encoding="utf-8")
    target.chmod(0o600)
    installation_file.unlink()
    installation_file.symlink_to(target)

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert "not a safe regular file" in result.stderr
    assert installation_file.is_symlink()
    assert_no_credentials(result)


def test_source_uninstall_refuses_nonregular_installation_id(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)
    installation_file = state / "installation-id"
    installation_file.unlink()
    installation_file.mkdir()

    result, calls = run_uninstall(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert "not a regular file" in result.stderr
    assert installation_file.is_dir()
    assert_no_credentials(result)


def test_source_uninstall_refuses_conflicting_installation_id_before_compose(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)

    result, calls = run_uninstall(
        tmp_path,
        runtime,
        DWARF_INSTALLATION_ID="install-conflicting",
    )

    assert result.returncode != 0
    assert calls == []
    assert "conflicts with persisted" in result.stderr
    assert (state / "installation-id").read_text(encoding="utf-8") == INSTALLATION_ID + "\n"
    assert_no_credentials(result)
