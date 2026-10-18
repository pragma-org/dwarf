"""DWARF submit-level differential primitives.

Closes the framework gap for submit-level accept-invalid differentials (most ledger findings): submit
one fixture transaction to a target and a reference node's submit-API and record each node's outcome
(accept / reject / crash), then assert the per-node outcomes match expectation. Reusable for every
submit-level differential (the cert-class VoteDeleg/StakeVoteDeleg/UpdateDRep, value/witness edges, …).

Kept in a separate module so a wrapper error can never break profile_manager.primitives.
Outcome classification: HTTP 2xx -> "accept"; HTTP 4xx -> "reject"; connection refused / no response /
HTTP 5xx -> "crash" (the node aborted or became unreachable).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable
from urllib import request as _request, error as _error

from profile_manager.primitives import AssertionPrimitive, LoadPrimitive, _resolve_runtime_path

_SUBMIT_PATH = "/api/submit/tx"


def _run_dir(handle) -> Path:
    rd = getattr(handle, "run_dir", None)
    return Path(rd) if rd else Path.cwd()


def _node_submit_url(nodes: dict, node_id: str) -> str | None:
    """Resolve a node's submit-API URL from runtime metadata (best-effort across field names)."""
    node = nodes.get(node_id) or {}
    for key in ("submit_api_url", "submit_api", "submit_api_address", "submit_address"):
        val = node.get(key)
        if val:
            val = str(val)
            if val.startswith("http"):
                return val if _SUBMIT_PATH in val else val.rstrip("/") + _SUBMIT_PATH
            host, _, port = val.partition(":")
            host = "127.0.0.1" if host in ("", "0.0.0.0") else host
            return f"http://{host}:{port}{_SUBMIT_PATH}"
    return None


def _submit(url: str, payload: bytes, timeout: int = 25) -> dict:
    req = _request.Request(url, data=payload, method="POST",
                           headers={"Content-Type": "application/cbor"})
    try:
        with _request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
            return {"http": resp.status, "outcome": _classify(resp.status), "body": body[:800]}
    except _error.HTTPError as exc:  # 4xx/5xx
        body = exc.read(4096).decode("utf-8", "replace") if hasattr(exc, "read") else ""
        return {"http": exc.code, "outcome": _classify(exc.code), "body": body[:800]}
    except Exception as exc:  # noqa: BLE001  — connection refused / reset / timeout => node crashed/unreachable
        return {"http": 0, "outcome": "crash", "error": f"{type(exc).__name__}: {exc}"}


def _classify(code: int) -> str:
    if 200 <= code < 300:
        return "accept"
    if 400 <= code < 500:
        return "reject"
    return "crash"  # 0 (no response) or 5xx


_DECODE_MARKERS = (
    "decodererror",
    "deserialisefailure",
    "invalid cbor",
    "txcmdtxreaderror",
    "decode error",
    "expected type",
    "expected/found mismatch",
)

_PHASE2_MARKERS = (
    "validationtagmismatch",
    "failedunexpectedly",
    "passedunexpectedly",
    "failed phase two validation",
    "phase two validation",
    "validation tag mismatch",
)

_MASKED_REJECTION_MARKERS = (
    "badinputsutxo",
    "bad inputs",
    "already spent",
    "unknown input",
)

_NON_DIAGNOSTIC_LEDGER_MARKERS = (
    "feetoosmallutxo",
    "minimum fee",
    "fee too small",
    "ppviewhashesdontmatch",
    "script integrity hash mismatch",
)

_LEDGER_REJECTION_MARKERS = (
    "phase one validation",
    "missingredeemers",
    "missing redeemers",
    "noredeemer",
    "no redeemer",
    "extraredeemers",
    "extraneous redeemers",
)

_CLASSIFIABLE_STAGES = {
    "accepted",
    "decode_reject",
    "ledger_reject",
    "phase2_reject",
}

_LEGACY_EXPECTATION_STAGES = {
    "phase1_reject": "ledger_reject",
}


def _validation_stage(response: dict) -> str:
    code = int(response.get("http") or 0)
    body = str(response.get("body") or "").lower()
    if 200 <= code < 300:
        return "accepted"
    if not 400 <= code < 500:
        return "unavailable"
    if any(marker in body for marker in _DECODE_MARKERS):
        return "decode_reject"
    if any(marker in body for marker in _PHASE2_MARKERS):
        return "phase2_reject"
    if code == 409 or any(marker in body for marker in _MASKED_REJECTION_MARKERS):
        return "inconclusive_reject"
    if any(marker in body for marker in _NON_DIAGNOSTIC_LEDGER_MARKERS):
        return "inconclusive_reject"
    if any(marker in body for marker in _LEDGER_REJECTION_MARKERS):
        return "ledger_reject"
    if 400 <= code < 500:
        return "inconclusive_reject"
    return "unavailable"


def _structural_expectation_matches(expected: str, observed: str) -> bool:
    return _LEGACY_EXPECTATION_STAGES.get(expected, expected) == observed


def run_redeemer_container_differential(
    *,
    corpus_dir: Path,
    target_url: str,
    reference_url: str,
    submitter: Callable[[str, bytes], dict] = _submit,
) -> dict:
    """Replay the immutable empty-redeemer corpus and retain rejection stages."""
    corpus_dir = corpus_dir.resolve()
    manifest = json.loads((corpus_dir / "corpus.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        raise ValueError("redeemer corpus manifest is invalid")
    validated = []
    for item in manifest["cases"]:
        if not isinstance(item, dict) or not str(item.get("case_id") or ""):
            raise ValueError("redeemer corpus case is invalid")
        transaction = (corpus_dir / str(item.get("tx_file") or "")).resolve()
        if corpus_dir not in transaction.parents or not transaction.is_file():
            raise ValueError("redeemer corpus transaction path is invalid")
        envelope = json.loads(transaction.read_text(encoding="utf-8"))
        try:
            payload = bytes.fromhex(str(envelope["cborHex"]))
        except (KeyError, ValueError) as error:
            raise ValueError("redeemer corpus transaction encoding is invalid") from error
        if len(payload) != int(item.get("cbor_size") or -1):
            raise ValueError("redeemer corpus transaction size mismatch")
        if hashlib.sha256(payload).hexdigest() != str(item.get("cbor_sha256") or ""):
            raise ValueError("redeemer corpus transaction digest mismatch")
        expected = item.get("expected")
        if not isinstance(expected, dict) or set(expected) != {"target", "reference"}:
            raise ValueError("redeemer corpus expected stages are invalid")
        validated.append((item, payload, expected))
    records = []
    for item, payload, expected in validated:
        observations = {}
        for role, url in (("target", target_url), ("reference", reference_url)):
            response = dict(submitter(url, payload))
            classification = _validation_stage(response)
            if classification == "unavailable" and response.get("outcome") == "crash":
                response["outcome"] = "unavailable"
            observations[role] = {
                **response,
                "classification": classification,
            }
        records.append(
            {
                "case_id": str(item["case_id"]),
                "cbor_size": len(payload),
                "cbor_sha256": hashlib.sha256(payload).hexdigest(),
                "expected": dict(expected),
                "observations": observations,
                "structural_expectation_matches": all(
                    _structural_expectation_matches(
                        str(expected[role]), observations[role]["classification"]
                    )
                    for role in ("target", "reference")
                ),
            }
        )
    classifications = [
        observation["classification"]
        for record in records
        for observation in record["observations"].values()
    ]
    return {
        "schema_version": 1,
        "evidence_scope": "structural_decoder_only",
        "limitations": [
            "The immutable five-case corpus has no ledger-success control.",
            "Its no-script source is intentionally under minimum fee.",
            "Its non-empty script source is expected to fail phase-two validation.",
            "Observed rejection stages do not establish chain impact or a security finding.",
        ],
        "ledger_control_status": {
            "valid_control_present": False,
            "reason": "audited funded missing-redeemer and valid-minimal controls are required; not run",
        },
        "cases": records,
        "checks": {
            "all_cases_retained": len(records) == len(manifest["cases"]),
            "all_observations_classifiable": all(
                value in _CLASSIFIABLE_STAGES for value in classifications
            ),
            "structural_expectations_match": all(
                record["structural_expectation_matches"] for record in records
            ),
            "no_transport_failure": all(value != "unavailable" for value in classifications),
        },
    }


class RuntimeTxSubmitDifferential(LoadPrimitive):
    """Submit a fixture tx to the target + reference submit-APIs; record each node's outcome."""
    primitive_name = "runtime_tx_submit_differential"

    def run(self, handle, rng):
        rd = _run_dir(handle)
        out = rd / self.params.get("output_dir", f"outputs/{self.primitive_name}")
        out.mkdir(parents=True, exist_ok=True)
        tx_path = Path(self.params["tx_file"])
        if not tx_path.is_absolute():
            tx_path = rd / tx_path
        raw = tx_path.read_bytes()
        # accept either a raw CBOR tx (.cbor) or a cardano-cli text envelope ({"cborHex": ...})
        payload = raw
        stripped = raw.lstrip()[:1]
        if stripped == b"{":
            try:
                payload = bytes.fromhex(json.loads(raw.decode("utf-8"))["cborHex"])
            except Exception:  # noqa: BLE001
                payload = raw

        nodes = {}
        rmp = self.params.get("runtime_metadata_path")
        if rmp:
            rmp = Path(rmp)
            if not rmp.is_absolute():
                rmp = rd / rmp
            try:
                meta = json.loads(rmp.read_text(encoding="utf-8"))
                raw = meta.get("nodes") or meta.get("haskell_nodes") or []
                nodes = {n.get("id") or n.get("name"): n for n in raw} if isinstance(raw, list) else raw
            except Exception:  # noqa: BLE001
                nodes = {}

        def url_for(role):
            explicit = self.params.get(f"{role}_submit_api")
            if explicit:
                u = str(explicit)
                return u if _SUBMIT_PATH in u else u.rstrip("/") + _SUBMIT_PATH
            return _node_submit_url(nodes, self.params.get(f"{role}_node", ""))

        target_url, ref_url = url_for("target"), url_for("reference")
        result = {"primitive": self.primitive_name, "tx_file": str(tx_path),
                  "target_node": self.params.get("target_node"), "reference_node": self.params.get("reference_node"),
                  "target": {"url": target_url, **(_submit(target_url, payload) if target_url else {"outcome": "unknown", "note": "no target submit-api resolved"})},
                  "reference": {"url": ref_url, **(_submit(ref_url, payload) if ref_url else {"outcome": "unknown", "note": "no reference submit-api resolved"})}}
        result["divergent"] = result["target"].get("outcome") != result["reference"].get("outcome")
        (out / "result.json").write_text(json.dumps(result, indent=2))
        return result


class RuntimeRedeemerContainerDifferential(LoadPrimitive):
    """Run the bounded empty-container corpus against exact target/reference APIs."""

    primitive_name = "runtime_redeemer_container_differential"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", f"outputs/{self.primitive_name}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(reference_identity, dict):
            raise RuntimeError(
                "redeemer-container differential requires resolved target and reference substrate nodes"
            )
        from scripts.runtime_redeemer_container_live_controls import (
            run_fresh_funded_live_controls,
        )

        report = run_fresh_funded_live_controls(
            runtime=metadata,
            output_dir=output_dir,
            corpus_dir=_resolve_runtime_path(self.params["corpus_dir"]),
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class RuntimeGovernanceZeroDenominatorAdmission(LoadPrimitive):
    """Run one fresh-funded governance admission control and denominator mutant."""

    primitive_name = "runtime_governance_zero_denominator_admission"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/governance-zero-denominator"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(
            reference_identity, dict
        ):
            raise RuntimeError(
                "governance admission requires resolved target and reference substrate nodes"
            )
        from scripts.runtime_governance_zero_denominator import (
            run_governance_zero_denominator_admission,
        )

        report = run_governance_zero_denominator_admission(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
            experiment_mode=str(
                self.params.get("experiment_mode", "zero-denominator")
            ),
            p2p_trace_recovery=bool(
                self.params.get("p2p_trace_recovery", False)
            ),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class RuntimeMultiassetCompactBoundary(LoadPrimitive):
    """Run the fresh-funded compact MultiAsset control and boundary case."""

    primitive_name = "runtime_multiasset_compact_boundary"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/multiasset-compact-boundary"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(
            reference_identity, dict
        ):
            raise RuntimeError(
                "multiasset compact-boundary case requires resolved target and reference nodes"
            )
        from scripts.runtime_multiasset_compact_boundary import (
            run_multiasset_compact_boundary,
        )

        report = run_multiasset_compact_boundary(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class RuntimeInlineDatumTag24Boundary(LoadPrimitive):
    """Run fresh-funded inline-datum controls and one nested tag-24 mutant."""

    primitive_name = "runtime_inline_datum_tag24_boundary"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/inline-datum-tag24-boundary"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(
            reference_identity, dict
        ):
            raise RuntimeError(
                "inline-datum tag-24 case requires resolved target and reference nodes"
            )
        from scripts.runtime_inline_datum_tag24_boundary import (
            run_inline_datum_tag24_boundary,
        )

        report = run_inline_datum_tag24_boundary(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class RuntimeUplcDeepDelayStackSafety(LoadPrimitive):
    """Run shallow controls and the release-fixed 28,000-deep UPLC vector."""

    primitive_name = "runtime_uplc_deep_delay_stack_safety"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/uplc-deep-delay-stack-safety"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(
            reference_identity, dict
        ):
            raise RuntimeError(
                "UPLC deep-delay case requires resolved target and reference nodes"
            )
        from scripts.runtime_uplc_deep_delay_stack_safety import (
            run_uplc_deep_delay_stack_safety,
        )

        report = run_uplc_deep_delay_stack_safety(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class RuntimeTransactionArrayArityDifferential(LoadPrimitive):
    """Run independent canonical controls and one exact three-element variant."""

    primitive_name = "runtime_transaction_array_arity_differential"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/transaction-array-arity"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {
            str(node.get("id")): node
            for node in (self.bound_substrate or {}).get("nodes", [])
            if isinstance(node, dict)
        }
        target_identity = nodes.get(target_node)
        reference_identity = nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(
            reference_identity, dict
        ):
            raise RuntimeError(
                "transaction-array case requires resolved target and reference nodes"
            )
        from scripts.runtime_transaction_array_arity import (
            run_transaction_array_arity_differential,
        )

        report = run_transaction_array_arity_differential(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
        )
        report.update(
            {
                "primitive": self.primitive_name,
                "target_node": target_node,
                "reference_node": reference_node,
            }
        )
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report


class SubmitOutcomeMatches(AssertionPrimitive):
    """Pass iff the recorded submit outcomes match expectation (per node: accept|reject|crash)."""

    def evaluate(self, handle):
        rd = _run_dir(handle)
        exp_t = str(self.params.get("expected_target_outcome") or "accept")
        exp_r = str(self.params.get("expected_reference_outcome") or "reject")
        observed = None
        for p in rd.rglob("runtime_tx_submit_differential/result.json"):
            try:
                observed = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                pass
        obs_t = (observed or {}).get("target", {}).get("outcome")
        obs_r = (observed or {}).get("reference", {}).get("outcome")
        ok = observed is not None and obs_t == exp_t and obs_r == exp_r
        return {
            "primitive": "submit_outcome_matches",
            "params": dict(self.params),
            "evaluated_value": {"expected": {"target": exp_t, "reference": exp_r},
                                "observed": {"target": obs_t, "reference": obs_r}},
            "result": "pass" if ok else "fail",
            "note": None if ok else f"expected target={exp_t}/reference={exp_r}, observed target={obs_t}/reference={obs_r}",
        }


class RedeemerContainerDifferentialMatches(AssertionPrimitive):
    """Require classified structural evidence plus a separate valid ledger control."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/redeemer-container-differential/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        ledger_control = report.get("ledger_control_status") if isinstance(report, dict) else None
        has_valid_control = (
            isinstance(ledger_control, dict)
            and ledger_control.get("valid_control_present") is True
        )
        ok = bool(
            has_valid_control
            and ledger_control.get("complete") is True
            and ledger_control.get("mutation_payloads_match") is True
            and ledger_control.get("funding_inputs_isolated") is True
            and ledger_control.get("outcomes_conclusive") is True
        )
        if not has_valid_control:
            note = "redeemer-container evidence has no valid ledger control"
        elif not ok:
            note = "fresh ledger-control evidence is incomplete or inconclusive"
        else:
            note = None
        return {
            "primitive": "redeemer_container_differential_matches",
            "params": dict(self.params),
            "evaluated_value": ledger_control or {},
            "data_points_used": [ledger_control] if isinstance(ledger_control, dict) else [],
            "result": "pass" if ok else "fail",
            "note": note,
        }


class GovernanceZeroDenominatorAdmissionMatches(AssertionPrimitive):
    """Require valid admission control plus conclusive zero-denominator outcomes."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/governance-zero-denominator/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("admission_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("valid_control_present") is True
            and status.get("funding_inputs_isolated") is True
            and status.get("outcomes_conclusive") is True
            and status.get("enactment_observed") is False
        )
        return {
            "primitive": "governance_zero_denominator_admission_matches",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": None if ok else "governance admission evidence is incomplete or inconclusive",
        }


class MultiassetCompactBoundaryMatches(AssertionPrimitive):
    """Require controls, identical mutant bytes and conclusive boundary outcomes."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/multiasset-compact-boundary/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("boundary_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("compact_boundary_reached") is True
        )
        return {
            "primitive": "multiasset_compact_boundary_matches",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": None if ok else "compact-boundary evidence is incomplete or inconclusive",
        }


class InlineDatumTag24BoundaryMatches(AssertionPrimitive):
    """Require controls, identical mutant bytes and conclusive nested outcomes."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/inline-datum-tag24-boundary/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("boundary_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("nested_boundary_reached") is True
        )
        return {
            "primitive": "inline_datum_tag24_boundary_matches",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": None if ok else "inline-datum boundary evidence is incomplete or inconclusive",
        }


class UplcDeepDelayStackSafetyMatches(AssertionPrimitive):
    """Require accepted controls and included/adopted exact deep-vector evidence."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/uplc-deep-delay-stack-safety/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("stack_safety_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("deep_boundary_reached") is True
            and status.get("boundary_included") is True
            and status.get("target_adopted_boundary") is True
        )
        return {
            "primitive": "uplc_deep_delay_stack_safety_matches",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": None if ok else "UPLC deep-delay evidence is incomplete or inconclusive",
        }


class TransactionArrayArityDifferentialMatches(AssertionPrimitive):
    """Require independent controls and a conclusive exact three-element boundary."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/transaction-array-arity/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("boundary_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("three_element_boundary_reached") is True
        )
        return {
            "primitive": "transaction_array_arity_differential_matches",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": None if ok else "transaction-array boundary evidence is incomplete or inconclusive",
        }
