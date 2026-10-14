from pathlib import Path
import hashlib
import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from profile_manager.config import DeploymentConfig
from profile_manager import dashboard
from profile_manager import cli
from profile_manager.remote import (
    CommandResult,
    render_launch_command,
    render_launch_preflight_command,
    render_topology_health_command,
    render_topology_redeploy_command,
)
from profile_manager.smoke import find_smoke_test
from profile_manager.topology_actions import TopologyActionService
from profile_manager.views import operate_coverage
from profile_manager.views.operate_config import render_operate_config


ROOT = Path(__file__).resolve().parents[1]
BUILD_GUARD = ROOT / "delivery/scripts/verify-build-source.sh"
BUILD_STAGER = ROOT / "delivery/scripts/stage-build-context.sh"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _clean_git_repo(path):
    path.mkdir(exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "DWARF tests")
    (path / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (path / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "baseline")
    return _git(path, "rev-parse", "HEAD")


def _run_build_guard(repo, source_revision, catalog_revision):
    return subprocess.run(
        [
            "bash",
            str(BUILD_GUARD),
            str(repo),
            source_revision,
            catalog_revision,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _load_control_shim():
    path = ROOT / "delivery/control-plane/dwarf-deploy-shim.py"
    spec = importlib.util.spec_from_file_location("dwarf_deploy_shim", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_ssh_key_matches_container_mount():
    config = DeploymentConfig.from_dict({})
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert config.ssh_key_path == "~/.ssh/cardano-box"
    assert ":/home/dwarf/.ssh/cardano-box:ro" in compose


def test_control_shim_executes_large_generated_script_without_argv_limit():
    shim = _load_control_shim()
    payload = "x" * 300_000
    script = ": <<'DWARF_LARGE_SCRIPT'\n" + payload + "\nDWARF_LARGE_SCRIPT\n"

    assert shim._run_script(script) == 0


def test_production_compose_never_overrides_packaged_source():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert ":/home/dwarf/dwarf-fw/dwarf" not in compose


def test_controller_reads_only_dashboard_version_discovery_overlay():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert (
        "ADA2_DWARF_VERSION_CATALOG_STATE_DIR: /var/dwarf/dashboard-state"
        in compose
    )
    assert (
        "state/version-catalog:/var/dwarf/dashboard-state/version-catalog:ro"
        in compose
    )


def test_controller_uses_ephemeral_private_docker_config_with_read_only_root():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")
    dashboard, controller = compose.split("\n  dwarf-controller:\n", 1)
    controller = controller.split("\nvolumes:\n", 1)[0]

    assert "DOCKER_CONFIG: /tmp/dwarf-docker-config" in controller
    assert "DOCKER_CONFIG:" not in dashboard
    assert "${DOCKER_CONFIG" not in controller
    assert "read_only: true" in controller
    assert "    tmpfs:\n      - /tmp\n" in controller
    assert "/home/dwarf/.docker" not in controller


def test_image_records_public_source_revision():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(encoding="utf-8")
    build = (ROOT / "delivery/scripts/build-image.sh").read_text(encoding="utf-8")
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert "ARG DWARF_SOURCE_REVISION" in dockerfile
    assert "org.opencontainers.image.revision=${DWARF_SOURCE_REVISION}" in dockerfile
    assert "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}" in dockerfile
    assert "ARG SOURCE_DATE_EPOCH" in dockerfile
    assert "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}" in dockerfile
    assert "--build-arg" in build
    assert "DWARF_SOURCE_REVISION" in build
    assert 'SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}' in build
    assert "git -C \"${PACKAGE_ROOT}\" log -1 --format=%ct" in (
        ROOT / "delivery/scripts/common.sh"
    ).read_text(encoding="utf-8")
    assert "DWARF_SOURCE_REVISION:" not in compose
    assert "SOURCE_DATE_EPOCH:" not in compose


def test_runtime_image_pins_docker_compose_plugin():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "ARG DOCKER_COMPOSE_VERSION=" in dockerfile
    assert "docker-compose=${DOCKER_COMPOSE_VERSION}" in dockerfile


def test_runtime_image_pins_qualified_adapter_shared_libraries():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "ARG LIBSODIUM_VERSION=1.0.18-1+deb13u1" in dockerfile
    assert "ARG LIBSECP256K1_VERSION=0.5.0-2+b1" in dockerfile
    assert "libsodium23=${LIBSODIUM_VERSION}" in dockerfile
    assert "libsecp256k1-2=${LIBSECP256K1_VERSION}" in dockerfile


def test_runtime_image_packages_proven_amaru_control_assets():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "COPY antithesis/cardano_amaru_relay_bootstrap_control/ "
        "/home/dwarf/dwarf-fw/antithesis/cardano_amaru_relay_bootstrap_control/"
    ) in dockerfile


def test_dashboard_image_records_component_and_catalog_identity():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(encoding="utf-8")
    build = (ROOT / "delivery/scripts/build-image.sh").read_text(encoding="utf-8")

    assert "ARG DWARF_COMPONENT_ROLE=dashboard" in dockerfile
    assert "ARG DWARF_CATALOG_REVISION=unknown" in dockerfile
    assert "python3 -m profile_manager.release_identity generate" in dockerfile
    assert "dwarf/release-identity.json" in dockerfile
    assert 'org.opencontainers.image.title="DWARF dashboard"' in dockerfile
    assert "io.dwarf.component.role=${DWARF_COMPONENT_ROLE}" in dockerfile
    assert "io.dwarf.catalog.revision=${DWARF_CATALOG_REVISION}" in dockerfile
    assert '--build-arg "DWARF_COMPONENT_ROLE=dashboard"' in build
    assert '--build-arg "DWARF_CATALOG_REVISION=${DWARF_CATALOG_REVISION}"' in build
    assert "verify-build-source.sh" in build


def test_release_identity_generator_hashes_actual_packaged_catalog(tmp_path):
    dwarf_root = tmp_path / "dwarf"
    scenarios = dwarf_root / "scenarios"
    primitives = dwarf_root / "primitives"
    scenarios.mkdir(parents=True)
    primitives.mkdir()
    (scenarios / "b.yaml").write_text("id: b\n", encoding="utf-8")
    (scenarios / "a.yaml").write_text("id: a\n", encoding="utf-8")
    (primitives / "registry.json").write_text(
        json.dumps({"primitives": {"p1": {"family": "load"}, "p2": {"family": "probe"}}}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "identity.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "dwarf")
    command = [
        sys.executable,
        "-m",
        "profile_manager.release_identity",
        "generate",
        "--dwarf-root",
        str(dwarf_root),
        "--output",
        str(output),
        "--component-role",
        "dashboard",
        "--release-revision",
        "a" * 40,
        "--catalog-revision",
        "a" * 40,
    ]

    first = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
    first_bytes = output.read_bytes() if output.exists() else b""
    second = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == first_bytes
    identity = json.loads(first_bytes)
    assert identity["packaged_scenario_count"] == 2
    assert identity["packaged_primitive_count"] == 2
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", identity["catalog_digest"])
    (scenarios / "a.yaml").write_text("id: a\nchanged: true\n", encoding="utf-8")
    changed = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
    assert changed.returncode == 0, changed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["catalog_digest"] != identity["catalog_digest"]

    controller_command = [
        *command[: command.index("dashboard")],
        "controller",
        *command[command.index("dashboard") + 1 :],
    ]
    controller = subprocess.run(
        controller_command,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert controller.returncode == 0, controller.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["component_role"] == "controller"


def test_build_source_guard_accepts_only_clean_exact_checkout(tmp_path):
    repo = tmp_path / "repo"
    head = _clean_git_repo(repo)
    (repo / "ignored.log").write_text("ignored\n", encoding="utf-8")

    result = _run_build_guard(repo, head, head)

    assert result.returncode == 0, result.stderr


def test_build_source_guard_requires_git_checkout(tmp_path):
    directory = tmp_path / "not-a-checkout"
    directory.mkdir()

    result = _run_build_guard(directory, "a" * 40, "a" * 40)

    assert result.returncode != 0
    assert "package root is not a Git checkout" in result.stderr


def test_build_source_guard_rejects_revision_mismatch_and_dirty_files(tmp_path):
    repo = tmp_path / "repo"
    head = _clean_git_repo(repo)

    wrong_source = _run_build_guard(repo, "f" * 40, head)
    wrong_catalog = _run_build_guard(repo, head, "e" * 40)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    dirty_tracked = _run_build_guard(repo, head, head)
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    dirty_untracked = _run_build_guard(repo, head, head)

    assert wrong_source.returncode != 0
    assert "DWARF_SOURCE_REVISION does not match HEAD" in wrong_source.stderr
    assert wrong_catalog.returncode != 0
    assert "DWARF_CATALOG_REVISION does not match HEAD" in wrong_catalog.stderr
    assert dirty_tracked.returncode != 0
    assert "worktree is not clean" in dirty_tracked.stderr
    assert dirty_untracked.returncode != 0
    assert "worktree is not clean" in dirty_untracked.stderr


def test_build_image_invokes_source_guard_before_docker(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "delivery" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("build-image.sh", "common.sh", "verify-build-source.sh"):
        source = ROOT / "delivery" / "scripts" / name
        if source.exists():
            shutil.copy2(source, scripts / name)
    head = _clean_git_repo(checkout)
    (checkout / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    environment = dict(os.environ)
    environment["DWARF_SOURCE_REVISION"] = head
    environment["DWARF_CATALOG_REVISION"] = head

    result = subprocess.run(
        ["bash", str(scripts / "build-image.sh")],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "worktree is not clean" in result.stderr
    assert "missing package component" not in result.stderr
    assert "missing required command: docker" not in result.stderr


def test_build_context_stager_excludes_ignored_runtime_and_evidence(tmp_path):
    repo = tmp_path / "repo"
    head = _clean_git_repo(repo)
    (repo / ".gitignore").write_text(
        "ignored.log\ndwarf/runs/\ndwarf/evidence/\n", encoding="utf-8"
    )
    tracked = repo / "dwarf" / "profile_manager" / "tracked.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("TRACKED = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "tracked build input")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "dwarf" / "runs").mkdir(parents=True)
    (repo / "dwarf" / "runs" / "sensitive.key").write_text("secret\n", encoding="utf-8")
    (repo / "dwarf" / "evidence").mkdir(parents=True)
    (repo / "dwarf" / "evidence" / "private.json").write_text("secret\n", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()

    result = subprocess.run(
        ["bash", str(BUILD_STAGER), str(repo), head, str(staged)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (staged / "dwarf" / "profile_manager" / "tracked.py").is_file()
    assert not (staged / ".git").exists()
    assert not (staged / "dwarf" / "runs" / "sensitive.key").exists()
    assert not (staged / "dwarf" / "evidence" / "private.json").exists()


def test_build_image_uses_ephemeral_archived_context(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "delivery" / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "build-image.sh",
        "common.sh",
        "verify-build-source.sh",
        "stage-build-context.sh",
    ):
        source = ROOT / "delivery" / "scripts" / name
        if source.exists():
            shutil.copy2(source, scripts / name)
    required_files = {
        "dwarf/cardano-profile": "#!/usr/bin/env python3\n",
        "dwarf/profile_manager/__init__.py": "",
        "infrastructure/docker/dwarf-fw.Dockerfile": "FROM scratch\n",
        "infrastructure/docker/dwarf-controller.Dockerfile": "ARG DWARF_FRAMEWORK_IMAGE\nFROM ${DWARF_FRAMEWORK_IMAGE}\n",
        "infrastructure/docker/dwarf-fw-entrypoint.sh": "#!/usr/bin/env bash\n",
        "infrastructure/docker/requirements-framework.txt": "",
        "delivery/docker-compose.dwarf.yml": "services: {}\n",
    }
    for relative, content in required_files.items():
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    head = _clean_git_repo(checkout)
    (checkout / ".gitignore").write_text(
        "ignored.log\ndwarf/runs/\ndwarf/evidence/\n", encoding="utf-8"
    )
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-qm", "ignore runtime data")
    head = _git(checkout, "rev-parse", "HEAD")
    (checkout / "dwarf" / "runs").mkdir(parents=True)
    (checkout / "dwarf" / "runs" / "sensitive.key").write_text("secret\n", encoding="utf-8")
    (checkout / "dwarf" / "evidence").mkdir(parents=True)
    (checkout / "dwarf" / "evidence" / "private.json").write_text("secret\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "build" ]]; then
  context="${!#}"
  dockerfile=""
  previous=""
  for argument in "$@"; do
    if [[ "${previous}" == "-f" ]]; then dockerfile="${argument}"; fi
    previous="${argument}"
  done
  sensitive=no
  if [[ -e "${context}/dwarf/runs/sensitive.key" || -e "${context}/dwarf/evidence/private.json" ]]; then sensitive=yes; fi
  printf 'build\t%s\t%s\t%s\n' "${context}" "${dockerfile}" "${sensitive}" >>"${DOCKER_LOG}"
else
  printf '%s\n' "$*" >>"${DOCKER_LOG}"
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    docker_log = tmp_path / "docker.log"
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["DOCKER_LOG"] = str(docker_log)
    environment["DWARF_SOURCE_REVISION"] = head
    environment["DWARF_CATALOG_REVISION"] = head

    result = subprocess.run(
        ["bash", str(scripts / "build-image.sh")],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    build_record = next(
        line.split("\t") for line in docker_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("build\t")
    )
    _, context, dockerfile, sensitive = build_record
    assert Path(context) != checkout
    assert Path(dockerfile) == Path(context) / "infrastructure/docker/dwarf-fw.Dockerfile"
    assert sensitive == "no"
    assert not Path(context).exists()


def test_runtime_root_default_is_checkout_independent():
    common = (ROOT / "delivery/scripts/common.sh").read_text(encoding="utf-8")
    getting_started = (ROOT / "dwarf/dashboard/templates/learn/getting_started.j2").read_text(
        encoding="utf-8"
    )
    operator_runbook = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    uninstall = (ROOT / "delivery/scripts/uninstall.sh").read_text(encoding="utf-8")

    assert '${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf' in common
    assert "${PACKAGE_ROOT}/var" not in common
    assert "~/.local/share/dwarf" in getting_started
    assert "~/.local/share/dwarf" in operator_runbook
    assert "\ndelivery/scripts/" not in getting_started
    assert "\ndelivery/scripts/" not in operator_runbook
    assert "~/.local/share/dwarf" in install
    assert "package-local runtime data under var/" not in uninstall


def test_dashboard_onboarding_uses_the_supported_one_script_install_contract():
    getting_started = (
        ROOT / "dwarf/dashboard/templates/learn/getting_started.j2"
    ).read_text(encoding="utf-8")
    operator_runbook = (
        ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2"
    ).read_text(encoding="utf-8")

    for source in (getting_started, operator_runbook):
        assert "run the four delivery scripts" not in source
        assert "bash delivery/scripts/install.sh       # build images, seed state, and start" in source
        assert "DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/install.sh" in source
        assert "bash delivery/scripts/build-image.sh   #" not in source
        assert "bash delivery/scripts/deploy.sh        #" not in source


def test_remote_control_channel_refresh_preserves_configured_endpoint():
    operations = (ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
    operator_runbook = (
        ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2"
    ).read_text(encoding="utf-8")

    for source in (operations, operator_runbook):
        assert "DEPLOY_HOST" in source
        assert "DEPLOY_USER" in source
        assert "install.sh --control-channel --no-build" in source
        assert "127.0.0.1" in source
        assert "known_hosts" in source


def test_public_delivery_examples_work_with_non_executable_archive_modes():
    docs = [
        ROOT / "README.md",
        ROOT / "INSTALL.md",
        ROOT / "OPERATIONS.md",
        ROOT / "infrastructure/docker/README.md",
        ROOT / "dwarf/README.md",
        ROOT / "dwarf/dashboard/static/overview.html",
        ROOT / "dwarf/profile_manager/data/learn_docs.py",
    ]

    for path in docs:
        source = path.read_text(encoding="utf-8")
        assert "./delivery/scripts/" not in source, str(path)
        assert "<code>delivery/scripts/build-image.sh &&" not in source, str(path)
        for line in source.splitlines():
            command = line.strip()
            if " delivery/scripts/" in command:
                assert "bash delivery/scripts/" in command, f"{path}: {line}"
            assert not command.startswith("delivery/scripts/"), f"{path}: {line}"


def test_framework_readme_describes_retained_runtime_without_stale_counts():
    source = (ROOT / "dwarf/README.md").read_text(encoding="utf-8")

    assert "239 scenarios" not in source
    assert "package-local `var/`" not in source
    assert "~/.local/share/dwarf" in source


def test_retention_defaults_and_runbook_match_keep_until_manual_removal():
    config = DeploymentConfig.from_dict({})
    runbook = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )

    assert config.runs_retention_days == 0
    assert config.bundles_retention_days == 0
    assert "Auto-pruned" not in runbook
    assert "until manually removed" in runbook


def test_dashboard_cli_commands_do_not_require_executable_archive_modes():
    entrypoint = str(dashboard.DEFAULT_CLI_ENTRYPOINT)

    scenario = dashboard._default_cli_command_builder(
        "scenario_run", scenario_path="dwarf/scenarios/example.yaml"
    )
    antithesis = dashboard._build_antithesis_command(
        "/api/moog/preflight", {}
    )

    assert scenario[:2] == [sys.executable, entrypoint]
    assert antithesis[:2] == [sys.executable, entrypoint]


def test_control_shim_routes_catalog_scenario_run_to_host(monkeypatch, tmp_path):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    scenario = scenarios / "mixed-demo.yaml"
    scenario.write_text('{"id":"mixed-demo"}\n', encoding="utf-8")
    config = DeploymentConfig.from_dict(
        {
            "host": "127.0.0.1",
            "ssh_user": "nigel",
            "ssh_key_path": str(tmp_path / "key"),
        }
    )
    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    monkeypatch.setattr(dashboard, "load_config", lambda: config)

    command = dashboard._default_cli_command_builder(
        "scenario_run", scenario_path=str(scenario)
    )

    assert command[-2:] == ["nigel@127.0.0.1", "scenario mixed-demo"]
    assert command[:4] == ["ssh", "-n", "-o", "BatchMode=yes"]


def test_control_shim_launch_command_accepts_only_strict_launch_ids(tmp_path):
    config = DeploymentConfig.from_dict(
        {
            "host": "127.0.0.1",
            "ssh_user": "nigel",
            "ssh_key_path": str(tmp_path / "key"),
        }
    )

    command = render_launch_command(config, "launch-0123456789abcdef01234567")

    assert command[-2:] == [
        "nigel@127.0.0.1",
        "launch launch-0123456789abcdef01234567",
    ]
    with pytest.raises(ValueError):
        render_launch_command(config, "../scenario.yaml")

    preflight = render_launch_preflight_command(
        config, "launch-0123456789abcdef01234567"
    )
    assert preflight[-1] == "launch-preflight launch-0123456789abcdef01234567"


def test_control_channel_provisions_restricted_launch_root():
    shim = (ROOT / "delivery/control-plane/dwarf-deploy-shim.py").read_text(
        encoding="utf-8"
    )
    provision = (
        ROOT / "delivery/control-plane/provision-control-channel.sh"
    ).read_text(encoding="utf-8")

    assert '"launch"' in shim
    assert '"launch-preflight"' in shim
    assert "_LAUNCH_ID_RE" in shim
    assert "LAUNCH_ROOT" in shim
    assert "load_launch" in shim
    assert "ADA2_DWARF_LAUNCH_ID" in shim
    assert "LAUNCH_ROOT=${LAUNCH_ROOT:-$STATE_DIR/launches}" in provision
    assert "LAUNCH_ROOT=$LAUNCH_ROOT" in provision


def test_control_shim_rejects_scenario_outside_catalog(monkeypatch, tmp_path):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text('{"id":"outside"}\n', encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))

    try:
        dashboard._default_cli_command_builder(
            "scenario_run", scenario_path=str(outside)
        )
    except ValueError as exc:
        assert "scenario catalog" in str(exc)
    else:
        raise AssertionError("scenario paths outside the catalog must be rejected")


def test_dashboard_sse_reports_process_start_failure(monkeypatch):
    def fail_to_start(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "cardano-profile")

    monkeypatch.setattr(subprocess, "Popen", fail_to_start)

    events = b"".join(dashboard.stream_subprocess_sse(["cardano-profile", "--help"]))

    assert b"event: error\n" in events
    assert b"Permission denied" in events
    assert b'event: done\ndata: {"exit_code": 126}' in events


def test_optional_moog_bootstrap_does_not_require_executable_archive_modes():
    common = (ROOT / "delivery/scripts/common.sh").read_text(encoding="utf-8")

    assert common.count(
        'docker exec -i "${DWARF_CONTAINER_NAME}" python3 '
        '/home/dwarf/dwarf-fw/dwarf/cardano-profile'
    ) == 3


def test_catalog_sync_refreshes_packaged_files_and_preserves_runtime_only_files(tmp_path):
    package = tmp_path / "package"
    scripts = package / "delivery/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "delivery/scripts/common.sh", scripts / "common.sh")

    source = package / "dwarf/scenarios"
    source.mkdir(parents=True)
    (source / "packaged.yaml").write_text("version: current\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime"
    runtime = runtime_root / "state/scenarios"
    runtime.mkdir(parents=True)
    (runtime / "packaged.yaml").write_text("version: stale\n", encoding="utf-8")
    (runtime / "custom.yaml").write_text("version: custom\n", encoding="utf-8")

    subprocess.run(
        ["bash", "-c", 'source "$1"; seed_scenarios', "bash", str(scripts / "common.sh")],
        check=True,
        env={**dict(os.environ), "DWARF_RUNTIME_ROOT": str(runtime_root)},
    )

    assert (runtime / "packaged.yaml").read_text(encoding="utf-8") == "version: current\n"
    assert (runtime / "custom.yaml").read_text(encoding="utf-8") == "version: custom\n"


def test_routine_deploy_synchronizes_all_packaged_catalogs():
    deploy = (ROOT / "delivery/scripts/deploy.sh").read_text(encoding="utf-8")

    assert "seed_scenarios" in deploy
    assert "seed_manifests" in deploy
    assert "seed_profiles" in deploy
    assert "seed_measurements" in deploy
    assert "seed_measurement_profiles" in deploy


def test_measurement_catalog_sync_refreshes_packaged_files_and_preserves_runtime_only_files(tmp_path):
    package = tmp_path / "package"
    scripts = package / "delivery/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "delivery/scripts/common.sh", scripts / "common.sh")

    source_measurements = package / "dwarf/measurements"
    source_profiles = package / "dwarf/measurement-profiles"
    source_measurements.mkdir(parents=True)
    source_profiles.mkdir(parents=True)
    (source_measurements / "packaged.yaml").write_text("version: current\n", encoding="utf-8")
    (source_profiles / "packaged.yaml").write_text("version: current\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime"
    runtime_measurements = runtime_root / "state/measurements"
    runtime_profiles = runtime_root / "state/measurement-profiles"
    runtime_measurements.mkdir(parents=True)
    runtime_profiles.mkdir(parents=True)
    (runtime_measurements / "packaged.yaml").write_text("version: stale\n", encoding="utf-8")
    (runtime_measurements / "custom.yaml").write_text("version: custom\n", encoding="utf-8")
    (runtime_profiles / "packaged.yaml").write_text("version: stale\n", encoding="utf-8")
    (runtime_profiles / "custom.yaml").write_text("version: custom\n", encoding="utf-8")

    subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; seed_measurements; seed_measurement_profiles',
            "bash",
            str(scripts / "common.sh"),
        ],
        check=True,
        env={**dict(os.environ), "DWARF_RUNTIME_ROOT": str(runtime_root)},
    )

    for runtime in (runtime_measurements, runtime_profiles):
        assert (runtime / "packaged.yaml").read_text(encoding="utf-8") == "version: current\n"
        assert (runtime / "custom.yaml").read_text(encoding="utf-8") == "version: custom\n"


def test_direct_deploy_preserves_existing_control_channel(tmp_path):
    common = ROOT / "delivery/scripts/common.sh"
    deploy = (ROOT / "delivery/scripts/deploy.sh").read_text(encoding="utf-8")
    key = tmp_path / "ssh_deploy_key"
    key.write_text("provisioned-key\n", encoding="utf-8")

    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; export DWARF_SSH_KEY_PATH="$2"; '
            'unset ADA2_DWARF_CONTROL_SHIM; enable_existing_control_channel; '
            'printf "%s" "$ADA2_DWARF_CONTROL_SHIM"',
            "bash",
            str(common),
            str(key),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "1"
    assert "enable_existing_control_channel" in deploy


def test_smoke_run_uses_one_fixed_control_verb_in_shim_mode(monkeypatch):
    calls = []

    def fake_ssh(config, remote_command, timeout=None, dry_run=False, verb=None):
        calls.append((remote_command, verb))
        return CommandResult(0, "ok\n", "", "ssh cardano-box smoke environment-smoke")

    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")
    monkeypatch.setattr(cli, "ssh_command", fake_ssh)
    monkeypatch.setattr(cli, "rsync_to", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("shim-mode smoke must not use rsync")
    ))

    result = cli._run_smoke_via_remote_scenario(
        SimpleNamespace(),
        DeploymentConfig.from_dict({}),
        find_smoke_test("environment-smoke"),
    )

    assert result.returncode == 0
    assert len(calls) == 1
    assert calls[0][1] == ("smoke", "environment-smoke")


def test_control_shim_builds_known_smoke_and_rejects_unknown_id(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\n",
        encoding="utf-8",
    )
    base_env = dict(os.environ)

    known = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={**base_env, "SSH_ORIGINAL_COMMAND": "smoke environment-smoke --dry-run"},
    )
    unknown = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={**base_env, "SSH_ORIGINAL_COMMAND": "smoke not-registered --dry-run"},
    )

    assert known.returncode == 0
    assert "SMOKE_ID=environment-smoke" in known.stdout
    assert unknown.returncode == 77
    assert "unknown-smoke-test" in unknown.stderr


def test_control_shim_runs_catalog_scenario_into_shared_runtime(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    scenarios = tmp_path / "state" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "mixed-demo.yaml").write_text(
        '{"id":"mixed-demo"}\n', encoding="utf-8"
    )
    runs = tmp_path / "runs"
    bundles = tmp_path / "bundles"
    (tmp_path / "dwarf-control.conf").write_text(
        "\n".join(
            [
                f"DWARF_ROOT={ROOT / 'dwarf'}",
                f"SCENARIOS_DIR={scenarios}",
                f"STATE_DIR={tmp_path / 'state'}",
                f"RUNS_DIR={runs}",
                f"BUNDLES_DIR={bundles}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "scenario mixed-demo --dry-run",
        },
    )

    assert completed.returncode == 0
    assert f"ADA2_DWARF_RUNS_DIR={runs}" in completed.stdout
    assert f"ADA2_DWARF_STATE_DIR={tmp_path / 'state'}" in completed.stdout
    assert f"ADA2_DWARF_BUNDLES_DIR={bundles}" in completed.stdout
    assert f"ADA2_DWARF_SCENARIOS_DIR={scenarios}" in completed.stdout
    assert f"python3 cardano-profile scenario run {scenarios / 'mixed-demo.yaml'}" in completed.stdout


def test_control_shim_labels_legacy_launch_and_coverage_as_dashboard_runs(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    scenarios = tmp_path / "state" / "scenarios"
    scenarios.mkdir(parents=True)
    scenario = scenarios / "mixed-demo.yaml"
    scenario.write_text('{"id":"mixed-demo"}\n', encoding="utf-8")
    launches = tmp_path / "state" / "launches"
    launch_id = "launch-0123456789abcdef01234567"
    launch = launches / launch_id
    launch.mkdir(parents=True)
    launch_scenario = launch / "scenario.yaml"
    launch_scenario.write_bytes(scenario.read_bytes())
    (launch / "plan.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "artifacts": {
                    "scenario.yaml": "sha256:"
                    + hashlib.sha256(launch_scenario.read_bytes()).hexdigest()
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "dwarf-control.conf").write_text(
        "\n".join(
            [
                f"DWARF_ROOT={ROOT / 'dwarf'}",
                f"SCENARIOS_DIR={scenarios}",
                f"STATE_DIR={tmp_path / 'state'}",
                f"RUNS_DIR={tmp_path / 'runs'}",
                f"BUNDLES_DIR={tmp_path / 'bundles'}",
                f"LAUNCH_ROOT={launches}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    base_env = dict(os.environ)

    launch_result = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **base_env,
            "SSH_ORIGINAL_COMMAND": f"launch {launch_id} --dry-run",
        },
    )
    coverage_result = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **base_env,
            "SSH_ORIGINAL_COMMAND": "coverage mixed-demo --dry-run",
        },
    )

    assert launch_result.returncode == 0, launch_result.stderr
    assert coverage_result.returncode == 0, coverage_result.stderr
    assert "--origin dashboard" in launch_result.stdout
    assert "--origin dashboard" in coverage_result.stdout


def test_control_shim_rejects_unknown_scenario(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\nSCENARIOS_DIR={scenarios}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "scenario not-registered --dry-run",
        },
    )

    assert completed.returncode == 77
    assert "unknown-scenario" in completed.stderr


def test_control_channel_provisions_shared_scenario_runtime_paths():
    provision = (
        ROOT / "delivery/control-plane/provision-control-channel.sh"
    ).read_text(encoding="utf-8")

    assert 'RUNS_DIR=${RUNS_DIR:-$RUNTIME_ROOT/runs}' in provision
    assert 'BUNDLES_DIR=${BUNDLES_DIR:-$RUNTIME_ROOT/bundles}' in provision
    assert 'STATE_DIR=$STATE_DIR' in provision
    assert 'RUNS_DIR=$RUNS_DIR' in provision
    assert 'BUNDLES_DIR=$BUNDLES_DIR' in provision


def test_control_shim_builds_read_only_topology_health_command(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    state = tmp_path / "state"
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\nSTATE_DIR={state}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "topology-health --dry-run",
        },
    )

    assert completed.returncode == 0
    assert "check_cardano_amaru_topology.py" in completed.stdout
    assert "--topology cardano_amaru" in completed.stdout
    assert "--sample-seconds 10" in completed.stdout
    assert str(state / "topology-health" / "latest.json") in completed.stdout
    assert "redeploy" not in completed.stdout


def test_control_shim_builds_profile_scoped_health_command(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\n", encoding="utf-8"
    )
    completed = subprocess.run(
        [sys.executable, str(shim)], text=True, capture_output=True, check=False,
        env={**dict(os.environ), "SSH_ORIGINAL_COMMAND": "profile-health profile-v-cardano-measurement-nanoseconds-v2 --dry-run"},
    )
    missing = subprocess.run(
        [sys.executable, str(shim)], text=True, capture_output=True, check=False,
        env={**dict(os.environ), "SSH_ORIGINAL_COMMAND": "profile-health --dry-run"},
    )

    assert completed.returncode == 0
    assert "/opt/dwarf/cardano-profiles/profile-v-cardano-measurement-nanoseconds-v2" in completed.stdout
    assert "## tip" in completed.stdout
    assert "DWARF_PERSISTED_DEPLOYMENT=" in completed.stdout
    assert "versioned-deployment.json" in completed.stdout
    assert missing.returncode == 77


def test_control_shim_uses_exact_topology_probe_for_amaru_control_profile(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [sys.executable, str(shim)], text=True, capture_output=True, check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": (
                "profile-health profile-w-amaru-measurement-plutus-v2 --dry-run"
            ),
        },
    )

    assert completed.returncode == 0
    assert "check_cardano_amaru_topology.py" in completed.stdout
    assert "--project dwarf-profile-w-amaru-measurement-plutus-v2" in completed.stdout
    assert "--sample-seconds 10" in completed.stdout
    assert "dashboard-health-latest.json" in completed.stdout
    assert "DWARF_PERSISTED_DEPLOYMENT=" in completed.stdout
    assert "versioned-deployment.json" in completed.stdout


def test_control_shim_allows_only_fixed_topology_redeploy_id(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    package = ROOT / "antithesis" / "cardano_amaru_relay_bootstrap_control"
    (tmp_path / "dwarf-control.conf").write_text(
        "\n".join(
            [
                f"DWARF_ROOT={ROOT / 'dwarf'}",
                f"STATE_DIR={tmp_path / 'state'}",
                f"TOPOLOGY_PACKAGE_DIR={package}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    allowed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "topology-redeploy cardano_amaru --dry-run",
        },
    )
    refused_id = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "topology-redeploy arbitrary --dry-run",
        },
    )
    refused_path = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": "topology-redeploy ../../tmp --dry-run",
        },
    )

    assert allowed.returncode == 0
    assert "redeploy_cardano_amaru_topology.py" in allowed.stdout
    assert "--topology cardano_amaru" in allowed.stdout
    assert "--confirm" in allowed.stdout
    assert refused_id.returncode == 77
    assert "unsupported-topology" in refused_id.stderr
    assert refused_path.returncode == 77


def test_dashboard_renders_fixed_topology_control_verbs(monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")
    config = DeploymentConfig.from_dict({})

    health = render_topology_health_command(config)
    redeploy = render_topology_redeploy_command(config, topology_id="cardano_amaru")

    assert health[-1] == "topology-health"
    assert redeploy[-1] == "topology-redeploy cardano_amaru"


def test_topology_action_endpoint_previews_selected_profile_and_streams_exact_apply():
    calls = []

    class Actions:
        def preview(self, action, snapshot):
            calls.append(("preview", action, snapshot["profile_id"]))
            return {
                "status": "planned",
                "plan_id": "plan-profile-e-001",
                "plan_digest": "sha256:" + "a" * 64,
                "action": action,
                "topology_id": snapshot["topology_id"],
            }

        def apply(self, plan_id, plan_digest, *, confirmation=None):
            calls.append(("apply", plan_id, plan_digest, confirmation))
            yield {"stage": "accepted", "execution_id": "execution-profile-e-001"}
            yield {"stage": "completed", "status": "applied"}

    preview_result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "preview",
                "profile_id": "profile-e-haskell-preview-proof",
                "action": "repair",
            }
        ).encode(),
        expected_token="secret",
        action_service=Actions(),
        snapshot_loader=lambda profile_id: {
            "profile_id": profile_id,
            "topology_id": profile_id,
        },
    )
    assert preview_result is not None
    status, content_type, body = preview_result
    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body)["plan_id"] == "plan-profile-e-001"

    apply_result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "apply",
                "plan_id": "plan-profile-e-001",
                "plan_digest": "sha256:" + "a" * 64,
            }
        ).encode(),
        expected_token="secret",
        action_service=Actions(),
    )
    status, content_type, body = apply_result
    assert status == 200
    assert content_type.startswith("text/event-stream")
    streamed = b"".join(body)
    assert b'"stage": "accepted"' in streamed
    assert b'"stage": "completed"' in streamed
    assert calls == [
        ("preview", "repair", "profile-e-haskell-preview-proof"),
        (
            "apply",
            "plan-profile-e-001",
            "sha256:" + "a" * 64,
            None,
        ),
    ]


def test_topology_action_preview_uses_current_catalog_desired_with_retained_health(
    monkeypatch, tmp_path,
):
    from profile_manager.data import operate_topology_health
    from profile_manager import profiles as profiles_module

    retained_desired = {
        "schema_version": "v1",
        "topology_id": "profile-r-amaru-measurement-stock-control",
        "profile_id": "profile-r-amaru-measurement-stock-control",
        "catalog_revision": "cfb1a19a29c7bab11f2a29359147be38b5a3e3dc937b63bd40cda4cae8dfa39b",
        "plan_sha256": "sha256:b627ed2d9e437155543a1d90ec8d4e28c4068c44f8ce2bed3150ef07c48585e4",
        "nodes": [
            {
                "id": "p1",
                "implementation": "cardano-node",
                "version": "10.11.0",
                "image": "ghcr.io/example/cardano-node@sha256:" + "a" * 64,
            },
            {
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "10.11.20260912",
                "image": "ghcr.io/example/amaru@sha256:" + "b" * 64,
            },
        ],
    }
    current_desired = {
        **retained_desired,
        "catalog_revision": "b9fbb14b56fb6cee2c1f4ffb78afb0a0cf8df8428ff24eba0c8e2c8a3d83a37c",
        "plan_sha256": "sha256:" + "2" * 64,
    }
    observed = {
        "managed": [{"id": "owned-p1", "topology_id": retained_desired["profile_id"]}],
        "unmanaged": [{"id": "protected-other"}],
    }
    health = {
        "state": "unhealthy",
        "reason_code": "semantic_gate_unhealthy",
        "fresh": True,
    }
    reconciliation = {
        "state": "drifted",
        "reason_code": "catalog_plan_differs",
    }
    action_policy = {
        "allowed": True,
        "reason": "review exact redeploy transition",
        "confirmation_required": True,
    }
    retained_snapshot = {
        "schema_version": "v1",
        "profile_id": retained_desired["profile_id"],
        "topology_id": retained_desired["profile_id"],
        "desired": retained_desired,
        "observed": observed,
        "health": health,
        "reconciliation": reconciliation,
        "actions": {"redeploy": action_policy},
    }
    profile = SimpleNamespace(id=retained_desired["profile_id"])
    monkeypatch.setattr(
        operate_topology_health,
        "_default_probe",
        lambda profile_id: json.loads(json.dumps(retained_snapshot)),
    )
    monkeypatch.setattr(profiles_module, "find_profile", lambda profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda selected_profile, preview: current_desired,
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda profile_data: {
            "catalog_revision": current_desired["catalog_revision"],
        },
    )

    captured = {}

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        captured["controller"] = {
            "operation": operation,
            "parameters": parameters,
            "timeout_seconds": timeout_seconds,
            "idempotency_key": idempotency_key,
        }
        return {
            "status": "planned",
            "plan_id": "plan-current-catalog",
            "plan_digest": "sha256:" + "e" * 64,
            "action": "redeploy",
            "topology_id": retained_desired["profile_id"],
        }

    service = TopologyActionService(controller, tmp_path)

    class Actions:
        def preview(self, action, snapshot):
            captured.update({"action": action, "snapshot": snapshot})
            return service.preview(action, snapshot)

    result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "preview",
                "profile_id": retained_desired["profile_id"],
                "action": "redeploy",
            }
        ).encode(),
        expected_token="secret",
        action_service=Actions(),
    )

    assert result[0] == 200
    assert captured["action"] == "redeploy"
    assert captured["snapshot"]["desired"] == current_desired
    assert captured["snapshot"]["observed"] == observed
    assert captured["snapshot"]["health"] == health
    assert captured["snapshot"]["reconciliation"] == reconciliation
    assert captured["snapshot"]["actions"]["redeploy"] == action_policy
    assert captured["controller"]["operation"] == "topology.plan"
    assert (
        captured["controller"]["parameters"]["topology"]["catalog_revision"]
        == current_desired["catalog_revision"]
    )


def test_current_desired_topology_rejects_profile_identity_mismatch(monkeypatch):
    from profile_manager.data import operate_topology_health
    from profile_manager import profiles as profiles_module

    profile = SimpleNamespace(id="profile-r-amaru-measurement-stock-control")
    monkeypatch.setattr(profiles_module, "find_profile", lambda profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda selected_profile, preview: {
            "profile_id": "foreign-profile",
            "topology_id": "foreign-profile",
            "nodes": [],
        },
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda profile_data: {"catalog_revision": "d" * 64},
    )

    with pytest.raises(
        ValueError, match="resolved desired topology identity does not match request"
    ):
        operate_topology_health._current_desired_topology(profile.id)


def test_topology_action_snapshot_preserves_operator_selected_registration(monkeypatch):
    from profile_manager.data import operate_topology_health
    from profile_manager import profiles as profiles_module

    profile_id = "profile-r-amaru-measurement-stock-control"
    registration_id = "registration-cb375efadbb3424faab322d42e87852c"
    overlay_revision = "sha256:" + "6" * 64
    retained = {
        "profile_id": profile_id,
        "topology_id": profile_id,
        "desired": {
            "profile_id": profile_id,
            "topology_id": profile_id,
            "operator_registration_id": registration_id,
            "operator_overlay_revision": overlay_revision,
        },
        "observed": {"managed": [], "unmanaged": []},
        "actions": {"redeploy": {"allowed": True}},
    }
    expected = {
        **retained["desired"],
        "catalog_revision": "d" * 64,
        "nodes": [],
    }
    profile = SimpleNamespace(id=profile_id)
    selected = {}
    monkeypatch.setattr(
        operate_topology_health, "_default_probe", lambda selected_id: retained
    )
    monkeypatch.setattr(profiles_module, "find_profile", lambda selected_id: profile)

    def preview(profile_data, *, operator_registration_id=None):
        selected["registration_id"] = operator_registration_id
        return {"operator_registration_id": operator_registration_id}

    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        preview,
    )
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda selected_profile, version_preview: expected,
    )

    result = operate_topology_health.topology_action_snapshot(profile_id)

    assert selected["registration_id"] == registration_id
    assert result["desired"] == expected
    assert result["observed"] is retained["observed"]


@pytest.mark.parametrize(
    "operator_fields",
    [
        {"operator_registration_id": "registration-cb375efadbb3424faab322d42e87852c"},
        {"operator_overlay_revision": "sha256:" + "6" * 64},
        {"operator_registration_id": None},
        {"operator_overlay_revision": None},
        {
            "operator_registration_id": None,
            "operator_overlay_revision": None,
        },
        {
            "operator_registration_id": "registration-cb375efadbb3424faab322d42e87852c",
            "operator_overlay_revision": "malformed",
        },
    ],
)
def test_topology_action_snapshot_rejects_incomplete_operator_identity(
    monkeypatch, operator_fields
):
    from profile_manager.data import operate_topology_health

    profile_id = "profile-r-amaru-measurement-stock-control"
    monkeypatch.setattr(
        operate_topology_health,
        "_default_probe",
        lambda selected_id: {
            "profile_id": profile_id,
            "topology_id": profile_id,
            "desired": {
                "profile_id": profile_id,
                "topology_id": profile_id,
                **operator_fields,
            },
        },
    )

    with pytest.raises(ValueError, match="operator registration identity is invalid"):
        operate_topology_health.topology_action_snapshot(profile_id)


def test_pre_controller_apply_rejection_is_terminal_for_reserved_execution(tmp_path):
    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        assert operation == "topology.plan"
        return {
            "status": "planned",
            "plan_id": "plan-remove-rejected-001",
            "plan_digest": "sha256:" + "b" * 64,
            "action": "remove",
            "topology_id": "local-lab",
        }

    actions = TopologyActionService(controller, tmp_path)
    preview = actions.preview(
        "remove",
        {
            "profile_id": "local-lab",
            "topology_id": "local-lab",
            "actions": {"remove": {"allowed": True}},
            "observed": {"managed": [], "unmanaged": []},
        },
    )
    result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "apply",
                "plan_id": preview["plan_id"],
                "plan_digest": preview["plan_digest"],
                "execution_id": preview["execution_id"],
                "confirmation": "wrong",
            }
        ).encode(),
        expected_token="secret",
        action_service=actions,
    )

    assert result[0] == 200
    streamed = b"".join(result[2]).decode()
    payload = json.loads(next(line[6:] for line in streamed.splitlines() if line.startswith("data: ")))
    assert payload == {
        "error": "exact destructive confirmation is required",
        "execution_id": preview["execution_id"],
        "stage": "error",
        "terminal": True,
    }


def test_topology_action_endpoint_rejects_unauthenticated_or_untyped_requests():
    requests = [
        ("GET", "/api/topology/actions?token=secret", {}),
        ("POST", "/api/topology/actions", {}),
        (
            "POST",
            "/api/topology/actions?token=secret",
            {"operation": "preview", "profile_id": "../../other", "action": "deploy"},
        ),
        (
            "POST",
            "/api/topology/actions?token=secret",
            {"operation": "shell", "command": "docker rm -f everything"},
        ),
        (
            "POST",
            "/api/topology/actions?token=secret",
            {
                "operation": "apply",
                "plan_id": "plan-1",
                "plan_digest": "sha256:" + "a" * 64,
                "command": "docker ps",
            },
        ),
    ]
    statuses = []
    for method, path, body in requests:
        result = dashboard.dispatch_topology_action_request(
            method=method,
            path=path,
            body=json.dumps(body).encode(),
            expected_token="secret",
            action_service=object(),
        )
        statuses.append(result[0])

    assert statuses == [405, 401, 400, 400, 400]


def test_topology_action_apply_honors_global_active_mutation_lock():
    assert dashboard.try_acquire_mutating_lock() is True
    try:
        result = dashboard.dispatch_topology_action_request(
            method="POST",
            path="/api/topology/actions?token=secret",
            body=json.dumps(
                {
                    "operation": "apply",
                    "plan_id": "plan-profile-e-001",
                    "plan_digest": "sha256:" + "a" * 64,
                }
            ).encode(),
            expected_token="secret",
            action_service=object(),
        )
    finally:
        dashboard.release_mutating_lock()

    assert result[0] == 409
    assert b"mutating action" in result[2]


def test_topology_action_cancel_uses_typed_execution_identity():
    class Actions:
        def cancel(self, execution_id, reason):
            return {"status": "cancelled", "execution_id": execution_id, "reason": reason}

    result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "cancel",
                "execution_id": "execution-profile-e-001",
                "reason": "operator requested cancellation",
            }
        ).encode(),
        expected_token="secret",
        action_service=Actions(),
    )

    assert result[0] == 200
    assert json.loads(result[2])["status"] == "cancelled"


def test_topology_action_status_reconciles_known_execution_identity():
    class Actions:
        def status(self, execution_id):
            return {
                "state": "applied",
                "execution_id": execution_id,
                "audit_receipt": "controller/controller-audit/audit-1.json",
            }

    result = dashboard.dispatch_topology_action_request(
        method="POST",
        path="/api/topology/actions?token=secret",
        body=json.dumps(
            {
                "operation": "status",
                "execution_id": "execution-profile-e-001",
            }
        ).encode(),
        expected_token="secret",
        action_service=Actions(),
    )

    assert result[0] == 200
    assert json.loads(result[2])["state"] == "applied"


def test_control_shim_builds_strict_moog_create_test_command(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({
            "moog": {
                "enabled": True,
                "moog_binary": "/srv/dwarf/bin/moog",
                "requester_wallet_file": "/srv/dwarf/moog-secrets/requester/requester.json",
                "mpfs_host": "https://mpfs.plutimus.com",
                "token_id": "token-id",
            }
        }),
        encoding="utf-8",
    )
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\nCONFIG_PATH={config}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": (
                "moog-create-test pragma-org/dwarf J-GainSec "
                "antithesis/cardano_amaru_miniprotocol_security "
                "63a62fadfaacddb7b1f586bafa62ae74af47cddc 1 1 faults --dry-run"
            ),
        },
    )

    assert completed.returncode == 0
    assert "moog requester create-test" in completed.stdout
    assert "-r pragma-org/dwarf" in completed.stdout
    assert "-d antithesis/cardano_amaru_miniprotocol_security" in completed.stdout
    assert "-c 63a62fadfaacddb7b1f586bafa62ae74af47cddc" in completed.stdout
    assert "--try 1" in completed.stdout
    assert "-t 1" in completed.stdout
    assert "--no-faults" not in completed.stdout


def test_control_shim_rejects_unsafe_moog_create_test_directory(tmp_path):
    shim = tmp_path / "dwarf-deploy-shim"
    shutil.copy2(ROOT / "delivery/control-plane/dwarf-deploy-shim.py", shim)
    (tmp_path / "dwarf-control.conf").write_text(
        f"DWARF_ROOT={ROOT / 'dwarf'}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(shim)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **dict(os.environ),
            "SSH_ORIGINAL_COMMAND": (
                "moog-create-test pragma-org/dwarf J-GainSec "
                "../escape 63a62fadfaacddb7b1f586bafa62ae74af47cddc 1 1 faults --dry-run"
            ),
        },
    )

    assert completed.returncode == 77
    assert "bad-directory" in completed.stderr


def test_environment_smoke_uses_portable_host_paths():
    manifest = json.loads(
        (ROOT / "dwarf/smoke-tests/environment-smoke.json").read_text(encoding="utf-8")
    )
    commands = "\n".join(manifest["commands"])

    assert manifest["working_directory"] == "/opt/dwarf/cardano-profiles"
    assert "/usr/local/bin/cardano-node" not in commands
    assert "/usr/local/bin/cardano-cli" not in commands
    assert "command -v cardano-node" in commands


def test_basic_config_states_the_supported_local_runtime_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "ADA2_PROFILE_MANAGER_CONFIG", str(tmp_path / "state" / "config.yaml")
    )

    html = render_operate_config()

    assert "Local runtime support" in html
    assert "Docker Engine" in html
    assert "Docker Desktop" in html
    assert "Podman" in html
    assert "Remote execution" in html
    assert "multi-host" in html
    assert "deferred" in html.lower()
    assert 'href="/run"' in html
    assert 'href="/operate/status"' in html
    assert "open Advanced" not in html


def test_basic_empty_coverage_links_to_the_supported_optional_afl_path(monkeypatch):
    monkeypatch.setattr(operate_coverage, "latest_coverage_run", lambda: None)
    monkeypatch.setattr(operate_coverage, "coverage_history", lambda limit: [])
    monkeypatch.setattr(
        operate_coverage,
        "coverage_trend_payload",
        lambda: {"empty": True, "targets": [], "total_runs": 0, "total_targets": 0},
    )

    html = operate_coverage.render_operate_coverage()

    assert "delivery/scripts/install.sh --afl" in html
    assert "optional host-side AFL" in html
    assert "does not mean that target is runnable" in html
    assert 'href="/operate/scenarios"' in html
    assert 'href="/operate/targets"' in html
    assert "open Advanced" not in html
