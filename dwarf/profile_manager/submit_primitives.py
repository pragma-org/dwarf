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


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_PACKAGE = _REPO_ROOT / "antithesis" / "cardano_amaru_adversarial"
_UNKNOWN_INPUT_MARKERS = ("badinputsutxo", "bad inputs", "unknown input", "inputs not found", "missing input",
                          "unknown (but required) transaction input")


def stage_fixture(run_dir: Path, tx_file: str) -> tuple[Path, dict]:
    """Copy a fixture transaction into the run directory and record where it came from.

    Scenario fixture paths are written relative to the run dir (already staged), the
    repository root (``antithesis/cardano_amaru_adversarial/fixture/...``) or the adversarial
    package (``fixture/...``). The staged copy is what is submitted, so the bundle holds the
    exact bytes and their digest.
    """
    path = Path(tx_file)
    if path.is_absolute():
        return path, {"source": str(path), "staged": False}
    staged = run_dir / path
    if staged.is_file():
        return staged, {"source": str(staged), "staged": False}
    for root in (_REPO_ROOT, _FIXTURE_PACKAGE):
        candidate = root / path
        if candidate.is_file():
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(candidate.read_bytes())
            return staged, {"source": str(candidate), "staged": True,
                            "sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
    raise FileNotFoundError(
        f"fixture {tx_file!r} is not in the run dir, the repository root or {_FIXTURE_PACKAGE}"
    )


def _unknown_inputs(response: dict) -> bool:
    body = str(response.get("body") or "").lower()
    return any(marker in body for marker in _UNKNOWN_INPUT_MARKERS)


_CLI_ALREADY_SPENT_MARKERS = ("badinputsutxo", "bad inputs", "already", "all inputs are spent")


def resolve_cli_submission(response: dict, *, tx_in_mempool: bool | None, tx_on_chain: bool | None) -> dict:
    """A cardano-node's own outcome for one ``cardano-cli transaction submit``.

    The target is submitted first and relays what it accepts, so the reference may already hold
    the exact transaction when the CLI reaches it ("inputs spent"). Its mempool or its chain then
    holding that transaction id is the reference's own acceptance; otherwise the rejection stands.
    A submission whose node response was never observed is ``not-measured``.
    """
    resolved = dict(response)
    code = int(response.get("http") or 0)
    if not response.get("node_response_observed") or code == 0:
        resolved["outcome"] = "not-measured"
    elif 200 <= code < 300:
        resolved["outcome"] = "accept"
    else:
        resolved["outcome"] = "reject"
        body = str(response.get("body") or "").lower()
        if any(marker in body for marker in _CLI_ALREADY_SPENT_MARKERS) and (tx_in_mempool or tx_on_chain):
            resolved.update({"outcome": "accept", "accepted_via": "mempool" if tx_in_mempool else "chain"})
    resolved.setdefault("accepted_via", None)
    return resolved


def _runtime_nodes(meta: dict) -> dict:
    nodes: dict = {}
    for key in ("nodes", "haskell_nodes", "amaru_nodes"):
        raw = meta.get(key) or []
        entries = raw.values() if isinstance(raw, dict) else raw
        for node in entries:
            ident = node.get("id") or node.get("name") if isinstance(node, dict) else None
            if ident:
                nodes[ident] = {**nodes.get(ident, {}), **node}
    return nodes


def _query_utxo_by_tx_in(cli: str, socket_path: str, magic: int, tx_in: str, destination: Path) -> dict:
    import subprocess  # noqa: PLC0415

    result = subprocess.run([cli, "query", "utxo", "--tx-in", tx_in, "--socket-path", socket_path,
                             "--testnet-magic", str(magic), "--out-file", str(destination)],
                            capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(destination.read_text(encoding="utf-8"))


def _submit_via_cardano_cli(*, run_dir: Path, runtime_metadata_path: Path, node_id: str, tx_path: Path,
                            out_dir: Path) -> dict:
    """Submit to a cardano-node that has no submit-API through cardano-cli on its own socket."""
    import shutil  # noqa: PLC0415

    try:
        from scripts.runtime_block_apply_bridge import find_node  # noqa: PLC0415
        from scripts.runtime_cardano_measurement_calibration import (  # noqa: PLC0415
            _containerized_cardano_workload, _controller_container, _transaction_id,
        )
        from scripts.runtime_redeemer_container_live_controls import (  # noqa: PLC0415
            _query_reference_mempool, _submit_reference_cli,
        )

        runtime = json.loads(Path(runtime_metadata_path).read_text(encoding="utf-8"))
        node = find_node(runtime, node_id)
        controller = _controller_container()
        if controller is None:
            return {"http": 0, "outcome": "not-measured", "via": "cardano-cli",
                    "note": "no Docker controller to run cardano-cli beside the node"}
        socket_path, magic = str(node.get("container_socket_path") or ""), int(runtime["network_magic"])
        staged = out_dir / f"cli-{tx_path.name}"
        shutil.copyfile(tx_path, staged)
        with _containerized_cardano_workload(runtime_root=Path(str(runtime["runtime_root"])), runtime=runtime,
                                             node=node, output_dir=out_dir, controller=controller) as (workload, _n):
            cli = str(workload["support_binaries"]["cardano-cli"])
            tx_id = _transaction_id(cli, staged)
            response = _submit_reference_cli(cli, staged, socket_path=socket_path, network_magic=magic)
            in_mempool = on_chain = None
            if any(m in str(response.get("body") or "").lower() for m in _CLI_ALREADY_SPENT_MARKERS):
                in_mempool = _query_reference_mempool(cli=cli, socket_path=socket_path, network_magic=magic,
                                                      transaction_id=tx_id)
                try:
                    on_chain = bool(_query_utxo_by_tx_in(cli, socket_path, magic, f"{tx_id}#0",
                                                         out_dir / "cli-tx-on-chain.json"))
                except Exception:  # noqa: BLE001
                    on_chain = None
        resolved = resolve_cli_submission(response, tx_in_mempool=in_mempool, tx_on_chain=on_chain)
        resolved.update({"via": "cardano-cli", "transaction_id": tx_id, "tx_in_mempool": in_mempool,
                         "tx_on_chain": on_chain})
        return resolved
    except Exception as exc:  # noqa: BLE001 - the node's outcome stays visibly unmeasured
        return {"http": 0, "outcome": "not-measured", "via": "cardano-cli",
                "error": f"{type(exc).__name__}: {exc}"[:800]}


class RuntimeTxSubmitDifferential(LoadPrimitive):
    """Submit a tx to the target + reference; record each node's outcome.

    Submit-API nodes are posted to over HTTP; a cardano-node without one is submitted to through
    cardano-cli on its own socket. The target is always submitted first. A transaction the run
    could not build live (``<tx_file>.unbuilt.json``) is recorded as not measured, with its reason.
    """
    primitive_name = "runtime_tx_submit_differential"

    def run(self, handle, rng):
        rd = _run_dir(handle)
        out = rd / self.params.get("output_dir", f"outputs/{self.primitive_name}")
        out.mkdir(parents=True, exist_ok=True)
        tx_file = str(self.params["tx_file"])
        unbuilt = rd / (tx_file + ".unbuilt.json")
        if not Path(tx_file).is_absolute() and not (rd / tx_file).is_file() and unbuilt.is_file():
            reason = str(json.loads(unbuilt.read_text(encoding="utf-8")).get("reason") or "not built")
            result = {"primitive": self.primitive_name, "tx_file": str(rd / tx_file), "measured": False,
                      "reason": reason, "target_node": self.params.get("target_node"),
                      "reference_node": self.params.get("reference_node"),
                      "target": {"outcome": "not-measured"}, "reference": {"outcome": "not-measured"},
                      "divergent": None, "expected_target": self.params.get("expected_target"),
                      "expected_reference": self.params.get("expected_reference"),
                      "inputs_unknown_on_live_chain": {"target": False, "reference": False}}
            (out / "result.json").write_text(json.dumps(result, indent=2))
            return result
        tx_path, fixture = stage_fixture(rd, tx_file)
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
                nodes = _runtime_nodes(json.loads(rmp.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                nodes = {}

        def url_for(role):
            explicit = self.params.get(f"{role}_submit_api")
            if explicit:
                u = str(explicit)
                return u if _SUBMIT_PATH in u else u.rstrip("/") + _SUBMIT_PATH
            return _node_submit_url(nodes, self.params.get(f"{role}_node", ""))

        def submit(role):
            url = url_for(role)
            if url:
                return {"url": url, **_submit(url, payload)}
            node_id = str(self.params.get(f"{role}_node") or "")
            node = nodes.get(node_id) or {}
            if rmp and node.get("impl") == "cardano-node" and node.get("container_socket_path"):
                return {"url": None, **_submit_via_cardano_cli(run_dir=rd, runtime_metadata_path=rmp, node_id=node_id,
                                                               tx_path=tx_path, out_dir=out)}
            return {"url": None, "outcome": "unknown", "note": f"no {role} submit-api or cardano-cli socket resolved"}

        target_result = submit("target")  # the target first; it relays what it accepts
        reference_result = submit("reference")
        result = {"primitive": self.primitive_name, "tx_file": str(tx_path),
                  "target_node": self.params.get("target_node"), "reference_node": self.params.get("reference_node"),
                  "target": target_result, "reference": reference_result}
        result["measured"] = all(r.get("outcome") in ("accept", "reject", "crash")
                                 for r in (target_result, reference_result))
        result["divergent"] = result["target"].get("outcome") != result["reference"].get("outcome")
        result["fixture"] = fixture
        result["expected_target"] = self.params.get("expected_target")
        result["expected_reference"] = self.params.get("expected_reference")
        # A fixture built against another chain's UTxO is rejected for its inputs by both nodes:
        # that is a stale fixture, not a ledger verdict on the rule under test.
        result["inputs_unknown_on_live_chain"] = {
            "target": _unknown_inputs(result["target"]), "reference": _unknown_inputs(result["reference"])}
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


class RuntimePlutusCaseDifferential(LoadPrimitive):
    """Submit specification-derived Plutus phase-2 cases to Amaru and cardano-node on a live devnet."""

    primitive_name = "runtime_plutus_case_differential"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get("output_dir", "outputs/plutus-case-differential")
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.is_absolute():
            metadata_path = run_dir / metadata_path
        runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        reference_node = str(self.params["reference_node"])
        nodes = {str(node.get("id")): node for node in (self.bound_substrate or {}).get("nodes", []) if isinstance(node, dict)}
        target_identity, reference_identity = nodes.get(target_node), nodes.get(reference_node)
        if not isinstance(target_identity, dict) or not isinstance(reference_identity, dict):
            raise RuntimeError("Plutus case differential requires resolved target and reference nodes")
        from scripts import runtime_plutus_case_differential as differential

        report = differential.run_plutus_case_differential(
            runtime=runtime,
            output_dir=output_dir,
            target_node=target_node,
            reference_node=reference_node,
            expected_target_version=str(target_identity["version"]),
            expected_target_source=str(target_identity["source_revision"]),
            expected_reference_version=str(reference_identity["version"]),
            expected_reference_source=str(reference_identity["source_revision"]),
            family_names=[str(name) for name in self.params["families"]],
        )
        report.update({"primitive": self.primitive_name, "target_node": target_node, "reference_node": reference_node})
        (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
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


class RuntimeValueCoinOverflowRegression(LoadPrimitive):
    """Run fresh-funded valid, i64 boundary, and overflow cases against Amaru."""

    primitive_name = "runtime_value_coin_overflow_regression"
    bound_substrate = None

    def run(self, handle, rng):
        run_dir = _run_dir(handle)
        output_dir = run_dir / self.params.get(
            "output_dir", "outputs/value-coin-overflow"
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
                "value-overflow regression requires resolved target and supporting nodes"
            )
        from scripts.runtime_value_coin_overflow import (
            run_value_coin_overflow_regression,
        )

        report = run_value_coin_overflow_regression(
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
        rows = []
        for path in sorted(rd.glob("outputs/**/result.json")):
            try:
                observed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(observed, dict) or observed.get("primitive") != "runtime_tx_submit_differential":
                continue
            # assertion params win; otherwise each submit step's own declared expectation
            exp_t = str(self.params.get("expected_target_outcome") or observed.get("expected_target") or "accept")
            exp_r = str(self.params.get("expected_reference_outcome") or observed.get("expected_reference") or "reject")
            obs_t = (observed.get("target") or {}).get("outcome")
            obs_r = (observed.get("reference") or {}).get("outcome")
            rows.append({"result_path": str(path.relative_to(rd)), "tx_file": observed.get("tx_file"),
                         "expected": {"target": exp_t, "reference": exp_r},
                         "observed": {"target": obs_t, "reference": obs_r},
                         "measured": observed.get("measured"), "reason": observed.get("reason"),
                         "inputs_unknown_on_live_chain": observed.get("inputs_unknown_on_live_chain"),
                         "match": obs_t == exp_t and obs_r == exp_r})
        ok = bool(rows) and all(row["match"] for row in rows)
        mismatched = [row for row in rows if not row["match"]]
        return {
            "primitive": "submit_outcome_matches",
            "params": dict(self.params),
            "evaluated_value": {"submissions": len(rows), "mismatched": len(mismatched)},
            "data_points_used": rows,
            "result": "pass" if ok else "fail",
            "note": None if ok else (
                "no runtime_tx_submit_differential result was recorded" if not rows else
                "; ".join(f"{row['tx_file']}: expected {row['expected']}, observed {row['observed']}"
                          + (" (inputs unknown on the live chain: stale fixture)"
                             if any((row.get("inputs_unknown_on_live_chain") or {}).values()) else "")
                          + (f" (not measured: {row['reason']})"
                             if row.get("measured") is False and row.get("reason") else "")
                          for row in mismatched)),
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


class PlutusCaseOutcomesAgree(AssertionPrimitive):
    """Require admitted/included/adopted controls, node agreement on every case, and the
    specification's expected outcome; a disagreement fails with the case named."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get("result_path", "outputs/plutus-case-differential/result.json")
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("differential_status") if isinstance(report, dict) else None
        ok = bool(isinstance(status, dict) and status.get("complete") is True and status.get("agreement") is True
                  and status.get("specification_match") is True)
        note = None
        if not ok and isinstance(status, dict):
            if status.get("disagreements"):
                note = "nodes disagree: " + ", ".join(str(item.get("case_id")) for item in status["disagreements"])
            elif status.get("specification_mismatches"):
                note = "both nodes differ from the specification: " + ", ".join(
                    str(item.get("case_id")) for item in status["specification_mismatches"])
            else:
                note = "Plutus case evidence is incomplete or inconclusive"
        elif not ok:
            note = "Plutus case differential produced no result"
        return {
            "primitive": "plutus_case_outcomes_agree",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": note,
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


class ValueCoinOverflowRegressionPassed(AssertionPrimitive):
    """Pass only when the fresh-funded overflow mutant rejects and Amaru survives."""

    def evaluate(self, handle):
        path = _run_dir(handle) / self.params.get(
            "result_path", "outputs/value-coin-overflow/result.json"
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        status = report.get("regression_status") if isinstance(report, dict) else None
        ok = bool(
            isinstance(status, dict)
            and status.get("complete") is True
            and status.get("regression_passed") is True
            and status.get("security_finding_observed") is False
        )
        if isinstance(status, dict) and status.get("security_finding_observed") is True:
            note = "fresh-funded value-overflow security finding observed"
        elif not ok:
            note = "value-overflow regression evidence is incomplete or inconclusive"
        else:
            note = None
        return {
            "primitive": "value_coin_overflow_regression_passed",
            "params": dict(self.params),
            "evaluated_value": status or {},
            "data_points_used": [status] if isinstance(status, dict) else [],
            "result": "pass" if ok else "fail",
            "note": note,
        }
