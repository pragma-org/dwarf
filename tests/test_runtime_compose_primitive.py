import json
import subprocess

from profile_manager import primitives


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.run_id = "20261009T040000Z-proof"
        self.events = []

    def log(self, **event):
        self.events.append(event)


def test_controller_scenario_runtime_uses_matching_host_visible_root(monkeypatch, tmp_path):
    controller_state = tmp_path / "controller-state"
    host_state = "/srv/dwarf/controller-state"
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(controller_state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("DWARF_CONTROLLER_HOST_STATE_DIR", host_state)
    monkeypatch.setattr(
        primitives.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"", b""),
    )

    substrate = {
        "compose_mode": "docker",
        "docker_host_runtime_root": None,
        "nodes": [{"id": "node1"}],
    }
    primitive = primitives.RuntimeComposeSubstrate(
        params={"output_dir": "outputs/substrate-compose"}
    )
    primitive.bound_substrate = substrate
    primitive.bound_state = {}
    handle = _Handle(tmp_path / "run")

    primitive.run(handle, None)

    config = json.loads(
        (handle.run_dir / "outputs/substrate-compose/compose-substrate-config.json").read_text()
    )
    project = "dwarf-substrate-20261009T040000Z-proof"
    relative_runtime = f"controller/scenario-runtimes/{project}"
    assert config["runtime_root"] == str(controller_state / relative_runtime)
    assert config["substrate"]["docker_host_runtime_root"] == f"{host_state}/{relative_runtime}"
    assert substrate["docker_host_runtime_root"] is None
