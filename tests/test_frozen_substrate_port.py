"""F57 — porting frozen self-composing substrates to the resolved-plan binding.

Part 1: a resolved-plan substrate whose nodes declare one concrete version per
implementation asks the version resolver for exactly that version when the
request pins nothing (the way library scenarios already do), so a ported
10.7.1 scenario keeps meaning 10.7.1.  Two concrete versions for one
implementation cannot be expressed and are refused up front.

Part 2: ``dwarf/scripts/port_frozen_substrates.py`` rewrites a frozen
substrate deterministically (binding, docker compose mode, catalog spelling
of Amaru versions), refuses what the resolver cannot express, preserves the
file's JSON style, and writes nothing without ``--apply``.
"""
import json
from dataclasses import replace

import pytest

from profile_manager import run_plan as run_plan_module
from profile_manager.run_plan import RunPlanError, RunPlanRequest
from profile_manager.scenario import load_scenario
from scripts import port_frozen_substrates as port


def _nodes(cardano="10.7.1", second_cardano=None, amaru=None, binding="resolved-plan"):
    nodes = [{"id": "node1", "impl": "cardano-node", "version": cardano, "role": "honest", "identity_binding": binding}]
    nodes.append({"id": "node2", "impl": "cardano-node", "version": second_cardano or cardano, "role": "honest", "identity_binding": binding})
    if amaru:
        nodes.append({"id": "amaru1", "impl": "amaru", "version": amaru, "role": "observer", "identity_binding": binding})
    return nodes


def _scenario(nodes, compose_mode="docker"):
    base = load_scenario("dwarf/scenarios/runtime-substrate-chainsync-nonincrementing-height-example-smoke.yaml")
    edges = [{"from": n["id"], "to": m["id"]} for n in nodes for m in nodes if n is not m]
    return replace(base, substrate={"compose_mode": compose_mode, "network": "testnet_42", "nodes": nodes, "topology": {"edges": edges}})


# --- part 1: resolver seeding ------------------------------------------------


def test_declared_uniform_versions_seed_an_exact_selection_when_the_request_pins_nothing():
    profile = run_plan_module._scenario_substrate_version_profile(
        _scenario(_nodes(cardano="10.7.1", amaru="10.11.20260925")), RunPlanRequest(scenario_id="x")
    )
    assert profile["cardano_version"] == "10.7.1"
    assert profile["amaru_version"] == "10.11.20260925"
    assert profile["version_policy"] == "exact"
    assert profile["version_policy_source"] == "scenario"


def test_the_sentinel_version_keeps_the_latest_confirmed_default():
    profile = run_plan_module._scenario_substrate_version_profile(
        _scenario(_nodes(cardano="resolved-plan")), RunPlanRequest(scenario_id="x")
    )
    assert "cardano_version" not in profile
    assert profile["version_policy"] == "latest-confirmed"
    assert profile["version_policy_source"] == "implicit-default"


def test_a_request_pin_overrides_the_declared_version():
    profile = run_plan_module._scenario_substrate_version_profile(
        _scenario(_nodes(cardano="10.7.1")), RunPlanRequest(scenario_id="x", cardano_version="11.1.2")
    )
    assert profile["cardano_version"] == "11.1.2"
    assert profile["version_policy_source"] == "explicit"


def test_two_concrete_versions_for_one_implementation_are_refused():
    with pytest.raises(RunPlanError, match="one release per implementation"):
        run_plan_module._scenario_substrate_version_profile(
            _scenario(_nodes(cardano="10.7.1", second_cardano="10.6.1")), RunPlanRequest(scenario_id="x")
        )


# --- part 2: the port script -------------------------------------------------


def _frozen_document(nodes, compose_mode=None):
    document = {"spec_version": "v1", "id": "x", "title": "x", "runtime": "devnet",
                "target": {"implementation": "cardano-node", "version": "any"},
                "substrate": {"network": "testnet_42", "nodes": nodes,
                              "topology": {"edges": [{"from": nodes[0]["id"], "to": nodes[1]["id"]}]}},
                "setup": [], "load": [], "assertions": []}
    if compose_mode:
        document["substrate"]["compose_mode"] = compose_mode
    return document


def _frozen_nodes(cardano="10.7.1", second_cardano=None, amaru=None):
    nodes = [{"id": "node1", "impl": "cardano-node", "version": cardano, "role": "honest"},
             {"id": "node2", "impl": "cardano-node", "version": second_cardano or cardano, "role": "honest"}]
    if amaru:
        nodes.append({"id": "amaru1", "impl": "amaru", "version": amaru, "role": "observer"})
    return nodes


def test_port_plan_rewrites_binding_compose_mode_and_amaru_spelling_only():
    plan = port.plan_port(_frozen_document(_frozen_nodes(amaru="v10.11.20260925")))
    assert plan["status"] == "port"
    substrate = plan["document"]["substrate"]
    assert substrate["compose_mode"] == "docker"
    assert [n["identity_binding"] for n in substrate["nodes"]] == ["resolved-plan"] * 3
    assert [n["version"] for n in substrate["nodes"]] == ["10.7.1", "10.7.1", "10.11.20260925"]
    assert substrate["topology"] == {"edges": [{"from": "node1", "to": "node2"}]}
    assert sorted(plan["changes"]) == sorted([
        "substrate.compose_mode: host -> docker",
        "substrate.nodes[0].identity_binding: frozen -> resolved-plan",
        "substrate.nodes[1].identity_binding: frozen -> resolved-plan",
        "substrate.nodes[2].identity_binding: frozen -> resolved-plan",
        "substrate.nodes[2].version: v10.11.20260925 -> 10.11.20260925",
    ])


@pytest.mark.parametrize("nodes,reason", [
    (_frozen_nodes(cardano="10.7.1", second_cardano="10.6.1"), "two cardano-node versions"),
    (_frozen_nodes(amaru="0.1.2"), "amaru version 0.1.2 is not a catalog release"),
])
def test_port_plan_refuses_what_the_resolver_cannot_express(nodes, reason):
    plan = port.plan_port(_frozen_document(nodes))
    assert plan["status"] == "skip"
    assert reason in plan["reason"]


def test_port_plan_skips_already_portable_and_large_substrates():
    portable = _frozen_document(_frozen_nodes())
    for node in portable["substrate"]["nodes"]:
        node["identity_binding"] = "resolved-plan"
    assert port.plan_port(portable)["status"] == "skip"
    large_nodes = [{"id": f"node{i}", "impl": "cardano-node", "version": "10.7.1", "role": "honest"} for i in range(1, 21)]
    plan = port.plan_port(_frozen_document(large_nodes), max_nodes=19)
    assert plan["status"] == "skip" and "20 nodes" in plan["reason"]


def test_port_preserves_the_file_style_and_writes_only_with_apply(tmp_path):
    compact = tmp_path / "compact.yaml"
    compact.write_text(json.dumps(_frozen_document(_frozen_nodes()), separators=(",", ":")) + "\n", encoding="utf-8")
    indented = tmp_path / "indented.yaml"
    indented.write_text(json.dumps(_frozen_document(_frozen_nodes()), indent=2) + "\n", encoding="utf-8")
    before = {p: p.read_text(encoding="utf-8") for p in (compact, indented)}

    report = port.port_files([compact, indented], apply=False)
    assert {row["status"] for row in report["rows"]} == {"port"}
    assert {p: p.read_text(encoding="utf-8") for p in (compact, indented)} == before

    port.port_files([compact, indented], apply=True)
    compact_text = compact.read_text(encoding="utf-8")
    indented_text = indented.read_text(encoding="utf-8")
    assert compact_text.count("\n") == 1 and '"identity_binding":"resolved-plan"' in compact_text
    assert indented_text.startswith("{\n  ") and '"identity_binding": "resolved-plan"' in indented_text
    assert json.loads(compact_text)["substrate"]["compose_mode"] == "docker"
    # A second apply is a no-op: the files are already portable.
    assert {row["status"] for row in port.port_files([compact, indented], apply=True)["rows"]} == {"skip"}


def test_port_accepts_future_amaru_release_labels():
    """Version-agnostic: any <major>.<minor>.<YYYYMMDD> label is a catalog shape; only the
    resolver knows whether the catalog has it.  A non-release pin stays refused."""
    plan = port.plan_port(_frozen_document(_frozen_nodes(amaru="v11.2.20270115")))
    assert plan["status"] == "port"
    assert plan["document"]["substrate"]["nodes"][2]["version"] == "11.2.20270115"
    assert port.plan_port(_frozen_document(_frozen_nodes(amaru="0.1.2")))["status"] == "skip"
