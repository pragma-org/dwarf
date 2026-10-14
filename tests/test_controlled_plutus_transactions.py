import json
import hashlib
import subprocess
from contextlib import ExitStack, contextmanager

import pytest

from scripts import runtime_controlled_plutus_transactions as subject
from profile_manager import primitives as primitive_module
from profile_manager.primitives import RuntimeControlledPlutusTransactions


def test_controlled_workload_retains_before_and_after_target_health(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "runtime": "fixture",
                "profile_id": "profile-legacy",
                "compose_project": "dwarf-profile-install-profile-legacy",
            }
        )
    )
    (runtime_root / "versioned-deployment.json").write_text(
        json.dumps(
            {
                "adapter": "runtime_amaru_control_substrate.py",
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
                "profile_id": "profile-legacy",
                "compose_project": "dwarf-profile-install-profile-legacy",
                "substrate": {
                    "network_magic": 42,
                    "controller_ownership": {
                        "installation_id": "install-test",
                        "topology_id": "profile-legacy",
                    },
                },
            }
        )
    )
    states = iter([
        {"running": True, "restart_count": 3, "oom_killed": False},
        {"running": True, "restart_count": 3, "oom_killed": False},
    ])
    tips = iter([
        {"block_height": 100, "hash": "aa", "slot": 100},
        {"block_height": 101, "hash": "bb", "slot": 101},
    ])
    monkeypatch.setattr(
        subject,
        "_target",
        lambda _runtime: ({}, {
            "container_name": "cardano-target",
            "container_socket_path": "/state/node.socket",
            "network_magic": 42,
        }),
    )
    monkeypatch.setattr(subject, "_container_state", lambda _container: next(states))
    monkeypatch.setattr(
        subject,
        "_query_cardano_tip",
        lambda _container, *, socket_path, network_magic: next(tips),
    )
    monkeypatch.setattr(
        subject,
        "_container_logs",
        lambda _container, *, since: "Chain extended",
    )
    monkeypatch.setattr(
        subject,
        "run_plutus_workload",
        lambda **_kwargs: {
            "records": [
                {"attempt_id": f"valid-{index}", "outcome": "accepted", "chain_outcome": "included-valid", "lock_transaction_id": f"lock-v-{index}", "spend_transaction_id": f"spend-v-{index}", "transaction_bytes": 111, "elapsed_nanos": 2184 + index, "elapsed_micros": (2184 + index) / 1000, "submit_to_protocol_response_nanos": 1000, "submit_to_protocol_response_micros": 1.0, "submit_to_block_inclusion_nanos": 2000, "submit_to_block_inclusion_micros": 2.0, "submit_to_chain_adoption_nanos": 2000, "submit_to_chain_adoption_micros": 2.0}
                for index in range(30)
            ] + [
                {"attempt_id": f"invalid-{index}", "outcome": "rejected", "chain_outcome": "included-invalid", "lock_transaction_id": f"lock-i-{index}", "spend_transaction_id": f"spend-i-{index}", "transaction_bytes": 111, "elapsed_nanos": 3184 + index, "elapsed_micros": (3184 + index) / 1000, "submit_to_protocol_response_nanos": 1000, "submit_to_protocol_response_micros": 1.0, "submit_to_block_inclusion_nanos": 3000, "submit_to_block_inclusion_micros": 3.0, "submit_to_chain_adoption_nanos": 3000, "submit_to_chain_adoption_micros": 3.0}
                for index in range(30)
            ],
        },
    )

    result = subject.run_controlled_plutus_transactions(
        runtime_root=runtime_root,
        output_dir=tmp_path / "out",
        transaction_count=60,
    )

    assert result["target_health"] == {
        "before": {"running": True, "restart_count": 3, "oom_killed": False},
        "after": {"running": True, "restart_count": 3, "oom_killed": False},
        "tip_before": {"block_height": 100, "hash": "aa", "slot": 100},
        "tip_after": {"block_height": 101, "hash": "bb", "slot": 101},
        "log_signals": {"fatal": [], "background": []},
    }
    assert result["checks"]["plutus_live_outcomes_observed"] is True
    assert result["checks"]["transaction_identifiers_retained"] is True
    assert result["checks"]["target_progress_continues"] is True
    assert result["checks"]["target_health_clean"] is True
    assert result["accounting"]["attempted"] == 60
    assert result["accounting"]["successful"] == 30
    assert result["accounting"]["rejected"] == 30
    assert result["accounting"]["timed_out"] == 0
    assert result["accounting"]["bytes"] == 6660
    assert len(result["accounting"]["attempts"]) == 60
    assert result["accounting"]["attempts"][0]["elapsed_nanos"] == 2184
    assert result["accounting"]["attempts"][0]["elapsed_micros"] == 2.184
    assert result["accounting"]["attempts"][0]["submit_to_protocol_response_nanos"] == 1000
    assert result["accounting"]["attempts"][0]["submit_to_chain_adoption_nanos"] == 2000
    assert json.loads((tmp_path / "out" / "result.json").read_text()) == result


def test_amaru_accounting_does_not_claim_support_producer_response_or_adoption():
    records = [{
        "attempt_id": "plutus-0000",
        "outcome": "accepted",
        "spend_transaction_id": "tx-1",
        "transaction_bytes": 222,
        "elapsed_nanos": 2184,
        "elapsed_micros": 2.184,
        "submit_to_protocol_response_nanos": 1000,
        "submit_to_protocol_response_micros": 1.0,
        "submit_to_block_inclusion_nanos": 2000,
        "submit_to_block_inclusion_micros": 2.0,
        "submit_to_chain_adoption_nanos": 2000,
        "submit_to_chain_adoption_micros": 2.0,
    }]

    accounting = subject._plutus_workload_accounting(records, "amaru")

    attempt = accounting["attempts"][0]
    assert attempt["elapsed_micros"] == 2.184
    assert attempt["submit_to_block_inclusion_nanos"] == 2000
    assert "submit_to_protocol_response_nanos" not in attempt
    assert "submit_to_chain_adoption_nanos" not in attempt


@pytest.mark.parametrize(
    ("runtime_magic", "deployment_magic"),
    [(None, True), (None, 43), (2, 42)],
)
def test_retained_runtime_magic_reconciliation_rejects_invalid_or_mismatched_values(
    runtime_magic, deployment_magic
):
    runtime = {
        "profile_id": "profile-legacy",
        "compose_project": "dwarf-profile-install-profile-legacy",
    }
    if runtime_magic is not None:
        runtime["network_magic"] = runtime_magic
    deployment = {
        "adapter": "runtime_amaru_control_substrate.py",
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "profile_id": "profile-legacy",
        "compose_project": "dwarf-profile-install-profile-legacy",
        "substrate": {
            "network_magic": deployment_magic,
            "controller_ownership": {
                "installation_id": "install-test",
                "topology_id": "profile-legacy",
            },
        },
    }

    with pytest.raises(ValueError, match="network magic"):
        subject.reconcile_amaru_runtime_metadata(runtime, deployment)


def test_primitive_emits_existing_workload_accounting_event(monkeypatch, tmp_path):
    class Handle:
        run_dir = tmp_path

        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    accounting = {
        "attempted": 60,
        "successful": 30,
        "rejected": 30,
        "timed_out": 0,
        "bytes": 6660,
        "batches": 1,
        "backlog": 0,
        "attempts": [{"input_id": "plutus-0000", "outcome": "accepted", "elapsed_nanos": 2184, "elapsed_micros": 2.184, "bytes": 111}],
    }

    def run(command, **_kwargs):
        output_dir = subject.Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True)
        (output_dir / "result.json").write_text(
            json.dumps({"accounting": accounting}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    primitive = RuntimeControlledPlutusTransactions(params={
        "profile_id": "profile-v-cardano-measurement-nanoseconds-v2",
        "measurement_implementation": "cardano-node",
    })

    primitive.run(handle, None)

    event = next(row for row in handle.events if row["event"] == "workload_accounting")
    assert event["payload"] == accounting


def test_primitive_uses_exact_prepared_runtime_metadata_path(monkeypatch, tmp_path):
    prepared_root = tmp_path / "prepared-runtime"
    prepared_root.mkdir()
    prepared_path = prepared_root / "runtime.json"
    prepared_path.write_text(
        json.dumps({"profile_id": "profile-za", "network_magic": 42}),
        encoding="utf-8",
    )
    decoy_root = tmp_path / "decoy-runtime"
    decoy_root.mkdir()
    (decoy_root / "runtime.json").write_text(
        json.dumps({"profile_id": "profile-za", "network_magic": 999}),
        encoding="utf-8",
    )

    class Handle:
        run_dir = tmp_path
        _prepared_runtime_metadata_path = prepared_path

        def log(self, **_event):
            pass

    captured = {}

    def run(command, **_kwargs):
        captured["command"] = command
        output_dir = subject.Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        primitive_module,
        "_resolve_client_runtime_target",
        lambda _handle, _params: {
            "id": "amaru-relay-1",
            "implementation": "amaru",
            "container": "owned-amaru-relay-1",
        },
    )
    monkeypatch.setattr(
        primitive_module,
        "_capture_amaru_measurement_window",
        lambda *_args, **_kwargs: {"record_count": 0},
    )
    primitive = RuntimeControlledPlutusTransactions(
        params={
            "profile_id": "profile-za",
            "runtime_root": str(decoy_root),
            "measurement_implementation": "amaru",
        }
    )

    primitive.run(Handle(), None)

    command = captured["command"]
    assert command[command.index("--runtime-root") + 1] == str(prepared_root)


def test_plutus_primitive_does_not_create_a_duplicate_node_trace_capture(
    monkeypatch, tmp_path
):
    prepared_root = tmp_path / "prepared-runtime"
    prepared_root.mkdir()
    prepared_path = prepared_root / "runtime.json"
    prepared_path.write_text(
        json.dumps({"profile_id": "profile-za", "network_magic": 42}),
        encoding="utf-8",
    )

    class Handle:
        run_dir = tmp_path
        _prepared_runtime_metadata_path = prepared_path

        def log(self, **_event):
            pass

    monkeypatch.setattr(
        primitive_module,
        "_resolve_client_runtime_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scenario-level trace ownership must not be duplicated")
        ),
    )
    monkeypatch.setattr(
        primitive_module,
        "_capture_amaru_measurement_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scenario-level trace ownership must not be duplicated")
        ),
    )

    def run(command, **_kwargs):
        output_dir = subject.Path(command[command.index("--output-dir") + 1])
        assert not output_dir.exists(), (
            "measurement trace preparation must not pre-create the helper's "
            "exclusive output directory"
        )
        output_dir.mkdir(parents=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="helper output is not node telemetry",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    primitive = RuntimeControlledPlutusTransactions(
        params={
            "profile_id": "profile-za",
            "measurement_implementation": "amaru",
        }
    )

    primitive.run(Handle(), None)

    assert not (tmp_path / "outputs" / "controlled-plutus-measurements").exists()


def test_amaru_measurement_window_retains_only_owned_node_records_in_window(
    monkeypatch, tmp_path
):
    rows = [
        {"timestamp": "2026-10-11T01:00:00Z", "fields": {"message": "before"}},
        {"timestamp": "2026-10-11T01:00:01Z", "fields": {"message": "inside"}},
        {"timestamp": "2026-10-11T01:00:03Z", "fields": {"message": "after"}},
    ]
    observed = {}

    def docker_result(command, *, timeout):
        observed["command"] = command
        observed["timeout"] = timeout
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="\n".join(json.dumps(row) for row in rows),
            stderr="helper/conformance text must not be retained",
        )

    monkeypatch.setattr(primitive_module, "_protocol_docker_result", docker_result)
    destination = tmp_path / "amaru-relay-1.ndjson"

    result = primitive_module._capture_amaru_measurement_window(
        {
            "id": "amaru-relay-1",
            "implementation": "amaru",
            "container": "owned-amaru-relay-1",
        },
        started_at="2026-10-11T01:00:00.500000Z",
        ended_at="2026-10-11T01:00:02Z",
        destination=destination,
    )

    assert observed["command"] == [
        "logs",
        "--since",
        "2026-10-11T01:00:00.500000Z",
        "--until",
        "2026-10-11T01:00:02Z",
        "owned-amaru-relay-1",
    ]
    assert result == {
        "container": "owned-amaru-relay-1",
        "record_count": 1,
        "trace_path": str(destination),
    }
    assert [
        json.loads(line)["fields"]["message"]
        for line in destination.read_text(encoding="utf-8").splitlines()
    ] == ["inside"]


def test_amaru_measurement_window_timeout_publishes_no_partial_trace(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("docker logs", 120)
        ),
    )
    destination = tmp_path / "amaru-relay-1.ndjson"

    with pytest.raises(RuntimeError, match="cannot retain"):
        primitive_module._capture_amaru_measurement_window(
            {
                "id": "amaru-relay-1",
                "implementation": "amaru",
                "container": "owned-amaru-relay-1",
            },
            started_at="2026-10-11T01:00:00Z",
            ended_at="2026-10-11T01:00:02Z",
            destination=destination,
        )

    assert not destination.exists()


def test_amaru_measurement_window_write_failure_publishes_no_partial_trace(
    monkeypatch, tmp_path
):
    destination = tmp_path / "amaru-relay-1.ndjson"
    destination.write_text('{"timestamp":"stale"}\n', encoding="utf-8")
    row = {
        "timestamp": "2026-10-11T01:00:01Z",
        "fields": {"message": "inside"},
    }
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(row), stderr=""
        ),
    )
    monkeypatch.setattr(
        primitive_module,
        "atomic_write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(RuntimeError, match="cannot publish"):
        primitive_module._capture_amaru_measurement_window(
            {
                "id": "amaru-relay-1",
                "implementation": "amaru",
                "container": "owned-amaru-relay-1",
            },
            started_at="2026-10-11T01:00:00Z",
            ended_at="2026-10-11T01:00:02Z",
            destination=destination,
        )

    assert not destination.exists()


def test_primitive_rejects_prepared_runtime_for_different_profile(
    monkeypatch, tmp_path
):
    prepared_root = tmp_path / "prepared-runtime"
    prepared_root.mkdir()
    prepared_path = prepared_root / "runtime.json"
    prepared_path.write_text(
        json.dumps({"profile_id": "profile-other", "network_magic": 42}),
        encoding="utf-8",
    )

    class Handle:
        run_dir = tmp_path
        _prepared_runtime_metadata_path = prepared_path

        def log(self, **_event):
            pass

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("workload must not start"),
    )
    primitive = RuntimeControlledPlutusTransactions(
        params={
            "profile_id": "profile-za",
            "measurement_implementation": "amaru",
        }
    )

    with pytest.raises(RuntimeError, match="prepared runtime profile identity"):
        primitive.run(Handle(), None)


def test_health_target_selects_measured_amaru_instead_of_transaction_producer():
    runtime = {
        "network_magic": 42,
        "identity": {
            "services": {
                "amaru-relay-1": {
                    "container": "measured-amaru",
                },
            },
        },
    }
    transaction_producer = {
        "container_name": "cardano-producer",
        "container_socket_path": "/state/node.socket",
    }

    target = subject._health_target(runtime, transaction_producer, "amaru")

    assert target == {
        "implementation": "amaru",
        "container": "measured-amaru",
        "network_magic": 42,
    }


def test_amaru_support_bridge_is_digest_pinned_and_removes_temporary_keys(
    monkeypatch, tmp_path
):
    runtime = {
        "profile_id": "profile-za",
        "compose_project": "qualified-profile",
        "network_magic": 42,
        "actual_topology": {
            "selected_support_node_services": {"bootstrap-cardano": "p1"},
            "selected_nodes": [{
                "id": "bootstrap-cardano",
                "service": "p1",
                "implementation": "cardano-node",
                "supporting": True,
                "image": "example/cardano@sha256:" + "a" * 64,
            }],
        },
        "identity": {
            "services": {
                "p1": {
                    "matched": True,
                    "container": "qualified-producer",
                    "expected_image": "example/cardano@sha256:" + "a" * 64,
                },
            },
        },
    }

    def copy_key(_container, _source, destination):
        destination.write_text("fixture-key", encoding="utf-8")
        destination.chmod(0o600)

    monkeypatch.setattr(subject, "_copy_fixture_key", copy_key)
    output = tmp_path / "output"
    output.mkdir()
    with ExitStack() as stack:
        workload_runtime, node = subject._prepare_amaru_transaction_producer(
            runtime, output, stack
        )
        key_root = subject.Path(workload_runtime["workload_key_root"])
        wrapper = subject.Path(workload_runtime["support_binaries"]["cardano-cli"])
        assert key_root.joinpath("utxo.skey").stat().st_mode & 0o777 == 0o600
        assert "@sha256:" in wrapper.read_text(encoding="utf-8")
        assert node == {
            "container_name": "qualified-producer",
            "container_socket_path": "/state/node.socket",
            "socket_path": "/state/node.socket",
        }

    assert not key_root.exists()
    assert not wrapper.exists()


def test_installed_amaru_support_bridge_reuses_hardened_containerized_workload(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime = {
        "profile_id": "profile-za",
        "compose_project": "qualified-profile",
        "network_magic": 42,
        "actual_topology": {
            "selected_support_node_services": {"bootstrap-cardano": "p1"},
            "selected_nodes": [{
                "id": "bootstrap-cardano",
                "service": "p1",
                "implementation": "cardano-node",
                "supporting": True,
                "image": "example/cardano@sha256:" + "a" * 64,
            }],
        },
        "identity": {
            "services": {
                "p1": {
                    "matched": True,
                    "container": "qualified-producer",
                    "expected_image": "example/cardano@sha256:" + "a" * 64,
                    "expected_image_id": "sha256:" + "b" * 64,
                },
            },
        },
    }

    def copy_key(_container, _source, destination):
        destination.write_text("fixture-key", encoding="utf-8")
        destination.chmod(0o600)

    monkeypatch.setattr(subject, "_copy_fixture_key", copy_key)
    monkeypatch.setattr(
        subject,
        "_inspect_container",
        lambda _container: {"Id": "c" * 64},
    )
    captured = {}

    @contextmanager
    def hardened(**kwargs):
        captured.update(kwargs)
        assert "support_binaries" not in kwargs["runtime"]
        yield (
            {**kwargs["runtime"], "support_binaries": {"cardano-cli": "/work/cardano-cli"}},
            kwargs["node"],
        )

    monkeypatch.setattr(subject, "_containerized_cardano_workload", hardened)
    output = tmp_path / "output"
    output.mkdir()
    controller = {"Id": "d" * 64}
    with ExitStack() as stack:
        workload_runtime, node = subject._prepare_amaru_transaction_producer(
            runtime,
            output,
            stack,
            runtime_root=runtime_root,
            controller=controller,
        )
        assert workload_runtime["support_binaries"] == {
            "cardano-cli": "/work/cardano-cli"
        }
        assert node["container_name"] == "qualified-producer"
        assert node["id"] == "bootstrap-cardano"
        assert node["service_name"] == "p1"
        assert node["socket_mount_destination"] == "/state"
        assert node["socket_mount_type"] == "volume"
        assert node["container_id"] == "c" * 64
        assert node["image_ref"] == runtime["identity"]["services"]["p1"]["expected_image"]
        assert node["image_id"] == runtime["identity"]["services"]["p1"]["expected_image_id"]
    assert captured["runtime_root"] == runtime_root
    assert captured["output_dir"] == output
    assert captured["controller"] is controller


@pytest.mark.parametrize(
    "actual_topology",
    [
        {},
        {
            "selected_support_node_services": {"bootstrap-cardano": "p2"},
            "selected_nodes": [],
        },
        {
            "selected_support_node_services": {
                "bootstrap-cardano": "p1",
                "other-cardano": "p1",
            },
            "selected_nodes": [],
        },
    ],
)
def test_installed_amaru_support_bridge_rejects_missing_or_ambiguous_producer_mapping(
    monkeypatch, tmp_path, actual_topology
):
    runtime = {
        "profile_id": "profile-za",
        "compose_project": "qualified-profile",
        "network_magic": 42,
        "actual_topology": actual_topology,
        "identity": {
            "services": {
                "p1": {
                    "matched": True,
                    "container": "qualified-producer",
                    "expected_image": "example/cardano@sha256:" + "a" * 64,
                    "expected_image_id": "sha256:" + "b" * 64,
                },
            },
        },
    }
    monkeypatch.setattr(subject, "_copy_fixture_key", lambda *_args: None)
    monkeypatch.setattr(subject, "_inspect_container", lambda _container: {"Id": "c" * 64})
    output = tmp_path / "output"
    output.mkdir()

    with ExitStack() as stack, pytest.raises(
        RuntimeError, match="supporting Cardano producer mapping"
    ):
        subject._prepare_amaru_transaction_producer(
            runtime,
            output,
            stack,
            runtime_root=tmp_path / "runtime",
            controller={"Id": "d" * 64},
        )


@pytest.mark.parametrize("network_magic", [None, True])
def test_installed_amaru_support_bridge_rejects_missing_or_boolean_network_magic(
    monkeypatch, tmp_path, network_magic
):
    runtime = {
        "compose_project": "qualified-profile",
        "network_magic": network_magic,
        "identity": {
            "services": {
                "p1": {
                    "matched": True,
                    "container": "qualified-producer",
                    "expected_image": "example/cardano@sha256:" + "a" * 64,
                }
            }
        },
    }
    monkeypatch.setattr(
        subject,
        "_copy_fixture_key",
        lambda _container, _source, destination: destination.write_text(
            "fixture-key", encoding="utf-8"
        ),
    )
    output = tmp_path / "output"
    output.mkdir()

    with ExitStack() as stack, pytest.raises(
        RuntimeError, match="exact network magic"
    ):
        subject._prepare_amaru_transaction_producer(runtime, output, stack)

def test_amaru_workload_requires_verified_live_plutus_v2_topology(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "compose_project": "profile",
                "network_magic": 42,
                "plutus_v2": {"verified": False},
            }
        )
    )

    with pytest.raises(RuntimeError, match="verified live PlutusV2"):
        subject.run_controlled_plutus_transactions(
            runtime_root=runtime_root,
            output_dir=tmp_path / "out",
            transaction_count=60,
            measurement_implementation="amaru",
        )


def test_live_plutus_v2_topology_files_are_copied_and_digest_checked(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    paths = []
    for name in (
        "pinned.json",
        "alonzo-p1.json",
        "alonzo-p2.json",
        "alonzo-p3.json",
        "conway-p1.json",
        "conway-p2.json",
        "conway-p3.json",
        "protocol.json",
    ):
        path = source / name
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    runtime = {
        "plutus_v2": {
            "verified": True,
            "pinned_cost_model": {
                "path": str(paths[0]),
                "sha256": hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            },
            "generated_genesis": [
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in paths[1:7]
            ],
            "live_protocol_parameters": {
                "path": str(paths[7]),
                "sha256": hashlib.sha256(paths[7].read_bytes()).hexdigest(),
            },
        }
    }

    retained = subject._retain_plutus_v2_topology(runtime, tmp_path / "retained")

    assert retained["verified"] is True
    assert len(list((tmp_path / "retained").iterdir())) == 8
    paths[7].write_text("changed", encoding="utf-8")
    import pytest

    with pytest.raises(RuntimeError, match="digest mismatch"):
        subject._retain_plutus_v2_topology(runtime, tmp_path / "bad")
