"""Offline planning must freeze identities without creating runtime evidence."""
from copy import deepcopy
from pathlib import Path
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from profile_manager import security_campaign as api
from profile_manager.security_campaign_suite import load_security_campaign_suite, load_measurement_suite
from profile_manager.testplease_candidates import discover_candidates

ROOT = Path(__file__).resolve().parents[1]
MIXED = "ledger-submit-transaction-array-arity-differential-amaru-cardano-node"


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "dwarf/testplease/amaru/proposal"
    (source / "crates/amaru/src").mkdir(parents=True)
    (source / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/amaru"]\n')
    (source / "crates/amaru/Cargo.toml").write_text('[package]\nname = "amaru"\nversion = "0.1.0"\n')
    (source / "crates/amaru/src/main.rs").write_text("fn main() {}\n")
    candidate, = discover_candidates(tmp_path, repository_commit="a" * 40)
    security = load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml")
    measurements = load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml")
    catalog = {"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "compatibility_pairs": [], "releases": [{
        "implementation": "cardano-node", "version": "99.0.0", "channel": "stable", "source_revision": "b" * 40,
        "artifacts": [{"kind": "oci", "reference": "example.invalid/cardano:99.0.0", "availability": "available", "digest": "sha256:" + "c" * 64}],
        "verification": {},
    }]}
    selection = {"entry_ids": [MIXED], "implementations": {
        "amaru": {"kind": "candidate", "version": "proposal"},
        "cardano-node": {"kind": "release", "version": "99.0.0"},
    }}
    return candidate, security, measurements, selection, catalog


def plan(inputs, **kwargs):
    candidate, security, measurements, selection, catalog = inputs
    return api.resolve_security_campaign(candidate, security, measurements, selection=selection, catalog=catalog, **kwargs)


def test_freezes_exact_inputs_without_inventing_build_or_security_results(inputs):
    result = plan(inputs)
    schema = json.loads((ROOT / "dwarf/spec/v1/security-campaign.schema.json").read_text())
    Draft202012Validator(schema).validate(result)
    assert result["candidate"]["repository_commit"] == "a" * 40
    assert result["candidate"]["source_tree_digest"] == inputs[0].source_tree_digest
    assert "source_root" not in result["candidate"]  # Portable identity, not machine-local path.
    assert result["suites"]["security"]["digest"].startswith("sha256:")
    assert result["suites"]["measurement"]["document"] == inputs[2]
    assert result["security_result"] == "pending"
    assert result["measurement_completeness"] == "pending"
    assert result["readiness"]["status"] == "pending"
    assert result["readiness"]["executed"] is False
    variants = result["build_variants"]
    assert {v["mode"] for v in variants} == {"stock", "patched", "coverage"}
    assert all(v["source"] == result["candidate"] for v in variants)
    assert all(v["artifacts"] == {"image_digest": None, "binary_digest": None, "executable_digest": None} for v in variants)
    assert all(v["status"] == "pending-build" for v in variants)
    entry = result["entries"][0]
    assert entry["contract"]["normal_control"]
    assert entry["contract"]["workload_fault"] and entry["contract"]["oracle"]
    scenario = result["definitions"]["scenarios"][MIXED]
    assert scenario["behavior_digest"].startswith("sha256:")
    assert scenario["document"]["assertions"]
    assert result["threat_risk_refs"]["status"] == "unmapped"


def test_mixed_is_one_shared_heterogeneous_topology_with_per_node_identity_and_taps(inputs):
    result = plan(inputs)
    assert len(result["topologies"]) == 1
    topology = result["topologies"][0]
    assert topology["mode"] == "shared-runtime"
    nodes = {n["node_id"]: n for n in topology["nodes"]}
    assert nodes["node1"]["identity"]["kind"] == "candidate"
    official = nodes["node2"]["identity"]
    assert official["version"] == "99.0.0"
    assert official["source_revision"] == "b" * 40
    assert official["artifacts"]["image_digest"] == "sha256:" + "c" * 64
    assert official["qualification"]["status"] == "unknown"
    assert official["artifacts"]["binary_digest"] is None
    attachments = result["entries"][0]["measurement_attachments"]
    assert {a["node_id"] for a in attachments} == {"node1", "node2"}
    assert all(a["topology_id"] == topology["id"] for a in attachments)
    assert {c["node_id"] for c in result["required_tap_cells"]} == {"node1", "node2"}
    assert all(c["status"] == "pending" and c["rule"] for c in result["required_tap_cells"])
    assert any(c["runtime"]["status"] == "missing" for c in result["required_tap_cells"])


def test_declared_future_artifacts_are_retained_without_claiming_build_evidence(inputs):
    inputs[3]["build_variants"] = {"patched": {"patch_set_digest": "sha256:" + "d" * 64,
        "artifacts": {"image_digest": "sha256:" + "e" * 64, "binary_digest": "sha256:" + "f" * 64, "executable_digest": "sha256:" + "1" * 64}}}
    result = plan(inputs)
    patched = next(v for v in result["build_variants"] if v["mode"] == "patched")
    assert patched["artifacts"] == inputs[3]["build_variants"]["patched"]["artifacts"]
    assert patched["status"] == "pending-build"
    assert patched["patch_set_digest"] == "sha256:" + "d" * 64


def test_deterministic_portable_canonical_identity_and_no_aliases(inputs):
    left = plan(inputs)
    right = plan(inputs)
    assert api.serialize_campaign(left) == api.serialize_campaign(right)
    assert left["campaign_digest"] == api.digest_security_campaign(left)
    assert left["campaign_id"] == "campaign-" + left["campaign_digest"].split(":")[1]
    moved = deepcopy(inputs)
    object.__setattr__(moved[0], "source_root", Path("/other/host/proposal"))
    assert plan(moved)["campaign_id"] == left["campaign_id"]
    inputs[1]["title"] = "Changed suite"
    assert left["suites"]["security"]["document"]["title"] != "Changed suite"
    assert plan(inputs)["campaign_id"] != left["campaign_id"]


@pytest.mark.parametrize("change", ["candidate", "measurement", "selection", "scenario"])
def test_each_immutable_input_changes_identity(inputs, monkeypatch, change):
    before = plan(inputs)["campaign_id"]
    if change == "candidate":
        object.__setattr__(inputs[0], "source_tree_digest", "2" * 64)
    elif change == "measurement":
        inputs[2]["exercises"][0]["evidence_rule"] += " Additional evidence."
    elif change == "selection":
        inputs[3]["implementations"]["amaru"]["version"] = "proposal-v2"
    else:
        original = api.load_definition
        def changed(catalog, definition_id):
            record = original(catalog, definition_id)
            if catalog == "scenarios" and definition_id == MIXED:
                from dataclasses import replace
                document = deepcopy(record.data)
                document["seed"] = "0x1234"
                return replace(record, data=document)
            return record
        monkeypatch.setattr(api, "load_definition", changed)
    assert plan(inputs)["campaign_id"] != before


@pytest.mark.parametrize("field,left,right", [("era", "conway", "babbage"), ("genesis_digest", "sha256:" + "1" * 64, "sha256:" + "2" * 64), ("protocol_version", "10", "11")])
def test_rejects_known_incompatible_network_metadata(inputs, field, left, right):
    implementations = inputs[3]["implementations"]
    implementations["amaru"]["compatibility"] = {field: left}
    implementations["cardano-node"]["compatibility"] = {field: right}
    with pytest.raises(ValueError, match=field):
        plan(inputs)


@pytest.mark.parametrize("mutation", ["missing-release", "missing-node", "wrong-candidate", "moving-version", "duplicate-entry", "unknown-entry", "empty-selection", "invalid-artifact"])
def test_rejects_ambiguous_or_unfrozen_selection(inputs, mutation):
    selection = inputs[3]
    if mutation == "missing-release":
        selection["implementations"]["cardano-node"]["version"] = "absent"
    elif mutation == "missing-node":
        del selection["implementations"]["cardano-node"]
    elif mutation == "wrong-candidate":
        selection["implementations"]["amaru"]["kind"] = "release"
    elif mutation == "moving-version":
        selection["implementations"]["amaru"]["version"] = "latest"
    elif mutation == "duplicate-entry":
        selection["entry_ids"] *= 2
    elif mutation == "unknown-entry":
        selection["entry_ids"] = ["absent"]
    elif mutation == "empty-selection":
        selection["entry_ids"] = []
    else:
        selection["build_variants"] = {"stock": {"artifacts": {"image_digest": "mutable:tag"}}}
    with pytest.raises(ValueError):
        plan(inputs)


def test_schema_rejects_claimed_results_and_missing_node_identity(inputs):
    schema = json.loads((ROOT / "dwarf/spec/v1/security-campaign.schema.json").read_text())
    validator = Draft202012Validator(schema)
    result = plan(inputs)
    result["security_result"] = "pass"
    assert list(validator.iter_errors(result))
    result["security_result"] = "pending"
    del result["topologies"][0]["nodes"][0]["identity"]
    assert list(validator.iter_errors(result))


def test_plan_does_not_execute_process_or_network_and_cli_is_offline(inputs, monkeypatch, tmp_path, capsys):
    from profile_manager import cli
    def forbidden(*args, **kwargs):
        raise AssertionError("planning attempted execution/network")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    expected = plan(inputs)
    selection_path, catalog_path = tmp_path / "selection.json", tmp_path / "catalog.json"
    selection_path.write_text(json.dumps(inputs[3]))
    catalog_path.write_text(json.dumps(inputs[4]))
    output = tmp_path / "campaign.json"
    assert cli.main(["campaign", "plan", "--repository-root", str(inputs[0].source_root.parents[3]),
        "--repository-commit", "a" * 40, "--candidate", "amaru/proposal",
        "--security-suite", str(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml"),
        "--measurement-suite", str(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml"),
        "--selection", str(selection_path), "--catalog", str(catalog_path), "--output", str(output), "--json"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == json.loads(output.read_text()) == expected


@pytest.mark.parametrize("command", ["run", "resume", "verify"])
def test_runtime_cli_names_fail_explicitly_without_orchestration(command, capsys):
    from profile_manager import cli
    assert cli.main(["campaign", command]) == 2
    assert "not implemented" in capsys.readouterr().err.lower()


def test_shared_scenario_entries_reuse_one_mixed_topology(inputs):
    inputs[3]["entry_ids"].append("ledger-submit-empty-redeemers-container-differential-amaru-cardano-node")
    result = plan(inputs)
    assert len(result["entries"]) == 2
    assert len(result["topologies"]) == 1
    assert len({entry["topology_id"] for entry in result["entries"]}) == 1


def test_planned_readiness_uses_existing_qualification_gate_contract(inputs):
    from scripts.qualify_node_versions import classify_qualification
    result = plan(inputs)
    expected = classify_qualification("mixed", {})["required_gates"]
    assert result["readiness"]["required_checks"] == expected
    assert result["readiness"]["executed"] is False


def test_declared_refs_are_preserved_without_claiming_coverage(inputs):
    refs = {"threat_model": ["TM-001"], "risk_register": ["RR-001"]}
    inputs[3]["threat_risk_refs"] = refs
    before = deepcopy(inputs[1:])
    result = plan(inputs)
    assert result["threat_risk_refs"] == {"status": "declared", **refs}
    assert result["security_result"] == "pending"
    assert inputs[1:] == before


def test_known_release_metadata_cannot_be_overridden_or_ignored(inputs):
    inputs[4]["releases"][0]["era"] = "babbage"
    inputs[3]["implementations"]["amaru"]["compatibility"] = {"era": "conway"}
    with pytest.raises(ValueError, match="era"):
        plan(inputs)


def test_invalid_cli_input_returns_actionable_error_without_writing(inputs, tmp_path, capsys):
    from profile_manager import cli
    output = tmp_path / "absent.json"
    assert cli.main(["campaign", "plan", "--repository-root", str(tmp_path), "--repository-commit", "a" * 40,
        "--candidate", "amaru/absent", "--security-suite", "absent", "--measurement-suite", "absent",
        "--selection", "absent", "--output", str(output)]) == 2
    assert "select exactly one" in capsys.readouterr().err
    assert not output.exists()


def mixed_entry(inputs):
    return next(e for g in inputs[1]['groups'] for e in g['entries'] if e['id'] == MIXED)


def test_mixed_execution_freezes_real_recipe_roles_edges_controls_and_assertions(inputs):
    result = plan(inputs)
    entry, = result['entries']
    topology, = result['topologies']
    original = result['definitions']['scenarios'][MIXED]['document']
    execution = entry['execution']
    assert execution['status'] == 'pending-runtime'
    assert execution['scenario_digest'] == result['definitions']['scenarios'][MIXED]['digest']
    bound = execution['scenario']
    assert bound['substrate']['topology']['edges'] == topology['edges'] == original['substrate']['topology']['edges']
    assert {n['node_id']: n['role'] for n in topology['nodes']} == {'node1': 'submitter', 'node2': 'reference'}
    for phase in ('setup', 'load', 'probes', 'assertions', 'teardown'):
        assert bound[phase] == original[phase]
    workload = bound['load'][0]
    assert workload['primitive'] == 'runtime_transaction_array_arity_differential'
    assert (workload['target_node'], workload['reference_node']) == ('node1', 'node2')
    assert execution['normal_control']['scenario'] == bound
    nodes = {n['id']: n for n in bound['substrate']['nodes']}
    assert nodes['node1']['version'] == 'proposal'
    assert nodes['node1']['source_revision'] == 'a' * 40
    assert nodes['node2']['version'] == '99.0.0'
    assert nodes['node2']['source_revision'] == 'b' * 40
    assert nodes['node2']['image_digest'] == 'sha256:' + 'c' * 64
    assert bound['target']['version'] == 'proposal'
    assert original['target']['version'] == 'resolved-plan'


def test_unknown_mixed_pair_stays_explicitly_pending(inputs):
    compatibility = plan(inputs)['entries'][0]['execution']['compatibility']
    assert compatibility['status'] == 'pending-prerequisites'
    assert {item['field'] for item in compatibility['missing']} == {'era', 'genesis_digest', 'protocol_version'}
    assert {item['node_id'] for item in compatibility['missing']} == {'node1', 'node2'}


def test_declared_compatible_pair_retains_operator_provenance_without_readiness(inputs):
    common = {'era': 'conway', 'genesis_digest': 'sha256:' + '1' * 64, 'protocol_version': '10'}
    for selected in inputs[3]['implementations'].values():
        selected['compatibility'] = common.copy()
    result = plan(inputs)
    assert result['entries'][0]['execution']['compatibility'] == {'status': 'declared-compatible', 'missing': []}
    assert result['readiness']['executed'] is False
    assert result['security_result'] == 'pending'


def test_per_node_modes_bind_expected_build_without_claiming_observation(inputs):
    attachment = mixed_entry(inputs)['measurement_attachments'][0]
    attachment.update(target_mode='patched', measurement_profile='amaru-security-patched',
                      result_namespace='nodes/node1/measurements/patched')
    inputs[3]['build_variants'] = {'patched': {'patch_set_digest': 'sha256:' + 'd' * 64,
        'instrumentation_digest': 'sha256:' + 'e' * 64,
        'artifacts': {'image_digest': 'sha256:' + 'f' * 64, 'executable_digest': 'sha256:' + '1' * 64}}}
    result = plan(inputs)
    attachments = result['entries'][0]['measurement_attachments']
    candidate, peer = (a['planning_resolution'] for a in attachments)
    assert candidate['status'] == 'pending-build'
    assert candidate['target_identity']['mode'] == 'patched'
    assert candidate['target_identity']['source_revision'] == 'a' * 40
    assert candidate['target_identity']['source_tree_digest'] == inputs[0].source_tree_digest
    assert candidate['target_identity']['image_digest'] == 'sha256:' + 'f' * 64
    assert candidate['build_variant'] == next(v for v in result['build_variants'] if v['mode'] == 'patched')
    assert peer['target_identity']['mode'] == 'stock'
    assert peer['target_identity']['image_digest'] == 'sha256:' + 'c' * 64
    assert attachments[0]['topology_id'] == attachments[1]['topology_id']
    assert attachments[0]['result_namespace'] != attachments[1]['result_namespace']
    assert result['security_result'] == 'pending'


@pytest.mark.parametrize('node_index', [0, 1])
def test_attachment_mode_must_match_profile_even_before_build(inputs, node_index):
    mixed_entry(inputs)['measurement_attachments'][node_index]['target_mode'] = 'coverage'
    with pytest.raises(ValueError, match='mode'):
        plan(inputs)


def test_one_node_cannot_select_two_build_modes_in_one_security_run(inputs):
    entry = mixed_entry(inputs)
    other = deepcopy(entry['measurement_attachments'][0])
    other.update(target_mode='patched', measurement_profile='amaru-security-patched',
                 result_namespace='nodes/node1/measurements/patched')
    entry['measurement_attachments'].append(other)
    with pytest.raises(ValueError, match='one.*mode'):
        plan(inputs)


def test_release_cannot_invent_a_patched_build_from_stock_artifact(inputs):
    attachment = mixed_entry(inputs)['measurement_attachments'][1]
    attachment.update(target_mode='patched', measurement_profile='cardano-security-patched',
                      result_namespace='nodes/node2/measurements/patched')
    with pytest.raises(ValueError, match='release.*mode'):
        plan(inputs)


def test_pending_measurements_keep_exact_peer_identity(inputs):
    peer = plan(inputs)['entries'][0]['measurement_attachments'][1]['planning_resolution']
    assert peer['status'] == 'pending-prerequisites'
    assert peer['target_identity']['version'] == '99.0.0'
    assert peer['target_identity']['source_revision'] == 'b' * 40


def test_execution_contract_cannot_be_replaced_by_measurement_mirror(inputs):
    result = plan(inputs)
    execution = result['entries'][0]['execution']
    assert execution['scenario']['id'] == MIXED
    assert execution['normal_control']['scenario']['id'] == MIXED
    assert execution['scenario']['assertions'][0]['primitive'] == 'transaction_array_arity_differential_matches'
    assert all(c['scenario_id'] != MIXED for c in result['required_tap_cells'])
    assert result['security_result'] == 'pending'
