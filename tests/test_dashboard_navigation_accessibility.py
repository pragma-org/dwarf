import json
import re

from profile_manager.data.operate_bundles import _enrich_bundle_row
from profile_manager.data.operate_runs import _enrich_run_row, apply_run_filters
from profile_manager.templating import render
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.learn_attack_cost import render_learn_attack_cost
from profile_manager.views.learn_docs import render_learn_glossary
from profile_manager.views.learn_examples import render_learn_examples
from profile_manager.views.operate_bundles import render_operate_bundles
from profile_manager.views.scenarios import render_operate_scenarios
from profile_manager.views.threat_coverage import render_learn_threat_coverage
from profile_manager.views.learn_docs import render_learn_troubleshooting
from profile_manager.views.learn_runbooks import render_learn_operator_runbook


def test_scenario_and_example_deep_links_have_real_destinations():
    scenarios = render_operate_scenarios()
    examples = render_learn_examples()
    ids = re.findall(r'href="/operate/scenarios#([^"]+)"', examples)

    assert ids
    for scenario_id in ids:
        assert f'id="{scenario_id}"' in scenarios
        assert f'id="{scenario_id}"' in examples


def test_mixed_topology_docs_distinguish_presence_convergence_and_readiness():
    glossary = render_learn_glossary()
    runbook = render_learn_operator_runbook()
    troubleshooting = render_learn_troubleshooting()

    assert "producer-side convergence only" in glossary
    assert "does not prove full mixed readiness" in glossary
    assert "Container presence is not node health" in runbook
    assert "precondition_failed" in runbook
    assert "Preserve evidence &amp; redeploy" in runbook
    assert "never redeploys merely because the page was viewed" in runbook
    assert "disabled by default" in runbook
    assert "Amaru relays or the Amaru-fed consumer are stalled" in troubleshooting


def test_consensus_glossary_anchor_matches_attack_cost_link(monkeypatch):
    from profile_manager.data import attack_cost

    attack_cost.reset_cache()
    monkeypatch.setattr(attack_cost, "_fetch_json", lambda url, timeout: (_ for _ in ()).throw(TimeoutError()))
    attack = render_learn_attack_cost()
    glossary = render_learn_glossary()
    target = re.search(r'href="/learn/glossary#([^"]+)"', attack)

    assert target is not None
    assert f'id="{target.group(1)}"' in glossary
    assert target.group(1) == "term-k-security-parameter"


def test_missing_historical_profile_is_not_linked(tmp_path):
    run_id = "historic-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    manifest = {
        "profile": {"id": "profile-that-no-longer-exists"},
        "target": {"implementation": "cardano-node"},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run = {"run_id": run_id, "source": "local"}

    row = _enrich_run_row(run, tmp_path)

    assert row["profile_id"] == "profile-that-no-longer-exists"
    assert row["profile_url"] is None


def test_attached_run_exposes_execution_and_phase_inventory(tmp_path):
    run_id = "mixed-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "profile": {"id": None},
            "runtime": "devnet",
            "target": {"implementation": "cardano-node"},
        }),
        encoding="utf-8",
    )
    (run_dir / "scenario.yaml").write_text(
        """\
attach:
  topology: cardano_amaru
setup:
  - primitive: runtime_attach_topology
load:
  - primitive: runtime_tracer_capture
  - primitive: runtime_multi_node_observation
faults: []
probes: []
assertions:
  - primitive: chain_select_differential
teardown: []
""",
        encoding="utf-8",
    )

    row = _enrich_run_row({"run_id": run_id, "source": "local"}, tmp_path)

    assert row["execution"] == {
        "kind": "attach",
        "label": "attach",
        "value": "cardano_amaru",
    }
    assert row["workload_label"] == "setup 1 · load 2 · assert 1"
    assert row["phase_inventory"] == [
        {"key": "setup", "label": "setup", "count": 1, "primitives": ["runtime_attach_topology"]},
        {
            "key": "load",
            "label": "load",
            "count": 2,
            "primitives": ["runtime_tracer_capture", "runtime_multi_node_observation"],
        },
        {
            "key": "assertions",
            "label": "assert",
            "count": 1,
            "primitives": ["chain_select_differential"],
        },
    ]
    assert row["primitive_names"] == [
        "runtime_attach_topology",
        "runtime_tracer_capture",
        "runtime_multi_node_observation",
        "chain_select_differential",
    ]


def test_profile_execution_survives_missing_scenario_snapshot(tmp_path):
    run_id = "profile-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": "profile-c"}, "runtime": "devnet"}),
        encoding="utf-8",
    )

    row = _enrich_run_row(
        {"run_id": run_id, "source": "local"},
        tmp_path,
        profile_ids={"profile-c"},
    )

    assert row["execution"] == {"kind": "profile", "label": "profile", "value": "profile-c"}
    assert row["profile_url"] == "/operate/profiles/profile-c"
    assert row["phase_inventory"] == []


def test_malformed_scenario_snapshot_degrades_to_unknown(tmp_path):
    run_id = "malformed-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": None}, "runtime": "devnet"}),
        encoding="utf-8",
    )
    (run_dir / "scenario.yaml").write_text("setup: [", encoding="utf-8")

    row = _enrich_run_row({"run_id": run_id, "source": "local"}, tmp_path)

    assert row["execution"] == {"kind": "unknown", "label": "unknown", "value": None}
    assert row["workload_label"] == "—"
    assert row["primitive_names"] == []


def test_substrate_execution_uses_node_count_and_network_not_raw_topology(tmp_path):
    run_id = "substrate-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": None}, "runtime": "devnet"}),
        encoding="utf-8",
    )
    (run_dir / "scenario.yaml").write_text(
        """\
substrate:
  network: testnet_42
  nodes:
    - {id: p1}
    - {id: p2}
    - {id: relay1}
    - {id: amaru1}
    - {id: amaru2}
  topology:
    edges:
      - {from: p1, to: p2}
""",
        encoding="utf-8",
    )

    row = _enrich_run_row({"run_id": run_id, "source": "local"}, tmp_path)

    assert row["execution"] == {
        "kind": "substrate",
        "label": "substrate",
        "value": "5 nodes · testnet_42",
    }


def test_run_search_matches_attachment_and_primitive_names():
    row = {
        "run_id": "mixed-run",
        "scenario_id": "mixed-scenario",
        "profile_id": None,
        "target_implementation": "cardano-node",
        "execution": {"kind": "attach", "label": "attach", "value": "cardano_amaru"},
        "primitive_names": ["runtime_multi_node_observation", "chain_select_differential"],
        "evidence_tags": ["crash", "candidate"],
    }

    assert apply_run_filters([row], q="cardano_amaru") == [row]
    assert apply_run_filters([row], q="multi_node") == [row]
    assert apply_run_filters([row], q="candidate") == [row]
    assert apply_run_filters([row], q="not-present") == []


def test_run_row_renders_execution_and_compact_workload():
    row = {
        "run_id": "mixed-run",
        "ended_at": "2026-09-14T05:13:02Z",
        "scenario_id": "mixed-scenario",
        "profile_id": None,
        "profile_url": None,
        "runtime": "devnet",
        "exit_status": "pass",
        "target_implementation": "cardano-node",
        "run_url": "/operate/runs/mixed-run",
        "source": "local",
        "is_local": True,
        "thinness_signals": [],
        "execution": {"kind": "attach", "label": "attach", "value": "cardano_amaru"},
        "workload_label": "setup 1 · load 2 · assert 1",
        "workload_title": (
            "setup: runtime_attach_topology; load: runtime_tracer_capture, "
            "runtime_multi_node_observation; assert: chain_select_differential"
        ),
        "evidence_tags": ["crash", "candidate"],
    }

    html = render("operate/_partials/_run_row.j2", row=row)

    assert "attach" in html
    assert "cardano_amaru" in html
    assert "setup 1 · load 2 · assert 1" in html
    assert "runtime_multi_node_observation" in html
    assert "crash" in html
    assert "candidate" in html


def test_run_row_attaches_only_exact_matching_interesting_evidence(tmp_path):
    run_id = "run-a"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": None}, "runtime": "library"}),
        encoding="utf-8",
    )
    matching = {
        "id": "tc-a",
        "source_run_id": run_id,
        "classification": "crash",
        "triage_reason": "decoder-abort",
        "replay_state": "pending",
        "compare_state": "pending",
        "minimization_state": "none",
        "promotion_state": "candidate",
    }
    duplicate_tags = {
        **matching,
        "id": "tc-b",
        "triage_reason": "second-abort",
    }
    unrelated = {
        **matching,
        "id": "tc-other",
        "source_run_id": "run-b",
        "classification": "hang",
    }

    row = _enrich_run_row(
        {"run_id": run_id, "source": "local"},
        tmp_path,
        lifecycle_rows=[matching, duplicate_tags, unrelated],
    )

    assert [item["id"] for item in row["interesting_evidence"]] == ["tc-a", "tc-b"]
    assert row["evidence_tags"] == [
        "crash",
        "replay pending",
        "differential",
        "compare pending",
        "candidate",
    ]


def test_run_without_lifecycle_match_has_no_interesting_evidence(tmp_path):
    run_id = "ordinary-run"
    (tmp_path / run_id).mkdir()

    row = _enrich_run_row(
        {"run_id": run_id, "source": "local"},
        tmp_path,
        lifecycle_rows=[{"id": "tc-other", "source_run_id": "another-run"}],
    )

    assert row["interesting_evidence"] == []
    assert row["evidence_tags"] == []


def test_missing_bundle_profile_is_not_linked(tmp_path):
    run_id = "historic-bundle"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": "profile-that-no-longer-exists"}}),
        encoding="utf-8",
    )
    bundle = tmp_path / f"{run_id}.tar.gz"
    bundle.write_bytes(b"archive")

    row = _enrich_bundle_row(bundle, tmp_path)

    assert row["profile_id"] == "profile-that-no-longer-exists"
    assert row["profile_url"] is None


def test_operate_and_learn_controls_have_accessible_names():
    landing = render_learn_landing()
    bundles = render_operate_bundles()
    threat = render_learn_threat_coverage()

    assert len(re.findall(r"<h1(?:\s|>)", landing)) == 1
    assert re.search(r'<label[^>]+for="bundle-import"', bundles)
    assert 'id="bundle-import"' in bundles
    assert re.search(r'<label[^>]+for="q"', threat)


def test_shim_enabled_coverage_runner_has_an_accessible_label(monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")

    scenarios = render_operate_scenarios()

    assert 'id="run-cov-id"' in scenarios
    assert re.search(r'<label[^>]+for="run-cov-id"', scenarios)
