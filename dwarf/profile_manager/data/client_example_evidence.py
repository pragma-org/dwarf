"""Verified retained evidence for the frozen five-card client program."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


DWARF_ROOT = Path(__file__).resolve().parents[2]

_CARDS = (
    {"id": "01", "title": "CBOR decoding", "measurement_revision": "nanoseconds-v2", "legs": (
        ("amaru", "client-example-cbor-decoding-amaru-d3a6dafc-regression", "20260920T235440Z-050046a4"),
        ("cardano-node", "client-example-cbor-decoding-cardano-patched", "20260920T132629Z-ea000d37"),
    )},
    {"id": "02", "title": "Plutus VM", "measurement_revision": "nanoseconds-v2", "legs": (
        ("amaru", "client-example-plutus-vm-amaru-onchain-v2", "20260921T013953Z-565b77c3"),
        ("cardano-node", "client-example-plutus-vm-cardano", "20260920T135958Z-362eedc7"),
    )},
    {"id": "03", "title": "Invalid mini-protocol", "measurement_revision": "whole-microseconds-v1", "legs": (
        ("amaru", "client-example-invalid-mini-protocol-amaru", "20260920T072858Z-2cc3bb0c"),
        ("cardano-node", "client-example-invalid-mini-protocol-cardano", "20260920T073447Z-ab81bfb7"),
    )},
    {"id": "04", "title": "Block application", "measurement_revision": "Amaru nanoseconds-v3; Cardano-node nanoseconds-v2", "legs": (
        ("amaru", "client-example-block-application-amaru-canonical-v3", "20260921T035546Z-9747122c"),
        ("cardano-node", "client-example-block-application-cardano-canonical-v2", "20260921T021935Z-3b58eafc"),
    )},
    {"id": "05", "title": "Restart, recovery, and synchronization", "measurement_revision": "nanoseconds-v2", "legs": (
        ("amaru", "client-example-restart-recovery-sync-amaru", "20260921T045619Z-15e864a0"),
        ("cardano-node", "client-example-restart-recovery-sync-cardano", "20260921T045807Z-8e2bbb0e"),
    )},
)


def five_card_evidence() -> list[dict]:
    """Return the ledger, checking its scenarios, contracts, and run manifests."""
    rows = []
    for definition in _CARDS:
        contract_path = DWARF_ROOT / "docs/client-examples/contracts" / f"{definition['id']}-{_contract_slug(definition['id'])}.yaml"
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        security = contract.get("security_mapping") or {}
        legs = []
        for implementation, scenario_id, run_id in definition["legs"]:
            scenario_path = DWARF_ROOT / "scenarios" / f"{scenario_id}.yaml"
            manifest_path = DWARF_ROOT / "runs" / run_id / "manifest.json"
            proof_path = DWARF_ROOT / "docs/client-examples/03-INVALID-MINI-PROTOCOL-PROOF.md"
            run_available = manifest_path.is_file()
            proof_text = proof_path.read_text(encoding="utf-8") if proof_path.is_file() else ""
            retained_proof = (
                definition["id"] == "03"
                and run_id in proof_text
                and "whole-microseconds-v1" in proof_text
                and "Exported bundle" in proof_text
            )
            verified = scenario_path.is_file() and (run_available or retained_proof)
            manifest = {}
            if run_available:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                verified = verified and manifest.get("run_id") == run_id
                verified = verified and (manifest.get("scenario") or {}).get("id") == scenario_id
            legs.append({
                "implementation": implementation,
                "scenario_id": scenario_id,
                "run_id": run_id,
                "run_available": run_available,
                "run_url": f"/operate/runs/{run_id}" if run_available else None,
                "evidence_url": f"/operate/runs/{run_id}" if run_available else "/learn/measurements#five-client-examples",
                "verified": verified,
                "exit_status": manifest.get("exit_status"),
            })
        rows.append({
            **{key: value for key, value in definition.items() if key != "legs"},
            "state": "accepted" if all(leg["verified"] for leg in legs) else "retained artifact unavailable",
            "legs": legs,
            "threat_ids": [row["id"] for row in security.get("threat_model") or []],
            "risk_ids": [row["id"] for row in security.get("risk_register") or []],
        })
    return rows


def _contract_slug(card_id: str) -> str:
    return {"01": "cbor-decoding", "02": "plutus-vm", "03": "invalid-mini-protocol", "04": "block-application", "05": "restart-recovery-sync"}[card_id]
