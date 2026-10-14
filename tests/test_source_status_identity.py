"""Source-install status.sh must hydrate persisted identity without minting it."""

import os
from pathlib import Path
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
            (state / name).write_text(value + "\n")
            (state / name).chmod(0o600)
    if installation:
        (state / "installation-id").write_text(INSTALLATION_ID + "\n")
        (state / "installation-id").chmod(0o600)
    return state


def run_status(tmp_path, runtime, **overrides):
    calls = tmp_path / "calls"
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"ADA2_DWARF_TOKEN", "DWARF_CONTROLLER_TOKEN", "DWARF_INSTALLATION_ID"}
    }
    env.update(
        DWARF_RUNTIME_ROOT=str(runtime),
        CALLS=str(calls),
        STATUS=str(ROOT / "delivery/scripts/status.sh"),
    )
    env.update(overrides)
    # Unit-level Docker stub: `compose` mirrors Compose's `${VAR:?}` failure
    # for the identity variables; this is not runtime acceptance.
    command = r'''
set -euo pipefail
docker() {
  case "$1" in
    version) return 0 ;;
    compose)
      [[ "$2" == version ]] && return 0
      for var in DWARF_INSTALLATION_ID ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN; do
        if [[ -z "${!var:-}" ]]; then
          echo "required variable $var is missing a value" >&2
          return 1
        fi
      done
      printf 'compose %s|%s\n' "${*:2}" "$DWARF_INSTALLATION_ID" >> "$CALLS"
      return 0 ;;
    image|inspect) return 1 ;;
    *) return 91 ;;
  esac
}
export -f docker
bash "$STATUS"
'''
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
    return result, calls.read_text().splitlines() if calls.exists() else []


def assert_no_credentials(result):
    output = result.stdout + result.stderr
    assert DASHBOARD_TOKEN not in output
    assert CONTROLLER_TOKEN not in output


def test_fresh_shell_status_hydrates_persisted_identity(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)

    result, calls = run_status(tmp_path, runtime)

    assert result.returncode == 0, result.stderr
    assert calls == [f"compose -f {ROOT}/delivery/docker-compose.dwarf.yml ps|{INSTALLATION_ID}"]
    assert (state / "installation-id").read_text() == INSTALLATION_ID + "\n"
    assert_no_credentials(result)


def test_status_refuses_missing_installation_id_without_creating_one(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime, installation=False)

    result, calls = run_status(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert not (state / "installation-id").exists()
    assert "installation-id" in result.stderr
    assert_no_credentials(result)


def test_status_refuses_missing_tokens_without_creating_them(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime, tokens=False)

    result, calls = run_status(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert not (state / "dashboard-token").exists()
    assert not (state / "controller-token").exists()


def test_status_refuses_installation_id_conflicting_with_persisted_state(tmp_path):
    runtime = tmp_path / "runtime"
    state = persist_identity(runtime)

    result, calls = run_status(tmp_path, runtime, DWARF_INSTALLATION_ID="install-other")

    assert result.returncode != 0
    assert calls == []
    assert (state / "installation-id").read_text() == INSTALLATION_ID + "\n"
    assert_no_credentials(result)


def test_status_does_not_create_runtime_state(tmp_path):
    runtime = tmp_path / "runtime"

    result, calls = run_status(tmp_path, runtime)

    assert result.returncode != 0
    assert calls == []
    assert not runtime.exists()
