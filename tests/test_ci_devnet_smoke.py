import os
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "dwarf" / "scripts" / "ci_devnet_smoke.sh"


def _function_block(name: str) -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    return next(
        block for block in source.split("\n\n") if block.startswith(f"{name}()")
    )


def test_projects_now_preserves_the_complete_hyphenated_run_identity():
    command = f"""
docker() {{
  printf '%s\\n' \\
    'dwarf-substrate-20261008t224918z-f3460f73' \\
    'dwarf-substrate-20261008t224522z-499c6d69' \\
    'unrelated-project'
}}
{_function_block("projects_now")}
projects_now
"""

    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "dwarf-substrate-20261008t224522z-499c6d69",
        "dwarf-substrate-20261008t224918z-f3460f73",
    ]


@pytest.mark.parametrize("failed_command", ["ps", "network"])
def test_projects_now_fails_closed_if_either_inventory_fails(failed_command):
    command = f"""
docker() {{
  if [ "$1" = {failed_command!r} ]; then
    return 73
  fi
  printf '%s\\n' 'dwarf-substrate-existing-aabbccdd'
}}
{_function_block("projects_now")}
projects_now
"""

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""


def test_remove_project_targets_only_the_exact_compose_label(tmp_path):
    calls = tmp_path / "docker-calls.txt"
    project = "dwarf-substrate-20261008t224918z-f3460f73"
    command = f"""
CALLS={str(calls)!r}
docker() {{
  printf '%s\\n' "$*" >> "$CALLS"
  case "$*" in
    'ps -aq --filter label=com.docker.compose.project={project}') printf '%s\\n' container-id ;;
    'network ls -q --filter label=com.docker.compose.project={project}') printf '%s\\n' network-id ;;
  esac
}}
{_function_block("remove_project")}
remove_project {project!r}
"""

    subprocess.run(["bash", "-c", command], check=True)

    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"ps -aq --filter label=com.docker.compose.project={project}",
        "rm -f container-id",
        f"network ls -q --filter label=com.docker.compose.project={project}",
        "network rm network-id",
    ]


def test_script_aborts_before_scenarios_when_baseline_inventory_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 73\n", encoding="utf-8")
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DWARF_CI_RUNS_DIR": str(tmp_path / "runs"),
        "DWARF_CI_STATE_DIR": str(tmp_path / "state"),
        "DWARF_CI_IMPORTS_DIR": str(tmp_path / "imports"),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "must-not-run.yaml"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unable to capture complete pre-run Docker inventory" in result.stderr
    assert "== scenario:" not in result.stdout


def test_cleanup_skips_deletion_and_fails_if_final_inventory_is_incomplete(tmp_path):
    calls = tmp_path / "docker-calls.txt"
    command = f"""
CALLS={str(calls)!r}
docker() {{
  printf '%s\\n' "$*" >> "$CALLS"
  if [ "$1 $2" = 'ps -a' ]; then
    return 73
  fi
  printf '%s\\n' 'dwarf-substrate-new-aabbccdd'
}}
{_function_block("projects_now")}
{_function_block("remove_project")}
{_function_block("cleanup")}
BEFORE='dwarf-substrate-existing-aabbccdd'
cleanup
"""

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "cleanup skipped" in result.stderr
    assert all(" rm" not in call for call in calls.read_text(encoding="utf-8").splitlines())


def test_successful_cleanup_preserves_a_failing_main_body_status():
    command = f"""
docker() {{
  printf '%s\\n' 'dwarf-substrate-existing-aabbccdd'
}}
{_function_block("projects_now")}
{_function_block("remove_project")}
{_function_block("cleanup")}
BEFORE='dwarf-substrate-existing-aabbccdd'
trap cleanup EXIT
exit 42
"""

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 42
