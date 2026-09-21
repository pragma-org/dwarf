import json
import hashlib
from contextlib import ExitStack

from scripts import runtime_controlled_plutus_transactions as subject


def test_controlled_workload_retains_before_and_after_target_health(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(json.dumps({"runtime": "fixture"}))
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
                {"attempt_id": f"valid-{index}", "outcome": "accepted", "chain_outcome": "included-valid", "lock_transaction_id": f"lock-v-{index}", "spend_transaction_id": f"spend-v-{index}"}
                for index in range(30)
            ] + [
                {"attempt_id": f"invalid-{index}", "outcome": "rejected", "chain_outcome": "included-invalid", "lock_transaction_id": f"lock-i-{index}", "spend_transaction_id": f"spend-i-{index}"}
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
    assert json.loads((tmp_path / "out" / "result.json").read_text()) == result


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
        "compose_project": "qualified-profile",
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


def test_amaru_workload_requires_verified_live_plutus_v2_topology(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(
        json.dumps({"compose_project": "profile", "plutus_v2": {"verified": False}})
    )

    import pytest

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
