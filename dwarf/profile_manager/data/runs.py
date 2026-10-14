"""Pure forensic-run helpers for the dashboard data layer."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re as _re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from profile_manager.config import storage_roots
from profile_manager.run_ledger import FINAL_STATES_BY_EXIT_STATUS, RunLedger, atomic_write_bytes
from profile_manager.scenario_verdict import TEST_RUN_KINDS, evaluate_scenario_verdict


HISTORY_SCOPES = {
    "scenario": frozenset({"scenario"}),
    "ci": frozenset({"ci"}),
    "external": frozenset({"external"}),
    "tests": TEST_RUN_KINDS,
    "operations": frozenset({"deployment", "maintenance"}),
    "all": TEST_RUN_KINDS | frozenset({"deployment", "maintenance"}),
}


class InvalidRunHistoryCursor(ValueError):
    pass


def _forensic_runs_dir():
    return storage_roots().runs


def _history_at(entry: Mapping[str, Any]) -> str:
    return str(
        entry.get("queued_at")
        or entry.get("started_at")
        or entry.get("ended_at")
        or entry.get("created_at")
        or ""
    )


def _history_order(value: str) -> int:
    """Normalize RFC3339 offsets to UTC microseconds for chronological cursors."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return 0
        return int(parsed.astimezone(timezone.utc).timestamp() * 1_000_000)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return 0


def _cursor_encode(*, scope: str, outcome: str, history_order: int, run_id: str) -> str:
    body = json.dumps(
        {
            "v": 1,
            "scope": scope,
            "outcome": outcome,
            "history_order": history_order,
            "run_id": run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str, *, scope: str, outcome: str) -> tuple[int, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        body = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        value = json.loads(body)
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise InvalidRunHistoryCursor("invalid run-history cursor") from exc
    if (
        not isinstance(value, dict)
        or value.get("v") != 1
        or value.get("scope") != scope
        or value.get("outcome") != outcome
        or not isinstance(value.get("history_order"), int)
        or not isinstance(value.get("run_id"), str)
        or not value["run_id"]
    ):
        raise InvalidRunHistoryCursor("invalid run-history cursor")
    return value["history_order"], value["run_id"]


def _ledger_history_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    raw_scenario = entry.get("scenario")
    scenario: Mapping[str, Any] = raw_scenario if isinstance(raw_scenario, Mapping) else {}
    raw_profile = entry.get("profile")
    profile: Mapping[str, Any] = raw_profile if isinstance(raw_profile, Mapping) else {}
    raw_provenance = entry.get("provenance")
    provenance: Mapping[str, Any] = raw_provenance if isinstance(raw_provenance, Mapping) else {}
    raw_exit_status = provenance.get("legacy_exit_status")
    exit_status = raw_exit_status if isinstance(raw_exit_status, str) else None
    if not exit_status and entry.get("state") == "completed":
        exit_status = "pass"
    elif not exit_status and entry.get("state") == "failed":
        exit_status = "fail"
    history_at = _history_at(entry)
    return {
        "run_id": entry["run_id"],
        "run_kind": entry["kind"],
        "origin": entry["origin"],
        "backend": entry["backend"],
        "state": entry["state"],
        "verdict": entry.get("verdict"),
        "scenario_id": scenario.get("id"),
        "profile_id": profile.get("id"),
        "runtime": None,
        "exit_status": exit_status,
        "operational_outcome": exit_status,
        "queued_at": entry.get("queued_at"),
        "started_at": entry.get("started_at"),
        "ended_at": entry.get("ended_at"),
        "actor": provenance.get("actor"),
        "assertion_summary": None,
        "resource_snapshot": None,
        "history_at": history_at,
        "history_order": _history_order(history_at),
        "ledger_backed": True,
        "source": "local",
    }


def _legacy_manifest_row(manifest_path: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    run_id = manifest_path.parent.name
    if manifest.get("run_id") not in {None, run_id}:
        return None
    assertions = manifest.get("assertion_summary") or {}
    if not isinstance(assertions, Mapping):
        assertions = {}
    scenario = manifest.get("scenario")
    if not isinstance(scenario, Mapping):
        scenario = {}
    profile = manifest.get("profile")
    if not isinstance(profile, Mapping):
        profile = {}
    verdict = evaluate_scenario_verdict(
        exit_status=manifest.get("exit_status") or "unknown",
        assertions=assertions,
        measurements=manifest.get("measurements"),
        run_kind="external",
    ).security_verdict
    raw_exit_status = manifest.get("exit_status")
    exit_status = raw_exit_status if isinstance(raw_exit_status, str) else None
    history_at = str(manifest.get("started_at") or manifest.get("ended_at") or run_id)
    return {
        "run_id": run_id,
        "run_kind": "external",
        "origin": "legacy-manifest",
        "backend": "legacy",
        "state": FINAL_STATES_BY_EXIT_STATUS.get(exit_status, "failed") if exit_status else "failed",
        "verdict": verdict,
        "scenario_id": scenario.get("id"),
        "profile_id": profile.get("id"),
        "runtime": manifest.get("runtime"),
        "exit_status": exit_status,
        "operational_outcome": exit_status,
        "queued_at": None,
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "actor": manifest.get("actor"),
        "assertion_summary": assertions,
        "resource_snapshot": manifest.get("resource_snapshot"),
        "history_at": history_at,
        "history_order": _history_order(history_at),
        "ledger_backed": False,
        "source": "local",
    }


def _hydrate_history_row(row: dict[str, Any], *, runs_dir: Path) -> dict[str, Any]:
    """Read at most the selected page's manifests; ledger identity stays authoritative."""
    manifest_path = runs_dir / row["run_id"] / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return dict(row)
    if not isinstance(manifest, dict):
        return dict(row)
    hydrated = dict(row)
    scenario = manifest.get("scenario") or {}
    profile = manifest.get("profile") or {}
    if not isinstance(scenario, Mapping):
        scenario = {}
    if not isinstance(profile, Mapping):
        profile = {}
    hydrated.update(
        {
            "scenario_id": scenario.get("id") or hydrated.get("scenario_id"),
            "profile_id": profile.get("id") or hydrated.get("profile_id"),
            "runtime": manifest.get("runtime"),
            "exit_status": manifest.get("exit_status"),
            "operational_outcome": manifest.get("exit_status"),
            "started_at": manifest.get("started_at") or hydrated.get("started_at"),
            "ended_at": manifest.get("ended_at") or hydrated.get("ended_at"),
            "actor": manifest.get("actor"),
            "assertion_summary": manifest.get("assertion_summary"),
            "resource_snapshot": manifest.get("resource_snapshot"),
        }
    )
    return hydrated


def _legacy_history_rows(*, runs_dir: Path, state_dir: Path) -> list[dict[str, Any]]:
    """Materialize manifest-only compatibility rows once per run-root generation."""
    index_path = state_dir / "run-ledger" / "legacy-history-index-v1.json"
    try:
        generation = runs_dir.stat().st_mtime_ns
    except OSError:
        generation = 0
    try:
        cached = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        cached = None
    if (
        isinstance(cached, dict)
        and cached.get("schema_version") == "v1"
        and cached.get("runs_dir") == str(runs_dir.resolve())
        and cached.get("runs_generation") == generation
        and isinstance(cached.get("rows"), list)
    ):
        return [row for row in cached["rows"] if isinstance(row, dict)]

    rows: list[dict[str, Any]] = []
    if runs_dir.is_dir():
        for child in runs_dir.iterdir():
            if not child.is_dir():
                continue
            legacy = _legacy_manifest_row(child / "manifest.json")
            if legacy is not None:
                rows.append(legacy)
    atomic_write_bytes(
        index_path,
        json.dumps(
            {
                "schema_version": "v1",
                "runs_dir": str(runs_dir.resolve()),
                "runs_generation": generation,
                "rows": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    return rows


def run_history_page(
    *,
    runs_dir=None,
    state_dir=None,
    limit=50,
    cursor: str | None = None,
    scope="scenario",
    outcome: str = "",
):
    """Return one stable newest-first page from the ledger plus legacy-only runs.

    The ledger is the index: completed ledger-backed histories parse manifests
    only for the selected page. Directory names are compared with ledger IDs so
    manifest-only legacy bundles remain visible without reparsing ledger-backed
    run trees.
    """
    if scope not in HISTORY_SCOPES:
        raise ValueError("unknown run-history scope")
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("run-history limit must be from 1 to 200")
    if outcome not in {"", "pass", "fail", "error", "inconclusive"}:
        raise ValueError(
            "run-history outcome must be pass, fail, error, inconclusive, or empty"
        )
    if runs_dir is None:
        base = storage_roots().runs
    else:
        base = Path(runs_dir)
    if state_dir is None:
        state = storage_roots().state
    else:
        state = Path(state_dir)
    entries = RunLedger(state, create=False).history_entries()
    ledger_ids = {entry["run_id"] for entry in entries}
    rows = [_ledger_history_row(entry) for entry in entries]
    rows.extend(
        legacy for legacy in _legacy_history_rows(runs_dir=base, state_dir=state)
        if legacy["run_id"] not in ledger_ids
    )
    kind_totals: dict[str, int] = {}
    for row in rows:
        kind = row["run_kind"]
        kind_totals[kind] = kind_totals.get(kind, 0) + 1
    scope_totals = {
        name: sum(kind_totals.get(kind, 0) for kind in kinds)
        for name, kinds in HISTORY_SCOPES.items()
    }
    allowed = HISTORY_SCOPES[scope]
    rows = [row for row in rows if row["run_kind"] in allowed]
    rows.sort(key=lambda row: (row["history_order"], row["run_id"]), reverse=True)
    scoped_rows = list(rows)
    if outcome:
        rows = [
            row for row in rows
            if (
                row.get("verdict")
                if row["run_kind"] in TEST_RUN_KINDS
                else row.get("exit_status") or row.get("state")
            ) == outcome
        ]
    total = len(rows)
    if cursor:
        before = _cursor_decode(cursor, scope=scope, outcome=outcome)
        rows = [row for row in rows if (row["history_order"], row["run_id"]) < before]
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit and selected:
        last = selected[-1]
        next_cursor = _cursor_encode(
            scope=scope,
            outcome=outcome,
            history_order=last["history_order"],
            run_id=last["run_id"],
        )
    counts = {
        "states": {},
        "verdicts": {},
        "kinds": {},
    }
    for row in scoped_rows:
        for bucket, value in (
            ("states", row.get("state")),
            ("verdicts", row.get("verdict")),
            ("kinds", row.get("run_kind")),
        ):
            key = str(value) if value is not None else "none"
            counts[bucket][key] = counts[bucket].get(key, 0) + 1
    return {
        "runs": [_hydrate_history_row(row, runs_dir=base) for row in selected],
        "scope": scope,
        "outcome": outcome,
        "total": total,
        "scope_total": len(scoped_rows),
        "returned": len(selected),
        "limit": limit,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "counts": counts,
        "scope_totals": scope_totals,
    }


def recent_runs_payload(*, runs_dir=None, state_dir=None, limit=20, cursor=None, scope="tests", outcome=""):
    page = run_history_page(
        runs_dir=runs_dir,
        state_dir=state_dir,
        limit=limit,
        cursor=cursor,
        scope=scope,
        outcome=outcome,
    )
    return {"recent_runs": page.pop("runs"), "page": page}


def humanize_decode_error(message):
    """Translate a shim's stderr/stdout error string into plain English for non-experts.

    Returns None if the message is not an error (e.g. starts with OK).
    Returns the original message wrapped in a generic preamble if no pattern matches.
    """
    if not message or not isinstance(message, str):
        return None
    s = message.strip()
    if s.startswith("OK"):
        return None
    body = s.removeprefix("ERR ").strip()

    m = _re.search(r'DecoderErrorDeserialiseFailure\s+"([^"]+)"\s*\(.*?"([^"]+)"', body)
    if m:
        type_name, reason = m.group(1), m.group(2)
        return (
            f"Input was malformed CBOR — the {type_name} parser correctly rejected it "
            f"({reason})."
        )
    if "DecoderError" in body or "DeserialiseFailure" in body:
        return f"Input was malformed CBOR — the parser correctly rejected it ({body[:120]})."
    m = _re.search(r"unexpected type (\S+) at position (\d+): expected (\S+)", body)
    if m:
        return (
            f"Input was malformed CBOR at byte {m.group(2)} — expected a "
            f"{m.group(3)} but got {m.group(1)}; parser correctly rejected it."
        )
    if "stdin read failed" in body:
        return "The shim could not read its input from stdin."
    return f"Parser rejected the input. (raw: {body[:120]})"


def parse_remote_sources(env_var="ADA2_DWARF_REMOTE_SOURCES"):
    """Parse a comma-separated list of remote-source specs.

    Each spec is `name=ssh://user@host/abs/runs_dir`. Returns
    [{"name", "user", "host", "runs_dir"}, ...].
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return []
    out = []
    for spec in raw.split(","):
        spec = spec.strip()
        if not spec or "=" not in spec:
            continue
        name, url = spec.split("=", 1)
        m = _re.match(r"ssh://([^@]+)@([^/]+)(/.+)$", url.strip())
        if not m:
            continue
        out.append({
            "name": name.strip(),
            "user": m.group(1),
            "host": m.group(2),
            "runs_dir": m.group(3),
        })
    return out


def _ssh_remote_lister(source, *, limit):
    """Default remote-lister: SSH to the source host and read manifest.json files."""
    cmd = [
        "ssh", "-n", "-o", "BatchMode=yes",
        f"{source['user']}@{source['host']}",
        f"for d in {source['runs_dir']}/*/; do "
        f"  rid=$(basename \"$d\"); "
        f"  m=\"$d/manifest.json\"; "
        f"  [ -f \"$m\" ] && python3 -c "
        f"\"import json,sys; d=json.load(open('$m')); d['_run_id']='$rid'; "
        f"print(json.dumps(d, separators=(',',':')))\"; "
        f"done | head -n " + str(int(limit)),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append({
            "run_id": m.get("_run_id") or m.get("run_id"),
            "scenario_id": (m.get("scenario") or {}).get("id"),
            "runtime": m.get("runtime"),
            "exit_status": m.get("exit_status"),
            "started_at": m.get("started_at"),
            "ended_at": m.get("ended_at"),
            "actor": m.get("actor"),
            "assertion_summary": m.get("assertion_summary"),
            "resource_snapshot": m.get("resource_snapshot"),
        })
    return entries


def list_recent_runs_with_remote(*, local_runs_dir, remote_sources=None, limit=20, remote_lister=None):
    """Merge local + remote run listings, label each entry with its source."""
    from profile_manager import forensic
    local = forensic.list_recent_runs(runs_dir=local_runs_dir, limit=limit)
    for entry in local:
        entry.setdefault("source", "local")
    out = list(local)
    sources = remote_sources or parse_remote_sources()
    lister = remote_lister or _ssh_remote_lister
    for source in sources:
        try:
            remote_entries = lister(source, limit=limit)
        except Exception:
            remote_entries = []
        for entry in remote_entries:
            entry["source"] = source["name"]
            out.append(entry)
    out.sort(key=lambda e: (e.get("ended_at") or "", e.get("run_id") or ""), reverse=True)
    return out[:limit]
