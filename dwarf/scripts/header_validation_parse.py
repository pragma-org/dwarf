"""Extract per-header validation verdicts and reasons from node logs.

Both parsers turn a node's normal structured log lines into
``{"header_hash", "verdict", "reason", "at"}`` events, correlated later by
header hash. A line whose verdict cannot be extracted is skipped; the caller
treats a missing verdict as ``unavailable``, never as an implicit accept.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

_OCERT = re.compile(
    r"(KESBeforeStartOCERT|KESAfterEndOCERT|CounterTooSmallOCERT|"
    r"CounterOverIncrementedOCERT|InvalidKesSignatureOCERT|InvalidSignatureOCERT)"
)
_AMARU = re.compile(
    r"(OpCertKesPeriodTooLarge|OpCertKesPeriodTooOld|InvalidKesSignature|"
    r"SequenceNumberTooSmall|SequenceNumberTooFarAhead|InvalidSignature)"
)


def _loads(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def parse_cardano_header_events(lines: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        document = _loads(line)
        if not isinstance(document, dict):
            continue
        namespace = str(document.get("ns", ""))
        data = document.get("data")
        if not isinstance(data, dict):
            continue
        if namespace.endswith("AddedToCurrentChain") or namespace.endswith("SwitchedToAFork"):
            block = data.get("block")
            header_hash = data.get("newtip") or (block.get("hash") if isinstance(block, dict) else block)
            if header_hash:
                out.append({"header_hash": str(header_hash), "verdict": "accepted",
                            "reason": None, "at": document.get("at")})
        elif "InvalidBlock" in namespace:
            block = data.get("block")
            header_hash = block.get("hash") if isinstance(block, dict) else block
            match = _OCERT.search(json.dumps(data))
            if header_hash:
                out.append({"header_hash": str(header_hash), "verdict": "rejected",
                            "reason": match.group(1) if match else None, "at": document.get("at")})
    return out


def parse_amaru_header_events(lines: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        document = _loads(line)
        if not isinstance(document, dict):
            continue
        fields = document.get("fields") or {}
        header_hash = fields.get("header_hash")
        if not header_hash:
            continue
        message = str(fields.get("message", ""))
        error = str(fields.get("error", ""))
        if fields.get("outcome") == "new_tip" or message == "chain.tip_accepted":
            out.append({"header_hash": str(header_hash), "verdict": "accepted",
                        "reason": None, "at": document.get("timestamp")})
        elif fields.get("outcome") == "invalid_header" or "header_rejected" in message or "validation failed" in error:
            match = _AMARU.search(error)
            out.append({"header_hash": str(header_hash), "verdict": "rejected",
                        "reason": match.group(1) if match else None, "at": document.get("timestamp")})
    return out


def verdict_by_hash(events: list[dict]) -> dict[str, dict]:
    """Last event per header hash wins (a later accept supersedes an earlier reject)."""
    result: dict[str, dict] = {}
    for event in events:
        result[event["header_hash"]] = event
    return result
