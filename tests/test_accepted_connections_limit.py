"""F140: the duplex hard limit is the node's own AcceptedConnectionsLimit, set by the scenario.

Before, hard_limit 8 existed only in the probe while the node ran cardano-node's default, so the
check compared the node with a number it was never configured with (live 48dd04e0: 24 vs 8).
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_compose_substrate as compose
from scripts import runtime_exposure_probe as probe
from scripts import runtime_substrate_common as common

LIMIT = {"hardLimit": 8, "softLimit": 6, "delay": 1}


def test_normalize_accepts_and_validates_the_limit():
    assert common._accepted_connections_limit({"cardano_accepted_connections_limit": LIMIT}) == LIMIT
    assert common._accepted_connections_limit({}) is None
    with pytest.raises(ValueError, match="softLimit"):
        common._accepted_connections_limit({"cardano_accepted_connections_limit": {"hardLimit": 4, "softLimit": 6, "delay": 1}})
    with pytest.raises(ValueError, match="hardLimit"):
        common._accepted_connections_limit({"cardano_accepted_connections_limit": {"hardLimit": "8", "softLimit": 6, "delay": 1}})


def test_compose_writes_it_into_the_node_config(tmp_path):
    config = tmp_path / "configuration.yaml"
    config.write_text(json.dumps({"Protocol": "Cardano"}))
    evidence = compose._pin_cardano_accepted_connections_limit(config_path=config, limit=LIMIT)
    assert json.loads(config.read_text())["AcceptedConnectionsLimit"] == LIMIT
    assert evidence == {"pinned": True, "value": LIMIT}
    assert compose._pin_cardano_accepted_connections_limit(config_path=config, limit=None) == {"pinned": False, "value": None}


def test_probe_uses_the_node_configured_hard_limit(tmp_path):
    config = tmp_path / "configuration.yaml"
    config.write_text(json.dumps({"AcceptedConnectionsLimit": LIMIT}))
    assert probe._node_hard_limit({"config_path": str(config)}, {}) == (8, "node-config")
    assert probe._node_hard_limit({"config_path": str(config)}, {"hard_limit": 5}) == (5, "probe-configured")
    empty = tmp_path / "empty.yaml"
    empty.write_text(json.dumps({}))
    assert probe._node_hard_limit({"config_path": str(empty)}, {}) == (None, "not-configured")


def test_scenario_loader_keeps_the_cardano_config_overrides():
    """F140b: the scenario loader rebuilt the substrate from known keys and dropped both
    cardano_accepted_connections_limit and cardano_experimental_protocols (the F138 override)."""
    from profile_manager import scenario as scenario_module
    body = json.loads(open("dwarf/scenarios/runtime-substrate-duplex-promotion-pressure-example-smoke.yaml").read())
    body["substrate"]["cardano_experimental_protocols"] = True
    sc = scenario_module.scenario_from_body(json.dumps(body).encode())
    assert sc.substrate["cardano_accepted_connections_limit"] == LIMIT
    assert sc.substrate["cardano_experimental_protocols"] is True


def test_scenario_loader_rejects_a_bad_limit():
    from profile_manager import scenario as scenario_module
    body = json.loads(open("dwarf/scenarios/runtime-substrate-duplex-promotion-pressure-example-smoke.yaml").read())
    body["substrate"]["cardano_accepted_connections_limit"] = {"hardLimit": 2, "softLimit": 5, "delay": 1}
    with pytest.raises(scenario_module.ScenarioValidationError, match="softLimit"):
        scenario_module.scenario_from_body(json.dumps(body).encode())
