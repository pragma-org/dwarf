import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "dwarf" / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_adapter_resolves_namespaced_compose_services(monkeypatch):
    adapter = load_script("adapt_cardano_amaru_runtime.py")
    running = {
        "dwarf-control-p1",
        "dwarf-control-p2",
        "dwarf-control-p3",
        "dwarf-control-relay1",
        "dwarf-control-relay2",
        "dwarf-control-amaru-relay-1",
        "dwarf-control-amaru-relay-2",
        "dwarf-control-amaru-consumer",
    }
    services = {name.removeprefix("dwarf-control-"): name for name in running}
    monkeypatch.setattr(adapter, "_running_containers", lambda: running)
    monkeypatch.setattr(adapter, "_compose_service_containers", lambda: services)

    runtime = adapter.build_runtime(
        network_magic=42,
        cardano_node_version="10.7.1",
        amaru_version="10.11.0",
        runtime_root="/tmp/control",
        require_all=True,
    )

    by_id = {node["id"]: node for node in runtime["nodes"]}
    assert by_id["p1"]["container_name"] == "dwarf-control-p1"
    assert by_id["amaru-consumer"]["container_name"] == "dwarf-control-amaru-consumer"
    assert by_id["amaru-relay-1"]["container_name"] == "dwarf-control-amaru-relay-1"


def test_runtime_adapter_prefers_exact_legacy_name(monkeypatch):
    adapter = load_script("adapt_cardano_amaru_runtime.py")
    monkeypatch.setattr(adapter, "_running_containers", lambda: {"p1", "dwarf-control-p1"})
    monkeypatch.setattr(
        adapter,
        "_compose_service_containers",
        lambda: {"p1": "dwarf-control-p1"},
    )
    assert adapter._resolve_container("p1") == "p1"


def test_tracer_capture_resolves_namespaced_compose_service(monkeypatch):
    tracer = load_script("runtime_tracer_capture.py")

    def fake_run(args, timeout=60):
        class Result:
            returncode = 0
            stderr = ""
            stdout = "dwarf-control-tracer\n"

        assert args[:3] == ["docker", "ps", "--filter"]
        assert args[3] == "label=com.docker.compose.service=tracer"
        return Result()

    monkeypatch.setattr(tracer, "_run", fake_run)
    assert tracer._resolve_container("tracer") == "dwarf-control-tracer"


def test_tracer_prometheus_uses_cardano_tracer_root_endpoint_and_fails_http_errors(monkeypatch):
    tracer = load_script("runtime_tracer_capture.py")
    monkeypatch.setattr(tracer, "_tracer_ip", lambda _container: "10.0.0.7")
    calls = []

    def fake_run(args, timeout=60):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = "cardano_node_metrics_epoch_int 4\n"

        return Result()

    monkeypatch.setattr(tracer, "_run", fake_run)
    body, error = tracer._prometheus("dwarf-control-tracer", 4000)

    assert error is None
    assert body.startswith("cardano_node_metrics_")
    assert "-fS" in calls[0]
    assert calls[0][-1] == "http://10.0.0.7:4000/"


def test_tracer_prometheus_follows_cardano_tracer_per_node_index(monkeypatch):
    tracer = load_script("runtime_tracer_capture.py")
    monkeypatch.setattr(tracer, "_tracer_ip", lambda _container: "10.0.0.7")

    def fake_run(args, timeout=60):
        url = args[-1]

        class Result:
            returncode = 0
            stdout = ""

        result = Result()
        if url.endswith(":4000/"):
            result.stdout = (
                '<html><body><a href="/p1example-3001">p1</a>'
                '<a href="/amaru-consumerexample-3001">consumer</a></body></html>'
            )
        elif url.endswith("/p1example-3001"):
            result.stdout = "# TYPE cardano_node_metrics_epoch_int gauge\ncardano_node_metrics_epoch_int 4\n"
        elif url.endswith("/amaru-consumerexample-3001"):
            result.stdout = "# TYPE cardano_node_metrics_blockNum_int gauge\ncardano_node_metrics_blockNum_int 320\n"
        else:
            result.returncode = 22
        return result

    monkeypatch.setattr(tracer, "_run", fake_run)
    body, error = tracer._prometheus("dwarf-control-tracer", 4000)

    assert error is None
    assert "<html>" not in body
    assert "endpoint /p1example-3001" in body
    assert "cardano_node_metrics_epoch_int 4" in body
    assert "endpoint /amaru-consumerexample-3001" in body
    assert "cardano_node_metrics_blockNum_int 320" in body
