"""Capability classification for guided run execution backends."""
from __future__ import annotations


def classify_run_backends(scenario, *, local_confirmed=False):
    local = {
        "id": "local",
        "label": "Local",
        "state": "confirmed" if local_confirmed else "supported-unconfirmed",
        "reason": (
            "Retained matching evidence confirms local execution for this scenario."
            if local_confirmed
            else "The local DWARF engine supports this scenario; this selection has no matching retained confirmation yet."
        ),
        "action": "start-local",
    }

    github = {
        "id": "github-actions",
        "label": "GitHub Actions",
        "state": "unsupported",
        "reason": "No manual GitHub workflow preserves this selected scenario's execution contract.",
        "action": None,
    }
    if (
        scenario.runtime == "devnet"
        and scenario.target.get("implementation") == "cardano-node"
        and scenario.substrate is not None
    ):
        github = {
            "id": "github-actions",
            "label": "GitHub Actions",
            "state": "supported-unconfirmed",
            "reason": "The self-hosted devnet smoke workflow accepts this exact scenario path; runner readiness is checked at dispatch time.",
            "action": "dispatch-github",
            "workflow": "dwarf-devnet-smoke.yml",
            "inputs": {"scenarios": f"dwarf/scenarios/{scenario.id}.yaml"},
        }

    antithesis = {
        "id": "antithesis",
        "label": "Antithesis",
        "state": "unsupported",
        "reason": "The native Antithesis generator does not support this scenario.",
        "action": None,
    }
    try:
        from profile_manager import antithesis_generator

        antithesis_generator.fuzz_spec(scenario)
        antithesis_generator.derive_adversary(scenario)
        antithesis_generator.map_assertions(scenario)
    except Exception as exc:  # GeneratorError plus bounded mapping failures.
        antithesis["reason"] = str(exc)
    else:
        antithesis = {
            "id": "antithesis",
            "label": "Antithesis",
            "state": "supported-unconfirmed",
            "reason": "The real native generator can build and statically verify this scenario; live submission remains a separate Moog gate.",
            "action": "prepare-antithesis",
        }

    return {
        "local": local,
        "github-actions": github,
        "antithesis": antithesis,
    }
