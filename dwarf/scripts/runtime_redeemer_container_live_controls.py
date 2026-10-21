#!/usr/bin/env python3
"""Build and assess fresh-funded empty-redeemer live controls.

The immutable structural corpus remains separate.  This module constructs a new
transaction family from a controller-owned private runtime and records every
generated identity.  Pure mutation and assessment helpers are kept independent
from Docker/cardano-cli orchestration so their fail-closed behavior is testable.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import cbor2

from scripts.runtime_cardano_measurement_calibration import (
    _checked,
    _containerized_cardano_workload,
    _controller_container,
    _inspect_container,
    _query_utxo,
    _transaction_id,
    _wait_for_utxo,
)


@dataclass(frozen=True)
class LiveCase:
    case_id: str
    family: str
    mutation: str
    control: bool = False


@dataclass(frozen=True)
class MintLanguage:
    name: str
    language_tag: int
    cost_model: list[int]


# Normal controls establish admission and inclusion before mutations.  Mutation
# cases use one signed payload for both implementations, while different cases
# have different funding.  Controls use independent inputs per role.
LIVE_CASES = (
    LiveCase("no-script-valid-control", "no-script", "omitted", control=True),
    LiveCase("script-valid-control", "script", "valid", control=True),
    LiveCase("script-redeemers-omitted", "script", "omitted"),
    LiveCase("script-redeemers-empty-list", "script", "empty-list"),
    LiveCase("script-redeemers-empty-map", "script", "empty-map"),
    LiveCase("no-script-redeemers-empty-list", "no-script", "empty-list"),
    LiveCase("no-script-redeemers-empty-map", "no-script", "empty-map"),
)

FUNDING_OUTPUT_LOVELACE = 20_000_000
FUNDING_FEE_RESERVE_LOVELACE = 5_000_000
_REPO_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_CONFIG_ID_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REFERENCE_LOG_SLICE_BYTES = 256 * 1024
_RAW_REJECT_WIRE_LIMIT = 65_536
_RAW_REJECT_TERM_DEPTH_LIMIT = 64
_RAW_REJECT_TERM_NODE_LIMIT = 4_096
_RAW_REJECT_TERM_JSON_LIMIT = 262_144
_RAW_REJECT_WIRE_SCOPE = "localtxsubmission-mini-protocol-response-after-demux"
_RAW_REJECT_CANONICAL_SCOPE = "decoded-reject-term-canonical-cbor"
_RAW_REJECT_TERM_WIRE_SCOPE = "localtxsubmission-submitfail-reject-term"
_CARDANO_NODE_11_1_2_SOURCE = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
_CARDANO_NODE_11_1_2_NETWORK_SOURCE = "c45735a56c567fa977969173d18943bac6bb3821"
_CARDANO_NODE_11_1_2_CONSENSUS_SOURCE = "82ecba329d7d054340bf707d44fe6e9ac27cec40"
_CARDANO_NODE_11_1_2_LEDGER_SOURCE = "f649f9751074d2ab3de033fc3912f29c9862c1f5"
_UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
)


def _decode_reject_base64(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{field} is not a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{field} is not valid base64") from error


def _extract_reject_term_wire(wire: bytes, canonical: bytes) -> dict[str, Any]:
    if not wire.startswith(b"\x82\x02"):
        raise ValueError("reject wire response is not SubmitFail")
    exact_term = wire[2:]

    def decode_one(raw: bytes) -> object:
        decoder = cbor2.CBORDecoder(io.BytesIO(raw))
        try:
            value = decoder.decode()
        except cbor2.CBORDecodeError as error:
            raise ValueError("reject term CBOR is invalid") from error
        try:
            decoder.decode()
        except cbor2.CBORDecodeEOF:
            return value
        except cbor2.CBORDecodeError as error:
            raise ValueError("reject term CBOR is invalid") from error
        raise ValueError("reject term CBOR has trailing values")

    wire_term = decode_one(exact_term)
    canonical_term = decode_one(canonical)
    if wire_term != canonical_term:
        raise ValueError("wire and canonical reject terms differ")

    return {
        "schema_version": 1,
        "scope": _RAW_REJECT_TERM_WIRE_SCOPE,
        "encoding": "base64",
        "bytes_base64": base64.b64encode(exact_term).decode("ascii"),
        "length": len(exact_term),
        "sha256": hashlib.sha256(exact_term).hexdigest(),
        "message_tag": 2,
        "matches_canonical_cbor": exact_term == canonical,
    }


def _derive_cardano_node_11_1_2_reject_term(
    canonical: bytes,
    *,
    source_revision: str,
    negotiated_version: str,
) -> dict[str, Any] | None:
    """Name one exact Conway rejection using the selected node's source codecs."""

    if (
        source_revision != _CARDANO_NODE_11_1_2_SOURCE
        or negotiated_version != "NodeToClientV_20"
    ):
        return None
    decoder = cbor2.CBORDecoder(io.BytesIO(canonical))
    try:
        term = decoder.decode()
        try:
            decoder.decode()
        except cbor2.CBORDecodeEOF:
            pass
        else:
            return None
        policy_id = term[0][1][0][1][1][1][1][0][1][1]
    except (cbor2.CBORDecodeError, IndexError, KeyError, TypeError):
        return None
    if not isinstance(policy_id, bytes) or len(policy_id) != 28:
        return None

    # Source-bound constructor chain:
    # HardForkApplyTxErr/ConwayApplyTxError -> ConwayUtxowFailure, with both
    # UtxosFailure(CollectErrors(NoRedeemer)) and MissingRedeemers.
    minting_purpose = [1, policy_id]
    no_redeemer = [0, minting_purpose]
    collect_errors = [1, [no_redeemer]]
    utxos_failure = [0, collect_errors]
    utxo_failure = [0, utxos_failure]
    expected = [
        [
            6,
            [
                [1, utxo_failure],
                [1, [10, [[minting_purpose, policy_id]]]],
            ],
        ]
    ]
    if term != expected:
        return None

    policy_hex = policy_id.hex()
    return {
        "schema_version": 1,
        "status": "exact-source-bound-derivation",
        "derivation_lineage": {
            "cardano_node_source_revision": _CARDANO_NODE_11_1_2_SOURCE,
            "ouroboros_network_source_revision": _CARDANO_NODE_11_1_2_NETWORK_SOURCE,
            "ouroboros_consensus_source_revision": _CARDANO_NODE_11_1_2_CONSENSUS_SOURCE,
            "cardano_ledger_source_revision": _CARDANO_NODE_11_1_2_LEDGER_SOURCE,
        },
        "negotiated_version": negotiated_version,
        "era": "Conway",
        "apply_tx_error": "ConwayApplyTxError",
        "predicate_failures": [
            {
                "path": [
                    "ConwayUtxowFailure",
                    "UtxoFailure",
                    "UtxosFailure",
                    "CollectErrors",
                    "NoRedeemer",
                ],
                "plutus_purpose": "ConwayMinting",
                "policy_id": policy_hex,
            },
            {
                "path": ["ConwayUtxowFailure", "MissingRedeemers"],
                "plutus_purpose": "ConwayMinting",
                "policy_id": policy_hex,
                "script_hash": policy_hex,
            },
        ],
        "canonical_cbor_length": len(canonical),
        "canonical_cbor_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _reject_term_tree_metrics(tree: object) -> tuple[int, int]:
    nodes = 0

    def visit(value: object, depth: int) -> int:
        nonlocal nodes
        if depth > _RAW_REJECT_TERM_DEPTH_LIMIT:
            raise ValueError("term tree exceeds depth limit")
        if not isinstance(value, dict):
            raise ValueError("term tree node is not an object")
        nodes += 1
        if nodes > _RAW_REJECT_TERM_NODE_LIMIT:
            raise ValueError("term tree exceeds node limit")
        kind = value.get("type")
        if kind in {"int", "integer", "simple"}:
            if (
                set(value) != {"type", "value"}
                or isinstance(value["value"], bool)
                or not isinstance(value["value"], int)
            ):
                raise ValueError(f"invalid {kind} term node")
        elif kind in {"half", "float", "double", "text", "text_indefinite"}:
            if set(value) != {"type", "value"} or not isinstance(value["value"], str):
                raise ValueError(f"invalid {kind} term node")
        elif kind == "bool":
            if set(value) != {"type", "value"} or not isinstance(value["value"], bool):
                raise ValueError("invalid bool term node")
        elif kind == "null":
            if set(value) != {"type"}:
                raise ValueError("invalid null term node")
        elif kind in {"bytes", "bytes_indefinite"}:
            if (
                set(value) != {"type", "encoding", "value"}
                or value.get("encoding") != "base64"
            ):
                raise ValueError(f"invalid {kind} term node")
            _decode_reject_base64(value.get("value"), f"{kind}.value")
        elif kind in {"list", "list_indefinite"}:
            if set(value) != {"type", "items"} or not isinstance(value["items"], list):
                raise ValueError(f"invalid {kind} term node")
            for child in value["items"]:
                visit(child, depth + 1)
        elif kind in {"map", "map_indefinite"}:
            if set(value) != {"type", "entries"} or not isinstance(value["entries"], list):
                raise ValueError(f"invalid {kind} term node")
            for entry in value["entries"]:
                if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
                    raise ValueError(f"invalid {kind} term entry")
                visit(entry["key"], depth + 1)
                visit(entry["value"], depth + 1)
        elif kind == "tag":
            if (
                set(value) != {"type", "tag", "value"}
                or isinstance(value["tag"], bool)
                or not isinstance(value["tag"], int)
                or value["tag"] < 0
            ):
                raise ValueError("invalid tag term node")
            visit(value["value"], depth + 1)
        else:
            raise ValueError("unknown term tree node type")
        return depth

    def max_depth(value: dict[str, Any], depth: int) -> int:
        kind = value["type"]
        if kind in {"list", "list_indefinite"}:
            return max(
                [depth, *(max_depth(child, depth + 1) for child in value["items"])]
            )
        if kind in {"map", "map_indefinite"}:
            children = [
                child
                for entry in value["entries"]
                for child in (entry["key"], entry["value"])
            ]
            return max([depth, *(max_depth(child, depth + 1) for child in children)])
        if kind == "tag":
            return max_depth(value["value"], depth + 1)
        return depth

    visit(tree, 1)
    encoded = json.dumps(
        tree, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > _RAW_REJECT_TERM_JSON_LIMIT:
        raise ValueError("term tree exceeds serialized size limit")
    return nodes, max_depth(tree, 1)


def _validated_reject_evidence(
    evidence: object, outcome: str
) -> tuple[dict[str, Any] | None, str | None]:
    if evidence is None:
        return None, None
    try:
        if outcome != "node_rejected":
            raise ValueError("reject evidence requires typed node_rejected")
        if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
            raise ValueError("reject evidence schema is invalid")
        status = evidence.get("status")
        if status not in {"complete", "oversized", "term-unavailable", "wire-unavailable"}:
            raise ValueError("reject evidence status is invalid")
        if (
            evidence.get("wire_scope") != _RAW_REJECT_WIRE_SCOPE
            or evidence.get("wire_encoding") != "base64"
            or evidence.get("wire_capture_limit") != _RAW_REJECT_WIRE_LIMIT
            or evidence.get("term_tree_schema_version") != 1
            or evidence.get("term_depth_limit") != _RAW_REJECT_TERM_DEPTH_LIMIT
            or evidence.get("term_node_limit") != _RAW_REJECT_TERM_NODE_LIMIT
            or evidence.get("term_json_size_limit") != _RAW_REJECT_TERM_JSON_LIMIT
            or evidence.get("canonical_cbor_scope") != _RAW_REJECT_CANONICAL_SCOPE
            or evidence.get("canonical_cbor_encoding") != "base64"
            or evidence.get("canonical_matches_captured_reject") is not None
        ):
            raise ValueError("reject evidence scope or bounds are invalid")

        wire_length = evidence.get("wire_length")
        if (
            isinstance(wire_length, bool)
            or not isinstance(wire_length, int)
            or wire_length < 0
        ):
            raise ValueError("wire length is invalid")
        wire_value = evidence.get("wire_bytes_base64")
        wire_hash = evidence.get("wire_sha256")
        wire_truncated = evidence.get("wire_truncated")
        if wire_value is None:
            expected_truncated = wire_length > _RAW_REJECT_WIRE_LIMIT
            if wire_truncated is not expected_truncated:
                raise ValueError("unavailable wire evidence is inconsistent")
            if expected_truncated:
                if (
                    not isinstance(wire_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", wire_hash) is None
                ):
                    raise ValueError("oversized wire hash is invalid")
            elif wire_hash is not None:
                raise ValueError("unavailable wire evidence has a hash")
        else:
            wire = _decode_reject_base64(wire_value, "wire_bytes_base64")
            if (
                len(wire) != wire_length
                or wire_length > _RAW_REJECT_WIRE_LIMIT
                or wire_truncated is not False
                or not isinstance(wire_hash, str)
                or hashlib.sha256(wire).hexdigest() != wire_hash
            ):
                raise ValueError("wire bytes identity is invalid")

        term_tree = evidence.get("term_tree")
        term_nodes = evidence.get("term_node_count")
        term_depth = evidence.get("term_max_depth")
        if term_tree is None:
            if status != "term-unavailable" or term_nodes is not None or term_depth is not None:
                raise ValueError("term tree availability is inconsistent")
        else:
            actual_nodes, actual_depth = _reject_term_tree_metrics(term_tree)
            if term_nodes != actual_nodes or term_depth != actual_depth:
                raise ValueError("term tree metrics are invalid")

        canonical_value = evidence.get("canonical_cbor_base64")
        canonical_length = evidence.get("canonical_cbor_length")
        canonical_hash = evidence.get("canonical_cbor_sha256")
        if canonical_value is None:
            if (
                status != "term-unavailable"
                or canonical_length is not None
                or canonical_hash is not None
            ):
                raise ValueError("canonical CBOR availability is inconsistent")
        else:
            canonical = _decode_reject_base64(
                canonical_value, "canonical_cbor_base64"
            )
            if (
                isinstance(canonical_length, bool)
                or canonical_length != len(canonical)
                or canonical_length > _RAW_REJECT_TERM_JSON_LIMIT
                or not isinstance(canonical_hash, str)
                or hashlib.sha256(canonical).hexdigest() != canonical_hash
            ):
                raise ValueError("canonical CBOR identity is invalid")

        term_unavailable = term_tree is None or canonical_value is None
        wire_oversized = wire_value is None and wire_length > _RAW_REJECT_WIRE_LIMIT
        wire_unavailable = wire_value is None and not wire_oversized
        expected_status = (
            "term-unavailable"
            if term_unavailable
            else "oversized"
            if wire_oversized
            else "wire-unavailable"
            if wire_unavailable
            else "complete"
        )
        if status != expected_status:
            raise ValueError("reject evidence status is inconsistent")

        failure_reason = evidence.get("failure_reason")
        if status == "complete" and failure_reason is not None:
            raise ValueError("complete reject evidence has a failure reason")
        if status != "complete" and (
            not isinstance(failure_reason, str) or not failure_reason or len(failure_reason) > 256
        ):
            raise ValueError("incomplete reject evidence lacks a bounded failure reason")
        retained = dict(evidence)
        if status == "complete":
            retained["wire_reject_term"] = _extract_reject_term_wire(wire, canonical)
    except ValueError as error:
        return None, str(error)[:256]
    return retained, None


_TX_SUBMISSION_INIT_DELAY_RE = re.compile(
    r"ncTxSubmissionInitDelay\s*=\s*"
    r"(?:TxSubmissionInitDelay\s+)?(?P<seconds>\d+(?:\.\d+)?)s"
)


def _docker_started_at_epoch(timestamp: object) -> float:
    if not isinstance(timestamp, str) or not timestamp:
        raise RuntimeError("reference container State.StartedAt is missing")
    if _UTC_TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise RuntimeError("reference container State.StartedAt is invalid")
    base, _, suffix = timestamp[:-1].partition(".")
    normalized = base
    if suffix:
        normalized += "." + suffix[:6].ljust(6, "0")
    try:
        started_at = datetime.fromisoformat(normalized + "+00:00")
        epoch = started_at.timestamp()
    except (OverflowError, ValueError) as exc:
        raise RuntimeError("reference container State.StartedAt is invalid") from exc
    if epoch <= 0:
        raise RuntimeError("reference container State.StartedAt is invalid")
    return epoch


def _reference_tx_submission_propagation_gate(
    reference: Mapping[str, Any],
    *,
    inspect_container: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Bind retained submission delay to the exact live reference startup."""

    log_path = Path(str(reference.get("log_path") or ""))
    if not log_path.is_absolute() or not log_path.is_file():
        raise RuntimeError(
            "target propagation requires the retained reference startup log"
        )
    container_id = str(reference.get("container_id") or "")
    image_id = str(reference.get("container_image_id") or "")
    image_reference = str(
        reference.get("image") or reference.get("image_ref") or ""
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        or not image_reference
    ):
        raise RuntimeError("reference container startup identity is incomplete")
    inspected = inspect_container(container_id)
    if (
        inspected.get("Id") != container_id
        or inspected.get("Image") != image_id
        or ((inspected.get("Config") or {}).get("Image") != image_reference)
    ):
        raise RuntimeError("reference container startup image identity does not match")
    state = inspected.get("State")
    if not isinstance(state, dict):
        raise RuntimeError("reference container inspect State is missing")
    if state.get("Running") is not True:
        raise RuntimeError("reference container is not running")
    timestamp = state.get("StartedAt")
    started_at_epoch = _docker_started_at_epoch(timestamp)
    with log_path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            delay_match = _TX_SUBMISSION_INIT_DELAY_RE.search(line)
            if delay_match is None:
                continue
            delay = Decimal(delay_match.group("seconds"))
            if not delay.is_finite() or delay <= 0:
                raise RuntimeError(
                    "reference startup log TxSubmissionInitDelay is invalid"
                )
            delay_seconds = float(delay)
            eligible_at_epoch = started_at_epoch + delay_seconds
            eligible_at = datetime.fromtimestamp(
                eligible_at_epoch, tz=timezone.utc
            ).isoformat(timespec="microseconds").replace("+00:00", "Z")
            return {
                "source": "reference-startup-log+docker-inspect",
                "log_path": str(log_path),
                "delay_source": "reference-startup-log",
                "startup_source": "docker-inspect:State.StartedAt",
                "reference_container_id": container_id,
                "reference_container_image_id": image_id,
                "reference_container_image_reference": image_reference,
                "reference_started_at": timestamp,
                "reference_started_epoch_seconds": started_at_epoch,
                "tx_submission_init_delay_seconds": delay_seconds,
                "eligible_at": eligible_at,
                "eligible_at_epoch_seconds": eligible_at_epoch,
            }
    raise RuntimeError(
        "reference startup log does not retain ncTxSubmissionInitDelay"
    )


def select_mint_language(protocol_parameters: dict[str, Any]) -> MintLanguage:
    """Select only a mint language whose exact live cost model is present."""

    models = protocol_parameters.get("costModels") or {}
    if not isinstance(models, dict):
        raise RuntimeError("live protocol parameters have no supported Plutus mint cost model")
    for name, language_tag in (("PlutusV3", 2), ("PlutusV2", 1)):
        model = models.get(name)
        if (
            isinstance(model, list)
            and model
            and all(isinstance(value, int) and not isinstance(value, bool) for value in model)
        ):
            return MintLanguage(name=name, language_tag=language_tag, cost_model=list(model))
    raise RuntimeError("live protocol parameters have no supported Plutus mint cost model")


def split_transaction(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Return the raw body, witness set and tail of a definite four-part tx."""

    if not raw or raw[0] != 0x84:
        raise ValueError("expected a definite four-element transaction")
    stream = io.BytesIO(raw[1:])
    decoder = cbor2.CBORDecoder(stream)
    decoder.decode()
    body_end = 1 + stream.tell()
    decoder.decode()
    witness_end = 1 + stream.tell()
    return raw[1:body_end], raw[body_end:witness_end], raw[witness_end:]


def script_integrity_hash(
    redeemers: object, cost_model: list[int], language_tag: int
) -> bytes:
    """Compute the empty-datum script-data hash for one exact language view."""

    language_views = cbor2.dumps({language_tag: cost_model})
    return hashlib.blake2b(
        cbor2.dumps(redeemers) + language_views, digest_size=32
    ).digest()


def _replace_integrity_hash(body_raw: bytes, old_hash: bytes, new_hash: bytes) -> bytes:
    encoded = b"\x0b\x58\x20" + old_hash
    if body_raw.count(encoded) != 1:
        raise ValueError("transaction body does not contain one exact integrity hash")
    return body_raw.replace(encoded, b"\x0b\x58\x20" + new_hash)


def derive_script_variants(raw: bytes, language: MintLanguage) -> dict[str, bytes]:
    """Derive exact final bodies and empty-container witness representations."""

    body_raw, witness_raw, tail = split_transaction(raw)
    body = cbor2.loads(body_raw)
    witnesses = cbor2.loads(witness_raw)
    if not isinstance(body, dict) or not isinstance(witnesses, dict):
        raise ValueError("transaction body or witness set is not a map")
    if not isinstance(body.get(11), bytes) or not isinstance(witnesses.get(5), dict):
        raise ValueError("script control has no map redeemers or integrity hash")
    old_hash = body[11]
    if old_hash != script_integrity_hash(
        witnesses[5], language.cost_model, language.language_tag
    ):
        raise ValueError("script control integrity hash does not match live cost model")
    if cbor2.dumps(witnesses) != witness_raw:
        raise ValueError("script witness set does not round-trip exactly")

    variants = {"valid": raw}
    for name, container in (
        ("omitted", None),
        ("empty-list", []),
        ("empty-map", {}),
    ):
        changed = dict(witnesses)
        if container is None:
            changed.pop(5, None)
            hash_container: object = {}
        else:
            changed[5] = container
            hash_container = container
        changed_body = _replace_integrity_hash(
            body_raw,
            old_hash,
            script_integrity_hash(
                hash_container, language.cost_model, language.language_tag
            ),
        )
        variants[name] = b"\x84" + changed_body + cbor2.dumps(changed) + tail
    return variants


def derive_no_script_variants(raw: bytes) -> dict[str, bytes]:
    """Add only the requested redeemer-container representation to a control."""

    body_raw, witness_raw, tail = split_transaction(raw)
    witnesses = cbor2.loads(witness_raw)
    if not isinstance(witnesses, dict):
        raise ValueError("no-script witness set is not a map")
    if 5 in witnesses:
        raise ValueError("no-script control already contains redeemers")
    if cbor2.dumps(witnesses) != witness_raw:
        raise ValueError("no-script witness set does not round-trip exactly")

    variants = {"omitted": raw}
    for name, container in (("empty-list", []), ("empty-map", {})):
        changed = dict(witnesses)
        changed[5] = container
        variants[name] = b"\x84" + body_raw + cbor2.dumps(changed) + tail
    return variants


def prepare_variant_for_signing(control: bytes, intended: bytes) -> bytes:
    """Use the final body with the control's decodable witness structure."""

    _control_body, control_witness_raw, control_tail = split_transaction(control)
    intended_body, intended_witness_raw, intended_tail = split_transaction(intended)
    control_witnesses = cbor2.loads(control_witness_raw)
    intended_witnesses = cbor2.loads(intended_witness_raw)
    if not isinstance(control_witnesses, dict) or not isinstance(
        intended_witnesses, dict
    ):
        raise ValueError("signing control or intended witness set is not a map")
    if control_tail != intended_tail:
        raise ValueError("intended transaction changes fields outside body and witnesses")
    if {
        key: value for key, value in control_witnesses.items() if key != 5
    } != {
        key: value for key, value in intended_witnesses.items() if key != 5
    }:
        raise ValueError("intended transaction changes non-redeemer witnesses")
    return b"\x84" + intended_body + control_witness_raw + intended_tail


def apply_intended_witness_container(signed: bytes, intended: bytes) -> bytes:
    """Retain the signature over the final body and replace only witness key 5."""

    signed_body, signed_witness_raw, signed_tail = split_transaction(signed)
    intended_body, intended_witness_raw, intended_tail = split_transaction(intended)
    if signed_body != intended_body:
        raise ValueError("signed body does not match the intended final body")
    if signed_tail != intended_tail:
        raise ValueError("signed transaction tail does not match the intended transaction")
    signed_witnesses = cbor2.loads(signed_witness_raw)
    intended_witnesses = cbor2.loads(intended_witness_raw)
    if not isinstance(signed_witnesses, dict) or not isinstance(
        intended_witnesses, dict
    ):
        raise ValueError("signed or intended witness set is not a map")
    for key, value in intended_witnesses.items():
        if key not in (0, 5) and signed_witnesses.get(key) != value:
            raise ValueError("signed transaction changed a non-redeemer witness")
    final_witnesses = dict(signed_witnesses)
    if 5 in intended_witnesses:
        final_witnesses[5] = intended_witnesses[5]
    else:
        final_witnesses.pop(5, None)
    return b"\x84" + signed_body + cbor2.dumps(final_witnesses) + signed_tail


def funding_inputs(setup_transaction_id: str) -> dict[tuple[str, str], str]:
    """Assign one shared mutation input per case and isolated control inputs."""

    if len(setup_transaction_id) != 64 or any(
        character not in "0123456789abcdef" for character in setup_transaction_id
    ):
        raise ValueError("setup transaction id must be lowercase hex")
    assigned = {}
    index = 0
    for case in LIVE_CASES:
        if case.control:
            for role in ("target", "reference"):
                assigned[(case.case_id, role)] = f"{setup_transaction_id}#{index}"
                index += 1
        else:
            value = f"{setup_transaction_id}#{index}"
            assigned[(case.case_id, "target")] = value
            assigned[(case.case_id, "reference")] = value
            index += 1
    return assigned


def funding_collateral_inputs(
    setup_transaction_id: str,
) -> dict[tuple[str, str], str]:
    """Assign collateral distinct from every spending input for script cases."""

    spending = funding_inputs(setup_transaction_id)
    index = len(set(spending.values()))
    assigned = {}
    for case in LIVE_CASES:
        if case.family != "script":
            continue
        if case.control:
            for role in ("target", "reference"):
                assigned[(case.case_id, role)] = f"{setup_transaction_id}#{index}"
                index += 1
        else:
            value = f"{setup_transaction_id}#{index}"
            assigned[(case.case_id, "target")] = value
            assigned[(case.case_id, "reference")] = value
            index += 1
    return assigned


def _funding_output_count() -> int:
    setup_id = "0" * 64
    return len(
        set(funding_inputs(setup_id).values())
        | set(funding_collateral_inputs(setup_id).values())
    )


def build_funding_setup_command(
    *,
    cli: str,
    socket_path: str,
    network_magic: int,
    funding_input: str,
    address: str,
    output_file: Any,
) -> list[str]:
    """Build one fresh split transaction; no historical input is accepted implicitly."""

    if not funding_input or "#" not in funding_input:
        raise ValueError("fresh funding input is required")
    command = [
        cli,
        "conway",
        "transaction",
        "build",
        "--socket-path",
        socket_path,
        "--testnet-magic",
        str(network_magic),
        "--tx-in",
        funding_input,
        "--change-address",
        address,
    ]
    for _index in range(_funding_output_count()):
        command.extend(["--tx-out", f"{address}+{FUNDING_OUTPUT_LOVELACE}"])
    command.extend(["--out-file", str(output_file)])
    return command


def validate_runtime_identity(
    runtime: dict[str, Any],
    *,
    target_node: str,
    reference_node: str,
    expected_target_version: str,
    expected_target_source: str,
    expected_reference_version: str,
    expected_reference_source: str,
) -> dict[str, dict[str, Any]]:
    """Bind the generated ledger to exact independently verified executables."""

    if runtime.get("network") != "testnet_42" or runtime.get("network_magic") != 42:
        raise RuntimeError("live controls require the owned private testnet_42 substrate")
    raw_nodes = runtime.get("nodes") or []
    if not isinstance(raw_nodes, list):
        raise RuntimeError("runtime node metadata is invalid")
    nodes = {str(node.get("id") or ""): node for node in raw_nodes if isinstance(node, dict)}
    target = nodes.get(target_node)
    reference = nodes.get(reference_node)
    if not target or target.get("impl") != "amaru":
        raise RuntimeError("target node is not the exact Amaru runtime")
    if not reference or reference.get("impl") != "cardano-node":
        raise RuntimeError("reference node is not the exact Cardano runtime")

    checks = (
        (target, "resolved_version", expected_target_version, "target version"),
        (target, "source_revision", expected_target_source, "target source revision"),
        (reference, "resolved_version", expected_reference_version, "reference version"),
        (reference, "source_revision", expected_reference_source, "reference source revision"),
    )
    for node, field, expected, label in checks:
        if str(node.get(field) or "") != expected:
            raise RuntimeError(f"{label} does not match")
    for node, role in ((target, "target"), (reference, "reference")):
        if (node.get("artifact_identity") or {}).get("satisfied") is not True:
            raise RuntimeError(f"{role} artifact identity is not verified")
        if (node.get("version_identity") or {}).get("satisfied") is not True:
            raise RuntimeError(f"{role} reported version identity is not verified")
        if not str(node.get("container_id") or "") or not str(
            node.get("container_image_id") or ""
        ).startswith("sha256:"):
            raise RuntimeError(f"{role} container identity is incomplete")
    return {"target": dict(target), "reference": dict(reference)}


def _has_missing_redeemer_diagnostic(observation: dict[str, Any]) -> bool:
    body = str(observation.get("body") or "").lower()
    if observation.get("classification") != "ledger_reject":
        return False
    if any(
        marker in body
        for marker in ("missingredeemers", "missing redeemers", "noredeemer", "no redeemer")
    ):
        return True

    derived = (
        ((observation.get("protocol_receipt") or {}).get("reject_evidence") or {}).get(
            "source_bound_derivation"
        )
        or {}
    )
    if (
        not isinstance(derived, dict)
        or derived.get("schema_version") != 1
        or derived.get("status") != "exact-source-bound-derivation"
        or derived.get("negotiated_version") != "NodeToClientV_20"
        or derived.get("era") != "Conway"
        or derived.get("apply_tx_error") != "ConwayApplyTxError"
        or derived.get("derivation_lineage")
        != {
            "cardano_node_source_revision": _CARDANO_NODE_11_1_2_SOURCE,
            "ouroboros_network_source_revision": _CARDANO_NODE_11_1_2_NETWORK_SOURCE,
            "ouroboros_consensus_source_revision": _CARDANO_NODE_11_1_2_CONSENSUS_SOURCE,
            "cardano_ledger_source_revision": _CARDANO_NODE_11_1_2_LEDGER_SOURCE,
        }
    ):
        return False
    for failure in derived.get("predicate_failures") or []:
        if not isinstance(failure, dict):
            continue
        policy_id = failure.get("policy_id")
        if (
            failure.get("path") == ["ConwayUtxowFailure", "MissingRedeemers"]
            and failure.get("plutus_purpose") == "ConwayMinting"
            and isinstance(policy_id, str)
            and re.fullmatch(r"[0-9a-f]{56}", policy_id) is not None
            and failure.get("script_hash") == policy_id
        ):
            return True
    return False


def reconcile_control_inclusions(
    records: list[dict[str, Any]], output_dir: Path
) -> list[dict[str, Any]]:
    """Derive late control inclusion from retained UTxO queries without rewriting observations."""

    retained_queries: list[tuple[Path, bytes, dict[str, Any]]] = []
    for path in sorted(output_dir.glob("*-included-utxo.json")):
        raw = path.read_bytes()
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(body, dict):
            retained_queries.append((path, raw, body))

    control_case_ids = {case.case_id for case in LIVE_CASES if case.control}
    reconciled = []
    for record in records:
        if str(record.get("case_id") or "") not in control_case_ids:
            continue
        observation = record.get("observation") or {}
        if observation.get("classification") != "accepted":
            continue
        if observation.get("reference_chain_included") is True:
            continue
        transaction_id = str(observation.get("transaction_id") or "")
        if not transaction_id:
            continue
        evidence = []
        for path, raw, body in retained_queries:
            if any(
                str(output_ref) == transaction_id
                or str(output_ref).startswith(f"{transaction_id}#")
                for output_ref in body
            ):
                evidence.append(
                    {
                        "path": path.name,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
        if evidence:
            reconciled.append(
                {
                    "case_id": str(record.get("case_id") or ""),
                    "role": str(record.get("role") or ""),
                    "transaction_id": transaction_id,
                    "initial_reference_chain_included": False,
                    "reconciled_reference_chain_included": True,
                    "evidence": evidence,
                }
            )
    return reconciled


def assess_live_controls(
    records: list[dict[str, Any]],
    *,
    inclusion_reconciliation: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess control validity without requiring a hypothesized divergence."""

    by_key = {
        (str(record.get("case_id")), str(record.get("role"))): record
        for record in records
    }
    expected = {
        (case.case_id, role)
        for case in LIVE_CASES
        for role in ("target", "reference")
    }
    complete_matrix = set(by_key) == expected
    case_inputs: list[str] = []
    funding_isolated = complete_matrix
    payloads_match = complete_matrix
    for case in LIVE_CASES:
        target = by_key.get((case.case_id, "target")) or {}
        reference = by_key.get((case.case_id, "reference")) or {}
        target_input = str(target.get("funding_input") or "")
        reference_input = str(reference.get("funding_input") or "")
        if case.control:
            funding_isolated = funding_isolated and bool(target_input) and bool(
                reference_input
            ) and target_input != reference_input
            case_inputs.extend((target_input, reference_input))
        else:
            funding_isolated = funding_isolated and bool(target_input) and (
                target_input == reference_input
            )
            case_inputs.append(target_input)
            payloads_match = payloads_match and bool(target.get("payload_sha256")) and (
                target.get("payload_sha256") == reference.get("payload_sha256")
            )
    funding_isolated = funding_isolated and len(case_inputs) == len(set(case_inputs))

    reconciled_by_key = {
        (str(item.get("case_id") or ""), str(item.get("role") or "")): item
        for item in inclusion_reconciliation or []
        if item.get("reconciled_reference_chain_included") is True
        and item.get("evidence")
    }

    controls_valid = complete_matrix
    matched_reconciled_keys: set[tuple[str, str]] = set()
    for case in (case for case in LIVE_CASES if case.control):
        for role in ("target", "reference"):
            observation = (by_key.get((case.case_id, role)) or {}).get("observation") or {}
            reconciliation = reconciled_by_key.get((case.case_id, role)) or {}
            reconciliation_matches = (
                bool(reconciliation)
                and str(reconciliation.get("transaction_id") or "")
                == str(observation.get("transaction_id") or "")
            )
            if reconciliation_matches and observation.get(
                "reference_chain_included"
            ) is not True:
                matched_reconciled_keys.add((case.case_id, role))
            controls_valid = controls_valid and (
                observation.get("classification") == "accepted"
                and (
                    observation.get("reference_chain_included") is True
                    or reconciliation_matches
                )
                and bool(observation.get("transaction_id"))
            )

    target_missing = (
        (by_key.get(("script-redeemers-omitted", "target")) or {}).get("observation")
        or {}
    )
    reference_missing = (
        (by_key.get(("script-redeemers-omitted", "reference")) or {}).get("observation")
        or {}
    )
    reference_missing_evidence = complete_matrix and _has_missing_redeemer_diagnostic(
        reference_missing
    )
    target_missing_classification = str(target_missing.get("classification") or "")
    classifications = [
        str((record.get("observation") or {}).get("classification") or "")
        for record in records
    ]
    conclusive = complete_matrix and all(
        value in {"accepted", "decode_reject", "ledger_reject", "phase2_reject"}
        for value in classifications
    )
    complete = bool(
        complete_matrix
        and funding_isolated
        and payloads_match
        and controls_valid
        and reference_missing_evidence
        and conclusive
    )
    if not complete_matrix:
        reason = "live control matrix is incomplete"
    elif not funding_isolated:
        reason = "live control funding inputs are not isolated"
    elif not payloads_match:
        reason = "target and reference mutation payload bytes do not match"
    elif not conclusive:
        reason = "one or more live outcomes are unavailable or inconclusive"
    elif not controls_valid:
        reason = "normal live controls were not independently accepted and included"
    elif not reference_missing_evidence:
        reason = "reference missing-redeemer control lacks an explicit ledger diagnostic"
    else:
        reason = "live controls and mutation observations are complete"
    return {
        "valid_control_present": controls_valid,
        "missing_redeemer_evidence": reference_missing_evidence,
        "reference_missing_redeemer_evidence": reference_missing_evidence,
        "target_missing_redeemer_classification": target_missing_classification,
        "funding_inputs_isolated": funding_isolated,
        "mutation_payloads_match": payloads_match,
        "outcomes_conclusive": conclusive,
        "reconciled_control_inclusion_count": len(matched_reconciled_keys),
        "complete": complete,
        "reason": reason,
    }

def execute_case_matrix(
    *,
    assigned: dict[tuple[str, str], str],
    build_case: Callable[[LiveCase, str, str], dict[str, Any]],
    submit_case: Callable[[str, bytes], dict[str, Any]],
    wait_for_reference_chain_inclusion: Callable[
        [str, str, str, dict[str, Any]], bool
    ],
) -> list[dict[str, Any]]:
    """Submit shared mutation bytes to both endpoints before any chain wait."""

    records = []
    for case in LIVE_CASES:
        if not case.control:
            accepted_controls = {
                (record["case_id"], record["role"])
                for record in records
                if record.get("control") is True
                and (record.get("observation") or {}).get("classification") == "accepted"
            }
            required_controls = {
                (control.case_id, role)
                for control in LIVE_CASES
                if control.control
                for role in ("target", "reference")
            }
            if accepted_controls != required_controls:
                raise RuntimeError(
                    "all normal controls must receive AcceptTx before mutation submission"
                )
            funding_input = assigned[(case.case_id, "target")]
            if funding_input != assigned[(case.case_id, "reference")]:
                raise RuntimeError("mutation payload funding inputs do not match")
            built = dict(build_case(case, "shared", funding_input))
            payload = built.pop("payload")
            if not isinstance(payload, bytes) or not payload:
                raise RuntimeError("built live control has no transaction bytes")
            transaction_id = str(built.get("transaction_id") or "")
            if not transaction_id:
                raise RuntimeError("built live control has no transaction id")
            payload_sha256 = hashlib.sha256(payload).hexdigest()
            responses = {
                role: dict(submit_case(role, payload))
                for role in ("target", "reference")
            }
            classifications = {
                role: _classify_live_response(response)
                for role, response in responses.items()
            }
            reference_chain_included = False
            if classifications["reference"] == "accepted":
                reference_chain_included = (
                    wait_for_reference_chain_inclusion(
                        transaction_id,
                        case.case_id,
                        "reference",
                        responses["reference"],
                    )
                    is True
                )
            for role in ("target", "reference"):
                observation = {
                    **responses[role],
                    "classification": classifications[role],
                    "transaction_id": transaction_id,
                    "reference_chain_included": reference_chain_included,
                }
                records.append(
                    {
                        "case_id": case.case_id,
                        "family": case.family,
                        "mutation": case.mutation,
                        "control": False,
                        "role": role,
                        "funding_input": funding_input,
                        "payload_sha256": payload_sha256,
                        **built,
                        "observation": observation,
                    }
                )
            continue

        for role in ("target", "reference"):
            funding_input = assigned[(case.case_id, role)]
            built = dict(build_case(case, role, funding_input))
            payload = built.pop("payload")
            if not isinstance(payload, bytes) or not payload:
                raise RuntimeError("built live control has no transaction bytes")
            transaction_id = str(built.get("transaction_id") or "")
            if not transaction_id:
                raise RuntimeError("built live control has no transaction id")
            response = dict(submit_case(role, payload))
            classification = _classify_live_response(response)
            reference_chain_included = False
            if classification == "accepted":
                reference_chain_included = (
                    wait_for_reference_chain_inclusion(
                        transaction_id, case.case_id, role, response
                    )
                    is True
                )
            observation = {
                **response,
                "classification": classification,
                "transaction_id": transaction_id,
                "reference_chain_included": reference_chain_included,
            }
            records.append(
                {
                    "case_id": case.case_id,
                    "family": case.family,
                    "mutation": case.mutation,
                    "control": case.control,
                    "role": role,
                    "funding_input": funding_input,
                    **built,
                    "observation": observation,
                }
            )
    return records


def _envelope(path: Path) -> tuple[dict[str, Any], bytes]:
    body = json.loads(path.read_text(encoding="utf-8"))
    payload = bytes.fromhex(str(body.get("cborHex") or ""))
    if not payload:
        raise RuntimeError(f"transaction envelope is empty: {path.name}")
    return body, payload


def _write_envelope(path: Path, template: dict[str, Any], payload: bytes) -> None:
    body = dict(template)
    body["cborHex"] = payload.hex()
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _select_funding_input(
    utxos: dict[str, Any], *, minimum_lovelace: int
) -> tuple[str, int]:
    candidates = []
    for reference, entry in utxos.items():
        value = entry.get("value") if isinstance(entry, dict) else None
        lovelace = value.get("lovelace") if isinstance(value, dict) else None
        if isinstance(lovelace, int) and not isinstance(lovelace, bool):
            candidates.append((lovelace, str(reference)))
    eligible = [candidate for candidate in candidates if candidate[0] >= minimum_lovelace]
    if not eligible:
        raise RuntimeError(
            "fresh substrate funding address has no UTxO with enough lovelace"
        )
    lovelace, reference = max(eligible)
    return reference, lovelace


def _largest_lovelace_input(utxos: dict[str, Any]) -> str:
    """Compatibility wrapper for callers that only require a positive input."""

    return _select_funding_input(utxos, minimum_lovelace=1)[0]


def _query_reference_tip(
    *, cli: str, socket_path: str, network_magic: int
) -> dict[str, Any]:
    result = _checked(
        [
            cli,
            "query",
            "tip",
            "--socket-path",
            socket_path,
            "--testnet-magic",
            str(network_magic),
        ]
    )
    body = json.loads(result.stdout)
    if not isinstance(body.get("block"), int) or not isinstance(body.get("slot"), int):
        raise RuntimeError("reference tip response lacks block or slot")
    return {
        "block": body["block"],
        "slot": body["slot"],
        "hash": body.get("hash"),
    }


def _query_reference_mempool(
    *, cli: str, socket_path: str, network_magic: int, transaction_id: str
) -> bool | None:
    result = subprocess.run(
        [
            cli,
            "query",
            "tx-mempool",
            "--socket-path",
            socket_path,
            "--testnet-magic",
            str(network_magic),
            "tx-exists",
            transaction_id,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = text.lower()
    if isinstance(body, bool):
        return body
    if isinstance(body, dict):
        for key in ("exists", "txExists", "present"):
            if isinstance(body.get(key), bool):
                return body[key]
    if body in ("true", "false"):
        return body == "true"
    return None


def _wait_for_reference_inclusion(
    *,
    cli: str,
    socket_path: str,
    network_magic: int,
    address: str,
    destination: Path,
    transaction_id: str,
    initial_tip: dict[str, Any],
    evidence_path: Path,
    timeout_seconds: float = 360,
    target_propagation_gate: dict[str, Any] | None = None,
    terminal_progress_blocks: int = 1,
) -> dict[str, Any]:
    """Wait through real reference-chain progress, retaining bounded evidence."""

    if (
        isinstance(terminal_progress_blocks, bool)
        or not isinstance(terminal_progress_blocks, int)
        or terminal_progress_blocks < 1
    ):
        raise RuntimeError("terminal progress block threshold must be a positive integer")
    initial_block = initial_tip.get("block")
    if not isinstance(initial_block, int):
        raise RuntimeError("initial reference tip lacks block number")
    propagation_evidence: dict[str, Any] | None = None
    propagation_eligible_at: float | None = None
    post_delay_progress_baseline: dict[str, Any] | None = None
    if target_propagation_gate is not None:
        reference_started = target_propagation_gate.get(
            "reference_started_epoch_seconds"
        )
        init_delay = target_propagation_gate.get(
            "tx_submission_init_delay_seconds"
        )
        if (
            isinstance(reference_started, bool)
            or not isinstance(reference_started, (int, float))
            or isinstance(init_delay, bool)
            or not isinstance(init_delay, (int, float))
            or float(init_delay) <= 0
        ):
            raise RuntimeError(
                "target propagation requires retained reference startup time "
                "and TxSubmissionInitDelay"
            )
        propagation_eligible_at = float(reference_started) + float(init_delay)
        propagation_evidence = {
            "source": target_propagation_gate.get("source"),
            "log_path": target_propagation_gate.get("log_path"),
            "delay_source": target_propagation_gate.get("delay_source"),
            "startup_source": target_propagation_gate.get("startup_source"),
            "reference_container_id": target_propagation_gate.get(
                "reference_container_id"
            ),
            "reference_container_image_id": target_propagation_gate.get(
                "reference_container_image_id"
            ),
            "reference_container_image_reference": target_propagation_gate.get(
                "reference_container_image_reference"
            ),
            "reference_started_at": target_propagation_gate.get(
                "reference_started_at"
            ),
            "reference_started_epoch_seconds": float(reference_started),
            "tx_submission_init_delay_seconds": float(init_delay),
            "eligible_at": target_propagation_gate.get("eligible_at"),
            "eligible_at_epoch_seconds": propagation_eligible_at,
            "post_delay_progress_baseline": None,
        }
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    while True:
        utxos = _query_utxo(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=address,
            destination=destination,
        )
        tip = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        mempool_contains = _query_reference_mempool(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            transaction_id=transaction_id,
        )
        included = any(key.startswith(transaction_id + "#") for key in utxos)
        post_submit_blocks = max(0, int(tip["block"]) - initial_block)
        elapsed = time.monotonic() - started
        propagation_delay_elapsed = False
        post_delay_progress_blocks = 0
        terminal_non_inclusion_eligible = (
            post_submit_blocks >= terminal_progress_blocks
        )
        if propagation_evidence is not None:
            propagation_delay_elapsed = time.time() >= propagation_eligible_at
            if propagation_delay_elapsed and post_delay_progress_baseline is None:
                post_delay_progress_baseline = dict(tip)
                propagation_evidence["post_delay_progress_baseline"] = dict(tip)
            if post_delay_progress_baseline is not None:
                post_delay_progress_blocks = max(
                    0,
                    int(tip["block"])
                    - int(post_delay_progress_baseline["block"]),
                )
            terminal_non_inclusion_eligible = (
                propagation_delay_elapsed
                and post_delay_progress_blocks >= terminal_progress_blocks
            )
        observation = {
            "elapsed_seconds": round(elapsed, 3),
            "tip": tip,
            "post_submit_blocks": post_submit_blocks,
            "mempool_contains": mempool_contains,
            "included": included,
        }
        if propagation_evidence is not None:
            observation.update(
                {
                    "propagation_delay_elapsed": propagation_delay_elapsed,
                    "post_delay_progress_blocks": post_delay_progress_blocks,
                    "terminal_non_inclusion_eligible": (
                        terminal_non_inclusion_eligible
                    ),
                }
            )
        if not observations or any(
            observation[key] != observations[-1][key]
            for key in ("tip", "post_submit_blocks", "mempool_contains", "included")
        ):
            observations.append(observation)
        else:
            observations[-1]["elapsed_seconds"] = observation["elapsed_seconds"]
        evidence = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "initial_tip": initial_tip,
            "status": "waiting",
            "terminal_progress_block_threshold": terminal_progress_blocks,
            "observations": observations,
        }
        if propagation_evidence is not None:
            evidence["target_propagation_gate"] = propagation_evidence
        if included:
            evidence["status"] = "included"
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return utxos
        if terminal_non_inclusion_eligible and mempool_contains is False:
            final_utxos = _query_utxo(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=address,
                destination=destination,
            )
            final_included = any(
                key.startswith(transaction_id + "#") for key in final_utxos
            )
            observations.append(
                {
                    **observation,
                    "included": final_included,
                    "terminal_utxo_recheck": True,
                }
            )
            if final_included:
                evidence["status"] = "included"
                evidence_path.write_text(
                    json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                )
                return final_utxos
            evidence["status"] = "not-included-after-reference-progress"
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError(
                f"transaction {transaction_id} was absent after {post_submit_blocks} "
                "post-submit reference block(s)"
            )
        if elapsed >= timeout_seconds:
            evidence["status"] = "timed-out"
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError(
                f"transaction {transaction_id} was not observed after "
                f"{post_submit_blocks} post-submit reference block(s)"
            )
        time.sleep(0.5)


def _wait_for_target_adoption(
    *,
    log_path: Path,
    expected_tip: dict[str, Any],
    evidence_path: Path,
    timeout_seconds: float = 180,
    minimum_log_offset: int = 0,
) -> dict[str, Any]:
    """Require Amaru to adopt the exact reference block before spending it."""

    block = expected_tip.get("block")
    slot = expected_tip.get("slot")
    header_hash = str(expected_tip.get("hash") or "")
    if (
        not isinstance(block, int)
        or not isinstance(slot, int)
        or len(header_hash) != 64
        or any(character not in "0123456789abcdef" for character in header_hash)
    ):
        raise RuntimeError("reference funding tip identity is incomplete")
    expected = {"block": block, "slot": slot, "hash": header_hash}
    if (
        isinstance(minimum_log_offset, bool)
        or not isinstance(minimum_log_offset, int)
        or minimum_log_offset < 0
    ):
        raise RuntimeError("target adoption log offset is invalid")
    started = time.monotonic()
    last_diagnostic = "target log has not reported the exact funding block"
    while True:
        if log_path.is_file():
            with log_path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(minimum_log_offset, size - 262_144))
                text = stream.read().decode("utf-8", "replace")
            matching = [line for line in text.splitlines() if header_hash in line]
            for line in matching:
                if 'outcome="invalid_header"' in line or "Invalid VRF proof" in line:
                    evidence = {
                        "schema_version": 1,
                        "status": "invalid-header",
                        "expected_tip": expected,
                        "minimum_log_offset": minimum_log_offset,
                        "blocking_diagnostic": line[-4096:],
                    }
                    evidence_path.write_text(
                        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                    )
                    raise RuntimeError(
                        "target rejected the exact funding block before live controls"
                    )
                if (
                    "tip.adopt" in line
                    and f"slot={slot}" in line
                    and f"block_height={block}" in line
                ):
                    evidence = {
                        "schema_version": 1,
                        "status": "adopted",
                        "expected_tip": expected,
                        "minimum_log_offset": minimum_log_offset,
                        "adoption_diagnostic": line[-4096:],
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                    evidence_path.write_text(
                        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                    )
                    return evidence
            if matching:
                last_diagnostic = matching[-1][-4096:]
        if time.monotonic() - started >= timeout_seconds:
            evidence = {
                "schema_version": 1,
                "status": "not-adopted",
                "expected_tip": expected,
                "minimum_log_offset": minimum_log_offset,
                "blocking_diagnostic": last_diagnostic,
                "timeout_seconds": timeout_seconds,
            }
            evidence_path.write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
            raise RuntimeError(
                "target did not adopt the exact funding block before live controls"
            )
        time.sleep(2)


def _submit_amaru_container(container_id: str, payload: bytes) -> dict[str, Any]:
    """Submit raw bytes to the already-running Amaru built-in API."""

    request = (
        b"POST /api/submit/tx HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/cbor\r\n"
        + f"Content-Length: {len(payload)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + payload
    )
    try:
        result = subprocess.run(
            [
                "docker", "exec", "-i", container_id,
                "/bin/busybox", "nc", "-w", "30", "127.0.0.1", "3011",
            ],
            input=request,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {"http": 0, "body": "", "transport": "Amaru submit timed out or failed"}
    if result.returncode != 0:
        return {"http": 0, "body": "", "transport": "amaru submit API unavailable"}
    header, _, raw_body = result.stdout.partition(b"\r\n\r\n")
    first = header.splitlines()[0].decode("ascii", "replace") if header else ""
    parts = first.split()
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        return {"http": 0, "body": "", "transport": "invalid Amaru submit response"}
    return {"http": status, "body": raw_body[:4096].decode("utf-8", "replace")}


def _submit_reference_cli(cli: str, path: Path, *, socket_path: str, network_magic: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                cli, "conway", "transaction", "submit", "--tx-file", str(path),
                "--socket-path", socket_path, "--testnet-magic", str(network_magic),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {
            "http": 0,
            "body": "",
            "transport": "reference submit timed out or failed",
            "observation_boundary": "client-transport",
            "node_response_observed": False,
        }
    body = (result.stderr.strip() or result.stdout.strip())[:4096]
    if result.returncode == 0:
        return {
            "http": 202,
            "body": body or "accepted",
            "observation_boundary": "node-submit-accepted",
            "node_response_observed": True,
        }
    lowered = body.lower()
    client_decode_markers = (
        "textenvelope decode error",
        "deserialisefailure",
        "failed to decode ledger cddl",
    )
    if any(marker in lowered for marker in client_decode_markers):
        return {
            "http": 0,
            "body": body,
            "transport": "reference CLI rejected transaction before node submission",
            "observation_boundary": "client-decode",
            "node_response_observed": False,
        }
    validation_markers = (
        "submitfail", "validation", "badinputsutxo", "missingredeemers",
        "noredeemer", "decoder", "deserialise", "fee", "ppviewhashes",
    )
    validation_response = any(marker in lowered for marker in validation_markers)
    return {
        "http": 400 if validation_response else 0,
        "body": body,
        "observation_boundary": (
            "node-submit-rejected" if validation_response else "client-submit-error"
        ),
        "node_response_observed": validation_response,
    }


def _submit_reference_raw(
    *,
    runtime: dict[str, Any],
    reference: dict[str, Any],
    payload: bytes,
    helper_image: str,
) -> dict[str, Any]:
    """Submit exact bytes through one bounded, scenario-owned N2C helper."""

    if not (
        _REPO_DIGEST_IMAGE.fullmatch(helper_image)
        or _CONFIG_ID_IMAGE.fullmatch(helper_image)
    ):
        raise RuntimeError("raw LocalTxSubmission helper image is not immutable")
    installation_id = str(os.environ.get("DWARF_INSTALLATION_ID") or "")
    compose_project = str(runtime.get("compose_project") or "")
    topology_id = str(runtime.get("profile_id") or "")
    node_id = str(reference.get("id") or "")
    container_id = str(reference.get("container_id") or "")
    image_id = str(reference.get("container_image_id") or "")
    source_revision = str(reference.get("source_revision") or "")
    socket_path = str(reference.get("container_socket_path") or "")
    network_magic = runtime.get("network_magic")
    if (
        not installation_id
        or not compose_project
        or not topology_id
        or node_id != "node2"
        or not re.fullmatch(r"[0-9a-f]{64}", container_id)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
        or not socket_path.startswith("/env/socket/")
        or isinstance(network_magic, bool)
        or not isinstance(network_magic, int)
    ):
        raise RuntimeError("raw LocalTxSubmission runtime identity is incomplete")

    inspected = _inspect_container(container_id)
    container_config = inspected.get("Config") or {}
    labels = container_config.get("Labels") or {}
    runtime_user = str(container_config.get("User") or "")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*)", runtime_user):
        raise RuntimeError(
            "raw LocalTxSubmission reference has no exact numeric runtime user"
        )
    expected = {
        "installation_id": installation_id,
        "compose_project": compose_project,
        "topology_id": topology_id,
        "node_id": node_id,
        "container_id": container_id,
        "container_image_id": image_id,
        "source_revision": source_revision,
        "socket_path": socket_path,
        "network_magic": network_magic,
    }
    observed = {
        "installation_id": str(labels.get("io.dwarf.installation") or ""),
        "compose_project": str(labels.get("com.docker.compose.project") or ""),
        "topology_id": str(labels.get("io.dwarf.topology") or ""),
        "node_id": str(labels.get("io.dwarf.node_id") or ""),
        "container_id": str(inspected.get("Id") or ""),
        "container_image_id": str(inspected.get("Image") or ""),
        # Source and socket are frozen in the already-validated runtime identity;
        # Docker labels do not duplicate either value.
        "source_revision": source_revision,
        "socket_path": socket_path,
        "network_magic": int(str(labels.get("io.dwarf.network_magic") or "-1")),
    }
    if labels.get("io.dwarf.managed") != "true" or labels.get(
        "io.dwarf.implementation"
    ) != "cardano-node" or observed != expected:
        raise RuntimeError("raw LocalTxSubmission reference ownership does not match")

    try:
        helper_proc = subprocess.run(
            ["docker", "image", "inspect", helper_image],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        helper_values = json.loads(helper_proc.stdout) if helper_proc.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        helper_values = []
    if (
        not isinstance(helper_values, list)
        or len(helper_values) != 1
        or not isinstance(helper_values[0], dict)
    ):
        raise RuntimeError("raw LocalTxSubmission helper image inspection failed")
    helper_inspect = helper_values[0]
    helper_config_id = str(helper_inspect.get("Id") or "")
    helper_kind = (
        "local_config_id"
        if _CONFIG_ID_IMAGE.fullmatch(helper_image)
        else "repository_digest"
    )
    if (
        not _CONFIG_ID_IMAGE.fullmatch(helper_config_id)
        or (helper_kind == "local_config_id" and helper_config_id != helper_image)
        or (
            helper_kind == "repository_digest"
            and helper_image not in (helper_inspect.get("RepoDigests") or [])
        )
    ):
        raise RuntimeError("raw LocalTxSubmission helper image identity does not match")
    helper_labels = (helper_inspect.get("Config") or {}).get("Labels") or {}
    helper_revision = str(helper_labels.get("org.opencontainers.image.revision") or "")
    helper_component = str(helper_labels.get("io.dwarf.component") or "")
    if (
        (helper_inspect.get("Os"), helper_inspect.get("Architecture"))
        != ("linux", "amd64")
        or not re.fullmatch(r"[0-9a-f]{40}", helper_revision)
        or helper_component != "dwarf-localtx-submit-raw"
    ):
        raise RuntimeError("raw LocalTxSubmission helper image provenance is invalid")
    helper_identity = {
        "kind": helper_kind,
        "reference": helper_image,
        "config_id": helper_config_id,
        "platform": "linux/amd64",
        "source_revision": helper_revision,
        "component": helper_component,
    }

    request = {
        "schema_version": 1,
        "expected_identity": expected,
        "observed_identity": observed,
        "payload_hex": payload.hex(),
        "transaction_era": "conway",
        "timeout_seconds": 30,
    }
    command = [
        "docker", "run", "--rm", "--pull", "never",
        "-i",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "64",
        "--memory", "128m",
        "--cpus", "1",
        "--user", runtime_user,
        "--volumes-from", f"{container_id}:ro",
        "--entrypoint", "/usr/local/bin/dwarf-localtx-submit-raw",
        helper_image,
    ]
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(request, separators=(",", ":")),
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {
            "http": 0,
            "body": "",
            "transport": "raw LocalTxSubmission helper unavailable",
            "observation_boundary": "node-submit-transport",
            "node_response_observed": False,
        }
    if proc.returncode != 0:
        return {
            "http": 0,
            "body": "",
            "transport": "raw LocalTxSubmission helper failed",
            "observation_boundary": "node-submit-transport",
            "node_response_observed": False,
        }
    try:
        receipt = json.loads(proc.stdout)
    except json.JSONDecodeError:
        receipt = {}
    if (
        receipt.get("schema_version") != 1
        or receipt.get("payload_sha256") != hashlib.sha256(payload).hexdigest()
        or receipt.get("payload_length") != len(payload)
        or receipt.get("transaction_era") != "conway"
        or receipt.get("network_magic") != network_magic
    ):
        return {
            "http": 0,
            "body": "",
            "transport": "raw LocalTxSubmission receipt invalid",
            "observation_boundary": "node-submit-transport",
            "node_response_observed": False,
        }
    outcome = str(receipt.get("outcome") or "unknown")
    protocol_receipt = dict(receipt)
    raw_reject_evidence = protocol_receipt.pop("reject_evidence", None)
    reject_evidence, reject_evidence_error = _validated_reject_evidence(
        raw_reject_evidence, outcome
    )
    if reject_evidence is not None:
        reject_evidence.pop("source_bound_derivation", None)
        canonical_value = reject_evidence.get("canonical_cbor_base64")
        if isinstance(canonical_value, str):
            canonical = _decode_reject_base64(
                canonical_value, "canonical_cbor_base64"
            )
            derived = _derive_cardano_node_11_1_2_reject_term(
                canonical,
                source_revision=source_revision,
                negotiated_version=str(receipt.get("negotiated_version") or ""),
            )
            if derived is not None:
                reject_evidence["source_bound_derivation"] = derived
        protocol_receipt["reject_evidence"] = reject_evidence
    elif raw_reject_evidence is not None:
        protocol_receipt["reject_evidence_validation"] = {
            "status": "invalid",
            "reason": reject_evidence_error,
        }
    boundary = {
        "accepted": "node-submit-accepted",
        "node_rejected": "node-submit-rejected",
        "decode_close": "node-submit-decode-close",
        "transport_unavailable": "node-submit-transport",
    }.get(outcome, "node-submit-unknown")
    node_response = outcome in {"accepted", "node_rejected"}
    return {
        "http": 202 if outcome == "accepted" else 400 if outcome == "node_rejected" else 0,
        "body": str(receipt.get("rejection") or ("accepted" if outcome == "accepted" else ""))[:4096],
        "transport": None if node_response else str(receipt.get("detail") or outcome),
        "observation_boundary": boundary,
        "node_response_observed": node_response,
        "protocol_receipt": protocol_receipt,
        "helper_image": helper_image,
        "helper_identity": helper_identity,
        "identity_binding": expected,
    }


def _submit_reference_with_decoder_log(
    *,
    submit: Callable[[], dict[str, Any]],
    payload: bytes,
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Correlate one unavailable raw submission with its appended node log slice."""

    log_path = Path(str(reference.get("log_path") or ""))
    try:
        log_start_offset = log_path.stat().st_size if log_path.is_absolute() else -1
    except OSError:
        log_start_offset = -1
    attempt_started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = submit()
    attempt_ended_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if response.get("classification") != "unavailable" or log_start_offset < 0:
        return response

    payload_sha256 = hashlib.sha256(payload).hexdigest()
    receipt = response.get("protocol_receipt") or {}
    identity_binding = response.get("identity_binding") or {}
    identity = {
        "node_id": str(reference.get("id") or ""),
        "container_id": str(reference.get("container_id") or ""),
        "container_image_id": str(reference.get("container_image_id") or ""),
        "source_revision": str(reference.get("source_revision") or ""),
    }
    if (
        receipt.get("outcome") not in {"transport_unavailable", "decode_close"}
        or receipt.get("payload_sha256") != payload_sha256
        or receipt.get("payload_length") != len(payload)
        or any(identity_binding.get(key) != value for key, value in identity.items())
        or not re.fullmatch(r"[0-9a-f]{64}", identity["container_id"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity["container_image_id"])
        or not re.fullmatch(r"[0-9a-f]{40}", identity["source_revision"])
    ):
        return response

    try:
        log_end_offset = log_path.stat().st_size
        slice_length = log_end_offset - log_start_offset
        if not 0 < slice_length <= _MAX_REFERENCE_LOG_SLICE_BYTES:
            return response
        with log_path.open("rb") as stream:
            stream.seek(log_start_offset)
            log_slice = stream.read(slice_length)
    except OSError:
        return response
    if len(log_slice) != slice_length:
        return response

    matched_line = ""
    for line in log_slice.decode("utf-8", errors="replace").splitlines():
        lowered = line.lower()
        if (
            "miniprotocolnum 6" in lowered
            and "responderdir terminated with exception decoderfailure" in lowered
            and "localtxsubmission" in lowered
            and "deserialisefailure" in lowered
        ):
            matched_line = line[:4096]
            break
    if not matched_line:
        return response

    return {
        **response,
        "classification": "decode_reject",
        "observation_boundary": "node-log-correlated-decode-reject",
        "node_log_observed": True,
        "reference_decoder_evidence": {
            "schema_version": 1,
            "classification": "decode_reject",
            "correlation_method": "same-attempt-appended-log-slice",
            "payload_sha256": payload_sha256,
            "payload_length": len(payload),
            "attempt_started_at": attempt_started_at,
            "attempt_ended_at": attempt_ended_at,
            "identity": identity,
            "log_path": str(log_path),
            "log_start_offset": log_start_offset,
            "log_end_offset": log_end_offset,
            "log_slice_sha256": hashlib.sha256(log_slice).hexdigest(),
            "matched_line": matched_line,
        },
    }


def _classify_live_response(response: dict[str, Any]) -> str:
    """Honor only the exact same-attempt decoder classification over HTTP 0."""

    evidence = response.get("reference_decoder_evidence")
    if (
        response.get("classification") == "decode_reject"
        and response.get("observation_boundary") == "node-log-correlated-decode-reject"
        and isinstance(evidence, dict)
        and evidence.get("classification") == "decode_reject"
    ):
        return "decode_reject"
    from profile_manager.submit_primitives import _validation_stage

    return _validation_stage(response)


def run_fresh_funded_live_controls(
    *, runtime: dict[str, Any], output_dir: Path, corpus_dir: Path,
    target_node: str, reference_node: str,
    expected_target_version: str, expected_target_source: str,
    expected_reference_version: str, expected_reference_source: str,
) -> dict[str, Any]:
    """Construct, submit and retain one fresh private-ledger control matrix."""

    identities = validate_runtime_identity(
        runtime,
        target_node=target_node,
        reference_node=reference_node,
        expected_target_version=expected_target_version,
        expected_target_source=expected_target_source,
        expected_reference_version=expected_reference_version,
        expected_reference_source=expected_reference_source,
    )
    controller = _controller_container()
    if controller is None:
        raise RuntimeError("fresh live controls require the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    reference = identities["reference"]
    target = identities["target"]
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    target_container = str(target["container_id"])
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")
    target_propagation_gate = _reference_tx_submission_propagation_gate(
        reference, inspect_container=_inspect_container
    )
    if not (
        _REPO_DIGEST_IMAGE.fullmatch(helper_image)
        or _CONFIG_ID_IMAGE.fullmatch(helper_image)
    ):
        raise RuntimeError(
            "DWARF_LOCALTXSUBMISSION_HELPER_IMAGE must be an exact immutable reference"
        )

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])
        key_root = Path(str(workload_runtime["workload_key_root"]))
        address = _checked([
            cli, "latest", "genesis", "initial-addr", "--verification-key-file",
            str(key_root / "utxo.vkey"), "--testnet-magic", str(network_magic),
        ]).stdout.strip()
        if not address.startswith("addr_test"):
            raise RuntimeError("fresh funding address is invalid")
        funding_identity = runtime.get("generated_workload_funding") or {}
        if funding_identity.get("address") != address:
            raise RuntimeError(
                "fresh funding address does not match the generated substrate identity"
            )
        funding_utxos = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=output_dir / "funding-before.json",
        )
        required_lovelace = (
            _funding_output_count() * FUNDING_OUTPUT_LOVELACE
            + FUNDING_FEE_RESERVE_LOVELACE
        )
        fresh_input, fresh_input_lovelace = _select_funding_input(
            funding_utxos, minimum_lovelace=required_lovelace
        )
        setup_body = output_dir / "funding-setup.body"
        _checked(build_funding_setup_command(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            funding_input=fresh_input, address=address, output_file=setup_body,
        ))
        setup_tx = output_dir / "funding-setup.tx"
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(setup_body),
            "--signing-key-file", str(key_root / "utxo.skey"), "--testnet-magic",
            str(network_magic), "--out-file", str(setup_tx),
        ])
        setup_id = _transaction_id(cli, setup_tx)
        initial_tip = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        submit_result = _checked([
            cli, "conway", "transaction", "submit", "--tx-file", str(setup_tx),
            "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        ])
        (output_dir / "funding-setup-submit.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "transaction_id": setup_id,
                    "returncode": submit_result.returncode,
                    "stdout": submit_result.stdout[:4096],
                    "stderr": submit_result.stderr[:4096],
                    "initial_tip": initial_tip,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _wait_for_reference_inclusion(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=output_dir / "funding-ready.json",
            transaction_id=setup_id,
            initial_tip=initial_tip,
            evidence_path=output_dir / "funding-inclusion.json",
        )
        funding_evidence = json.loads(
            (output_dir / "funding-inclusion.json").read_text(encoding="utf-8")
        )
        included_observations = [
            observation
            for observation in funding_evidence.get("observations", [])
            if observation.get("included") is True
        ]
        if not included_observations:
            raise RuntimeError("reference funding inclusion omitted its terminal tip")
        target_log_text = str(target.get("log_path") or "")
        if not target_log_text:
            raise RuntimeError("target runtime identity omitted its log path")
        _wait_for_target_adoption(
            log_path=Path(target_log_text),
            expected_tip=dict(included_observations[-1].get("tip") or {}),
            evidence_path=output_dir / "funding-target-adoption.json",
        )
        assigned = funding_inputs(setup_id)
        collateral_assigned = funding_collateral_inputs(setup_id)

        pparams = output_dir / "protocol-parameters.json"
        _checked([
            cli, "query", "protocol-parameters", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--out-file", str(pparams),
        ])
        language = select_mint_language(json.loads(pparams.read_text(encoding="utf-8")))
        asset_root = Path(__file__).resolve().parents[1] / "corpora" / "redeemer-container" / "live-controls"
        script_source = (
            asset_root / "always-succeeds-v3.plutus"
            if language.name == "PlutusV3"
            else Path(__file__).resolve().parents[1] / "corpora" / "cardano-measurement" / "always-succeeds-v2.plutus"
        )
        script_path = output_dir / script_source.name
        unit_path = output_dir / "unit.json"
        script_path.write_bytes(script_source.read_bytes())
        unit_path.write_bytes((asset_root / "unit.json").read_bytes())
        policy_id = _checked([
            cli, "conway", "transaction", "policyid", "--script-file", str(script_path),
        ]).stdout.strip()
        token = f"1 {policy_id}.454d5054"

        def build(case: LiveCase, role: str, funding_input: str) -> dict[str, Any]:
            stem = f"{case.case_id}-{role}"
            raw_path = output_dir / f"{stem}.raw"
            command = [
                cli, "conway", "transaction", "build-raw", "--tx-in", funding_input,
                "--tx-out", f"{address}+19000000", "--fee", "1000000",
            ]
            if case.family == "script":
                collateral_role = role if case.control else "target"
                collateral_input = collateral_assigned[(case.case_id, collateral_role)]
                command[command.index("--tx-out") + 1] += f"+{token}"
                command.extend([
                    "--mint", token, "--mint-script-file", str(script_path),
                    "--mint-redeemer-file", str(unit_path), "--mint-execution-units",
                    "(500000000,1000000)", "--protocol-params-file", str(pparams),
                    "--tx-in-collateral", collateral_input,
                ])
            else:
                collateral_input = None
            command.extend(["--out-file", str(raw_path)])
            _checked(command)
            template, raw_payload = _envelope(raw_path)
            variants = (
                derive_script_variants(raw_payload, language)
                if case.family == "script"
                else derive_no_script_variants(raw_payload)
            )
            intended = variants[case.mutation]
            signable = prepare_variant_for_signing(raw_payload, intended)
            _write_envelope(raw_path, template, signable)
            signed_valid = output_dir / f"{stem}.signed-valid.tx"
            _checked([
                cli, "conway", "transaction", "sign", "--tx-body-file", str(raw_path),
                "--signing-key-file", str(key_root / "utxo.skey"), "--testnet-magic",
                str(network_magic), "--out-file", str(signed_valid),
            ])
            signed_template, signed_payload = _envelope(signed_valid)
            transaction_id = _transaction_id(cli, signed_valid)
            payload = apply_intended_witness_container(signed_payload, intended)
            signed = output_dir / f"{stem}.tx"
            _write_envelope(signed, signed_template, payload)
            final_body, final_witness_raw, _final_tail = split_transaction(payload)
            signed_body, signed_witness_raw, _signed_tail = split_transaction(
                signed_payload
            )
            return {
                "payload": payload,
                "transaction_id": transaction_id,
                "body_sha256": hashlib.sha256(final_body).hexdigest(),
                "cbor_sha256": hashlib.sha256(payload).hexdigest(),
                "signed_body_sha256": hashlib.sha256(signed_body).hexdigest(),
                "signed_witness_sha256": hashlib.sha256(signed_witness_raw).hexdigest(),
                "final_witness_sha256": hashlib.sha256(final_witness_raw).hexdigest(),
                "collateral_input": collateral_input,
            }

        submit_index = 0

        def submit(role: str, payload: bytes) -> dict[str, Any]:
            nonlocal submit_index
            submit_index += 1
            pre_submit_tip = _query_reference_tip(
                cli=cli, socket_path=socket_path, network_magic=network_magic
            )
            if role == "target":
                response = _submit_amaru_container(target_container, payload)
                return {**response, "pre_submit_reference_tip": pre_submit_tip}

            def submit_reference() -> dict[str, Any]:
                raw_response = _submit_reference_raw(
                    runtime=runtime,
                    reference=reference,
                    payload=payload,
                    helper_image=helper_image,
                )
                return {
                    **raw_response,
                    "classification": _classify_live_response(raw_response),
                }

            response = _submit_reference_with_decoder_log(
                submit=submit_reference,
                payload=payload,
                reference=reference,
            )
            return {**response, "pre_submit_reference_tip": pre_submit_tip}

        def wait_reference_chain(
            transaction_id: str,
            case_id: str,
            role: str,
            response: dict[str, Any],
        ) -> bool:
            initial_tip = response.get("pre_submit_reference_tip")
            if not isinstance(initial_tip, dict):
                raise RuntimeError("accepted submission lacks its pre-submit reference tip")
            try:
                _wait_for_reference_inclusion(
                    cli=cli, socket_path=socket_path, network_magic=network_magic,
                    address=address,
                    destination=output_dir / f"{case_id}-{role}-included-utxo.json",
                    transaction_id=transaction_id,
                    initial_tip=initial_tip,
                    evidence_path=output_dir / f"{case_id}-{role}-inclusion.json",
                    target_propagation_gate=(
                        target_propagation_gate if role == "target" else None
                    ),
                )
            except RuntimeError:
                return False
            return True

        from profile_manager.submit_primitives import run_redeemer_container_differential

        records = execute_case_matrix(
            assigned=assigned,
            build_case=build,
            submit_case=submit,
            wait_for_reference_chain_inclusion=wait_reference_chain,
        )

        # The fresh normal controls must establish both submission boundaries
        # before the immutable historical decoder corpus is interpreted.
        structural = run_redeemer_container_differential(
            corpus_dir=corpus_dir,
            target_url="target",
            reference_url="reference",
            submitter=lambda role, payload: submit(role, payload),
        )

    inclusion_reconciliation = reconcile_control_inclusions(records, output_dir)
    status = assess_live_controls(
        records, inclusion_reconciliation=inclusion_reconciliation
    )
    structural_diagnostics = dict(structural)
    return {
        "schema_version": 1,
        "evidence_scope": "structural_and_fresh_private_ledger_controls",
        "structural_diagnostics": structural_diagnostics,
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "funding_setup_transaction_id": setup_id,
        "funding_identity": {
            "address": address,
            "selected_input": fresh_input,
            "selected_input_lovelace": fresh_input_lovelace,
            "required_lovelace": required_lovelace,
        },
        "mint_language": language.name,
        "cases": records,
        "derived_inclusion_reconciliation": inclusion_reconciliation,
        "ledger_control_status": status,
        "checks": {
            "fresh_live_controls_complete": status["complete"],
            "mutation_payloads_match": status["mutation_payloads_match"],
            "funding_inputs_isolated": status["funding_inputs_isolated"],
            "outcomes_conclusive": status["outcomes_conclusive"],
        },
        "limitations": [
            "Reference-chain inclusion does not by itself prove target Amaru adoption.",
            "Duplicate, spent-input, transport, and non-diagnostic responses are inconclusive.",
            "The immutable historical corpus remains separate structural decoder evidence.",
        ],
    }
