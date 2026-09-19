import json
from pathlib import Path

from scripts.qualify_cardano_measurement_target import qualify_target


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
PATCH_SET = "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
DIGEST = "sha256:" + "a" * 64


def test_qualification_requires_live_samples_for_every_patched_surface(tmp_path):
    build_root = tmp_path / "build"
    evidence = build_root / "evidence"
    evidence.mkdir(parents=True)
    build_result = evidence / "build-result.json"
    build_result.write_text(json.dumps({
        "source": {"release": "11.1.2", "revision": REVISION},
        "patch_set_sha256": PATCH_SET,
        "executable": {"sha256": "b" * 64},
        "image": {
            "status": "built",
            "reference": "dwarf/cardano-measurement:exact",
            "image_id": DIGEST,
            "repo_digests": ["dwarf/cardano-measurement@" + DIGEST],
            "smoke": {"status": "passed", "log": {"sha256": "c" * 64}},
        },
    }))
    runtime_root = tmp_path / "runtime"
    trace_root = runtime_root / "logs" / "node1"
    trace_root.mkdir(parents=True)
    runtime_root.joinpath("runtime.json").write_text(json.dumps({
        "nodes": [{
            "id": "node1", "impl": "cardano-node", "target_mode": "patched",
            "source_revision": REVISION, "patch_set_sha256": PATCH_SET,
            "image_digest": DIGEST,
        }],
    }))
    common = {
        "schema_version": "v1", "target": "cardano-node::measurement",
        "ended_monotonic_ns": 10, "duration_us": 1,
    }
    trace_root.joinpath("cardano-measurement.ndjson").write_text("\n".join([
        json.dumps({**common, "event": "protocol_receive_decode", "boundary": "receive-plus-incremental-decode", "protocol": "Handshake", "state": "StPropose", "outcome": "accepted"}),
        json.dumps({**common, "event": "ledger_stage", "stage": "block-application", "outcome": "accepted"}),
        json.dumps({**common, "event": "ledger_stage", "stage": "epoch-transition", "outcome": "completed"}),
    ]) + "\n")
    trace_root.joinpath("cardano-plutus.ndjson").write_text("\n".join([
        json.dumps({**common, "event": "ledger_stage", "stage": "plutus-vm", "outcome": "accepted"}),
        json.dumps({**common, "event": "ledger_stage", "stage": "plutus-vm", "outcome": "rejected"}),
    ]) + "\n")
    compose_report = tmp_path / "compose-report.json"
    compose_report.write_text(json.dumps({
        "healthy": True,
        "cardano_chain_progress_gate": {"ready": True, "ready_node_count": 3},
    }))
    workload = tmp_path / "workload.json"
    workload.write_text(json.dumps({
        "target": {"mode": "patched", "source_revision": REVISION, "patch_set_sha256": PATCH_SET, "image_digest": DIGEST},
        "attempts": {"total": 30},
        "plutus_workload": {"transaction_count": 2, "records": [{"outcome": "accepted"}, {"outcome": "rejected"}]},
    }))

    result = qualify_target(
        build_result_path=build_result,
        runtime_root=runtime_root,
        compose_report_path=compose_report,
        workload_result_path=workload,
        qualification_path=evidence / "runtime-probe.json",
        registry_root=tmp_path / "registry",
    )

    assert result["status"] == "passed"
    assert result["sample_counts"] == {
        "block_application": 1,
        "epoch_transition": 1,
        "plutus_vm": 2,
        "protocol_receive_decode": 1,
    }
    assert result["plutus_transaction_count"] == 2
    assert result["plutus_measured_outcomes"] == ["accepted", "rejected"]
    record = json.loads(next((tmp_path / "registry").rglob("*.json")).read_text())
    assert record["image_digest"] == DIGEST
    assert record["runtime_probe_log_sha256"].startswith("sha256:")
