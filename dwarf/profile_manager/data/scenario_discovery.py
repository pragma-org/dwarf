"""Security-first presentation metadata for the native scenario catalog.

This module classifies parsed catalog records for discovery only.  It never
changes scenario identity, availability, resolution, or execution.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from profile_manager.data.scenario_cards import scenario_card


_PHASES = ("setup", "load", "faults", "probes", "assertions", "teardown")
_IMPLEMENTATIONS = {"amaru", "cardano-node", "mixed", "other"}
_EXECUTION_TYPES = {"live", "standalone-library", "fuzz", "evidence-utility"}
_SUBJECTS = (
    ("cbor", "CBOR"),
    ("n2n", "N2N"),
    ("opcert", "Opcert"),
    ("plutus", "Plutus"),
    ("consensus", "Consensus"),
    ("recovery", "Recovery"),
    ("measurements", "Measurement validation"),
    ("utilities", "Utilities"),
)
RECOMMENDED_SCENARIO_IDS = {"cardano-measurement-e2e-stock"}
RETAINED_SCENARIO_EVIDENCE = {
    "client-example-cbor-decoding-amaru-d3a6dafc-regression": "20260920T235440Z-050046a4",
    "client-example-cbor-decoding-cardano-patched": "20260920T132629Z-ea000d37",
    "client-example-plutus-vm-amaru-onchain-v2": "20260921T204537Z-ff5a800a",
    "client-example-plutus-vm-cardano": "20260921T202611Z-27eeadb5",
    "client-example-invalid-mini-protocol-amaru": "20260920T072858Z-2cc3bb0c",
    "client-example-invalid-mini-protocol-cardano": "20260920T073447Z-ab81bfb7",
    "client-example-block-application-amaru-canonical-v3": "20260921T035546Z-9747122c",
    "client-example-block-application-cardano-canonical-v2": "20260921T021935Z-3b58eafc",
    "client-example-restart-recovery-sync-amaru": "20260921T045619Z-15e864a0",
    "client-example-restart-recovery-sync-cardano": "20260921T045807Z-8e2bbb0e",
    "client-example-simple-transfer-amaru": "20260921T194749Z-26542a57",
    "client-example-simple-transfer-cardano": "20260921T195334Z-e713d0de",
}
_GENERIC_FAMILY_PRIMITIVES = {
    "runtime_verify_exact_target",
    "runtime_wait_for_chain_progress",
    "runtime_multi_node_observation",
    "all_nodes_responsive",
    "no_target_fatal_signal",
}

_GOAL_TERMS = (
    (
        "peer-isolation",
        ("peer", "blockfetch", "chainsync", "handshake", "mini-protocol", "mini_protocol", "mux", "n2n"),
    ),
    (
        "malformed-input",
        ("fuzz", "afl", "parser", "cbor", "malformed", "invalid", "decode", "deserial", "serdes"),
    ),
    (
        "conformance",
        ("conformance", "differential", "roundtrip", "parity", "outcome_matches", "rules"),
    ),
    (
        "consensus-ledger",
        ("consensus", "ledger", "block_apply", "chain_select", "epoch", "opcert", "kes", "plutus"),
    ),
    (
        "resource-dos",
        ("resource", "cpu", "ram", "disk", "starvation", "pressure", "overrun", "exhaust", "large-n"),
    ),
    (
        "recovery",
        ("recovery", "restart", "rollback", "resume", "restore", "remediation", "reconnect"),
    ),
    (
        "exploit-resilience",
        ("panic", "contain", "crash", "byzantine", "corrupt", "fatal", "overflow", "forg", "attack"),
    ),
    (
        "evidence-integrity",
        ("bundle", "attestation", "evidence", "timeline", "manifest", "signature", "sarif", "forensic"),
    ),
)

_DEFENSIVE_TERMS = (
    "adversarial",
    "attack",
    "byzantine",
    "contain",
    "conformance",
    "corrupt",
    "differential",
    "eclipse",
    "finding validation",
    "fuzz",
    "invalid",
    "malformed",
    "measurement boundary",
    "overflow",
    "panic",
    "pressure",
    "recovery",
    "restart",
    "risk-support",
    "rollback",
    "starvation",
    "sybil",
)
_MAINTENANCE_TERMS = ("baseline", "calibration", "health-check", "health check", "measurement-overhead")
_HISTORICAL_CONTEXT = {
    "client-example-cbor-decoding-amaru-d3a6dafc-regression": {
        "label": "DWARF CBOR finding and fixed regression history",
        "href": "/learn/measurements#five-client-examples",
        "scope": "Exact historical pinned CBOR failure and fixed-revision evidence; not a claim about every Amaru release.",
    },
    "client-example-plutus-vm-amaru-onchain-v2": {
        "label": "DWARF Plutus V2 topology resolution history",
        "href": "/learn/measurements#five-client-examples",
        "scope": "Historical topology capability gap resolved by an additive profile; not a node vulnerability claim.",
    },
    "client-example-block-application-amaru-canonical-v3": {
        "label": "DWARF block-application fork history",
        "href": "/learn/measurements#five-client-examples",
        "scope": "Historical strict-monotonic false failure and corrected canonical-progress contract with retained verdicts.",
    },
}


def _words(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip()


def _sentence(prefix: str, value: str) -> str:
    body = value.strip().rstrip(". ")
    return f"{prefix} {body}." if body else f"{prefix}."


def _humanize_primitive(value: str) -> str:
    text = _words(value)
    replacements = {
        "cbor": "CBOR",
        "aflpp": "AFL++",
        "n2n": "N2N",
        "plutus": "Plutus",
        "txsubmission": "TxSubmission",
        "blockfetch": "BlockFetch",
        "chainsync": "ChainSync",
        "opcert": "OpCert",
        "kes": "KES",
    }
    return " ".join(replacements.get(word, word) for word in text.split())


_PRIMITIVE_LABELS = {
    "runtime_aflpp_campaign": "AFL++ coverage campaign",
    "runtime_protocol_decode_cases": "Protocol decode cases",
    "runtime_simulate_peer_set_capture": "Peer-set capture",
    "runtime_inject_hot_warm_churn": "Hot/warm peer churn",
    "runtime_perturb_ledger_peer_weights": "Ledger-peer weight perturbation",
    "runtime_substitute_big_ledger_peers": "Big-ledger-peer substitution",
    "runtime_real_target_restart_and_readiness": "Real target restart and readiness",
    "block_apply_differential": "Block application differential",
    "serve_crafted_block": "Crafted block delivery",
    "runtime_bundle_timeline": "Evidence-bundle timeline",
    "cbor_fuzz_target": "CBOR fuzzing",
}


def _primitive_label(value: str) -> str:
    label = _PRIMITIVE_LABELS.get(value, _humanize_primitive(value))
    return label[:1].upper() + label[1:]


def _workload_primitives(phase_primitives: dict[str, list[str]]) -> list[str]:
    ignored = {
        "runtime_verify_exact_target",
        "runtime_mark_baseline_window",
        "runtime_mark_hostile_window",
        "runtime_mark_recovery_window",
        "runtime_multi_node_observation",
        "runtime_install_version",
        "runtime_compose_substrate",
        "runtime_teardown_substrate",
    }
    ordered = []
    for phase in ("load", "faults", "probes", "setup"):
        for primitive in phase_primitives.get(phase) or []:
            if primitive not in ignored and primitive not in ordered:
                ordered.append(primitive)
    return ordered


def _intent_title(
    entry: dict[str, Any], card_title: str, workload: list[str]
) -> str:
    tags = {str(value).casefold() for value in entry.get("tags") or []}
    if "runtime_aflpp_campaign" in workload and "applyblock" in tags:
        return "AFL++ Conway applyBlock coverage campaign"
    if {"eclipse", "sybil"} <= tags:
        return "Eclipse and Sybil resilience"
    if len(card_title) <= 110:
        return card_title
    declared = str(entry.get("title") or "").strip().rstrip(".")
    prefix = declared.split(":", 1)[0]
    prefix = re.sub(r"(?i)^ledger block-apply differential\s*[-—]\s*", "Block application · ", prefix)
    prefix = re.sub(r"(?i)^ledger epoch-boundary differential\s*[-—]\s*", "Epoch boundary · ", prefix)
    if len(prefix) <= 110:
        return prefix
    primary = _primitive_label(workload[0]) if workload else "Scenario workload"
    implementation = str(entry.get("target_impl") or "").replace("cardano-node", "Cardano")
    return f"{primary} · {implementation or 'framework'}"


def _semantic_text(entry: dict[str, Any]) -> str:
    phase_primitives = entry.get("phase_primitives") or {}
    values = [
        entry.get("title") or "",
        entry.get("evidence_intent") or "",
        entry.get("expected_security_finding") or "",
        *(entry.get("tags") or []),
        *(entry.get("promotion_blockers") or []),
    ]
    for phase in _PHASES:
        values.extend(phase_primitives.get(phase) or [])
    return " ".join(str(value).casefold() for value in values if value)


def _implementation_context(
    entry: dict[str, Any], profile_shapes: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    target_key = str(entry.get("target_impl") or "")
    target_label = {
        "amaru": "Amaru",
        "cardano-node": "Cardano",
    }.get(target_key, "Other")
    physical: set[str] = set()
    profile_id = entry.get("profile") if isinstance(entry.get("profile"), str) else None
    if profile_id and profile_id in profile_shapes:
        cardano_count, amaru_count = profile_shapes[profile_id]
        if amaru_count:
            physical.add("amaru")
        if cardano_count:
            physical.add("cardano-node")

    substrate = entry.get("substrate") if isinstance(entry.get("substrate"), dict) else {}
    nodes = substrate.get("nodes") if isinstance(substrate.get("nodes"), list) else []
    for node in nodes:
        implementation = node.get("impl") if isinstance(node, dict) else None
        if implementation in {"amaru", "cardano-node"}:
            physical.add(implementation)

    if entry.get("attached_topology") == "cardano_amaru":
        physical.update({"amaru", "cardano-node"})

    adapters = {
        str(value)
        for value in entry.get("adapter_implementations") or []
        if value in {"amaru", "cardano-node"}
    }
    participants = physical | adapters
    runtime = str(entry.get("runtime") or "")
    if (
        not participants
        and runtime in {"library", "single-node"}
        and target_key in {"amaru", "cardano-node"}
    ):
        participants.add(target_key)
    if physical == {"amaru", "cardano-node"}:
        scope_key, scope_label = "mixed", "Mixed topology"
    elif physical == {"amaru"}:
        scope_key, scope_label = "amaru-only", "Amaru-only topology"
    elif physical == {"cardano-node"}:
        scope_key, scope_label = "cardano-only", "Cardano-only topology"
    elif runtime == "library":
        scope_key, scope_label = "standalone-library", "Standalone library"
    elif runtime == "single-node":
        scope_key, scope_label = "single-node", "Single-node tool"
    elif profile_id:
        scope_key, scope_label = "unresolved", "Unresolved topology"
    else:
        scope_key, scope_label = "other", "Other topology"

    if scope_key == "mixed":
        implementation_key = "mixed"
    elif physical == {"amaru"} or (not physical and participants == {"amaru"}):
        implementation_key = "amaru"
    elif physical == {"cardano-node"} or (
        not physical and participants == {"cardano-node"}
    ):
        implementation_key = "cardano-node"
    else:
        implementation_key = "other"

    ordered_participants = [
        value for value in ("amaru", "cardano-node") if value in participants
    ]
    if not ordered_participants:
        ordered_participants = ["other"]
    facets = [*ordered_participants]
    if scope_key == "mixed":
        facets.append("mixed")
    return {
        "implementation_key": implementation_key,
        "implementation": scope_label,
        "implementation_participants": ordered_participants,
        "implementation_facets": facets,
        "topology_scope_key": scope_key,
        "topology_scope": scope_label,
        "primary_target_key": target_key or "other",
        "primary_target": target_label,
    }


def _reader_copy(
    entry: dict[str, Any], workload: list[str], assertions: list[str]
) -> tuple[str, str]:
    workload_set = set(workload)
    assertion_set = set(assertions)
    tags = {str(value).casefold() for value in entry.get("tags") or []}
    declared_primitives = {
        str(primitive)
        for phase in _PHASES
        for primitive in ((entry.get("phase_primitives") or {}).get(phase) or [])
    }

    measurement_implementation = next(
        (
            label
            for primitive, label in (
                ("runtime_amaru_measurement_calibration", "Amaru"),
                ("runtime_cardano_measurement_calibration", "Cardano"),
            )
            if primitive in workload_set
        ),
        None,
    )
    if measurement_implementation and assertion_set == {"load_events_are_ok"}:
        if "end-to-end" in tags:
            return (
                f"Runs a real-node {measurement_implementation} workload and collects "
                "the configured DWARF measurements end to end.",
                "Checks that the measurement pipeline recorded at least one successful "
                "workload event; it does not establish a security finding.",
            )
        if {"measurement", "calibration"} <= tags:
            return (
                "Measures DWARF telemetry overhead during a repeated real-node "
                f"{measurement_implementation} workload.",
                "Checks that the calibration workload produced the required successful "
                "load event; it does not establish a security finding.",
            )
    if (
        "runtime_protocol_decode_cases" in workload_set
        and {
            "invalid_protocol_cases_contained",
            "target_progress_continues",
            "unrelated_peer_session_usable",
        } <= assertion_set
        and {
            "runtime_mark_hostile_window",
            "runtime_mark_recovery_window",
            "runtime_target_health_and_progress",
            "runtime_peer_session_health",
        } <= declared_primitives
    ):
        return (
            "Sends valid and invalid mini-protocol cases through the selected node while "
            "observing hostile and recovery windows.",
            "Checks that invalid cases remain contained, the selected node keeps "
            "progressing, and an unrelated peer session stays usable.",
        )
    if (
        "runtime_controlled_chain_progress_window" in workload_set
        and {
            "canonical_chain_progress_complete",
            "block_application_samples_correlated",
            "target_progress_continues",
        } <= assertion_set
        and "runtime_target_health_and_progress" in declared_primitives
    ):
        return (
            "Advances a controlled chain window and correlates block-application "
            "telemetry with canonical progress.",
            "Checks that canonical progress and block-application samples agree while "
            "the target remains live.",
        )
    if (
        "runtime_txsubmission_window_pressure" in workload_set
        and {
            "txsubmission_window_enforced",
            "target_progress_continues",
            "unrelated_peer_session_usable",
        } <= assertion_set
        and {
            "runtime_mark_recovery_window",
            "runtime_target_health_and_progress",
            "runtime_peer_session_health",
        } <= declared_primitives
    ):
        return (
            "Applies bounded TxSubmission pressure while observing target recovery and "
            "an unrelated peer session.",
            "Checks that the submission window remains bounded, the target keeps "
            "progressing, and an unrelated peer session stays usable.",
        )
    if (
        "runtime_real_target_restart_and_readiness" in workload_set
        and "runtime_controlled_sync_range" in workload_set
        and {
            "restart_readiness_complete",
            "controlled_sync_range_complete",
            "target_progress_continues",
        } <= assertion_set
        and {
            "runtime_target_health_and_progress",
            "runtime_peer_session_health",
        } <= declared_primitives
    ):
        return (
            "Restarts the selected node and follows its listener, chain progress, peer "
            "role and controlled synchronization recovery.",
            "Checks that listener, chain-progress, peer-role and synchronization gates "
            "recover while the target stays live.",
        )
    if (
        "runtime_panic_path_probe" in workload_set
        and {"panic_path_contained", "all_nodes_responsive"} <= assertion_set
        and {
            "runtime_compose_substrate",
            "runtime_multi_node_observation",
        } <= declared_primitives
    ):
        return (
            "Exercises the declared panic path inside an isolated multi-node substrate.",
            "Checks that every node remains responsive and the panic remains contained.",
        )
    if (
        "runtime_bundle_timeline" in workload_set
        and "bundle_timeline_emitted_clean" in assertion_set
    ):
        return (
            "Builds an ordered event timeline from a retained run bundle.",
            "Checks that the ordered timeline is emitted cleanly from the selected "
            "retained inputs.",
        )
    if (
        "runtime_cardano_cbor_dataset_differential" in workload_set
        and "cardano_cbor_dataset_differential_clean" in assertion_set
    ):
        return (
            "Runs the qualified Plutus-data CBOR corpus through the Cardano and Amaru "
            "decoders and compares their outcomes.",
            "Checks that both implementations complete the declared dataset comparison "
            "without an unexplained mismatch.",
        )
    if (
        "runtime_aflpp_campaign" in workload_set
        and "aflpp_smoke_exit_clean" in assertion_set
    ):
        campaign_inputs = "generated Conway blocks" if "applyblock" in tags else "generated inputs"
        return (
            f"Feeds {campaign_inputs} to the existing AFL++ harness and records coverage "
            "and crash outcomes.",
            "Checks that the fuzzing campaign met its declared progress and artifact "
            "floors; a clean exit is not proof that no vulnerability exists.",
        )
    if "cbor_fuzz_target" in workload_set and {
        "parse_succeeds_or_clean_error",
        "roundtrip_equals_original",
    } <= assertion_set:
        return (
            "Mutates encoded blocks and feeds them to Amaru's parser to exercise "
            "controlled rejection and round-trip behavior.",
            "Checks that each input either parses or returns a controlled error, and "
            "that accepted inputs encode back to their original bytes.",
        )
    if assertions:
        check_text = ", ".join(_primitive_label(value) for value in assertions[:4])
        if len(assertions) > 4:
            check_text += f", and {len(assertions) - 4} more declared checks"
        security_check = _sentence("Checks", check_text)
    else:
        security_check = "Checks that the declared workload completes without an unhandled failure."
    workload_text = ", ".join(_primitive_label(value) for value in workload[:3])
    if len(workload) > 3:
        workload_text += f", and {len(workload) - 3} more workload steps"
    check_summary = ", ".join(_primitive_label(value) for value in assertions[:2])
    if workload_text and check_summary:
        purpose = _sentence(f"Runs {workload_text} to evaluate", check_summary)
    elif workload_text:
        purpose = _sentence("Runs", workload_text)
    else:
        purpose = "Defines an exact catalog workflow whose local prerequisites are checked on selection."
    return purpose, security_check


def _execution_type(entry: dict[str, Any], semantic_text: str) -> str:
    tags = {str(value).casefold() for value in entry.get("tags") or []}
    phase_primitives = entry.get("phase_primitives") or {}
    primitives = {
        primitive.casefold()
        for phase in _PHASES
        for primitive in phase_primitives.get(phase) or []
    }
    if tags.intersection({"fuzz", "afl", "afl++"}) or any(
        "fuzz" in value or "afl" in value for value in primitives
    ):
        return "fuzz"
    if any(
        term in semantic_text
        for term in ("bundle timeline", "bundle_timeline", "attestation chain", "sarif", "forensic snapshot")
    ):
        return "evidence-utility"
    if entry.get("runtime") == "library":
        return "standalone-library"
    return "live"


def _security_goals(semantic_text: str, execution_type: str) -> list[str]:
    goals = [
        goal
        for goal, terms in _GOAL_TERMS
        if any(term in semantic_text for term in terms)
    ]
    if execution_type == "fuzz" and "malformed-input" not in goals:
        goals.append("malformed-input")
    return goals or ["unclassified"]


def _maintenance(
    semantic_text: str, execution_type: str, phase_primitives: dict[str, list[str]]
) -> dict[str, str]:
    if execution_type == "fuzz" or (phase_primitives.get("faults") or []):
        return {"state": "shown", "reason": "security workload"}
    if any(term in semantic_text for term in _DEFENSIVE_TERMS):
        return {"state": "shown", "reason": "security workload"}
    if "baseline" in semantic_text and "recheck" in semantic_text and any(
        term in semantic_text
        for term in (
            "upstream delay", "upstream drop", "upstream loss", "upstream reset",
            "upstream_delay", "upstream_drop", "upstream_loss", "upstream_reset",
            "upstream-delay", "upstream-drop", "upstream-loss", "upstream-reset",
        )
    ):
        return {"state": "shown", "reason": "fault/recovery workload"}
    if any(term in semantic_text for term in _MAINTENANCE_TERMS):
        return {
            "state": "hidden-by-default",
            "reason": "baseline/health maintenance check",
        }
    return {"state": "shown", "reason": "not classified as maintenance"}


def _compatibility(
    entry: dict[str, Any], profile_shapes: dict[str, tuple[int, int]]
) -> dict[str, str]:
    profile_id = entry.get("profile") if isinstance(entry.get("profile"), str) else None
    if profile_id and profile_id not in profile_shapes:
        return {"state": "profile-unavailable", "reason": "Referenced topology profile is not installed."}
    if profile_id:
        return {"state": "profile-declared", "reason": "A declared topology profile will be resolved exactly."}
    if entry.get("runtime") in {"library", "single-node"}:
        return {"state": "standalone", "reason": "No topology profile is required by this definition."}
    if entry.get("target_impl") in {"amaru", "cardano-node"}:
        return {"state": "resolved-on-selection", "reason": "Compatibility is resolved by the canonical Run plan after selection."}
    return {"state": "unknown", "reason": "No supported compatibility contract is declared."}


def _local_capability(entry: dict[str, Any], execution_type: str) -> dict[str, str]:
    tags = {str(value).casefold() for value in entry.get("tags") or []}
    if execution_type == "fuzz" and tags.intersection({"afl", "afl++", "coverage"}):
        return {"state": "host-harness-required", "reason": "Requires the existing host AFL/coverage harness; catalog presence is not execution proof."}
    if execution_type == "evidence-utility":
        return {"state": "retained-input-required", "reason": "Requires matching retained evidence inputs."}
    return {"state": "check-on-selection", "reason": "Local capability is checked by the canonical Run resolver."}


def _context_links(scenario_id: str) -> list[dict[str, str]]:
    links = []
    history = _HISTORICAL_CONTEXT.get(scenario_id)
    if history:
        links.append(dict(history))
    return links


def _family(
    entry: dict[str, Any], friendly_title: str, intent_title: str
) -> tuple[str, str]:
    phase_primitives = entry.get("phase_primitives") or {}
    semantic_primitives = []
    for phase in ("load", "faults", "assertions", "probes"):
        for primitive in phase_primitives.get(phase) or []:
            if primitive not in _GENERIC_FAMILY_PRIMITIVES and primitive not in semantic_primitives:
                semantic_primitives.append(primitive)
    signature = semantic_primitives[:3]
    if signature:
        key = "--".join(signature)
    else:
        key = re.sub(r"[^a-z0-9]+", "-", friendly_title.casefold()).strip("-") or "other"
    family_title = intent_title if signature else friendly_title
    return key, family_title


def _subject(entry: dict[str, Any], coverage_surfaces: Iterable[str]) -> tuple[str, str | None]:
    """Classify presentation subject from declared behavior, never from its title."""
    phase_primitives = entry.get("phase_primitives") or {}
    primitives = {
        str(value).casefold()
        for phase in _PHASES
        for value in phase_primitives.get(phase) or []
    }
    assertions = {
        str(value).casefold() for value in entry.get("assertion_primitives") or []
    }
    tags = {str(value).casefold() for value in entry.get("tags") or []}
    surfaces = {str(value).casefold() for value in coverage_surfaces}
    facts = primitives | assertions
    load_target_ids = {
        str(value).casefold() for value in entry.get("load_target_ids") or []
    }

    if any("measurement" in value or "calibration" in value for value in facts) or (
        {"measurement", "calibration"} <= tags
    ):
        return "measurements", None
    if any(
        token in value
        for value in facts
        for token in ("restart", "recovery", "rollback", "restore", "shutdown")
    ):
        return "recovery", None
    if any("opcert" in value or value.startswith("kes_") for value in facts):
        return "opcert", None
    if any("plutus" in value or "script.execute" in value or "cek" in value for value in facts):
        return "plutus", None
    if "runtime_version_pinned_cbor_conformance" in primitives:
        return "cbor", None
    # Structural transaction-body parsers belong under CBOR even when the
    # canonical coverage contract also associates them with TxSubmission.
    if any("invalid_tx_body" in value or "transaction_body" in value for value in facts) or any(
        value.endswith("cbor-decode-tx-body") for value in load_target_ids
    ):
        return "cbor", "tx-body"
    if surfaces.intersection(
        {"handshake", "keepalive", "blockfetch", "chainsync", "peersharing", "txsubmission", "mux/connection"}
    ) or any(
        token in value
        for value in facts
        for token in (
            "txsubmission", "mini_protocol", "protocol_decode", "handshake",
            "blockfetch", "chainsync", "peer_session", "mux_",
        )
    ):
        return "n2n", None
    if any(
        token in value
        for value in facts
        for token in ("chain_select", "consensus", "epoch_boundary", "ledger_rules", "canonical_chain")
    ) or surfaces.intersection({"consensus/chain-selection", "bootstrap/genesis"}):
        return "consensus", None
    if "cbor/parser" in surfaces or any(
        token in value
        for value in facts
        for token in ("cbor", "decode", "encode", "deserial", "roundtrip")
    ):
        return "cbor", None
    return "utilities", None


def scenario_discovery(
    entry: dict[str, Any],
    *,
    profile_shapes: dict[str, tuple[int, int]],
    recommended_ids: Iterable[str] = (),
    retained_evidence: dict[str, str] | None = None,
    threat_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return safe discovery metadata while preserving the exact scenario id."""
    scenario_id = str(entry.get("id") or "")
    card = scenario_card(entry)
    phase_primitives = {
        phase: list((entry.get("phase_primitives") or {}).get(phase) or [])
        for phase in _PHASES
    }
    workload = _workload_primitives(phase_primitives)
    friendly_title = _intent_title(
        entry,
        str(card.get("title") or entry.get("title") or scenario_id).strip(),
        workload,
    )
    semantic_text = _semantic_text(entry)
    implementation_context = _implementation_context(entry, profile_shapes)
    execution_type = _execution_type(entry, semantic_text)
    goals = _security_goals(semantic_text, execution_type)
    assertions = list(entry.get("assertion_primitives") or [])
    purpose, security_check = _reader_copy(entry, workload, assertions)
    family_key, family_title = _family(entry, friendly_title, friendly_title)
    target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
    requirements = [
        str(entry.get("runtime") or "runtime unknown"),
        implementation_context["topology_scope"],
        f'{implementation_context["primary_target"]} target',
    ]
    version = str(target.get("version") or entry.get("target_version") or "").strip()
    if version:
        requirements.append(f"target {version}")
    requirements.append("source-pinned" if target.get("source_revision") else "source not pinned")
    requirements.append("profile-backed" if entry.get("profile") else "profile resolved on selection")
    retained_run = (retained_evidence or {}).get(scenario_id)
    threat_risk = threat_risk or {
        "threat_model_ids": ["unmapped"],
        "risk_register_ids": ["unmapped"],
        "threat_model_search": [],
        "risk_register_search": [],
        "coverage_surfaces": [],
    }
    subject_key, subject_child_key = _subject(
        entry, threat_risk.get("coverage_surfaces") or []
    )
    subject_label = dict(_SUBJECTS)[subject_key]
    evidence = (
        {"state": "retained-proof", "reason": "A retained exact scenario run is linked.", "run_id": retained_run}
        if retained_run
        else {
            "state": "no-retained-proof",
            "reason": (
                "No execution evidence is linked here; this does not mean this scenario "
                "was never run."
            ),
            "run_id": None,
        }
    )
    search_parts = [
        scenario_id,
        friendly_title,
        purpose,
        security_check,
        *(entry.get("tags") or []),
        *(primitive for phase in _PHASES for primitive in phase_primitives[phase]),
        *requirements,
        *(threat_risk.get("threat_model_ids") or []),
        *(threat_risk.get("risk_register_ids") or []),
        *(threat_risk.get("threat_model_search") or []),
        *(threat_risk.get("risk_register_search") or []),
        subject_label,
        "TX Body" if subject_child_key == "tx-body" else "",
    ]
    return {
        **entry,
        "selection_id": scenario_id,
        "friendly_title": friendly_title,
        "purpose": purpose,
        "security_check": security_check,
        "family_key": family_key,
        "family_title": family_title,
        **implementation_context,
        "execution_type": execution_type,
        "security_goals": goals,
        "compatibility": _compatibility(entry, profile_shapes),
        "local_capability": _local_capability(entry, execution_type),
        "evidence": evidence,
        "recommended": scenario_id in set(recommended_ids),
        "maintenance": _maintenance(semantic_text, execution_type, phase_primitives),
        "context_links": _context_links(scenario_id),
        "variant_requirements": requirements,
        "subject_key": subject_key,
        "subject_child_key": subject_child_key,
        "subject_path": [
            value for value in (subject_key, subject_child_key) if value
        ],
        "threat_model_ids": list(threat_risk.get("threat_model_ids") or ["unmapped"]),
        "risk_register_ids": list(threat_risk.get("risk_register_ids") or ["unmapped"]),
        "threat_model_search": list(threat_risk.get("threat_model_search") or []),
        "risk_register_search": list(threat_risk.get("risk_register_search") or []),
        "search_text": " ".join(str(value) for value in search_parts if value).casefold(),
    }


def catalog_discovery(
    entries: Iterable[dict[str, Any]],
    *,
    profile_shapes: dict[str, tuple[int, int]],
    recommended_ids: Iterable[str] = (),
    retained_evidence: dict[str, str] | None = None,
    threat_risk_by_scenario: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    recommended = set(recommended_ids)
    return [
        scenario_discovery(
            entry,
            profile_shapes=profile_shapes,
            recommended_ids=recommended,
            retained_evidence=retained_evidence,
            threat_risk=(threat_risk_by_scenario or {}).get(str(entry.get("id") or "")),
        )
        for entry in entries
    ]


def validate_catalog_discovery(
    rows: Iterable[dict[str, Any]], *, expected_scenario_ids: set[str]
) -> None:
    rows = list(rows)
    actual_ids = {row.get("id") for row in rows}
    if actual_ids != expected_scenario_ids or len(actual_ids) != len(rows):
        raise ValueError("discovery metadata must conserve every exact scenario identity")
    required_text = ("friendly_title", "purpose", "security_check", "family_key", "family_title", "search_text")
    for row in rows:
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required_text):
            raise ValueError(f"scenario {row.get('id')!r} has incomplete discovery text")
        if row.get("implementation_key") not in _IMPLEMENTATIONS:
            raise ValueError(f"scenario {row['id']!r} has invalid implementation bucket")
        participants = row.get("implementation_participants")
        if (
            not isinstance(participants, list)
            or not participants
            or any(value not in {"amaru", "cardano-node", "other"} for value in participants)
        ):
            raise ValueError(f"scenario {row['id']!r} has invalid implementation participants")
        if row.get("execution_type") not in _EXECUTION_TYPES:
            raise ValueError(f"scenario {row['id']!r} has invalid execution type")
        if row.get("selection_id") != row.get("id"):
            raise ValueError(f"scenario {row['id']!r} changes selection identity")
        if row.get("subject_key") not in dict(_SUBJECTS):
            raise ValueError(f"scenario {row['id']!r} has invalid subject")
        if row.get("subject_path") not in (
            [row["subject_key"]],
            ["cbor", "tx-body"],
        ):
            raise ValueError(f"scenario {row['id']!r} has invalid subject path")
        for key in ("threat_model_ids", "risk_register_ids"):
            if not isinstance(row.get(key), list) or not row[key]:
                raise ValueError(f"scenario {row['id']!r} has invalid {key}")


def group_scenario_families(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["family_key"]].append(row)
    return [
        {
            "key": key,
            "title": sorted(variants, key=lambda row: (len(row["family_title"]), row["family_title"]))[0]["family_title"],
            "variants": sorted(variants, key=lambda row: (row["friendly_title"], row["id"])),
            "security_goals": sorted({goal for row in variants for goal in row["security_goals"]}),
            "implementations": sorted({row["implementation"] for row in variants}),
            "execution_types": sorted({row["execution_type"] for row in variants}),
            "maintenance_hidden": all(
                row["maintenance"]["state"] == "hidden-by-default" for row in variants
            ),
        }
        for key, variants in sorted(grouped.items(), key=lambda item: item[0])
    ]


def group_scenario_subjects(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group every exact variant once under the stable subject hierarchy."""
    rows = list(rows)
    output = []
    for subject_key, subject_title in _SUBJECTS:
        subject_rows = [row for row in rows if row["subject_key"] == subject_key]
        children = []
        child_specs = (
            ((None, subject_title), ("tx-body", "TX Body"))
            if subject_key == "cbor"
            else ((None, subject_title),)
        )
        for child_key, child_title in child_specs:
            child_rows = [
                row for row in subject_rows if row.get("subject_child_key") == child_key
            ]
            children.append(
                {
                    "key": child_key or "general",
                    "title": child_title,
                    "families": group_scenario_families(child_rows),
                    "variant_count": len(child_rows),
                }
            )
        output.append(
            {
                "key": subject_key,
                "title": subject_title,
                "children": children,
                "variant_count": len(subject_rows),
            }
        )
    return output


_FACET_LABELS = {
    "implementation": {
        "amaru": "Amaru participant",
        "cardano-node": "Cardano participant",
        "mixed": "Mixed topology",
        "other": "Other",
    },
    "execution": {
        "live": "Live topology",
        "standalone-library": "Standalone library",
        "fuzz": "Fuzz campaign",
        "evidence-utility": "Evidence utility",
    },
    "goal": {
        "peer-isolation": "Peer isolation",
        "malformed-input": "Malformed input",
        "conformance": "Conformance",
        "consensus-ledger": "Consensus / ledger",
        "resource-dos": "Resource / DoS",
        "recovery": "Recovery",
        "exploit-resilience": "Exploit resilience",
        "evidence-integrity": "Evidence integrity",
        "unclassified": "Other goal",
    },
}


def scenario_discovery_facets(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return truthful facet values/counts from the projected catalog."""
    rows = list(rows)
    values: dict[str, list[str]] = {
        "implementation": [
            str(value) for row in rows for value in row["implementation_facets"]
        ],
        "execution": [str(row["execution_type"]) for row in rows],
        "goal": [str(value) for row in rows for value in row["security_goals"]],
        "recommended": ["recommended" for row in rows if row["recommended"]],
        "compatibility": [str(row["compatibility"]["state"]) for row in rows],
        "capability": [str(row["local_capability"]["state"]) for row in rows],
        "evidence": [str(row["evidence"]["state"]) for row in rows],
    }
    result = {}
    for group, items in values.items():
        counts = defaultdict(int)
        for item in items:
            counts[item] += 1
        result[group] = [
            {
                "value": value,
                "label": _FACET_LABELS.get(group, {}).get(value, _words(value).capitalize()),
                "count": counts[value],
            }
            for value in sorted(counts)
        ]
    return result
