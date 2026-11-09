"""F135: primitives can be retired (decision 2026-11-09): kept in the registry for history, refused in scenarios.

byzantine_isolation_observed expects no link either way between honest and byzantine nodes;
cardano-node accepts inbound connections and always retries local roots, so that expectation
never holds. It is retired with its reason.
"""
from __future__ import annotations

import json
from pathlib import Path

from profile_manager import primitives

REGISTRY = Path(__file__).resolve().parents[1] / "dwarf" / "primitives" / "registry.json"


def test_registry_entries_carry_a_lifecycle():
    registry = primitives.load_registry(REGISTRY)
    assert registry["byzantine_isolation_observed"].lifecycle == "retired"
    assert registry["byzantine_isolation_observed"].retired_reason
    assert registry["chain_select_consistent"].lifecycle == "active"


def test_unknown_lifecycle_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="lifecycle"):
        primitives._entries_from_map({"x": {"family": "assertion", "module": "m", "class": "C", "lifecycle": "zombie"}})


def test_a_scenario_using_a_retired_primitive_is_refused():
    from profile_manager import scenario as scenario_module
    body = json.loads((Path(__file__).resolve().parents[1] / "dwarf" / "scenarios"
                       / "runtime-substrate-byzantine-blockfetch-example-smoke.yaml").read_text())
    body["assertions"].append({"primitive": "byzantine_isolation_observed", "honest_node_ids": ["node1"],
                               "byzantine_node_ids": ["node3"]})
    result = scenario_module.semantic_validate_scenario_body(json.dumps(body).encode())
    assert any("retired" in error for error in result["errors"])
