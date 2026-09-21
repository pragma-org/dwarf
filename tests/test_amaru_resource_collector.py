from profile_manager.measurement_collectors.amaru_resources import (
    AmaruResourceCollector,
)
from profile_manager.measurement_runtime import CollectorContext


def _context(tmp_path):
    measurement_id = "amaru-stock-resources"
    return CollectorContext(
        measurement_id=measurement_id,
        definition={"id": measurement_id},
        parameters={},
        run_dir=tmp_path,
        collector_dir=tmp_path / "measurements" / "collectors" / measurement_id,
    )


def test_resource_collector_samples_actual_target_and_reports_cost(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    samples = iter(
        [
            {
                "pid": 4242,
                "monotonic_seconds": 10.0,
                "cpu_time_seconds": 1.0,
                "rss_bytes": 100,
                "network_rx_bytes": 1000,
                "network_tx_bytes": 2000,
                "network_scope": "process-network-namespace",
                "disk_read_bytes": 3000,
                "disk_write_bytes": 4000,
                "fd_count": 5,
                "threads": 6,
            },
            {
                "pid": 4242,
                "monotonic_seconds": 12.0,
                "cpu_time_seconds": 2.0,
                "rss_bytes": 200,
                "network_rx_bytes": 1300,
                "network_tx_bytes": 2600,
                "network_scope": "process-network-namespace",
                "disk_read_bytes": 3400,
                "disk_write_bytes": 4800,
                "fd_count": 7,
                "threads": 8,
            },
        ]
    )
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=lambda _pid, _index: next(samples),
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    assert result["target"] == {"node": "amaru-1", "pid": 4242}
    assert result["measurements"]["cpu_percent"]["mean"] == 50.0
    assert result["measurements"]["rss_bytes"]["maximum"] == 200.0
    assert result["measurements"]["network_rx_bytes"]["delta"] == 300.0
    assert result["measurements"]["network_tx_bytes"]["delta"] == 600.0
    assert result["measurements"]["disk_read_bytes"]["delta"] == 400.0
    assert result["measurements"]["disk_write_bytes"]["delta"] == 800.0
    assert result["measurements"]["fd_count"]["maximum"] == 7.0
    assert result["measurements"]["threads"]["maximum"] == 8.0
    assert result["measurements"]["network_rx_bytes"]["scope"] == "process-network-namespace"
    assert (context.collector_dir / "samples.json").is_file()


def test_resource_collector_keeps_missing_proc_fields_unavailable(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    samples = iter(
        [
            {"pid": 9, "monotonic_seconds": 1.0, "cpu_time_seconds": None},
            {"pid": 9, "monotonic_seconds": 2.0, "cpu_time_seconds": None},
        ]
    )
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 9,
        sample_reader=lambda _pid, _index: next(samples),
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    assert result["measurements"]["cpu_percent"]["status"] == "unavailable"
    assert result["measurements"]["network_rx_bytes"]["status"] == "unavailable"
    assert result["measurements"]["rss_bytes"]["maximum"] is None


def test_resource_collector_attributes_samples_to_named_windows(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    windows = tmp_path / "measurements" / "windows.ndjson"
    windows.parent.mkdir(parents=True)
    windows.write_text(
        "\n".join(
            [
                '{"phase_id":"baseline","state":"start","epoch_seconds":10}',
                '{"phase_id":"baseline","state":"end","epoch_seconds":19}',
                '{"phase_id":"hostile","state":"start","epoch_seconds":20}',
                '{"phase_id":"hostile","state":"end","epoch_seconds":29}',
                '{"phase_id":"recovery","state":"start","epoch_seconds":30}',
                '{"phase_id":"recovery","state":"end","epoch_seconds":39}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    samples = []
    for index, epoch in enumerate((11, 18, 21, 28, 31, 38)):
        samples.append(
            {
                "pid": 4242,
                "ts_epoch_s": float(epoch),
                "monotonic_seconds": float(epoch),
                "cpu_time_seconds": float(index),
                "rss_bytes": 100 + index,
                "network_rx_bytes": 1000 + index * 10,
                "network_tx_bytes": 2000 + index * 10,
                "network_scope": "process-network-namespace",
                "disk_read_bytes": 3000 + index * 10,
                "disk_write_bytes": 4000 + index * 10,
                "fd_count": 5 + index,
                "threads": 6 + index,
            }
        )
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=lambda _pid, index: samples[index],
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    for _ in range(4):
        collector._capture()
    collector.stop(context)

    result = collector.finalize(context)

    assert set(result["windows"]) == {"baseline", "hostile", "recovery"}
    assert result["windows"]["baseline"]["sample_count"] == 2
    assert result["windows"]["hostile"]["measurements"]["rss_bytes"]["sample_count"] == 2
    assert result["windows"]["recovery"]["measurements"]["cpu_percent"]["sample_count"] == 1


def test_resource_collector_explains_missing_window_markers(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=lambda _pid, _index: {
            "monotonic_seconds": 1.0,
            "ts_epoch_s": 1.0,
            "cpu_time_seconds": 1.0,
        },
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)

    result = collector.finalize(context)

    assert result["window_attribution"]["status"] == "unavailable"
    assert "windows.ndjson" in result["window_attribution"]["reason"]
    assert all(window["status"] == "unavailable" for window in result["windows"].values())


def test_resource_collector_attributes_single_controlled_chain_window(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    windows = tmp_path / "measurements" / "windows.ndjson"
    windows.parent.mkdir(parents=True)
    windows.write_text(
        '{"phase_id":"controlled-chain-progress","state":"start","epoch_seconds":10}\n'
        '{"phase_id":"controlled-chain-progress","state":"end","epoch_seconds":50}\n'
    )
    samples = [
        {
            "pid": 4242, "ts_epoch_s": float(epoch), "monotonic_seconds": float(epoch),
            "cpu_time_seconds": float(index), "rss_bytes": 100 + index,
        }
        for index, epoch in enumerate((11, 20, 30, 40, 49))
    ]
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=lambda _pid, index: samples[index],
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    for _ in range(3):
        collector._capture()
    collector.stop(context)
    result = collector.finalize(context)

    assert set(result["windows"]) == {"controlled-chain-progress"}
    assert result["windows"]["controlled-chain-progress"]["sample_count"] == 5
    assert result["window_attribution"]["status"] == "available"


def test_resource_collector_attributes_nested_recovery_and_sync_windows(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    windows = tmp_path / "measurements" / "windows.ndjson"
    windows.parent.mkdir(parents=True)
    windows.write_text(
        '{"phase_id":"restart-recovery","state":"start","epoch_seconds":10}\n'
        '{"phase_id":"controlled-sync-range","state":"start","epoch_seconds":20}\n'
        '{"phase_id":"controlled-sync-range","state":"end","epoch_seconds":40}\n'
        '{"phase_id":"restart-recovery","state":"end","epoch_seconds":50}\n'
    )
    samples = [
        {
            "pid": 4242, "ts_epoch_s": float(epoch),
            "monotonic_seconds": float(epoch),
            "cpu_time_seconds": float(index), "rss_bytes": 100 + index,
        }
        for index, epoch in enumerate((11, 20, 30, 40, 49))
    ]
    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=lambda _pid, index: samples[index],
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    for _ in range(3):
        collector._capture()
    collector.stop(context)
    result = collector.finalize(context)

    assert result["windows"]["restart-recovery"]["sample_count"] == 5
    assert result["windows"]["controlled-sync-range"]["sample_count"] == 3
    assert result["window_attribution"]["status"] == "available"


def test_resource_collector_re_resolves_pid_after_real_restart(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    pids = iter([100, 200])
    calls = []

    def reader(pid, index):
        calls.append((pid, index))
        if pid == 100 and index == 1:
            raise ProcessLookupError("old process exited")
        return {
            "pid": pid,
            "ts_epoch_s": float(index),
            "monotonic_seconds": float(index),
            "cpu_time_seconds": float(index),
            "rss_bytes": 100,
        }

    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        resolve_pid=lambda _path, _node: next(pids),
        sample_reader=reader,
        background=False,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    collector._capture()
    collector.stop(context)

    assert calls == [(100, 0), (100, 1), (200, 1), (200, 2)]
    assert collector._pid == 200
    assert len(collector._samples) == 3
