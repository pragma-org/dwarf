"""Load and validate the opcert header case table.

Each case keeps the header well-formed and validly signed and differs in
exactly one operational-certificate rule, so a node's verdict is attributable
to that rule. Cases are data rows; adding a case is adding a row.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
DEFAULT_PATH = DWARF_ROOT / "corpora" / "opcert" / "opcert-header-cases-v1.json"

# ouroboros-consensus gen-header mutation names, 1:1 with the reference generator.
MUTATIONS = frozenset({
    "NoMutation",
    "MutateColdKey",
    "MutateCounterUnder",
    "MutateCounterOver1",
    "MutateKESPeriodBefore",
    "MutateKESPeriod",
    "MutateKESKey",
})


def load_cases(path=None) -> list[dict]:
    cases = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    validate_cases(cases)
    return cases


_REQUIRED = ("id", "family", "mutation", "rule", "expected_verdict", "expected_reason")
_FAMILIES = frozenset({"rule", "boundary"})


def validate_cases(cases: list[dict]) -> None:
    seen: set[str] = set()
    for index, case in enumerate(cases):
        missing = [key for key in _REQUIRED if key not in case]
        if missing:
            raise ValueError(f"case[{index}] missing fields: {', '.join(missing)}")
        cid = case["id"]
        if cid in seen:
            raise ValueError(f"duplicate case id: {cid}")
        seen.add(cid)
        if case["family"] not in _FAMILIES:
            raise ValueError(f"bad family for {cid}: {case['family']}")
        if case["mutation"] not in MUTATIONS:
            raise ValueError(f"unknown mutation: {case['mutation']}")
        if case["expected_verdict"] not in ("accept", "reject"):
            raise ValueError(f"bad verdict: {case['expected_verdict']}")
        reason = case["expected_reason"]
        if set(reason) != {"cardano-node", "amaru"}:
            raise ValueError(f"expected_reason must key both nodes: {cid}")
        if case["expected_verdict"] == "accept" and any(reason.values()):
            raise ValueError(f"accept case must have null reasons: {cid}")
        if case["expected_verdict"] == "reject" and not all(reason.values()):
            raise ValueError(f"reject case must name a reason per node: {cid}")
