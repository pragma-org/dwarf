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

import json
from pathlib import Path
from urllib import request as _request, error as _error

from profile_manager.primitives import LoadPrimitive, AssertionPrimitive

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
