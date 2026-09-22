from pathlib import Path
import json
import importlib.util
import os
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


ROOT = Path(__file__).resolve().parents[1]


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

    assert config.ssh_key_path == "~/.ssh/id_ed25519"
    assert ":/home/dwarf/.ssh/id_ed25519:ro" in compose


def test_delivery_network_subnet_can_be_isolated_per_deployment():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert "${DWARF_NETWORK_SUBNET:-10.201.0.0/24}" in compose


def test_control_shim_executes_large_generated_script_without_argv_limit():
    shim = _load_control_shim()
    payload = "x" * 300_000
    script = ": <<'DWARF_LARGE_SCRIPT'\n" + payload + "\nDWARF_LARGE_SCRIPT\n"

    assert shim._run_script(script) == 0


def test_production_compose_never_overrides_packaged_source():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert ":/home/dwarf/dwarf-fw/dwarf" not in compose


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
        return CommandResult(0, "ok\n", "", "ssh dwarf-host-a smoke environment-smoke")

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


def test_topology_redeploy_endpoint_is_confirmed_token_gated_and_streamed():
    commands = []

    def build_command():
        command = ["ssh", "dwarf-host-a", "topology-redeploy cardano_amaru"]
        commands.append(command)
        return command

    result = dashboard.dispatch_topology_redeploy_request(
        method="POST",
        path="/api/topology/redeploy?token=secret",
        body=json.dumps(
            {
                "topology_id": "cardano_amaru",
                "confirmation": "REDEPLOY cardano_amaru",
            }
        ).encode(),
        expected_token="secret",
        command_builder=build_command,
        stream_builder=lambda command: iter(
            [f"data: {json.dumps({'stage': 'capture_complete'})}\n\n".encode()]
        ),
    )

    assert result is not None
    status, content_type, body = result
    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert b"capture_complete" in b"".join(body)
    assert commands == [["ssh", "dwarf-host-a", "topology-redeploy cardano_amaru"]]


def test_topology_redeploy_endpoint_rejects_every_unconfirmed_or_variable_target():
    requests = [
        ("GET", "/api/topology/redeploy?token=secret", {}),
        ("POST", "/api/topology/redeploy", {}),
        (
            "POST",
            "/api/topology/redeploy?token=secret",
            {"topology_id": "other", "confirmation": "REDEPLOY other"},
        ),
        (
            "POST",
            "/api/topology/redeploy?token=secret",
            {"topology_id": "cardano_amaru", "confirmation": "yes"},
        ),
        (
            "POST",
            "/api/topology/redeploy?token=secret",
            {
                "topology_id": "cardano_amaru",
                "confirmation": "REDEPLOY cardano_amaru",
                "compose_file": "/tmp/other.yaml",
            },
        ),
    ]
    statuses = []
    for method, path, body in requests:
        result = dashboard.dispatch_topology_redeploy_request(
            method=method,
            path=path,
            body=json.dumps(body).encode(),
            expected_token="secret",
            command_builder=lambda: (_ for _ in ()).throw(
                AssertionError("rejected request built a command")
            ),
        )
        statuses.append(result[0])

    assert statuses == [405, 401, 400, 400, 400]


def test_topology_redeploy_endpoint_honors_global_active_mutation_lock():
    assert dashboard.try_acquire_mutating_lock() is True
    try:
        result = dashboard.dispatch_topology_redeploy_request(
            method="POST",
            path="/api/topology/redeploy?token=secret",
            body=json.dumps(
                {
                    "topology_id": "cardano_amaru",
                    "confirmation": "REDEPLOY cardano_amaru",
                }
            ).encode(),
            expected_token="secret",
            command_builder=lambda: ["must-not-run"],
        )
    finally:
        dashboard.release_mutating_lock()

    assert result[0] == 409
    assert b"mutating action" in result[2]


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
