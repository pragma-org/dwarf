from collections import Counter
from copy import deepcopy
import base64
from pathlib import Path
import re
import subprocess

import pytest

from profile_manager import dashboard
from profile_manager.views.scenarios import render_operate_scenarios

from profile_manager.data.scenarios import _list_packaged_scenarios_for_compare
from profile_manager.profiles import load_profiles


def _catalog_by_id():
    rows = _list_packaged_scenarios_for_compare()
    assert len(rows) == 351
    return {row["id"]: row for row in rows}


def test_native_catalog_projection_exposes_safe_lifecycle_semantics_for_every_scenario():
    rows = _list_packaged_scenarios_for_compare()

    assert len(rows) == 351
    for row in rows:
        assert isinstance(row["phase_primitives"], dict)
        assert set(row["phase_primitives"]) == {
            "setup",
            "load",
            "faults",
            "probes",
            "assertions",
            "teardown",
        }
        assert all(
            isinstance(primitive, str) and primitive
            for primitives in row["phase_primitives"].values()
            for primitive in primitives
        )
        assert row["assertion_primitives"] == row["phase_primitives"]["assertions"]
        assert "expected_security_finding" in row
        assert "substrate" in row
        assert "attached_topology" in row
        assert "adapter_implementations" in row


def test_native_projection_uses_actual_workload_and_assertion_primitives():
    rows = _catalog_by_id()

    fuzz = rows["amaru-cbor-block-fuzz"]
    assert fuzz["phase_primitives"]["load"] == ["cbor_fuzz_target"]
    assert fuzz["assertion_primitives"] == [
        "parse_succeeds_or_clean_error",
        "roundtrip_equals_original",
    ]

    recovery = rows["client-example-restart-recovery-sync-amaru"]
    assert "runtime_real_target_restart_and_readiness" in recovery["phase_primitives"]["load"]
    assert "restart_readiness_complete" in recovery["assertion_primitives"]

    evidence = rows["runtime-bundle-timeline-example-smoke"]
    assert evidence["phase_primitives"]["load"] == ["runtime_bundle_timeline"]
    assert evidence["assertion_primitives"] == ["bundle_timeline_emitted_clean"]


def test_native_projection_does_not_expose_primitive_parameters():
    rows = _list_packaged_scenarios_for_compare()

    for row in rows:
        for primitives in row["phase_primitives"].values():
            assert all(not isinstance(primitive, dict) for primitive in primitives)


def test_native_projection_exposes_only_bounded_topology_participant_facts():
    rows = _catalog_by_id()

    attached = rows["consensus-state-lifecycle-bootstrap-differential"]
    assert attached["attached_topology"] == "cardano_amaru"
    assert attached["adapter_implementations"] == []

    adapter = rows["client-example-plutus-vm-cardano"]
    assert adapter["attached_topology"] is None
    assert adapter["adapter_implementations"] == ["amaru", "cardano-node"]
    assert "adapters" not in adapter

    ordinary = rows["cardano-measurement-e2e-stock"]
    assert ordinary["attached_topology"] is None
    assert ordinary["adapter_implementations"] == []


def test_native_projection_retains_validated_tags_authors_and_nested_phase_oracles():
    rows = _catalog_by_id()

    fuzz = rows["cardano-node-cov-applyblock-aflpp-smoke"]
    assert {"fuzz", "afl++", "coverage"} <= set(fuzz["tags"])
    assert fuzz["authors"] == ["dwarf"]

    phased = rows["phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo"]
    assert phased["phase_primitives"]["faults"]
    assert phased["phase_primitives"]["assertions"]


def _discovery_catalog():
    from profile_manager.data.scenario_discovery import catalog_discovery

    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in load_profiles()
    }
    return catalog_discovery(
        _list_packaged_scenarios_for_compare(), profile_shapes=profile_shapes
    )


def test_discovery_metadata_validates_every_catalog_scenario_and_participant_counts():
    from profile_manager.data.scenario_discovery import validate_catalog_discovery

    rows = _discovery_catalog()

    assert len(rows) == 351
    validate_catalog_discovery(rows, expected_scenario_ids={row["id"] for row in rows})
    participant_counts = Counter(
        participant
        for row in rows
        for participant in row["implementation_participants"]
    )
    assert participant_counts == {"amaru": 114, "cardano-node": 305, "other": 6}
    assert sum(row["topology_scope_key"] == "mixed" for row in rows) == 67
    for row in rows:
        assert row["friendly_title"]
        assert row["purpose"].endswith(".")
        assert row["security_check"].endswith(".")
        assert row["family_key"]
        assert row["family_title"]
        assert row["execution_type"] in {
            "live",
            "standalone-library",
            "fuzz",
            "evidence-utility",
        }
        assert row["security_goals"]
        assert row["compatibility"]["state"]
        assert row["local_capability"]["state"]
        assert row["evidence"]["state"]
        assert isinstance(row["recommended"], bool)
        assert row["maintenance"]["state"] in {"shown", "hidden-by-default"}
        assert row["search_text"]
        assert row["variant_requirements"]
        assert row["primary_target_key"] in {"amaru", "cardano-node"}
        assert row["implementation_participants"]
        assert set(row["implementation_facets"]) >= set(row["implementation_participants"])
    assert max(len(row["friendly_title"]) for row in rows) <= 110
    assert all(
        row["purpose"].casefold() != f"exercises {row['friendly_title']}.".casefold()
        for row in rows
    )


def test_amaru_participation_comes_from_explicit_topology_or_adapter_facts():
    from profile_manager.data.scenario_discovery import scenario_discovery

    rows = {row["id"]: row for row in _discovery_catalog()}
    amaru_ids = {
        row["id"] for row in rows.values()
        if "amaru" in row["implementation_participants"]
    }
    assert len(amaru_ids) == 114

    mixed_substrate = rows[
        "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
    ]
    assert mixed_substrate["implementation_participants"] == ["amaru", "cardano-node"]
    assert mixed_substrate["implementation_facets"] == ["amaru", "cardano-node", "mixed"]
    assert mixed_substrate["topology_scope_key"] == "mixed"
    assert mixed_substrate["primary_target_key"] == "amaru"

    attached = rows["consensus-state-lifecycle-bootstrap-differential"]
    assert attached["implementation_participants"] == ["amaru", "cardano-node"]
    assert attached["topology_scope_key"] == "mixed"
    assert attached["primary_target_key"] == "cardano-node"

    adapter = rows["client-example-plutus-vm-cardano"]
    assert adapter["implementation_participants"] == ["amaru", "cardano-node"]
    assert adapter["implementation_facets"] == ["amaru", "cardano-node"]
    assert adapter["topology_scope_key"] == "cardano-only"

    library = rows["amaru-cbor-block-fuzz"]
    assert library["implementation_participants"] == ["amaru"]
    assert library["topology_scope_key"] == "standalone-library"

    target_only_devnets = [
        row for row in rows.values()
        if row["primary_target_key"] == "amaru"
        and not row.get("profile")
        and row.get("runtime") == "devnet"
    ]
    assert len(target_only_devnets) == 32
    assert {row["topology_scope_key"] for row in target_only_devnets} == {"mixed"}

    misleading = scenario_discovery(
        {
            "id": "amaru-name-is-not-participation",
            "title": "Amaru-tagged Cardano scenario",
            "runtime": "devnet",
            "target_impl": "cardano-node",
            "target_version": "1",
            "target": {"implementation": "cardano-node", "version": "1"},
            "profile": "cardano-only",
            "substrate": {},
            "attached_topology": None,
            "adapter_implementations": [],
            "tags": ["amaru"],
            "authors": [],
            "phase_primitives": {key: [] for key in ("setup", "load", "faults", "probes", "assertions", "teardown")},
            "assertion_primitives": [],
            "expected_security_finding": None,
            "evidence_intent": None,
            "promotion_blockers": [],
            "measurement_profile": None,
        },
        profile_shapes={"cardano-only": (1, 0)},
    )
    assert misleading["implementation_participants"] == ["cardano-node"]
    assert "amaru" not in misleading["implementation_facets"]

    unresolved = deepcopy(misleading)
    unresolved["id"] = "known-target-with-missing-profile"
    unresolved["profile"] = "missing-profile"
    unresolved = scenario_discovery(unresolved, profile_shapes={})
    assert unresolved["primary_target_key"] == "cardano-node"
    assert unresolved["topology_scope_key"] == "unresolved"
    assert unresolved["implementation_participants"] == ["other"]
    assert unresolved["implementation_facets"] == ["other"]

    library_fallback = deepcopy(misleading)
    library_fallback["id"] = "known-library-target"
    library_fallback["runtime"] = "library"
    library_fallback["profile"] = None
    library_fallback = scenario_discovery(library_fallback, profile_shapes={})
    assert library_fallback["implementation_participants"] == ["cardano-node"]


def test_participant_facet_counts_overlap_without_duplicating_scenarios():
    from profile_manager.data.scenario_discovery import scenario_discovery_facets

    facets = scenario_discovery_facets(_discovery_catalog())
    implementations = {
        option["value"]: option for option in facets["implementation"]
    }
    assert implementations == {
        "amaru": {"value": "amaru", "label": "Amaru participant", "count": 114},
        "cardano-node": {
            "value": "cardano-node",
            "label": "Cardano participant",
            "count": 305,
        },
        "mixed": {"value": "mixed", "label": "Mixed topology", "count": 67},
        "other": {"value": "other", "label": "Other", "count": 6},
    }
    amaru_rows = [
        row for row in _discovery_catalog()
        if "amaru" in row["implementation_participants"]
    ]
    assert sum(
        row["maintenance"]["state"] == "hidden-by-default" for row in amaru_rows
    ) == 4
    assert sum(row["maintenance"]["state"] == "shown" for row in amaru_rows) == 110


def test_security_family_copy_uses_workloads_and_checks_instead_of_repeating_legacy_titles():
    rows = {row["id"]: row for row in _discovery_catalog()}

    afl = rows["cardano-node-cov-applyblock-aflpp-smoke"]
    assert afl["friendly_title"] == "AFL++ Conway applyBlock coverage campaign"
    assert afl["implementation_key"] == "cardano-node"
    assert afl["execution_type"] == "fuzz"
    assert afl["purpose"] == (
        "Feeds generated Conway blocks to the existing AFL++ harness and records "
        "coverage and crash outcomes."
    )
    assert afl["security_check"] == (
        "Checks that the fuzzing campaign met its declared progress and artifact "
        "floors; a clean exit is not proof that no vulnerability exists."
    )

    afl_handshake = rows["cardano-node-cov-handshake-aflpp-smoke"]
    assert afl_handshake["purpose"] == (
        "Feeds generated inputs to the existing AFL++ harness and records coverage and "
        "crash outcomes."
    )

    amaru_fuzz = rows["amaru-cbor-block-fuzz"]
    assert amaru_fuzz["implementation_key"] == "amaru"
    assert amaru_fuzz["execution_type"] == "fuzz"
    assert amaru_fuzz["purpose"] == (
        "Mutates encoded blocks and feeds them to Amaru's parser to exercise "
        "controlled rejection and round-trip behavior."
    )
    assert amaru_fuzz["security_check"] == (
        "Checks that each input either parses or returns a controlled error, and "
        "that accepted inputs encode back to their original bytes."
    )

    calibration = rows["amaru-measurement-overhead-calibration-stock"]
    assert calibration["purpose"] == (
        "Measures DWARF telemetry overhead during a repeated real-node Amaru workload."
    )
    assert calibration["security_check"] == (
        "Checks that the calibration workload produced the required successful load "
        "event; it does not establish a security finding."
    )

    containment = rows["client-example-invalid-mini-protocol-amaru"]
    assert containment["friendly_title"] == "Card 03 · Invalid Handshake containment"
    assert "valid and invalid mini-protocol cases" in containment["purpose"]
    assert "invalid cases remain contained" in containment["security_check"]

    eclipse = rows["runtime-substrate-eclipse-topology-example-smoke"]
    assert eclipse["friendly_title"] == "Eclipse and Sybil resilience"
    assert "Peer-set capture" in eclipse["purpose"]
    assert eclipse["family_title"] == "Eclipse and Sybil resilience"


def test_missing_linked_evidence_does_not_claim_a_scenario_never_ran():
    rows = {row["id"]: row for row in _discovery_catalog()}

    evidence = rows["amaru-cbor-block-fuzz"]["evidence"]
    assert evidence == {
        "state": "no-retained-proof",
        "reason": (
            "No execution evidence is linked here; this does not mean this scenario "
            "was never run."
        ),
        "run_id": None,
    }


def test_representative_security_goal_copy_explains_user_visible_intent():
    rows = {row["id"]: row for row in _discovery_catalog()}
    representatives = {
        "peer-isolation": (
            "client-example-invalid-mini-protocol-amaru",
            "Sends valid and invalid mini-protocol cases",
            "invalid cases remain contained",
        ),
        "malformed-input": (
            "amaru-cbor-block-fuzz",
            "feeds them to Amaru's parser",
            "returns a controlled error",
        ),
        "conformance": (
            "amaru-cbor-block-fuzz",
            "round-trip behavior",
            "encode back to their original bytes",
        ),
        "consensus-ledger": (
            "cardano-amaru-cbor-dataset-plutus-data-differential",
            "Runs the qualified Plutus-data CBOR corpus",
            "both implementations complete the declared dataset comparison",
        ),
        "resource-dos": (
            "client-example-txsubmission-window-amaru-portable-20260918",
            "Applies bounded TxSubmission pressure",
            "submission window remains bounded",
        ),
        "recovery": (
            "client-example-restart-recovery-sync-amaru",
            "Restarts the selected node",
            "listener, chain-progress, peer-role and synchronization gates",
        ),
        "exploit-resilience": (
            "runtime-substrate-panic-path-containment-example-smoke",
            "Exercises the declared panic path",
            "panic remains contained",
        ),
        "evidence-integrity": (
            "runtime-bundle-timeline-example-smoke",
            "Builds an ordered event timeline from a retained run bundle",
            "ordered timeline is emitted cleanly",
        ),
    }

    for goal, (scenario_id, purpose_fragment, check_fragment) in representatives.items():
        row = rows[scenario_id]
        assert goal in row["security_goals"]
        assert purpose_fragment in row["purpose"]
        assert check_fragment in row["security_check"]
        combined = f'{row["purpose"]} {row["security_check"]}'
        assert "Runtime " not in combined
        assert "Load events are ok" not in combined
        assert "_" not in combined


def test_stock_measurement_cards_explain_pipeline_and_calibration_boundaries():
    rows = {row["id"]: row for row in _discovery_catalog()}

    for implementation in ("Amaru", "Cardano"):
        prefix = implementation.casefold()
        if implementation == "Cardano":
            prefix = "cardano"
        e2e = rows[f"{prefix}-measurement-e2e-stock"]
        assert e2e["purpose"] == (
            f"Runs a real-node {implementation} workload and collects the configured "
            "DWARF measurements end to end."
        )
        assert e2e["security_check"] == (
            "Checks that the measurement pipeline recorded at least one successful "
            "workload event; it does not establish a security finding."
        )

        calibration = rows[f"{prefix}-measurement-overhead-calibration-stock"]
        assert calibration["purpose"] == (
            f"Measures DWARF telemetry overhead during a repeated real-node "
            f"{implementation} workload."
        )
        assert calibration["security_check"] == (
            "Checks that the calibration workload produced the required successful load "
            "event; it does not establish a security finding."
        )


def test_specialized_copy_does_not_claim_undeclared_secondary_checks():
    from profile_manager.data.scenario_discovery import scenario_discovery

    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in load_profiles()
    }
    source = _catalog_by_id()
    cases = (
        (
            "client-example-invalid-mini-protocol-amaru",
            ["invalid_protocol_cases_contained"],
            (),
            "unrelated peer session stays usable",
        ),
        (
            "client-example-block-application-amaru-canonical-v3",
            ["canonical_chain_progress_complete"],
            (),
            "block-application samples agree",
        ),
        (
            "client-example-txsubmission-window-amaru-portable-20260918",
            ["txsubmission_window_enforced"],
            (),
            "unrelated peer session stays usable",
        ),
        (
            "client-example-restart-recovery-sync-amaru",
            ["restart_readiness_complete", "target_progress_continues"],
            ("runtime_controlled_sync_range",),
            "synchronization gates recover",
        ),
        (
            "runtime-substrate-panic-path-containment-example-smoke",
            ["panic_path_contained"],
            (),
            "every node remains responsive",
        ),
        (
            "runtime-substrate-panic-path-containment-example-smoke",
            ["panic_path_contained", "all_nodes_responsive"],
            ("runtime_compose_substrate",),
            "isolated multi-node substrate",
        ),
    )

    for scenario_id, assertions, removed_workloads, forbidden_claim in cases:
        entry = deepcopy(source[scenario_id])
        entry["assertion_primitives"] = list(assertions)
        entry["phase_primitives"]["assertions"] = list(assertions)
        for phase in ("setup", "load", "faults", "probes"):
            entry["phase_primitives"][phase] = [
                primitive
                for primitive in entry["phase_primitives"][phase]
                if primitive not in removed_workloads
            ]
        row = scenario_discovery(entry, profile_shapes=profile_shapes)
        assert forbidden_claim not in f'{row["purpose"]} {row["security_check"]}'


def test_reader_context_links_preserve_historical_scope_without_claiming_current_coverage():
    rows = {row["id"]: row for row in _discovery_catalog()}

    cbor = rows["client-example-cbor-decoding-amaru-d3a6dafc-regression"]
    assert any("historical pinned CBOR" in item["scope"] for item in cbor["context_links"])

    plutus = rows["client-example-plutus-vm-amaru-onchain-v2"]
    assert any("topology capability" in item["scope"] for item in plutus["context_links"])
    assert any("not a node vulnerability claim" in item["scope"].casefold() for item in plutus["context_links"])

    forks = rows["client-example-block-application-amaru-canonical-v3"]
    assert any("corrected canonical-progress contract" in item["scope"] for item in forks["context_links"])


def test_security_fuzz_named_smoke_remains_visible_and_real_baseline_is_hidden():
    rows = {row["id"]: row for row in _discovery_catalog()}

    fuzz = rows["cardano-node-cov-applyblock-aflpp-smoke"]
    assert fuzz["execution_type"] == "fuzz"
    assert fuzz["maintenance"] == {"state": "shown", "reason": "security workload"}
    assert "malformed-input" in fuzz["security_goals"]

    containment = rows["runtime-substrate-panic-path-containment-example-smoke"]
    assert containment["maintenance"]["state"] == "shown"
    assert "exploit-resilience" in containment["security_goals"]

    baseline = rows["runtime-substrate-multihost-honest-baseline-example-smoke"]
    assert baseline["maintenance"]["state"] == "hidden-by-default"
    assert "baseline" in baseline["maintenance"]["reason"]

    formerly_false_hidden = {
        "amaru-measurement-boundary-control-stock",
        "amaru-n2n-handshake-version-table-forward-compat-20260918-mixed-1112",
        "phase3-runtime-haskell-preview-baseline-drop-recheck-capability-demo",
        "runtime-substrate-eclipse-topology-example-smoke",
        "runtime-substrate-large-50-node-honest-mesh-example-smoke",
    }
    assert all(
        rows[scenario_id]["maintenance"]["state"] == "shown"
        for scenario_id in formerly_false_hidden
    )
    assert sum(
        row["maintenance"]["state"] == "hidden-by-default"
        for row in rows.values()
    ) == 19


def test_unknown_metadata_fails_open_without_claiming_support():
    from profile_manager.data.scenario_discovery import (
        scenario_discovery,
        validate_catalog_discovery,
    )

    row = scenario_discovery(
        {
            "id": "future-shape",
            "title": "Future catalog shape",
            "runtime": "future-runtime",
            "target_impl": "future-node",
            "target_version": "experimental",
            "target": {"implementation": "future-node", "version": "experimental"},
            "profile": None,
            "substrate": {},
            "tags": [],
            "authors": [],
            "phase_primitives": {key: [] for key in ("setup", "load", "faults", "probes", "assertions", "teardown")},
            "assertion_primitives": [],
            "expected_security_finding": None,
            "evidence_intent": None,
            "promotion_blockers": [],
            "measurement_profile": None,
        },
        profile_shapes={},
    )

    assert row["implementation_key"] == "other"
    assert row["implementation_participants"] == ["other"]
    assert row["implementation_facets"] == ["other"]
    assert row["execution_type"] == "live"
    assert row["maintenance"]["state"] == "shown"
    assert row["compatibility"]["state"] == "unknown"
    assert row["local_capability"]["state"] == "check-on-selection"
    assert row["subject_path"] == ["utilities"]
    assert row["threat_model_ids"] == ["unmapped"]
    assert row["risk_register_ids"] == ["unmapped"]
    validate_catalog_discovery([row], expected_scenario_ids={"future-shape"})

    invalid = deepcopy(row)
    invalid["implementation_participants"] = []
    with pytest.raises(ValueError, match="invalid implementation participants"):
        validate_catalog_discovery([invalid], expected_scenario_ids={"future-shape"})


def test_family_groups_conserve_exact_variants_without_identity_substitution():
    from profile_manager.data.scenario_discovery import group_scenario_families

    rows = _discovery_catalog()
    families = group_scenario_families(rows)

    assert sum(len(family["variants"]) for family in families) == 351
    assert {variant["id"] for family in families for variant in family["variants"]} == {
        row["id"] for row in rows
    }
    for family in families:
        for variant in family["variants"]:
            assert variant["selection_id"] == variant["id"]


def test_run_and_scenario_catalog_render_the_same_security_first_discovery_contract():
    run_html = dashboard.render_route_html("/run")
    catalog_html = render_operate_scenarios()
    learn_html = dashboard.render_route_html("/learn")
    threat_coverage_html = dashboard.render_route_html("/learn/threat-coverage")

    for html in (run_html, catalog_html):
        assert 'data-scenario-discovery' in html
        assert 'data-discovery-query' in html
        assert 'data-discovery-show-maintenance' in html
        assert 'data-discovery-clear' in html
        assert 'data-discovery-count' in html
        assert 'data-facet-group="implementation"' in html
        assert 'data-facet-group="execution"' in html
        assert 'data-facet-group="goal"' in html
        assert 'data-facet-group="compatibility"' in html
        assert 'data-facet-group="capability"' in html
        assert 'data-facet-group="evidence"' in html
        assert 'data-facet-group="threat_model"' in html
        assert 'data-facet-group="risk_register"' in html
        assert 'data-facet-option-search="threat_model"' in html
        assert 'data-facet-option-search="risk_register"' in html
        assert "Threat Model v2" not in html
        assert "Risk Register v2" not in html
        assert "Threat Model" in html
        assert "Risk Register" in html
        assert "Choose Threat Model entries" in html
        assert "Choose Risk Register entries" in html
        assert '<summary>More filters</summary>' in html
        assert '/static/js/scenario-discovery.js' in html
        assert 'data-scenario-purpose' in html
        assert 'data-scenario-check' in html
        assert 'data-scenario-family' in html
        assert 'data-maintenance="hidden-by-default"' in html
        assert "Participant counts overlap" in html
        assert "Defensive context:" not in html

    assert "Invalid Handshake containment" in run_html
    assert "Invalid Handshake containment" in catalog_html
    assert 'class="scenario-picker__id"' not in run_html
    assert 'class="scenario-discovery__id"' not in catalog_html
    assert run_html.count('data-scenario-disclosure="subject:') == 8
    assert catalog_html.count('data-scenario-disclosure="subject:') == 8
    assert run_html.count('data-scenario-disclosure="family:') == 201
    assert catalog_html.count('data-scenario-disclosure="family:') == 201
    assert run_html.count("data-scenario-card") == 351
    assert catalog_html.count("data-scenario-card") == 351
    assert '<details class="scenario-discovery__family"' in run_html
    assert '<details class="scenario-discovery__family"' in catalog_html
    assert '<span>CBOR</span>' in run_html
    assert '<h3>TX Body</h3>' in run_html
    assert '<span>Opcert</span>' in catalog_html
    assert '<span>Measurement validation</span>' in run_html
    assert '<span>Measurement validation</span>' in catalog_html
    assert '<span>Measurements</span>' not in run_html
    assert '<span>Measurements</span>' not in catalog_html
    assert "Related context" in catalog_html
    assert re.search(
        r'data-scenario-id="cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"[^>]*data-facet-implementation="amaru cardano-node mixed"',
        run_html,
    )
    assert "Risk Register × Threat Model × scenarios" in learn_html
    assert "Threat Model v2" not in learn_html
    assert "Risk Register v2" not in learn_html
    assert "<b>Risk Register</b>" in threat_coverage_html
    assert "<b>Threat Model</b>" in threat_coverage_html
    assert "Threat Model v2" not in threat_coverage_html
    assert "Risk Register v2" not in threat_coverage_html
    assert "JG-RiskRegister-V2.csv" in threat_coverage_html
    assert "JG-Threatmodel-V2.docx" in threat_coverage_html


def test_shared_filter_controller_implements_and_between_facets_or_within_and_health_opt_in():
    source = Path(
        "dwarf/dashboard/static/js/scenario-discovery.js"
    ).read_text(encoding="utf-8")

    assert "export function scenarioMatchesDiscovery" in source
    assert "selected.some" in source
    assert "facetGroups.every" in source
    assert "showMaintenance" in source
    assert "hidden-by-default" in source
    assert "sessionStorage" in source
    assert "data-scenario-family-group" in source

    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    program = f"""
      const mod = await import('data:text/javascript;base64,{encoded}');
      const row = {{
        maintenance: 'shown', search: 'peer recovery amaru',
        facets: {{implementation: 'amaru', execution: 'live', goal: 'peer-isolation recovery'}}
      }};
      const state = {{showMaintenance: false, query: 'peer', facets: {{
        implementation: ['amaru', 'mixed'], execution: ['live'], goal: ['recovery'],
      }}}};
      if (!mod.scenarioMatchesDiscovery(row, state)) process.exit(11);
      state.facets.execution = ['fuzz'];
      if (mod.scenarioMatchesDiscovery(row, state)) process.exit(12);
      state.facets.execution = [];
      row.maintenance = 'hidden-by-default';
      if (mod.scenarioMatchesDiscovery(row, state)) process.exit(13);
      state.showMaintenance = true;
      if (!mod.scenarioMatchesDiscovery(row, state)) process.exit(14);
      if (!mod.familyShouldOpen({{query: 'exact-id', containsSelected: false, currentlyOpen: false, manuallyClosed: false}})) process.exit(15);
      if (mod.familyShouldOpen({{query: '', containsSelected: true, currentlyOpen: false, manuallyClosed: false}})) process.exit(16);
      if (mod.familyShouldOpen({{query: '', containsSelected: false, currentlyOpen: false, manuallyClosed: false}})) process.exit(17);
      if (mod.familyShouldOpen({{query: 'exact-id', containsSelected: false, currentlyOpen: true, manuallyClosed: true}})) process.exit(18);
      if (mod.familyShouldOpen({{query: '', containsSelected: true, currentlyOpen: true, manuallyClosed: true}})) process.exit(19);
    """
    subprocess.run(
        ["node", "--input-type=module", "-e", program],
        check=True,
        capture_output=True,
        text=True,
    )


def test_requested_exact_variant_stays_selected_without_forcing_family_open():
    scenario_id = "client-example-invalid-mini-protocol-amaru"
    html = dashboard.render_route_html(f"/run?scenario={scenario_id}")
    source = Path("dwarf/dashboard/static/js/scenario-discovery.js").read_text(
        encoding="utf-8"
    )

    opened = re.findall(
        r'<details class="scenario-discovery__family"[^>]*\sopen(?:\s|>)', html
    )
    assert len(opened) == 0
    assert f'data-scenario-id="{scenario_id}"' in html
    assert "family.open" in source
    assert "family.addEventListener('toggle'" in source
    assert "sessionStorage" in source


def test_browser_family_disclosure_search_exact_selection_and_back_state():
    subprocess.run(
        ["node", "tests/browser/test_scenario_discovery.cjs"],
        check=True,
        capture_output=True,
        text=True,
    )
