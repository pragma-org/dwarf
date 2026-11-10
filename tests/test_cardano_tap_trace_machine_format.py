"""F161: tapped composed cardano-node writes machine-format (JSON) stdout.

Taps campaign 20261110T043257Z (mixed cardano-node + Amaru): the namespaces were enabled but the
env's configuration sent stdout as "Stdout HumanFormatColoured", so the JSON tap loader read no
record and every trace tap reported "namespace not enabled".
"""
from __future__ import annotations

import json

from scripts import runtime_compose_substrate as rcs


def _config(tmp_path, backends):
    path = tmp_path / "configuration.yaml"
    path.write_text(json.dumps({"TraceOptions": {"": {"severity": "Notice", "backends": backends}}}))
    return path


def test_tap_traces_switch_human_stdout_to_machine_format(tmp_path):
    path = _config(tmp_path, ["Stdout HumanFormatColoured", "EKGBackend", "Forwarder"])
    out = rcs._enable_cardano_tap_trace_namespaces(path)
    root = json.loads(path.read_text())["TraceOptions"][""]
    assert root["backends"] == ["Stdout MachineFormat", "EKGBackend", "Forwarder"]
    assert out["stdout_format"] == {"before": "Stdout HumanFormatColoured", "after": "Stdout MachineFormat"}


def test_machine_format_stdout_is_left_as_is(tmp_path):
    path = _config(tmp_path, ["Stdout MachineFormat"])
    out = rcs._enable_cardano_tap_trace_namespaces(path)
    assert json.loads(path.read_text())["TraceOptions"][""]["backends"] == ["Stdout MachineFormat"]
    assert out["stdout_format"] == {"before": "Stdout MachineFormat", "after": "Stdout MachineFormat"}


def test_measurement_traces_keep_their_backends(tmp_path):
    # The opt-in measurement profile (and the Antithesis config program built from it) is unchanged.
    path = _config(tmp_path, ["Stdout HumanFormatUncoloured"])
    rcs._enable_cardano_measurement_traces(path)
    assert json.loads(path.read_text())["TraceOptions"][""]["backends"] == ["Stdout HumanFormatUncoloured"]


def test_a_config_without_stdout_gains_machine_stdout(tmp_path):
    path = _config(tmp_path, ["Forwarder"])
    rcs._enable_cardano_tap_trace_namespaces(path)
    assert json.loads(path.read_text())["TraceOptions"][""]["backends"] == ["Forwarder", "Stdout MachineFormat"]


def test_trace_settings_are_applied_again_after_the_amaru_bootstrap_rewrite():
    """Taps rerun 20261110T110311Z: the Amaru bootstrap synthesis rewrote configuration.yaml after the
    tap traces were enabled, so the node ran with HumanFormatColoured stdout and no tap namespaces."""
    import inspect
    source = inspect.getsource(rcs._compose_substrate_docker)
    bootstrap = source.index("_synthesize_amaru_bootstrap_for_custom_testnet(")
    reapply = source.index("_apply_cardano_trace_settings(", bootstrap)
    assert reapply > bootstrap
    assert source.index("_pin_cardano_accepted_connections_limit(") > reapply


def test_apply_trace_settings_restores_tap_namespaces_after_a_rewrite(tmp_path):
    path = _config(tmp_path, ["Stdout HumanFormatColoured"])
    plan = {"cardano_tap_traces": True}
    rcs._apply_cardano_trace_settings(path, plan)
    # a later step writes the image's pinned configuration back
    path.write_text(json.dumps({"TraceOptions": {"": {"severity": "Notice", "backends": ["Stdout HumanFormatColoured"]}}}))
    measurement, observability = rcs._apply_cardano_trace_settings(path, plan)
    options = json.loads(path.read_text())["TraceOptions"]
    assert options[""]["backends"] == ["Stdout MachineFormat"]
    assert set(rcs.CARDANO_MEASUREMENT_TRACE_NAMESPACES) <= set(options)
    assert measurement["enabled"] is True and observability["enabled"] is False


def test_apply_trace_settings_leaves_untapped_configs_alone(tmp_path):
    path = _config(tmp_path, ["Stdout HumanFormatColoured"])
    before = path.read_text()
    measurement, observability = rcs._apply_cardano_trace_settings(path, {})
    assert path.read_text() == before
    assert measurement == {"enabled": False, "namespaces": []}
