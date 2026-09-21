"""Auto-attachment of implicit-default measurement profiles to devnet scenarios."""
import json

import pytest

from profile_manager import scenario as scenario_module
from profile_manager.scenario import scenario_from_body


def _scenario(**updates):
    document = {
        "spec_version": "v1",
        "id": "amaru-devnet-demo",
        "title": "Amaru devnet demo",
        "target": {"implementation": "amaru", "version": "10.11.20260912"},
        "runtime": "devnet",
        "profile": "amaru-devnet",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    document.update(updates)
    return scenario_from_body((json.dumps(document) + "\n").encode())


def test_compatible_devnet_scenario_auto_attaches_without_declaring_a_profile():
    assert scenario_module.measurement_auto_attach_target(_scenario(), env={}) is True


def test_cardano_node_devnet_scenario_also_auto_attaches():
    scenario = _scenario(
        target={"implementation": "cardano-node", "version": "11.1.2"}
    )

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is True


def test_explicit_none_profile_still_opts_out_of_auto_attachment():
    scenario = _scenario(measurement_profile="none")

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is False


def test_explicitly_named_profile_is_not_an_auto_attachment():
    scenario = _scenario(measurement_profile="amaru-security-default")

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is False


def test_scenario_with_explicit_measurement_overrides_is_not_an_auto_attachment():
    scenario = _scenario(measurements=[{"id": "amaru-stock-mempool", "enabled": True}])

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is False


def test_library_runtime_scenario_does_not_auto_attach():
    scenario = _scenario(runtime="library", profile=None)

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is False


def test_attached_devnet_scenario_without_a_deployed_profile_does_not_auto_attach():
    """An attached topology emits no profile runtime.json, so there is nothing to bind."""
    scenario = _scenario(profile=None, attach={"topology": "cardano-amaru-upstream"})

    assert scenario_module.measurement_auto_attach_target(scenario, env={}) is False


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "OFF"])
def test_kill_switch_disables_auto_attachment(value):
    scenario = _scenario()

    assert (
        scenario_module.measurement_auto_attach_target(
            scenario, env={"DWARF_MEASUREMENTS": value}
        )
        is False
    )


def test_kill_switch_does_not_disable_an_explicitly_requested_profile():
    """The kill switch governs auto-attachment only; explicit intent still runs."""
    scenario = _scenario(measurement_profile="amaru-security-default")

    assert (
        scenario_module.measurement_explicitly_requested(scenario) is True
    )


def test_auto_attachment_degrades_to_a_skip_reason_when_preparation_fails(monkeypatch):
    scenario = _scenario()

    def explode(_scen):
        from profile_manager.measurement_execution import MeasurementExecutionError

        raise MeasurementExecutionError("fresh deployed runtime is unavailable")

    monkeypatch.setattr(
        "profile_manager.measurement_execution.prepare_scenario_measurements", explode
    )

    prepared, skip_reason = scenario_module.prepare_auto_measurements(scenario)

    assert prepared is None
    assert "fresh deployed runtime is unavailable" in skip_reason


def test_auto_attachment_degrades_when_default_profile_resolution_fails(monkeypatch):
    scenario = _scenario()

    def explode(_scen):
        from profile_manager.measurement_resolution import MeasurementResolutionError

        raise MeasurementResolutionError(
            "measurement profile amaru-security-default is unavailable",
            resolution={"incompatible": []},
        )

    monkeypatch.setattr(
        "profile_manager.measurement_execution.prepare_scenario_measurements", explode
    )

    prepared, skip_reason = scenario_module.prepare_auto_measurements(scenario)

    assert prepared is None
    assert "amaru-security-default is unavailable" in skip_reason


def test_explicit_preparation_failure_still_raises(monkeypatch):
    scenario = _scenario(measurement_profile="amaru-security-default")

    def explode(_scen):
        from profile_manager.measurement_execution import MeasurementExecutionError

        raise MeasurementExecutionError("fresh deployed runtime is unavailable")

    monkeypatch.setattr(
        "profile_manager.measurement_execution.prepare_scenario_measurements", explode
    )

    from profile_manager.measurement_execution import MeasurementExecutionError

    with pytest.raises(MeasurementExecutionError):
        scenario_module.prepare_auto_measurements(scenario)
