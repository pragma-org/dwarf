import json
from pathlib import Path
from types import SimpleNamespace

from scripts import runtime_resource_profile


def _metadata(path: Path, nodes):
    path.write_text(
        json.dumps(
            {
                "haskell_nodes": [n for n in nodes if n["impl"] == "cardano-node"],
                "amaru_nodes": [n for n in nodes if n["impl"] == "amaru"],
            }
        )
    )
    return path


def _proc_status(proc_root: Path, pid: int, *, name: str):
    root = proc_root / str(pid)
    root.mkdir(parents=True)
    (root / "status").write_text(
        f"Name:\t{name}\nState:\tS (sleeping)\nVmRSS:\t100 kB\nThreads:\t4\n"
        "voluntary_ctxt_switches:\t7\nnonvoluntary_ctxt_switches:\t2\n"
    )
    return root


def test_docker_resolution_selects_actual_amaru_child_not_container_init(
    monkeypatch, tmp_path
):
    metadata = _metadata(
        tmp_path / "runtime.json",
        [{"id": "amaru-1", "name": "amaru-1", "impl": "amaru", "container_name": "amaru-1"}],
    )
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 100, name="tini")
    _proc_status(proc_root, 102, name="amaru")

    def fake_run(command, **_kwargs):
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"State": {"Pid": 100}}]),
            )
        if command[:2] == ["docker", "top"]:
            return SimpleNamespace(
                returncode=0,
                stdout="PID COMMAND COMMAND\n100 tini /tini -- wrapper\n102 amaru /target/amaru node run\n",
            )
        raise AssertionError(command)

    monkeypatch.setattr(runtime_resource_profile.subprocess, "run", fake_run)

    assert runtime_resource_profile.resolve_target_pid(
        metadata, "amaru-1", proc_root=proc_root
    ) == 102


def test_docker_resolution_selects_loader_wrapped_cardano_child(
    monkeypatch, tmp_path
):
    metadata = tmp_path / "runtime.json"
    metadata.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "node1",
                        "impl": "cardano-node",
                        "container_name": "cardano-node1",
                    }
                ]
            }
        )
    )
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 120, name="bash")
    _proc_status(proc_root, 121, name="ld-linux-x86-64")
    _proc_status(proc_root, 122, name="tee")

    def fake_run(command, **_kwargs):
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"State": {"Pid": 120}}]),
            )
        if command[:2] == ["docker", "top"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "PID COMMAND COMMAND\n"
                    "120 bash bash -lc exec cardano-node run | tee stdout.log\n"
                    "121 ld-linux-x86-64 /opt/runtime/ld-linux-x86-64.so.2 "
                    "--library-path /opt/runtime/lib /opt/runtime/bin/cardano-node "
                    "run --database-path /env/node-data/node1/db\n"
                    "122 tee tee stdout.log\n"
                ),
            )
        raise AssertionError(command)

    monkeypatch.setattr(runtime_resource_profile.subprocess, "run", fake_run)

    assert runtime_resource_profile.resolve_target_pid(
        metadata, "node1", proc_root=proc_root
    ) == 121


def test_control_runtime_identity_services_are_valid_resource_targets(
    monkeypatch, tmp_path
):
    metadata = tmp_path / "runtime.json"
    metadata.write_text(
        json.dumps(
            {
                "identity": {
                    "services": {
                        "amaru-relay-1": {
                            "container": "dwarf-profile-r-amaru-relay-1",
                            "matched": True,
                        }
                    }
                }
            }
        )
    )
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 110, name="tini")
    _proc_status(proc_root, 112, name="amaru")

    def fake_run(command, **_kwargs):
        if command[:2] == ["docker", "inspect"]:
            assert command[2] == "dwarf-profile-r-amaru-relay-1"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"State": {"Pid": 110}}]),
            )
        if command[:2] == ["docker", "top"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "PID COMMAND COMMAND\n"
                    "110 tini /tini -- wrapper\n"
                    "112 amaru /target/amaru node run\n"
                ),
            )
        raise AssertionError(command)

    monkeypatch.setattr(runtime_resource_profile.subprocess, "run", fake_run)

    assert runtime_resource_profile.resolve_target_pid(
        metadata, "amaru-relay-1", proc_root=proc_root
    ) == 112


def test_pid_file_resolution_supports_amaru_and_combined_metadata(tmp_path):
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 202, name="amaru")
    pid_file = tmp_path / "amaru.pid"
    pid_file.write_text("202\n")
    metadata = _metadata(
        tmp_path / "runtime.json",
        [{"id": "amaru-1", "name": "amaru-1", "impl": "amaru", "pid_file": str(pid_file)}],
    )

    assert runtime_resource_profile.resolve_target_pid(
        metadata, "amaru-1", proc_root=proc_root
    ) == 202


def test_process_table_resolution_supports_cardano_and_amaru(monkeypatch, tmp_path):
    metadata = _metadata(
        tmp_path / "runtime.json",
        [
            {"id": "node-1", "name": "node-1", "impl": "cardano-node"},
            {"id": "amaru-1", "name": "amaru-1", "impl": "amaru"},
        ],
    )
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 301, name="cardano-node")
    _proc_status(proc_root, 302, name="amaru")

    def fake_run(command, **_kwargs):
        assert command[:3] == ["ps", "-eo", "pid=,comm=,args="]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "301 cardano-node cardano-node run --database-path node-data/node-1/db\n"
                "302 amaru amaru node run --ledger-dir node-data/amaru-1/db\n"
            ),
        )

    monkeypatch.setattr(runtime_resource_profile.subprocess, "run", fake_run)

    assert runtime_resource_profile.resolve_target_pid(metadata, "node-1", proc_root=proc_root) == 301
    assert runtime_resource_profile.resolve_target_pid(metadata, "amaru-1", proc_root=proc_root) == 302


def test_proc_sampling_captures_cpu_rss_network_disk_fds_and_threads(tmp_path):
    proc_root = tmp_path / "proc"
    root = _proc_status(proc_root, 401, name="amaru")
    (root / "fd").mkdir()
    (root / "fd/1").write_text("")
    (root / "fd/2").write_text("")
    (root / "io").write_text("read_bytes: 1000\nwrite_bytes: 2000\n")
    (root / "stat").write_text(
        "401 (amaru) S 1 1 1 0 0 0 0 0 0 0 50 25 0 0 20 0 4 0 0 0 0 0\n"
    )
    (root / "net").mkdir()
    (root / "net/dev").write_text(
        "Inter-| Receive | Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
        "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
        "  eth0: 3000 3 0 0 0 0 0 0 4000 4 0 0 0 0 0 0\n"
    )

    samples = runtime_resource_profile.collect_samples(
        pid=401,
        sample_count=1,
        sample_interval_seconds=0,
        proc_root=proc_root,
        clock_ticks_per_second=100,
    )

    sample = samples[0]
    assert sample["cpu_time_seconds"] == 0.75
    assert sample["cpu_percent"] is None
    assert sample["rss_bytes"] == 102400
    assert sample["network_rx_bytes"] == 3000
    assert sample["network_tx_bytes"] == 4000
    assert sample["network_scope"] == "process-network-namespace"
    assert sample["disk_read_bytes"] == 1000
    assert sample["disk_write_bytes"] == 2000
    assert sample["fd_count"] == 2
    assert sample["threads"] == 4


def test_unavailable_proc_fields_remain_null_instead_of_zero(tmp_path):
    proc_root = tmp_path / "proc"
    _proc_status(proc_root, 501, name="amaru")

    sample = runtime_resource_profile.collect_samples(
        pid=501,
        sample_count=1,
        sample_interval_seconds=0,
        proc_root=proc_root,
        clock_ticks_per_second=100,
    )[0]

    assert sample["cpu_time_seconds"] is None
    assert sample["network_rx_bytes"] is None
    assert sample["network_tx_bytes"] is None
    assert sample["disk_read_bytes"] is None
    assert sample["disk_write_bytes"] is None
    assert sample["fd_count"] is None


def test_permission_restricted_optional_proc_fields_do_not_discard_the_sample(
    monkeypatch, tmp_path
):
    proc_root = tmp_path / "proc"
    root = _proc_status(proc_root, 601, name="amaru")
    (root / "fd").mkdir()
    (root / "io").write_text("read_bytes: 1000\nwrite_bytes: 2000\n")
    original_iterdir = Path.iterdir
    original_read_text = Path.read_text

    def restricted_iterdir(path):
        if path == root / "fd":
            raise PermissionError("restricted fd table")
        return original_iterdir(path)

    def restricted_read_text(path, *args, **kwargs):
        if path == root / "io":
            raise PermissionError("restricted io counters")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", restricted_iterdir)
    monkeypatch.setattr(Path, "read_text", restricted_read_text)

    sample = runtime_resource_profile.collect_samples(
        pid=601,
        sample_count=1,
        sample_interval_seconds=0,
        proc_root=proc_root,
        clock_ticks_per_second=100,
    )[0]

    assert sample["rss_bytes"] == 102400
    assert sample["threads"] == 4
    assert sample["fd_count"] is None
    assert sample["disk_read_bytes"] is None
    assert sample["disk_write_bytes"] is None
