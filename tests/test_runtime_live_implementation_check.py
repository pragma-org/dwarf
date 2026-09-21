import json

from scripts import runtime_live_implementation_check as baseline


def _retained_runtime(tmp_path):
    root = tmp_path / "profile-q-amaru-measurement-patched"
    root.mkdir()
    (root / "runtime.json").write_text(
        json.dumps(
            {
                "compose_project": "dwarf-profile-q-amaru-measurement-patched",
                "actual_topology": {
                    "amaru_services": ["amaru-relay-1", "amaru-relay-2"],
                    "cardano_services": ["p1", "p2", "p3"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_retained_amaru_runtime_resolves_live_relay_not_synthetic_amaru1(tmp_path):
    root = _retained_runtime(tmp_path)

    descriptor = baseline._runtime_json_descriptor(root, "amaru")

    assert descriptor["mode"] == "container"
    assert descriptor["compose_project"] == "dwarf-profile-q-amaru-measurement-patched"
    assert descriptor["service"] == "amaru-relay-1"
    assert descriptor["data_dir_inside_container"] == "/srv/amaru"
    assert descriptor["listener_port"] == 3000


def test_live_baseline_checks_retained_amaru_container_and_internal_data(tmp_path, monkeypatch):
    root = _retained_runtime(tmp_path)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"target": {"implementation": "amaru"}}) + "\n")
    observed = {}

    monkeypatch.setattr(
        baseline,
        "_compose_container",
        lambda project, service: observed.update(project=project, service=service) or "relay-container",
    )
    monkeypatch.setattr(baseline, "_container_running", lambda name: name == "relay-container")
    monkeypatch.setattr(baseline, "_container_ip", lambda _name: "172.20.0.8")
    monkeypatch.setattr(baseline, "_listener_ok", lambda host, port: (host, port) == ("172.20.0.8", 3000))
    monkeypatch.setattr(
        baseline,
        "_docker_data_dir_bytes",
        lambda name, path: 4096 if (name, path) == ("relay-container", "/srv/amaru") else 0,
    )
    monkeypatch.setattr(baseline, "_docker_log_bytes", lambda _name: 2048)
    monkeypatch.setattr(baseline, "_emit_container_metrics", lambda **_kwargs: None)
    monkeypatch.setattr(baseline, "emit_runtime_metric", lambda *_args, **_kwargs: None)

    assert baseline.run_live_baseline(runtime_root=root, scenario_path=scenario) == 0
    assert observed == {
        "project": "dwarf-profile-q-amaru-measurement-patched",
        "service": "amaru-relay-1",
    }
