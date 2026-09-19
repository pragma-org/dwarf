from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_collectors.cardano_factory import build_cardano_measurement_factories
from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_SOURCE_REVISION,
    CardanoPatchedCollector,
    PATCHED_MEASUREMENT_IDS,
)
from profile_manager.measurement_collectors.cardano_resources import CardanoResourceCollector
from profile_manager.measurement_collectors.cardano_stock import CardanoStockCollector, STOCK_MEASUREMENT_IDS


def _entry(measurement_id):
    return {"id": measurement_id, "definition": {"id": measurement_id}, "parameters": {}}


def test_cardano_factory_binds_stock_external_and_real_resource_collectors(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"nodes":[{"id":"node1","impl":"cardano-node"}]}\n')
    trace = tmp_path / "node1.ndjson"
    trace.write_text("")
    factories = build_cardano_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="node1",
        trace_paths=[trace],
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        resource_resolve_pid=lambda _path, _node: 123,
        resource_sample_reader=lambda _pid, index: {
            "pid": 123,
            "monotonic_seconds": float(index),
            "cpu_time_seconds": float(index),
        },
        resource_background=False,
    )
    assert set(STOCK_MEASUREMENT_IDS).issubset(factories)
    assert isinstance(factories["cardano-stock-blockfetch"](_entry("cardano-stock-blockfetch")), CardanoStockCollector)
    assert isinstance(factories["cardano-stock-resources"](_entry("cardano-stock-resources")), CardanoResourceCollector)
    assert isinstance(factories["cardano-external-workload-accounting"](_entry("cardano-external-workload-accounting")), WorkloadAccountingCollector)
    assert isinstance(factories["cardano-external-restart-readiness"](_entry("cardano-external-restart-readiness")), RestartReadinessCollector)
    assert isinstance(factories["cardano-external-sync-speed"](_entry("cardano-external-sync-speed")), SyncSpeedCollector)


def test_cardano_factory_omits_sync_without_a_trusted_tip_probe(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"nodes":[{"id":"node1","impl":"cardano-node"}]}\n')
    factories = build_cardano_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="node1",
        trace_paths=[],
        tip_probe=None,
    )
    assert "cardano-external-sync-speed" not in factories


def test_cardano_factory_adds_patched_collectors_only_for_exact_identity(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"nodes":[{"id":"node1","impl":"cardano-node"}]}\n')
    trace = tmp_path / "node1.ndjson"
    trace.write_text("")
    patched_trace = tmp_path / "node1-measurement.ndjson"
    patched_trace.write_text("")
    identity = {
        "implementation": "cardano-node",
        "version": "11.1.2",
        "source_revision": CARDANO_SOURCE_REVISION,
        "mode": "patched",
        "image_digest": "sha256:" + "a" * 64,
        "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
    }

    factories = build_cardano_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="node1",
        trace_paths=[trace],
        patched_trace_paths=[patched_trace],
        tip_probe=None,
        target_identity=identity,
    )

    assert set(PATCHED_MEASUREMENT_IDS).issubset(factories)
    assert isinstance(
        factories["cardano-patched-protocol-decode"](
            _entry("cardano-patched-protocol-decode")
        ),
        CardanoPatchedCollector,
    )
