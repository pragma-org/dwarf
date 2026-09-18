from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_collectors.amaru_factory import (
    build_amaru_measurement_factories,
)
from profile_manager.measurement_collectors.amaru_resources import AmaruResourceCollector
from profile_manager.measurement_collectors.amaru_patched import (
    AMARU_MEASUREMENT_PATCH_SHA256,
    AMARU_SOURCE_REVISION,
    AmaruPatchedCollector,
    PATCHED_MEASUREMENT_IDS,
)
from profile_manager.measurement_collectors.amaru_stock import (
    STOCK_MEASUREMENT_IDS,
    AmaruStockCollector,
)


def _entry(measurement_id):
    return {"id": measurement_id, "definition": {"id": measurement_id}, "parameters": {}}


def test_factory_registry_binds_runtime_owned_inputs(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    trace = tmp_path / "amaru.ndjson"
    trace.write_text("")
    otlp = tmp_path / "otlp.json"
    otlp.write_text('{"resourceSpans":[]}\n')

    factories = build_amaru_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        json_trace_paths=[trace],
        otlp_trace_paths=[otlp],
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        expected_start_height=1,
        expected_end_height=2,
        peer_policy="single-controlled-producer",
        resource_resolve_pid=lambda _path, _node: 123,
        resource_sample_reader=lambda _pid, _index: {
            "pid": 123,
            "monotonic_seconds": float(_index),
            "cpu_time_seconds": float(_index),
        },
        resource_background=False,
    )

    assert set(STOCK_MEASUREMENT_IDS).issubset(factories)
    assert isinstance(
        factories["amaru-stock-header-lifecycle"](_entry("amaru-stock-header-lifecycle")),
        AmaruStockCollector,
    )
    assert isinstance(
        factories["amaru-stock-resources"](_entry("amaru-stock-resources")),
        AmaruResourceCollector,
    )
    assert isinstance(
        factories["amaru-external-workload-accounting"](
            _entry("amaru-external-workload-accounting")
        ),
        WorkloadAccountingCollector,
    )
    assert isinstance(
        factories["amaru-external-restart-readiness"](
            _entry("amaru-external-restart-readiness")
        ),
        RestartReadinessCollector,
    )
    assert isinstance(
        factories["amaru-external-sync-speed"](_entry("amaru-external-sync-speed")),
        SyncSpeedCollector,
    )


def test_factory_registry_omits_sync_without_a_trusted_tip_probe(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    factories = build_amaru_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        json_trace_paths=[],
        otlp_trace_paths=[],
        tip_probe=None,
    )

    assert "amaru-external-sync-speed" not in factories


def test_factory_registry_adds_patched_collectors_only_for_exact_patched_identity(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    trace = tmp_path / "amaru.ndjson"
    trace.write_text("")
    identity = {
        "implementation": "amaru",
        "version": "10.11.20260912",
        "source_revision": AMARU_SOURCE_REVISION,
        "mode": "patched",
        "image_digest": "sha256:" + "a" * 64,
        "patch_set_sha256": AMARU_MEASUREMENT_PATCH_SHA256,
    }

    factories = build_amaru_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        json_trace_paths=[trace],
        otlp_trace_paths=[],
        tip_probe=None,
        target_identity=identity,
    )

    assert set(PATCHED_MEASUREMENT_IDS).issubset(factories)
    assert isinstance(
        factories["amaru-patched-protocol-decode"](
            _entry("amaru-patched-protocol-decode")
        ),
        AmaruPatchedCollector,
    )
