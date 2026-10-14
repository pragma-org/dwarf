from profile_manager.measurement_collectors.amaru_resources import (
    AmaruResourceCollector,
)
from profile_manager.measurement_collectors.amaru_factory import (
    build_amaru_measurement_factories,
)
from profile_manager.measurement_collectors.cardano_resources import (
    CardanoResourceCollector,
    _docker_container_identity,
)
from profile_manager.measurement_runtime import CollectorContext

import json
import threading
import time
from types import SimpleNamespace

import pytest


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


def test_amaru_factory_samples_exact_owned_container_without_host_ps(
    monkeypatch, tmp_path
):
    """Control-plane runs cannot see sibling container PIDs in their /proc."""
    metadata = tmp_path / "runtime.json"
    container_name = "dwarf-profile-test-amaru-relay-1-1"
    compose_project = "dwarf-profile-test"
    metadata.write_text(
        json.dumps(
            {
                "profile_id": "profile-test",
                "compose_project": compose_project,
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
                "identity": {
                    "services": {
                        "amaru-relay-1": {
                            "container": container_name,
                            "matched": True,
                        }
                    }
                },
                "actual_topology": {
                    "selected_target_node_services": {
                        "amaru1": "amaru-relay-1"
                    },
                    "selected_nodes": [
                        {
                            "id": "amaru1",
                            "implementation": "amaru",
                            "service": "amaru-relay-1",
                            "supporting": False,
                        }
                    ],
                },
            }
        )
    )
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    stats = iter(
        [
            {
                "BlockIO": "1MB / 2MB",
                "CPUPerc": "2.00%",
                "ID": "abcdef123456",
                "MemUsage": "64MiB / 2GiB",
                "Name": container_name,
                "NetIO": "5MB / 7MB",
                "PIDs": "14",
            },
            {
                "BlockIO": "2MB / 4MB",
                "CPUPerc": "4.00%",
                "ID": "abcdef123456",
                "MemUsage": "96MiB / 2GiB",
                "Name": container_name,
                "NetIO": "8MB / 11MB",
                "PIDs": "16",
            },
        ]
    )
    commands = []

    def docker_run(command, **_kwargs):
        commands.append(command)
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "Id": "abcdef1234567890",
                            "Name": "/" + container_name,
                            "State": {"Status": "running"},
                            "Config": {
                                "Labels": {
                                    "io.dwarf.managed": "true",
                                    "io.dwarf.installation": "install-test",
                                    "io.dwarf.topology": "profile-test",
                                    "io.dwarf.component": "node",
                                    "io.dwarf.node_id": "amaru1",
                                    "io.dwarf.implementation": "amaru",
                                    "com.docker.compose.project": compose_project,
                                    "com.docker.compose.service": "amaru-relay-1",
                                }
                            },
                        }
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["docker", "stats", "--no-stream"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(next(stats)) + "\n",
                stderr="",
            )
        raise AssertionError(command)

    factories = build_amaru_measurement_factories(
        runtime_metadata_path=metadata,
        target_node="amaru-relay-1",
        json_trace_paths=[],
        otlp_trace_paths=[],
        tip_probe=None,
        resource_background=False,
        resource_docker_run=docker_run,
    )
    collector = factories["amaru-stock-resources"](
        {"id": "amaru-stock-resources", "parameters": {}}
    )
    context = _context(tmp_path)

    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    assert result["target"] == {
        "node": "amaru-relay-1",
        "container_name": container_name,
        "container_id": "abcdef1234567890",
    }
    assert result["source"]["kind"] == "docker-container-cgroup"
    assert result["source"]["sample_count"] == 2
    assert result["measurements"]["container_memory_bytes"]["maximum"] == 96 * 1024 * 1024
    assert all(command[0] == "docker" for command in commands)


@pytest.mark.parametrize(
    "mapping",
    [None, [], {}, {"amaru1": "different-service"}],
    ids=("missing", "malformed", "empty", "no-service-match"),
)
def test_amaru_control_resource_identity_requires_exact_target_mapping(
    tmp_path, mapping
):
    metadata = tmp_path / "runtime.json"
    actual_topology = {
        "selected_nodes": [
            {
                "id": "amaru1",
                "implementation": "amaru",
                "service": "amaru-relay-1",
                "supporting": False,
            }
        ]
    }
    if mapping is not None:
        actual_topology["selected_target_node_services"] = mapping
    metadata.write_text(
        json.dumps(
            {
                "profile_id": "profile-test",
                "compose_project": "dwarf-profile-test",
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
                "identity": {
                    "services": {
                        "amaru-relay-1": {
                            "container": "dwarf-profile-test-amaru-relay-1-1"
                        }
                    }
                },
                "actual_topology": actual_topology,
            }
        )
    )

    def docker_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": "abcdef1234567890",
                        "Name": "/dwarf-profile-test-amaru-relay-1-1",
                        "State": {"Status": "running"},
                        "Config": {"Labels": {}},
                    }
                ]
            ),
            stderr="",
        )

    with pytest.raises(RuntimeError, match="resource target mapping"):
        _docker_container_identity(
            metadata,
            "amaru-relay-1",
            docker_run=docker_run,
        )


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


def test_resource_collector_waits_for_active_capture_before_final_sample(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text('{"amaru_nodes":[{"name":"amaru-1","impl":"amaru"}]}\n')
    capture_started = threading.Event()
    release_capture = threading.Event()
    indexes = []

    def reader(_pid, index):
        indexes.append(index)
        if index == 1:
            capture_started.set()
            assert release_capture.wait(2)
        return {
            "pid": 4242,
            "monotonic_seconds": float(index),
            "cpu_time_seconds": float(index),
        }

    collector = AmaruResourceCollector(
        {"id": "amaru-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="amaru-1",
        sample_interval_seconds=0.01,
        capture_timeout_seconds=0.5,
        resolve_pid=lambda _path, _node: 4242,
        sample_reader=reader,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    assert capture_started.wait(1)

    stop_thread = threading.Thread(target=collector.stop, args=(context,))
    stop_thread.start()
    time.sleep(0.05)

    assert stop_thread.is_alive()
    assert indexes == [0, 1]
    release_capture.set()
    stop_thread.join(timeout=1)

    assert not stop_thread.is_alive()
    assert indexes == [0, 1, 2]
    assert [sample["sample_index"] for sample in collector._samples] == [0, 1, 2]
    assert collector._thread is not None and not collector._thread.is_alive()


@pytest.mark.parametrize(
    ("configured_installation", "container_installation"),
    [(None, "other-install"), ("install-test", "other-install")],
    ids=("missing-installation-identity", "mismatched-installation-label"),
)
def test_cardano_resource_collector_rejects_unproven_installation_ownership(
    monkeypatch, tmp_path, configured_installation, container_installation
):
    metadata = tmp_path / "runtime.json"
    metadata.write_text(
        json.dumps(
            {
                "profile_id": "profile-s-cardano-measurement-stock-control",
                "nodes": [
                    {
                        "id": "node1",
                        "impl": "cardano-node",
                        "container_name": "dwarf-cardano-node1",
                    }
                ],
            }
        )
    )
    if configured_installation is None:
        monkeypatch.delenv("DWARF_INSTALLATION_ID", raising=False)
    else:
        monkeypatch.setenv("DWARF_INSTALLATION_ID", configured_installation)

    def docker_run(command, **_kwargs):
        assert command[:2] == ["docker", "inspect"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": "abcdef1234567890",
                        "Name": "/dwarf-cardano-node1",
                        "State": {"Status": "running"},
                        "Config": {
                            "Labels": {
                                "io.dwarf.managed": "true",
                                "io.dwarf.installation": container_installation,
                                "io.dwarf.topology": "profile-s-cardano-measurement-stock-control",
                                "io.dwarf.component": "node",
                                "io.dwarf.node_id": "node1",
                            }
                        },
                    }
                ]
            ),
            stderr="",
        )

    collector = CardanoResourceCollector(
        {"id": "cardano-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="node1",
        docker_run=docker_run,
        background=False,
    )

    with pytest.raises(RuntimeError, match="installation|managed topology"):
        collector.prepare(_context(tmp_path))


def test_cardano_resource_collector_samples_exact_owned_container_cgroup(
    monkeypatch, tmp_path
):
    metadata = tmp_path / "runtime.json"
    metadata.write_text(
        json.dumps(
            {
                "profile_id": "profile-s-cardano-measurement-stock-control",
                "nodes": [
                    {
                        "id": "node1",
                        "impl": "cardano-node",
                        "container_name": "dwarf-cardano-node1",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    stats = iter(
        [
            {
                "BlockIO": "1.5MB / 2MiB",
                "CPUPerc": "2.00%",
                "Container": "dwarf-cardano-node1",
                "ID": "abcdef123456",
                "MemUsage": "64MiB / 2GiB",
                "Name": "dwarf-cardano-node1",
                "NetIO": "5MB / 7MB",
                "PIDs": "14",
            },
            {
                "BlockIO": "2.5MB / 4MiB",
                "CPUPerc": "4.00%",
                "Container": "dwarf-cardano-node1",
                "ID": "abcdef123456",
                "MemUsage": "96MiB / 2GiB",
                "Name": "dwarf-cardano-node1",
                "NetIO": "8MB / 11MB",
                "PIDs": "16",
            },
        ]
    )
    commands = []

    def docker_run(command, **_kwargs):
        commands.append(command)
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "Id": "abcdef1234567890",
                            "Name": "/dwarf-cardano-node1",
                            "State": {"Status": "running"},
                            "Config": {
                                "Labels": {
                                    "io.dwarf.managed": "true",
                                    "io.dwarf.installation": "install-test",
                                    "io.dwarf.topology": "profile-s-cardano-measurement-stock-control",
                                    "io.dwarf.component": "node",
                                    "io.dwarf.node_id": "node1",
                                }
                            },
                        }
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["docker", "stats", "--no-stream"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(next(stats)) + "\n",
                stderr="",
            )
        raise AssertionError(command)

    collector = CardanoResourceCollector(
        {"id": "cardano-stock-resources", "parameters": {}},
        runtime_metadata_path=metadata,
        target_node="node1",
        docker_run=docker_run,
        background=False,
    )
    context = CollectorContext(
        measurement_id="cardano-stock-resources",
        definition={"id": "cardano-stock-resources"},
        parameters={},
        run_dir=tmp_path,
        collector_dir=tmp_path / "measurements" / "collectors" / "cardano-stock-resources",
    )

    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    assert result["target"] == {
        "node": "node1",
        "container_name": "dwarf-cardano-node1",
        "container_id": "abcdef1234567890",
    }
    assert result["source"]["kind"] == "docker-container-cgroup"
    assert result["source"]["sample_count"] == 2
    assert result["source"]["errors"] == []
    assert result["measurements"]["cpu_percent"]["mean"] == 3.0
    assert result["measurements"]["rss_bytes"]["status"] == "unavailable"
    assert result["measurements"]["container_memory_bytes"]["maximum"] == 96 * 1024 * 1024
    assert result["measurements"]["network_rx_bytes"]["delta"] == 3_000_000
    assert result["measurements"]["network_tx_bytes"]["delta"] == 4_000_000
    assert result["measurements"]["disk_read_bytes"]["delta"] == 1_000_000
    assert result["measurements"]["disk_write_bytes"]["delta"] == 2 * 1024 * 1024
    assert result["measurements"]["threads"]["status"] == "unavailable"
    assert result["measurements"]["container_tasks"]["maximum"] == 16
    assert result["measurements"]["network_rx_bytes"]["scope"] == "container-network-namespace"
    assert commands[1][-1] == "abcdef1234567890"
