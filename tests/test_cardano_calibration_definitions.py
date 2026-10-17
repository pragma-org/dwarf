import json
import subprocess
from pathlib import Path

import pytest

from profile_manager.primitives import LoadEventsAreOk, RuntimeCardanoMeasurementCalibration
from profile_manager.scenario import scenario_from_body
from scripts import runtime_cardano_measurement_calibration as calibration


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"


def _document(name):
    return json.loads((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def test_cardano_calibration_scenarios_are_paired_real_node_workloads():
    stock = _document("cardano-measurement-overhead-calibration-stock")
    patched = _document("cardano-measurement-overhead-calibration-patched")
    for document in (stock, patched):
        parsed = scenario_from_body((json.dumps(document) + "\n").encode())
        assert parsed.target == {"implementation": "cardano-node", "version": "11.1.2"}
        load = document["load"][0]
        assert load["attempts"] >= 30
        assert load["plutus_transactions"] >= 2
        assert load["epoch_observation_seconds"] >= 50
    stock_load = {**stock["load"][0], "profile_id": "<paired>"}
    patched_load = {**patched["load"][0], "profile_id": "<paired>"}
    assert stock_load == patched_load
    assert stock["seed"] == patched["seed"]
    assert stock["measurement_profile"] == "cardano-security-default"
    assert patched["measurement_profile"] == "cardano-security-patched"


def test_cardano_calibration_primitive_passes_node_workload_parameters(tmp_path, monkeypatch):
    calls = []

    class Handle:
        run_dir = tmp_path

        def log(self, **_event):
            pass

    def run(command, **kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 30}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    primitive = RuntimeCardanoMeasurementCalibration(params={
        "runtime_root": "/runtime",
        "attempts": 30,
        "plutus_transactions": 2,
        "epoch_observation_seconds": 55,
    })

    primitive.run(Handle(), None)

    command = calls[0]
    assert command[command.index("--plutus-transactions") + 1] == "2"
    assert command[command.index("--epoch-observation-seconds") + 1] == "55.0"


def test_cardano_calibration_command_is_accepted_by_the_cardano_helper(monkeypatch, tmp_path):
    from scripts import runtime_cardano_measurement_calibration as helper

    calls = []

    class Handle:
        run_dir = tmp_path

        def log(self, **_kwargs):
            pass

    def run(command, **_kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 60}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    RuntimeCardanoMeasurementCalibration(params={
        "runtime_root": "/runtime",
        "attempts": 60,
        "case_set": "version-table-forward-compat-v1",
    }).run(Handle(), None)

    command = calls[0]
    assert "--progress-timeout-seconds" not in command
    received = {}

    def run_leg(**kwargs):
        received.update(kwargs)
        return {"node_trace": {"record_count": 1, "protocol_record_count": 1}}

    monkeypatch.setattr(helper, "run_leg", run_leg)
    assert helper.main(command[2:]) == 0
    assert received["case_set"] == "version-table-forward-compat-v1"


def _owned_workload_runtime():
    digest = "sha256:" + "6" * 64
    image_id = "sha256:" + "9" * 64
    image = "ghcr.io/intersectmbo/cardano-node:11.1.2@" + digest
    runtime = {
        "network_magic": 42,
        "profile_id": "profile-s-cardano-measurement-stock-control",
        "compose_project": "dwarf-profile-install-test-profile-s-cardano-measurement-stock-control",
    }
    node = {
        "id": "node1",
        "container_id": "a" * 64,
        "container_name": "owned-cardano-node1",
        "container_socket_path": "/env/socket/node1/sock",
        "socket_path": "/a/host/runtime/path/" + "x" * 120 + "/node1/sock",
        "image_ref": image,
        "image_id": image_id,
    }
    inspected = {
        "Id": node["container_id"],
        "Name": "/owned-cardano-node1",
        "Image": image_id,
        "Config": {
            "Image": image,
            "Labels": {
                "ada2.managed": "dwarf",
                "io.dwarf.managed": "true",
                "io.dwarf.component": "node",
                "io.dwarf.implementation": "cardano-node",
                "io.dwarf.topology": runtime["profile_id"],
                "io.dwarf.node_id": "node1",
                "com.docker.compose.project": runtime["compose_project"],
                "com.docker.compose.service": "node1",
            },
        },
        "State": {"Running": True},
        "Mounts": [{
            "Type": "bind",
            "Source": "/host/owned-topology/env",
            "Destination": "/env",
            "RW": True,
        }],
    }
    return runtime, node, inspected


def test_controller_detection_preserves_host_mode_but_fails_closed_in_controller(
    monkeypatch
):
    monkeypatch.setenv("HOSTNAME", "host-not-a-container")
    monkeypatch.delenv("ADA2_DWARF_RUNS_DIR", raising=False)
    monkeypatch.setattr(
        calibration,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="not found"
        ),
    )
    assert calibration._controller_container() is None

    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", "/var/dwarf/runs")
    with pytest.raises(RuntimeError, match="cannot inspect"):
        calibration._controller_container()


def test_cardano_plutus_workload_uses_the_target_container_socket(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    key_root.mkdir(parents=True)
    (key_root / "utxo.vkey").write_text("vkey", encoding="utf-8")
    (key_root / "utxo.skey").write_text("skey", encoding="utf-8")
    output_dir = Path("/var/dwarf/runs/run-1/outputs/cardano-measurement-calibration")
    runtime, node, inspected = _owned_workload_runtime()
    runtime["workload_key_root"] = "/env/utxo-keys/utxo1"
    runtime["support_binaries"] = {"cardano-cli": "/opt/dwarf/bin/cardano-cli"}
    image = node["image_ref"]
    controller_id = "b" * 64
    controller = {
        "Id": controller_id,
        "Config": {"Labels": {
            "io.dwarf.application": "true",
            "io.dwarf.component": "controller",
        }},
        "Mounts": [{
            "Type": "volume",
            "Source": "/var/lib/docker/volumes/dwarf-runs/_data",
            "Destination": "/var/dwarf/runs",
            "RW": True,
        }],
    }
    monkeypatch.setenv("HOSTNAME", controller_id[:12])
    def run(command, **_kwargs):
        if command[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        body = inspected if command[-1] == node["container_id"] else controller
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps([body]), stderr=""
        )

    monkeypatch.setattr(calibration, "_run", run)
    monkeypatch.setattr(
        calibration,
        "_owned_run_output_relative",
        lambda output, **_kwargs: (
            Path(output),
            Path("run-1/outputs/cardano-measurement-calibration"),
        ),
    )
    temporary_calls = []

    class RunVolumeTemporaryDirectory:
        def __init__(self, *, prefix, dir):
            temporary_calls.append({"prefix": prefix, "dir": dir})
            self.path = tmp_path / "wrapper"

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        calibration.tempfile, "TemporaryDirectory", RunVolumeTemporaryDirectory
    )

    with calibration._containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=node,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, workload_node):
        wrapper = Path(workload_runtime["support_binaries"]["cardano-cli"])
        text = wrapper.read_text(encoding="utf-8")
        assert workload_runtime["workload_socket_remote"] is True
        assert workload_runtime["controller_support_binaries"] == {
            "cardano-cli": "/opt/dwarf/bin/cardano-cli"
        }
        assert workload_runtime["workload_key_root"] == str(key_root)
        assert workload_runtime["controller_workload_key_root"] == str(key_root)
        assert workload_runtime["container_workload_key_root"] == (
            "/env/utxo-keys/utxo1"
        )
        assert workload_node["socket_path"] == "/env/socket/node1/sock"
        assert workload_node["container_socket_path"] == "/env/socket/node1/sock"
        assert '--user "$(id -u):$(id -g)"' in text
        assert "--label io.dwarf.component=cardano-cli-workload" in text
        assert '--label "io.dwarf.workload=$WORKLOAD_ID"' in text
        assert 'src=$HOST_WORK_SOURCE,dst=/work' in text
        assert 'src=$TARGET_ENV_SOURCE,dst=/env,readonly' in text
        assert "/var/lib/docker/volumes/dwarf-runs/_data/run-1/outputs/cardano-measurement-calibration" in text
        assert "/host/owned-topology/env" in text
        assert "--volumes-from" not in text
        assert '--entrypoint cardano-cli "$IMAGE"' in text
        assert image in text
    assert temporary_calls == [{
        "prefix": ".dwarf-cardano-cli-",
        "dir": output_dir,
    }]


def test_cardano_plutus_workload_uses_owned_support_service_socket_volume(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    output_dir = Path("/var/dwarf/runs/run-support/outputs/plutus")
    key_root = output_dir / ".dwarf-plutus-test" / "keys"
    project = "dwarf-profile-install-test-profile-za"
    profile_id = "profile-za"
    installation_id = "install-test"
    volume_name = f"{project}_p1-state"
    image = "ghcr.io/intersectmbo/cardano-node@sha256:" + "6" * 64
    image_id = "sha256:" + "9" * 64
    container_id = "a" * 64
    runtime = {
        "network_magic": 42,
        "profile_id": profile_id,
        "compose_project": project,
        "workload_key_root": str(key_root),
    }
    node = {
        "id": "bootstrap-cardano",
        "service_name": "p1",
        "container_id": container_id,
        "container_name": f"{project}-p1-1",
        "container_socket_path": "/state/node.socket",
        "socket_mount_destination": "/state",
        "socket_mount_type": "volume",
        "socket_volume_key": "p1-state",
        "image_ref": image,
        "image_id": image_id,
    }
    target = {
        "Id": container_id,
        "Name": f"/{project}-p1-1",
        "Image": image_id,
        "Config": {
            "Image": image,
            "User": "",
            "Labels": {
                "ada2.managed": "dwarf",
                "io.dwarf.managed": "true",
                "io.dwarf.component": "node",
                "io.dwarf.implementation": "cardano-node",
                "io.dwarf.topology": profile_id,
                "io.dwarf.node_id": "bootstrap-cardano",
                "com.docker.compose.project": project,
                "com.docker.compose.service": "p1",
            },
        },
        "State": {"Running": True},
        "Mounts": [{
            "Type": "volume",
            "Name": volume_name,
            "Source": f"/var/lib/docker/volumes/{volume_name}/_data",
            "Destination": "/state",
            "RW": True,
        }],
    }
    controller = {
        "Id": "b" * 64,
        "Config": {"Labels": {
            "io.dwarf.application": "true",
            "io.dwarf.component": "controller",
            "io.dwarf.installation": installation_id,
        }},
        "Mounts": [{
            "Type": "volume",
            "Source": "/var/lib/docker/volumes/dwarf-runs/_data",
            "Destination": "/var/dwarf/runs",
            "RW": True,
        }],
    }
    volume = {
        "Name": volume_name,
        "Labels": {
            "io.dwarf.managed": "true",
            "io.dwarf.component": "volume",
            "io.dwarf.installation": installation_id,
            "io.dwarf.topology": profile_id,
            "com.docker.compose.project": project,
            "com.docker.compose.volume": "p1-state",
        },
    }

    def run(command, **_kwargs):
        if command[:3] == ["docker", "inspect", container_id]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([target]), stderr="")
        if command[:3] == ["docker", "volume", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([volume]), stderr="")
        if command[:3] == ["docker", "exec", container_id]:
            return subprocess.CompletedProcess(command, 0, stdout="0:0:755:socket\n", stderr="")
        if command[:2] == ["docker", "exec"]:
            raise AssertionError(f"socket probe did not use immutable container id: {command}")
        if command[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(calibration, "_run", run)
    real_is_file = Path.is_file
    real_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True
        if str(path).endswith((
            "/.dwarf-plutus-test/keys/utxo.vkey",
            "/.dwarf-plutus-test/keys/utxo.skey",
        ))
        else real_is_file(path),
    )
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: path
        if str(path) == "/var/dwarf/runs"
        or str(path).startswith("/var/dwarf/runs/")
        else real_resolve(path, *args, **kwargs),
    )

    class RunVolumeTemporaryDirectory:
        def __init__(self, *, prefix, dir):
            self.path = tmp_path / "support-wrapper"

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(calibration.tempfile, "TemporaryDirectory", RunVolumeTemporaryDirectory)
    with calibration._containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=node,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, workload_node):
        wrapper = Path(workload_runtime["support_binaries"]["cardano-cli"])
        text = wrapper.read_text(encoding="utf-8")
        assert workload_node["socket_path"] == "/state/node.socket"
        assert '--user "0:0"' in text
        assert f"type=volume,src={volume_name},dst=/state,readonly" in text
        assert "TARGET_ENV_SOURCE" not in text
        assert 'OWNER_UID="$(id -u)"' in text
        assert 'OWNER_GID="$(id -g)"' in text
        assert "restore_work_owner()" in text
        assert 'trap restore_work_owner EXIT' in text
        assert '--entrypoint chown "$IMAGE" -R "$OWNER_UID:$OWNER_GID" /work' in text
        assert 'if restore_work_owner; then' in text
        assert 'restore_status=$?' in text
        assert 'trap - EXIT' in text
        assert 'if [ "$restore_status" -ne 0 ]; then' in text
        assert 'exit "$restore_status"' in text
        assert 'exit "$status"' in text


def test_cardano_workload_output_must_be_a_real_owned_run_directory(tmp_path):
    runs_root = tmp_path / "runs"
    output = runs_root / "run-1" / "outputs" / "plutus"
    output.mkdir(parents=True)
    absolute, relative = calibration._owned_run_output_relative(
        output, runs_root=runs_root
    )
    assert absolute == output
    assert relative == Path("run-1/outputs/plutus")

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = runs_root / "linked-output"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlinked"):
        calibration._owned_run_output_relative(linked, runs_root=runs_root)


def test_workload_container_rejects_service_name_that_differs_from_owned_labels(
    monkeypatch
):
    runtime, node, inspected = _owned_workload_runtime()
    node["service_name"] = "p1"
    monkeypatch.setattr(calibration, "_inspect_container", lambda _identifier: inspected)

    with pytest.raises(RuntimeError, match="ownership labels"):
        calibration._verify_workload_container(runtime, node)


def test_cardano_plutus_workload_rejects_a_replaced_foreign_container(
    tmp_path, monkeypatch
):
    runtime, node, inspected = _owned_workload_runtime()
    inspected["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    monkeypatch.setattr(
        calibration,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=json.dumps([inspected]), stderr=""
        ),
    )
    monkeypatch.setenv("HOSTNAME", "b" * 12)
    runtime_root = tmp_path / "runtime"
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    key_root.mkdir(parents=True)
    (key_root / "utxo.vkey").write_text("vkey", encoding="utf-8")
    (key_root / "utxo.skey").write_text("skey", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ownership labels"):
        with calibration._containerized_cardano_workload(
            runtime_root=runtime_root,
            runtime=runtime,
            node=node,
            output_dir=Path("/var/dwarf/runs/run-1/output"),
            controller={},
        ):
            raise AssertionError("foreign workload target was accepted")


def test_cardano_plutus_workload_requires_immutable_container_identity(tmp_path):
    runtime, node, _inspected = _owned_workload_runtime()
    node.pop("container_id")
    runtime_root = tmp_path / "runtime"
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    key_root.mkdir(parents=True)
    (key_root / "utxo.vkey").write_text("vkey", encoding="utf-8")
    (key_root / "utxo.skey").write_text("skey", encoding="utf-8")

    with pytest.raises(RuntimeError, match="immutable container identity"):
        with calibration._containerized_cardano_workload(
            runtime_root=runtime_root,
            runtime=runtime,
            node=node,
            output_dir=Path("/var/dwarf/runs/run-1/output"),
            controller={},
        ):
            raise AssertionError("mutable workload target was accepted")


def test_cardano_plutus_workload_cleans_a_timed_out_owned_sibling(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    key_root.mkdir(parents=True)
    (key_root / "utxo.vkey").write_text("vkey", encoding="utf-8")
    (key_root / "utxo.skey").write_text("skey", encoding="utf-8")
    runtime, node, inspected = _owned_workload_runtime()
    controller = {
        "Id": "b" * 64,
        "Config": {"Labels": {
            "io.dwarf.application": "true",
            "io.dwarf.component": "controller",
        }},
        "Mounts": [{
            "Type": "volume",
            "Source": "/var/lib/docker/volumes/dwarf-runs/_data",
            "Destination": "/var/dwarf/runs",
            "RW": True,
        }],
    }
    workload_id = "1" * 32
    leftover_id = "c" * 64
    removed = []

    class FixedUUID:
        hex = workload_id

    monkeypatch.setattr(calibration.uuid, "uuid4", lambda: FixedUUID())

    class RunVolumeTemporaryDirectory:
        def __init__(self, *, prefix, dir):
            assert prefix == ".dwarf-cardano-cli-"
            assert dir == Path("/var/dwarf/runs/run-1/output")
            self.path = tmp_path / "wrapper"

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        calibration.tempfile, "TemporaryDirectory", RunVolumeTemporaryDirectory
    )

    def run(command, **_kwargs):
        if command[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=leftover_id + "\n", stderr=""
            )
        if command[1:3] == ["rm", "-f"]:
            removed.extend(command[3:])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[-1] == node["container_id"]:
            body = inspected
        elif command[-1] == leftover_id:
            body = {
                "Id": leftover_id,
                "Config": {"Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.component": "cardano-cli-workload",
                    "io.dwarf.workload": workload_id,
                }},
            }
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps([body]), stderr=""
        )

    monkeypatch.setattr(calibration, "_run", run)
    monkeypatch.setattr(
        calibration,
        "_owned_run_output_relative",
        lambda output, **_kwargs: (Path(output), Path("run-1/output")),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        with calibration._containerized_cardano_workload(
            runtime_root=runtime_root,
            runtime=runtime,
            node=node,
            output_dir=Path("/var/dwarf/runs/run-1/output"),
            controller=controller,
        ):
            raise subprocess.TimeoutExpired(["cardano-cli"], 60)

    assert removed == [leftover_id]


def test_load_events_assertion_retains_the_real_helper_exit_code():
    class Handle:
        events = [{
            "phase": "load",
            "primitive": "runtime_cardano_measurement_calibration",
            "event": "completed",
            "payload": {
                "outcome": "unexpected_exit",
                "helper_exit_code": 1,
                "timed_out": False,
            },
        }]

    result = LoadEventsAreOk(
        params={"min_completed": 1, "min_event_count": 1}
    ).evaluate(
        Handle()
    )

    assert result["result"] == "fail"
    assert result["data_points_used"] == [
        {"outcome": "unexpected_exit", "exit_code": 1}
    ]
