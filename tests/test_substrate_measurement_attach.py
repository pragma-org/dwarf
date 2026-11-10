"""Measurement taps on self-composed devnets (runtime_compose_substrate).

Taps bind to the node the substrate actually composed and proved, start once the
substrate is up and finalize before ``runtime_teardown_substrate`` removes it.
"""
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from profile_manager import measurement_execution, scenario as scenario_module, telemetry
from profile_manager.measurement_execution import (
    MeasurementExecutionError,
    prepare_substrate_measurements,
)
from profile_manager.scenario import scenario_from_body
from test_measurement_runtime import RecordingCollector, _resolution, _resolved


CARDANO_VERSION = "11.1.2"
CARDANO_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
CARDANO_DIGEST = "sha256:" + "6" * 64
AMARU_VERSION = "10.11.20260918"
AMARU_REVISION = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
AMARU_DIGEST = "sha256:" + "2" * 64
IMAGE_ID = "sha256:" + "c" * 64


def _substrate(*nodes):
    return {
        "network": "testnet_42",
        "nodes": [
            {
                "id": node_id,
                "impl": impl,
                "version": "resolved-plan",
                "role": "honest",
                "identity_binding": "resolved-plan",
            }
            for node_id, impl in nodes
        ],
        "topology": {"edges": []},
        "compose_mode": "docker",
    }


def _scenario(implementation="cardano-node", version=CARDANO_VERSION, *, nodes=None, **updates):
    document = {
        "spec_version": "v1",
        "id": "substrate-measured",
        "title": "Substrate measured",
        "target": {"implementation": implementation, "version": version},
        "runtime": "devnet",
        "substrate": _substrate(*(nodes or [("node1", "cardano-node"), ("node2", "cardano-node")])),
        "setup": [
            {"primitive": "runtime_compose_substrate", "output_dir": "outputs/substrate-compose"}
        ],
        "load": [{"primitive": "load_shell_command", "command": "true"}],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [
            {"primitive": "runtime_teardown_substrate", "output_dir": "outputs/substrate-teardown"}
        ],
    }
    document.update(updates)
    return scenario_from_body((json.dumps(document) + "\n").encode())


def _node(root, node_id, impl, *, supporting=False, satisfied=True):
    version, revision, digest = (
        (CARDANO_VERSION, CARDANO_REVISION, CARDANO_DIGEST)
        if impl == "cardano-node"
        else (AMARU_VERSION, AMARU_REVISION, AMARU_DIGEST)
    )
    image = f"ghcr.io/example/{impl}:{version}@{digest}"
    return {
        "id": node_id,
        "name": node_id,
        "impl": impl,
        "version": version,
        "source_revision": revision,
        "supporting": supporting,
        "image": image,
        "image_ref": image,
        "image_id": IMAGE_ID,
        "image_digest": digest,
        "container_name": f"dwarf-substrate-run-{node_id}-1",
        "container_socket_path": f"/env/socket/{node_id}/sock",
        "log_path": str(Path(root) / "logs" / node_id / "stdout.log"),
        "artifact_identity": {
            "status": "running-image-verified" if satisfied else "running-image-mismatch",
            "satisfied": satisfied,
            "expected_image_id": IMAGE_ID,
            "container_image_id": IMAGE_ID if satisfied else "sha256:" + "9" * 64,
            "image_digest": digest,
        },
        "version_identity": {"satisfied": True, "reported_version": version},
    }


def _runtime(tmp_path, *nodes, **updates):
    root = tmp_path / "runtime-root"
    body = {
        "compose_mode": "docker",
        "profile_id": "dwarf-substrate-run",
        "compose_project": "dwarf-substrate-run",
        "runtime_root": str(root),
        "network_magic": 42,
        "nodes": [_node(root, *node[:2], **(node[2] if len(node) > 2 else {})) for node in nodes],
    }
    body.update(updates)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path, body


def test_cardano_composed_target_binds_proven_identity_and_its_node_log(tmp_path):
    path, runtime = _runtime(tmp_path, ("node1", "cardano-node"), ("node2", "cardano-node"))

    prepared = prepare_substrate_measurements(_scenario(), runtime_metadata_path=path)

    identity = prepared.resolution["target_identity"]
    assert identity["implementation"] == "cardano-node"
    assert identity["version"] == CARDANO_VERSION
    assert identity["source_revision"] == CARDANO_REVISION
    assert identity["image_digest"] == CARDANO_DIGEST
    assert identity["mode"] == "stock"
    assert prepared.resolution["profile"]["id"] == "cardano-security-default"
    assert prepared.resolution["resolved"]
    assert prepared.target_node == "node1"
    assert prepared.trace_paths == (Path(runtime["nodes"][0]["log_path"]),)
    factories = prepared.build_factories(tmp_path / "run")
    resolved_ids = {entry["id"] for entry in prepared.resolution["resolved"]}
    assert factories and set(factories) <= resolved_ids


def test_unproven_composed_identity_gets_no_taps(tmp_path):
    path, _ = _runtime(tmp_path, ("node1", "cardano-node", {"satisfied": False}))

    with pytest.raises(MeasurementExecutionError, match="not proven"):
        prepare_substrate_measurements(_scenario(), runtime_metadata_path=path)


@pytest.mark.parametrize(
    "updates, reason",
    [
        ({"multi_host": True}, "multi-host"),
        ({"compose_mode": "host"}, "Docker-composed"),
    ],
)
def test_nodes_the_controller_cannot_observe_locally_get_no_taps(tmp_path, updates, reason):
    """Remote or host-process nodes have no local log file or container to read;
    attaching would yield empty-trace zeros instead of evidence."""
    path, _ = _runtime(tmp_path, ("node1", "cardano-node"), **updates)

    with pytest.raises(MeasurementExecutionError, match=reason):
        prepare_substrate_measurements(_scenario(), runtime_metadata_path=path)


def test_mixed_substrate_measures_the_scenario_target_implementation_only(tmp_path):
    path, _ = _runtime(
        tmp_path,
        ("node1", "cardano-node"),
        ("amaru-producer", "amaru", {"supporting": True}),
        ("amaru1", "amaru"),
    )
    scen = _scenario(
        "amaru",
        AMARU_VERSION,
        nodes=[("node1", "cardano-node"), ("amaru1", "amaru")],
    )

    prepared = prepare_substrate_measurements(scen, runtime_metadata_path=path)

    assert prepared.target_node == "amaru1"
    assert prepared.implementation == "amaru"
    assert prepared.resolution["target_identity"]["image_digest"] == AMARU_DIGEST


def test_supporting_only_target_implementation_is_refused(tmp_path):
    path, _ = _runtime(
        tmp_path, ("node1", "cardano-node"), ("amaru1", "amaru", {"supporting": True})
    )
    scen = _scenario("amaru", AMARU_VERSION, nodes=[("node1", "cardano-node"), ("amaru1", "amaru")])

    with pytest.raises(MeasurementExecutionError, match="no composed amaru target"):
        prepare_substrate_measurements(scen, runtime_metadata_path=path)


def test_amaru_text_logs_drop_json_trace_taps_and_bind_no_trace_source(tmp_path):
    path, _ = _runtime(tmp_path, ("amaru1", "amaru"))
    scen = _scenario("amaru", AMARU_VERSION, nodes=[("amaru1", "amaru")])

    prepared = prepare_substrate_measurements(scen, runtime_metadata_path=path)

    resolved = {entry["id"] for entry in prepared.resolution["resolved"]}
    skipped = {entry["id"] for entry in prepared.resolution["skipped"]}
    assert "amaru-stock-mempool" in skipped
    assert "amaru-stock-mempool" not in resolved
    assert prepared.trace_paths == ()


def test_amaru_json_traces_bind_the_node_log_as_trace_source(tmp_path):
    path, runtime = _runtime(tmp_path, ("amaru1", "amaru"), amaru_json_traces=True)
    scen = _scenario("amaru", AMARU_VERSION, nodes=[("amaru1", "amaru")])

    prepared = prepare_substrate_measurements(scen, runtime_metadata_path=path)

    assert "amaru-stock-mempool" in {entry["id"] for entry in prepared.resolution["resolved"]}
    assert prepared.trace_paths == (Path(runtime["nodes"][0]["log_path"]),)


def test_frozen_launch_identity_must_match_the_composed_node(tmp_path):
    path, _ = _runtime(tmp_path, ("node1", "cardano-node"))
    frozen = {
        "profile": {"id": "cardano-security-default", "source": "explicit-profile"},
        "target_identity": {
            "implementation": "cardano-node",
            "version": CARDANO_VERSION,
            "source_revision": CARDANO_REVISION,
            "mode": "stock",
            "image_digest": "sha256:" + "7" * 64,
        },
    }

    with pytest.raises(MeasurementExecutionError, match="frozen launch"):
        prepare_substrate_measurements(
            _scenario(), runtime_metadata_path=path, expected_resolution=frozen
        )


def test_exact_scenario_version_must_match_the_composed_node(tmp_path):
    path, _ = _runtime(tmp_path, ("node1", "cardano-node"))

    with pytest.raises(MeasurementExecutionError, match="does not match scenario"):
        prepare_substrate_measurements(
            _scenario(version="10.7.1"), runtime_metadata_path=path
        )


def test_per_run_overrides_frozen_into_the_scenario_still_apply(tmp_path):
    path, _ = _runtime(tmp_path, ("node1", "cardano-node"))
    scen = _scenario(
        measurement_profile="none",
        measurements=[{"id": "cardano-stock-resources", "enabled": True}],
    )

    prepared = prepare_substrate_measurements(scen, runtime_metadata_path=path)

    assert [entry["id"] for entry in prepared.resolution["resolved"]] == [
        "cardano-stock-resources"
    ]


# --- intent ---------------------------------------------------------------


def test_self_composed_devnet_gets_implicit_default_intent():
    assert scenario_module.substrate_measurement_intent(_scenario(), env={}) == "implicit"


def test_profile_and_library_scenarios_are_not_substrate_attachments():
    profiled = _scenario(profile="profile-a", substrate=None, setup=[], teardown=[])
    library = _scenario(runtime="library", substrate=None, setup=[], teardown=[])

    assert scenario_module.substrate_measurement_intent(profiled, env={}) is None
    assert scenario_module.substrate_measurement_intent(library, env={}) is None


def test_explicit_none_and_kill_switch_opt_out_of_substrate_taps():
    assert (
        scenario_module.substrate_measurement_intent(
            _scenario(measurement_profile="none"), env={}
        )
        is None
    )
    assert (
        scenario_module.substrate_measurement_intent(
            _scenario(), env={"DWARF_MEASUREMENTS": "off"}
        )
        is None
    )


def test_frozen_launch_intent_follows_the_launch_resolution_sources():
    frozen_scenario = _scenario(
        measurement_profile="none",
        measurements=[{"id": "cardano-stock-resources", "enabled": True}],
    )
    explicit_launch = {
        "profile": {"id": "cardano-security-default", "source": "implicit-default"},
        "requested": [
            {"id": "cardano-stock-resources", "enabled": True, "source": "launch-override"}
        ],
    }
    no_measurements = {
        "profile": {"id": "none", "source": "explicit"},
        "requested": [],
        "resolved": [],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
        "target_identity": {},
    }

    assert (
        scenario_module.substrate_measurement_intent(
            frozen_scenario, expected_resolution=explicit_launch, env={}
        )
        == "explicit"
    )
    assert (
        scenario_module.substrate_measurement_intent(
            _scenario(measurement_profile="none"), expected_resolution=no_measurements, env={}
        )
        is None
    )
    # A plan that did not resolve taps (implicit default) leaves the runner to attach.
    assert (
        scenario_module.substrate_measurement_intent(
            _scenario(), expected_resolution={"profile": None, "resolved": []}, env={}
        )
        == "implicit"
    )


# --- run path -------------------------------------------------------------


class _NoopObserver:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


def _write_scenario(tmp_path, scen_body):
    path = tmp_path / "scenario.yaml"
    path.write_text(json.dumps(scen_body), encoding="utf-8")
    return path


def _scenario_body(**updates):
    body = json.loads(_scenario().raw_bytes)
    body.update(updates)
    return body


def _install_fake_primitives(monkeypatch, events, tmp_path):
    runtime_path = tmp_path / "composed-runtime.json"

    class Compose:
        bound_state = None
        bound_substrate = None

        def run(self, handle, rng):
            events.append(("primitive", "compose"))
            runtime_path.write_text("{}", encoding="utf-8")
            self.bound_state["substrate_runtime_metadata_path"] = str(runtime_path)

    class Teardown:
        bound_state = None

        def run(self, handle, rng):
            events.append(("primitive", "teardown"))

    class Load:
        def run(self, handle, rng):
            events.append(("primitive", "load"))

    classes = {
        "runtime_compose_substrate": Compose,
        "runtime_teardown_substrate": Teardown,
        "load_shell_command": Load,
    }
    monkeypatch.setattr(
        "profile_manager.primitives.instantiate",
        lambda _registry, *, name, **_kwargs: classes[name](),
    )
    monkeypatch.setattr(telemetry, "ObserverCollector", _NoopObserver)
    monkeypatch.delenv("ADA2_DWARF_LAUNCH_ID", raising=False)
    monkeypatch.delenv("DWARF_MEASUREMENTS", raising=False)
    return runtime_path


def test_self_compose_run_starts_taps_after_compose_and_finalizes_before_teardown(
    monkeypatch, tmp_path
):
    events = []
    runtime_path = _install_fake_primitives(monkeypatch, events, tmp_path)
    observed = {}

    def prepare(scen, *, runtime_metadata_path, expected_resolution=None):
        observed["runtime_metadata_path"] = Path(runtime_metadata_path)
        resolution = _resolution(_resolved("tap-a"))
        return SimpleNamespace(
            resolution=resolution,
            target_node="node1",
            build_factories=lambda _run_dir: {
                "tap-a": lambda _entry: RecordingCollector(
                    events,
                    result={"measurements": {"signal": {"status": "available", "value": 1}}},
                )
            },
        )

    monkeypatch.setattr(measurement_execution, "prepare_substrate_measurements", prepare)

    handle = scenario_module.run_scenario(
        _write_scenario(tmp_path, _scenario_body()),
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    assert observed["runtime_metadata_path"] == runtime_path
    order = [event for event in events if event[0] in {"primitive", "start", "finalize"}]
    assert order == [
        ("primitive", "compose"),
        ("start", "tap-a"),
        ("primitive", "load"),
        ("finalize", "tap-a"),
        ("primitive", "teardown"),
    ]
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "pass"
    assert manifest["measurements"]["collector_states"] == {"tap-a": "finalized"}
    for name in ("measurements/tap-a/result.json", "measurements/summary.json", "measurements/report.md"):
        assert (handle.run_dir / name).is_file(), name
    markers = [
        json.loads(line)
        for line in (handle.run_dir / "measurements/windows.ndjson").read_text().splitlines()
    ]
    assert [(item["phase_id"], item["state"]) for item in markers] == [
        ("run", "start"),
        ("default", "start"),
        ("run", "end"),
    ]
    log = [json.loads(line) for line in (handle.run_dir / "log.ndjson").read_text().splitlines()]
    attached = [entry for entry in log if entry["event"] == "measurement_substrate_attached"]
    assert attached and attached[0]["payload"]["target_node"] == "node1"
    from profile_manager import forensic

    assert forensic.verify(handle.run_id, runs_dir=tmp_path / "runs", state_dir=tmp_path / "state").ok
    bundle = forensic.export_bundle(handle.run_id, runs_dir=tmp_path / "runs", bundles_dir=tmp_path / "bundles")
    with tarfile.open(bundle, "r:gz") as archive:
        assert f"{handle.run_id}/measurements/tap-a/result.json" in archive.getnames()


def test_implicit_substrate_attach_failure_is_a_recorded_skip_not_a_failed_run(
    monkeypatch, tmp_path
):
    events = []
    _install_fake_primitives(monkeypatch, events, tmp_path)

    def refuse(scen, **_kwargs):
        raise MeasurementExecutionError("composed cardano-node identity is not proven")

    monkeypatch.setattr(measurement_execution, "prepare_substrate_measurements", refuse)

    handle = scenario_module.run_scenario(
        _write_scenario(tmp_path, _scenario_body()),
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "pass"
    assert "measurements" not in manifest
    log = [json.loads(line) for line in (handle.run_dir / "log.ndjson").read_text().splitlines()]
    skipped = [entry for entry in log if entry["event"] == "measurement_auto_attach_skipped"]
    assert skipped and "not proven" in skipped[0]["payload"]["reason"]
    assert [event for event in events if event[0] == "primitive"] == [
        ("primitive", "compose"),
        ("primitive", "load"),
        ("primitive", "teardown"),
    ]


def test_explicit_substrate_attach_failure_fails_loud_and_still_tears_down(
    monkeypatch, tmp_path
):
    events = []
    _install_fake_primitives(monkeypatch, events, tmp_path)

    def refuse(scen, **_kwargs):
        raise MeasurementExecutionError("composed cardano-node identity is not proven")

    monkeypatch.setattr(measurement_execution, "prepare_substrate_measurements", refuse)

    handle = scenario_module.run_scenario(
        _write_scenario(
            tmp_path,
            _scenario_body(measurements=[{"id": "cardano-stock-resources", "enabled": True}]),
        ),
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "error"
    assert ("primitive", "teardown") in events
    assert ("primitive", "load") not in events


def test_library_run_reports_measurements_not_applicable(monkeypatch, tmp_path):
    events = []
    _install_fake_primitives(monkeypatch, events, tmp_path)
    body = _scenario_body(runtime="library", setup=[], teardown=[])
    body.pop("substrate")

    handle = scenario_module.run_scenario(
        _write_scenario(tmp_path, body),
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert "measurements" not in manifest
    log = [json.loads(line) for line in (handle.run_dir / "log.ndjson").read_text().splitlines()]
    not_applicable = [entry for entry in log if entry["event"] == "measurement_not_applicable"]
    assert not_applicable
    assert not_applicable[0]["payload"]["state"] == "not_applicable"


# --- trace sources on composed nodes ------------------------------------------


def test_composed_amaru_json_traces_are_an_honoured_substrate_opt_in():
    """substrate.amaru_json_traces was accepted by the normalizer but never reached
    the composed Amaru container, so its JSON-trace taps had no source."""
    from scripts.runtime_compose_substrate import _docker_compose_body

    node = {
        "id": "amaru1",
        "impl": "amaru",
        "version": AMARU_VERSION,
        "role": "honest",
        "listen_address": "127.0.0.1:5001",
        "chain_dir": "/tmp/chain",
        "ledger_dir": "/tmp/ledger",
        "container_peer_addresses": ["node1:3001"],
        "image": "sha256:" + "d" * 64,
    }
    traced = _docker_compose_body(
        compose_project="json-trace", nodes=[node], network_name="testnet_42",
        amaru_json_traces=True,
    )["services"]["amaru1"]["environment"]
    ordinary = _docker_compose_body(
        compose_project="text-trace", nodes=[node], network_name="testnet_42",
    )["services"]["amaru1"]["environment"]

    assert traced["AMARU_WITH_JSON_TRACES"] == "true"
    assert "AMARU_WITH_JSON_TRACES" not in ordinary


# --- plan time --------------------------------------------------------------


def _registry_preview(*_args, **_kwargs):
    def release(implementation, version, revision, digest):
        return {
            "implementation": implementation,
            "version": version,
            "source_revision": revision,
            "artifacts": [{
                "kind": "oci",
                "availability": "available",
                "reference": f"ghcr.io/example/{implementation}:{version}@{digest}",
                "digest": digest,
                "platforms": ["linux/amd64"],
                "public": True,
            }],
        }

    return {
        "policy": "exact",
        "policy_source": "explicit",
        "scope": "mixed",
        "status": "confirmed",
        "reason": "test",
        "catalog_revision": "c" * 64,
        "operator_overlay_revision": None,
        "operator_registration_id": None,
        "pair": None,
        "requires_acknowledgement": False,
        "resolved": {
            "amaru": release("amaru", AMARU_VERSION, AMARU_REVISION, AMARU_DIGEST),
            "cardano-node": release("cardano-node", CARDANO_VERSION, CARDANO_REVISION, CARDANO_DIGEST),
        },
        "supporting": {},
    }


def _plan(monkeypatch, scenario_id, **request):
    import profile_manager.run_plan as run_plan_module
    from profile_manager.run_plan import RunPlanRequest, resolve_run_plan

    monkeypatch.setattr(run_plan_module, "build_deployment_version_preview", _registry_preview)
    return resolve_run_plan(
        RunPlanRequest(
            scenario_id=scenario_id,
            version_policy="exact",
            cardano_version=CARDANO_VERSION,
            amaru_version=AMARU_VERSION,
            **request,
        )
    )


MIXED_CARDANO_TARGET = "runtime-substrate-mempool-failure-containment-mixed-amaru"
MIXED_AMARU_TARGET = "runtime-substrate-mux-ingress-overrun-mixed-amaru"


def test_plan_leaves_implicit_substrate_taps_to_the_runner(monkeypatch):
    plan = _plan(monkeypatch, MIXED_CARDANO_TARGET)

    assert plan["measurements"]["profile"] is None
    assert plan["measurements"]["resolved"] == []


def test_plan_leaves_a_request_named_default_profile_to_the_runner(monkeypatch):
    """Legacy campaign launches name the default profile; on a composed Amaru its
    JSON-trace taps cannot resolve, which must not block the launch."""
    plan = _plan(monkeypatch, MIXED_AMARU_TARGET, measurement_profile="amaru-security-default")

    assert plan["measurements"]["profile"] is None


def test_plan_freezes_per_run_overrides_for_a_self_composed_devnet(monkeypatch, tmp_path):
    from profile_manager.launch_store import create_launch

    plan = _plan(
        monkeypatch,
        MIXED_CARDANO_TARGET,
        measurement_overrides=[{"id": "cardano-stock-resources", "enabled": False}],
    )

    measurements = plan["measurements"]
    assert measurements["target_identity"]["image_digest"] == CARDANO_DIGEST
    assert measurements["target_identity"]["source_revision"] == CARDANO_REVISION
    resolved = {entry["id"] for entry in measurements["resolved"]}
    assert resolved and "cardano-stock-resources" not in resolved
    assert {entry["id"] for entry in measurements["disabled"]} == {"cardano-stock-resources"}
    launch = create_launch(plan, root=tmp_path / "launches")
    frozen = json.loads(launch["path"].joinpath("scenario.yaml").read_text())
    assert frozen["measurement_profile"] == "none"
    assert {item["id"] for item in frozen["measurements"] if item["enabled"]} == resolved


def test_plan_refuses_an_explicit_trace_tap_the_composed_amaru_cannot_emit(monkeypatch):
    from profile_manager.run_plan import RunPlanError

    with pytest.raises(RunPlanError, match="amaru-stock-mempool"):
        _plan(
            monkeypatch,
            MIXED_AMARU_TARGET,
            measurement_overrides=[{"id": "amaru-stock-mempool", "enabled": True}],
        )
