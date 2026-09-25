import json
from pathlib import Path

import jsonschema


def test_soak_primitive_registered():
    reg = json.loads(Path("dwarf/primitives/registry.json").read_text())["primitives"]
    entry = reg["runtime_opcert_header_soak"]
    assert entry["class"] == "RuntimeOpcertHeaderSoak"
    assert entry["family"] == "load"
    assert set(entry["supports"]) == {"cardano-node", "amaru"}


def _validator():
    schema = json.loads(Path("dwarf/primitives/load/runtime_opcert_header_soak.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema)


def test_soak_schema_rejects_both_target_shapes():
    v = _validator()
    ok = {"family": "accept-boundary", "seed": 5, "profile_id": "p", "target_node": "node1",
          "output_dir": "o"}
    bad = {"family": "accept-boundary", "seed": 5, "profile_id": "p",
           "target_node": "n", "target_nodes": ["a", "b"], "output_dir": "o"}
    assert list(v.iter_errors(ok)) == []
    assert list(v.iter_errors(bad)) != []


def test_soak_schema_requires_target_nodes_for_differential():
    v = _validator()
    # differential family with a single target_node is invalid
    bad = {"family": "encoding-form", "seed": 5, "profile_id": "p", "target_node": "n",
           "output_dir": "o"}
    ok = {"family": "encoding-form", "seed": 5, "profile_id": "p",
          "target_nodes": ["node1", "amaru-relay-1"], "output_dir": "o"}
    assert list(v.iter_errors(bad)) != []
    assert list(v.iter_errors(ok)) == []


def test_soak_command_build(tmp_path):
    from profile_manager.primitives import RuntimeOpcertHeaderSoak
    prim = RuntimeOpcertHeaderSoak(params={
        "family": "encoding-form", "seed": 5,
        "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
        "target_nodes": ["node1", "amaru-relay-1"],
        "time_budget_seconds": 10800, "output_dir": "outputs/soak"})
    cmd = prim._build_command(runtime_root=str(tmp_path), output_dir=tmp_path / "o")
    assert "--family" in cmd and "encoding-form" in cmd
    assert "--time-budget-seconds" in cmd and "10800" in cmd
    assert "--target-nodes" in cmd
