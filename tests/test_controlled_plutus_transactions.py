import json
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
                {"outcome": "accepted", "chain_outcome": "included-valid"}
            ] * 30 + [
                {"outcome": "rejected", "chain_outcome": "included-invalid"}
            ] * 30,
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
