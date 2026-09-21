import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


FRAMEWORK_VERSION = "0.1.0"
DEFAULT_ACTOR = "shared:dwarf"


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _utc_timestamp_compact():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _ps_rss_bytes(pid):
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    if not out:
        return None
    try:
        return int(out.split()[0]) * 1024
    except (ValueError, IndexError):
        return None


def _dir_size_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def capture_local_resource_snapshot(*, pid=None, data_dir=None):
    snap = {
        "process_rss_bytes": _ps_rss_bytes(pid) if pid is not None else None,
        "data_dir_path": str(data_dir) if data_dir is not None else None,
        "data_dir_bytes": _dir_size_bytes(data_dir) if data_dir is not None and Path(data_dir).is_dir() else None,
    }
    return snap


def capture_remote_resource_snapshot(*, ssh_target, process_pattern=None, data_dir=None):
    """Capture a resource snapshot on a remote host via SSH.

    ssh_target is a sequence usable as the SSH command prefix
    (matching profile_manager.remote.ssh_command output).
    process_pattern is a pgrep-style pattern; if None, no process RSS is captured.
    data_dir is an absolute remote path; if None, no disk usage is captured.
    """
    snap = {
        "process_rss_bytes": None,
        "data_dir_path": data_dir,
        "data_dir_bytes": None,
    }
    parts = []
    if process_pattern:
        parts.append(
            "PID=$(pgrep -f " + shlex.quote(process_pattern) + " | head -n1); "
            "if [ -n \"$PID\" ]; then ps -o rss= -p \"$PID\" 2>/dev/null | tr -d ' '; fi; echo ---"
        )
    else:
        parts.append("echo ---")
    if data_dir:
        parts.append("du -sb " + shlex.quote(data_dir) + " 2>/dev/null | awk '{print $1}'")
    else:
        parts.append("echo")
    remote_cmd = " ; ".join(parts)
    try:
        result = subprocess.run(
            list(ssh_target) + [remote_cmd],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return snap
    if result.returncode != 0:
        return snap
    parts_out = result.stdout.split("---")
    if len(parts_out) >= 1:
        head = parts_out[0].strip().splitlines()
        if head:
            try:
                snap["process_rss_bytes"] = int(head[0]) * 1024
            except ValueError:
                pass
    if len(parts_out) >= 2:
        tail = parts_out[1].strip()
        if tail:
            try:
                snap["data_dir_bytes"] = int(tail.splitlines()[0])
            except (ValueError, IndexError):
                pass
    return snap


def capture_env():
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "host": socket.gethostname(),
        "clock_utc": _utc_now_iso(),
    }


def compute_run_id(*, timestamp, scenario_bytes, profile_bytes, env_bytes, seed):
    seed_bytes = repr(seed).encode("utf-8")
    digest = hashlib.sha256()
    for chunk in (scenario_bytes, b"\x1f", profile_bytes, b"\x1f", env_bytes, b"\x1f", seed_bytes):
        digest.update(chunk)
    return f"{timestamp}-{digest.hexdigest()[:8]}"


class RunHandle:
    def __init__(self, *, run_id, run_dir, state_dir, scenario_id, scenario_sha256, scenario_path,
                 target, runtime, profile_id, profile_sha256, env_sha256, seed, framework_version,
                 framework_commit, actor, started_at, measurement_context=None,
                 expected_security_finding=None):
        self.run_id = run_id
        self.run_dir = run_dir
        self._state_dir = state_dir
        self._scenario_id = scenario_id
        self._scenario_sha256 = scenario_sha256
        self._scenario_path = scenario_path
        self._target = target
        self._runtime = runtime
        self._profile_id = profile_id
        self._profile_sha256 = profile_sha256
        self._env_sha256 = env_sha256
        self._seed = seed
        self._framework_version = framework_version
        self._framework_commit = framework_commit
        self._actor = actor
        self._started_at = started_at
        self._assertions = []
        self._log_path = run_dir / "log.ndjson"
        self._probes_dir = run_dir / "probes"
        self._probes_dir.mkdir(exist_ok=True)
        self._events_dir = run_dir / "events"
        self._metrics_dir = run_dir / "metrics"
        self._metrics_host_dir = self._metrics_dir / "host"
        self._metrics_process_dir = self._metrics_dir / "process"
        self._metrics_runtime_dir = self._metrics_dir / "runtime"
        self._events_dir.mkdir(exist_ok=True)
        self._metrics_dir.mkdir(exist_ok=True)
        self._metrics_host_dir.mkdir(exist_ok=True)
        self._metrics_process_dir.mkdir(exist_ok=True)
        self._metrics_runtime_dir.mkdir(exist_ok=True)
        self._start_resource_snapshot = None
        self._end_resource_snapshot = None
        self._telemetry_summary = None
        self._precondition = None
        self._measurement_context = None
        self._expected_security_finding = (
            dict(expected_security_finding)
            if expected_security_finding is not None
            else None
        )
        if measurement_context is not None:
            self.set_measurement_context(measurement_context)

    def log(self, *, phase, primitive, level, event, payload=None):
        entry = {
            "ts": _utc_now_iso(),
            "phase": phase,
            "primitive": primitive,
            "level": level,
            "event": event,
        }
        if payload is not None:
            entry["payload"] = payload
        with self._log_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    def probe_sample(self, probe_name, *, value, meta=None):
        entry = {"ts": _utc_now_iso(), "value": value}
        if meta is not None:
            entry["meta"] = meta
        path = self._probes_dir / f"{probe_name}.ndjson"
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    def assertion_result(self, *, primitive, params, evaluated_value, data_points_used, result, note=None):
        entry = {
            "primitive": primitive,
            "params": params,
            "evaluated_value": evaluated_value,
            "data_points_used": data_points_used,
            "result": result,
        }
        if note is not None:
            entry["note"] = note
        self._assertions.append(entry)

    def set_start_resource_snapshot(self, snapshot):
        self._start_resource_snapshot = snapshot

    def set_telemetry_summary(self, summary):
        self._telemetry_summary = summary

    def set_precondition(self, precondition):
        self._precondition = dict(precondition) if precondition is not None else None

    def set_measurement_context(self, context):
        """Retain a detached JSON-safe snapshot of resolver/runtime state."""
        self._measurement_context = json.loads(
            json.dumps(context, sort_keys=True, ensure_ascii=False)
        )

    def end(self, *, exit_status, end_resource_snapshot=None):
        ended_at = _utc_now_iso()
        self._end_resource_snapshot = end_resource_snapshot

        # Write assertions.json (always, even if empty).
        (self.run_dir / "assertions.json").write_text(
            json.dumps(self._assertions, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        passes = sum(1 for a in self._assertions if a.get("result") == "pass")
        fails = sum(1 for a in self._assertions if a.get("result") == "fail")
        telemetry_summary = _materialize_telemetry(
            log_path=self._log_path,
            events_dir=self._events_dir,
            metrics_dir=self._metrics_dir,
            telemetry_summary=self._telemetry_summary,
        )
        measurement_manifest = _materialize_measurement_selection(
            run_dir=self.run_dir,
            context=self._measurement_context,
        )
        manifest = {
            "run_id": self.run_id,
            "framework": {"version": self._framework_version, "commit": self._framework_commit},
            "scenario": {"id": self._scenario_id, "spec_version": "v1", "path": str(self._scenario_path), "sha256": self._scenario_sha256},
            "target": dict(self._target),
            "runtime": self._runtime,
            "profile": (
                {"id": self._profile_id, "sha256": self._profile_sha256}
                if self._runtime == "devnet"
                else None
            ),
            "env_sha256": self._env_sha256,
            "seed": self._seed,
            "started_at": self._started_at,
            "ended_at": ended_at,
            "exit_status": exit_status,
            "assertion_summary": {"total": len(self._assertions), "pass": passes, "fail": fails},
            "actor": self._actor,
            "resource_snapshot": _build_resource_snapshot(self._start_resource_snapshot, self._end_resource_snapshot, self._started_at, ended_at),
            "telemetry": telemetry_summary,
            "execution": _classify_execution(
                exit_status=exit_status,
                assertions=self._assertions,
                target=self._target,
                expected_security_finding=self._expected_security_finding,
            ),
        }
        if measurement_manifest is not None:
            manifest["measurements"] = measurement_manifest
        if self._precondition is not None:
            manifest["precondition"] = dict(self._precondition)
        manifest_bytes = _canonical_json(manifest)
        (self.run_dir / "manifest.json").write_bytes(manifest_bytes)
        manifest_hash = _sha256_hex(manifest_bytes)

        # SARIF is a deterministic, unsigned view of the run's recorded
        # assertions. Generate it for every completed run; explicit export
        # remains available as an idempotent regeneration workflow.
        sarif_output = self.run_dir / "outputs" / "sarif-export"
        try:
            from profile_manager.sarif import run_sarif_export

            run_sarif_export(
                runs_dir=self.run_dir.parent,
                output_dir=sarif_output,
                target_run_id=self.run_id,
                generation="automatic-run-finalization",
            )
        except Exception as exc:  # noqa: BLE001 - evidence capture must retain the run
            sarif_output.mkdir(parents=True, exist_ok=True)
            (sarif_output / "error.json").write_text(
                json.dumps(
                    {
                        "schema_version": "v1",
                        "target_run_id": self.run_id,
                        "generation": "automatic-run-finalization",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        # Link only to a retained, fully verifiable head. Missing archived
        # history must not make every future run unverifiable, and older
        # evidence is never rewritten to hide the gap.
        head_path = self._state_dir / "chain-head.json"
        continuity = "genesis"
        continuity_metadata = {}
        if head_path.exists():
            try:
                prev_entry = json.loads(head_path.read_text(encoding="utf-8"))
                previous_head_hash = _sha256_hex(_canonical_json(prev_entry))
                previous_head_run_id = str(prev_entry.get("run_id") or "")
                retained_chain_path = self.run_dir.parent / previous_head_run_id / "chain.json"
                retained_entry = (
                    json.loads(retained_chain_path.read_text(encoding="utf-8"))
                    if retained_chain_path.is_file()
                    else None
                )
                head_matches_retained = retained_entry == prev_entry
                head_verify = (
                    verify(
                        previous_head_run_id,
                        runs_dir=self.run_dir.parent,
                        state_dir=self._state_dir,
                    )
                    if previous_head_run_id and head_matches_retained
                    else VerifyResult(ok=False, errors=["stored chain head is not retained verbatim"])
                )
                if head_verify.ok:
                    prev_hash = previous_head_hash
                    continuity = "linked"
                else:
                    prev_hash = "genesis"
                    continuity = "re-rooted"
                    continuity_metadata = {
                        "continuity_reason": "previous-head-unverifiable",
                        "previous_head_run_id": previous_head_run_id or None,
                        "previous_head_hash": previous_head_hash,
                    }
            except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
                prev_hash = "genesis"
                continuity = "re-rooted"
                continuity_metadata = {
                    "continuity_reason": "previous-head-unreadable",
                    "previous_head_run_id": None,
                }
        else:
            prev_hash = "genesis"

        chain_entry = {
            "run_id": self.run_id,
            "manifest_hash": manifest_hash,
            "prev_hash": prev_hash,
            "timestamp": _utc_now_iso(),
            "continuity": continuity,
            **continuity_metadata,
        }
        (self.run_dir / "chain.json").write_text(
            json.dumps(chain_entry, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Atomic head update via tmp file + rename.
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = head_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(chain_entry, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, head_path)

        return chain_entry


def _build_resource_snapshot(start, end, started_at, ended_at):
    snap = {}
    try:
        s = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        e = datetime.strptime(ended_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        snap["wall_time_seconds"] = (e - s).total_seconds()
    except ValueError:
        snap["wall_time_seconds"] = None

    def _rss(side):
        if side is None:
            return None
        return side.get("process_rss_bytes")

    rss_start = _rss(start)
    rss_end = _rss(end)
    if rss_start is not None or rss_end is not None:
        snap["process_rss"] = {
            "start_bytes": rss_start,
            "end_bytes": rss_end,
            "delta_bytes": (rss_end - rss_start) if (rss_start is not None and rss_end is not None) else None,
        }
    else:
        snap["process_rss"] = None

    def _disk(side):
        if side is None:
            return None
        return side.get("data_dir_bytes"), side.get("data_dir_path")

    disk_start = _disk(start)
    disk_end = _disk(end)
    if (disk_start and disk_start[0] is not None) or (disk_end and disk_end[0] is not None):
        path = (disk_end and disk_end[1]) or (disk_start and disk_start[1])
        sb = disk_start[0] if disk_start else None
        eb = disk_end[0] if disk_end else None
        snap["data_dir_disk"] = {
            "path": path,
            "start_bytes": sb,
            "end_bytes": eb,
            "delta_bytes": (eb - sb) if (sb is not None and eb is not None) else None,
        }
    else:
        snap["data_dir_disk"] = None

    snap["host_load"] = None
    return snap


def _classify_execution(*, exit_status, assertions, target, expected_security_finding):
    failed = [item for item in assertions if item.get("result") == "fail"]
    execution = {
        "state": "completed",
        "classification": "completed",
        "security_verdict": "pass" if exit_status == "pass" and not failed else "fail",
    }
    expectation = expected_security_finding
    if not expectation or exit_status != "fail" or not failed:
        return execution
    if target.get("source_revision") != expectation.get("target_source_revision"):
        return execution
    if any(item.get("primitive") != expectation.get("failed_assertion") for item in failed):
        return execution
    execution.update(
        {
            "classification": "completed_with_security_finding",
            "finding_id": expectation["finding_id"],
            "failed_assertion": expectation["failed_assertion"],
            "target_source_revision": expectation["target_source_revision"],
        }
    )
    return execution


def start_run(*, scenario_id, scenario_yaml, target, runtime, profile_id, profile_resolved,
              framework_version, framework_commit, seed, actor=DEFAULT_ACTOR,
              runs_dir, state_dir, start_resource_snapshot=None,
              measurement_context=None, expected_security_finding=None):
    if seed is None:
        seed = 0
    runs_dir = Path(runs_dir)
    state_dir = Path(state_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    env = capture_env()
    env_bytes = _canonical_json(env)
    profile_bytes = _canonical_json(profile_resolved if profile_resolved is not None else None)
    timestamp = _utc_timestamp_compact()
    run_id = compute_run_id(
        timestamp=timestamp,
        scenario_bytes=scenario_yaml,
        profile_bytes=profile_bytes,
        env_bytes=env_bytes,
        seed=seed,
    )
    run_dir = runs_dir / run_id
    # If a collision occurs (same content within the same second), append a counter.
    counter = 0
    while run_dir.exists():
        counter += 1
        run_dir = runs_dir / f"{run_id}-{counter}"
    run_dir.mkdir(parents=True)

    (run_dir / "scenario.yaml").write_bytes(scenario_yaml)
    (run_dir / "env.json").write_bytes(env_bytes)
    (run_dir / "resolved-profile.json").write_text(
        json.dumps(profile_resolved, sort_keys=True, ensure_ascii=False, indent=2)
        if profile_resolved is not None
        else "null",
        encoding="utf-8",
    )
    (run_dir / "inputs").mkdir()
    (run_dir / "outputs").mkdir()
    (run_dir / "events").mkdir()
    (run_dir / "metrics").mkdir()
    (run_dir / "metrics" / "host").mkdir(parents=True)
    (run_dir / "metrics" / "process").mkdir(parents=True)
    (run_dir / "metrics" / "runtime").mkdir(parents=True)

    handle = RunHandle(
        run_id=run_dir.name,
        run_dir=run_dir,
        state_dir=state_dir,
        scenario_id=scenario_id,
        scenario_sha256=_sha256_hex(scenario_yaml),
        scenario_path=f"scenario.yaml",
        target=target,
        runtime=runtime,
        profile_id=profile_id,
        profile_sha256=_sha256_hex(profile_bytes) if profile_resolved is not None else None,
        env_sha256=_sha256_hex(env_bytes),
        seed=seed,
        framework_version=framework_version,
        framework_commit=framework_commit,
        actor=actor,
        started_at=_utc_now_iso(),
        measurement_context=measurement_context,
        expected_security_finding=expected_security_finding,
    )
    if start_resource_snapshot is not None:
        handle.set_start_resource_snapshot(start_resource_snapshot)
    return handle


def _materialize_measurement_selection(*, run_dir, context):
    if context is None:
        return None
    snapshot = json.loads(json.dumps(context, sort_keys=True, ensure_ascii=False))
    resolution = snapshot.get("resolution") if "resolution" in snapshot else snapshot
    resolved = list(resolution.get("resolved") or [])
    collector_states = dict(snapshot.get("collector_states") or {})
    for entry in resolved:
        collector_states.setdefault(entry["id"], "selected")
    for state, key in (
        ("skipped", "skipped"),
        ("incompatible", "incompatible"),
        ("disabled", "disabled"),
    ):
        for entry in resolution.get(key) or []:
            if entry.get("id"):
                collector_states.setdefault(entry["id"], state)
    threshold_gates = dict(snapshot.get("threshold_gates") or {})
    for entry in resolved:
        threshold_gates.setdefault(
            entry["id"],
            dict(entry.get("threshold_gate") or {"enabled": False, "thresholds": []}),
        )
    retained = {
        "schema_version": "v1",
        "resolution": resolution,
        "collector_states": collector_states,
        "threshold_gates": threshold_gates,
    }
    measurement_dir = Path(run_dir) / "measurements"
    measurement_dir.mkdir(parents=True, exist_ok=True)
    selection_path = measurement_dir / "selection.json"
    selection_bytes = _canonical_json(retained)
    selection_path.write_bytes(selection_bytes)
    return {
        "selection_path": "measurements/selection.json",
        "selection_sha256": _sha256_hex(selection_bytes),
        "target_identity": resolution.get("target_identity"),
        "profile": resolution.get("profile"),
        "collector_states": collector_states,
        "threshold_gates": threshold_gates,
        "resolved_count": len(resolved),
        "skipped_count": len(resolution.get("skipped") or []),
        "incompatible_count": len(resolution.get("incompatible") or []),
        "disabled_count": len(resolution.get("disabled") or []),
    }


def _materialize_telemetry(*, log_path, events_dir, metrics_dir, telemetry_summary):
    observer_path = events_dir / "observer.ndjson"
    target_path = events_dir / "target.ndjson"
    target_hooks_path = events_dir / "target-hooks.ndjson"
    observer_count = 0
    target_count = 0
    hook_count = 0
    with observer_path.open("w", encoding="utf-8") as observer_fp, \
         target_path.open("w", encoding="utf-8") as target_fp:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as src:
                for line in src:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("primitive") == "framework" or entry.get("phase") == "framework":
                        observer_fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
                        observer_count += 1
                    else:
                        target_fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
                        target_count += 1
        if target_hooks_path.exists():
            with target_hooks_path.open("r", encoding="utf-8") as hooks_fp:
                for line in hooks_fp:
                    if not line.strip():
                        continue
                    target_fp.write(line if line.endswith("\n") else line + "\n")
                    hook_count += 1
    summary = {
        "observer_event_log": "events/observer.ndjson",
        "target_event_log": "events/target.ndjson",
        "target_event_hook_log": "events/target-hooks.ndjson",
        "observer_event_count": observer_count,
        "target_event_count": target_count + hook_count,
        "target_hook_event_count": hook_count,
        "metrics_summary_path": "metrics/summary.json",
    }
    if telemetry_summary:
        summary.update(telemetry_summary)
    (metrics_dir / "summary.json").write_text(
        json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


@dataclass
class VerifyResult:
    ok: bool
    errors: List[str] = field(default_factory=list)


def verify(run_id, *, runs_dir, state_dir):
    runs_dir = Path(runs_dir)
    state_dir = Path(state_dir)
    errors: List[str] = []
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        return VerifyResult(ok=False, errors=[f"run dir missing: {run_dir}"])

    # 1. Re-hash manifest and compare to chain entry's manifest_hash.
    manifest_path = run_dir / "manifest.json"
    chain_path = run_dir / "chain.json"
    if not manifest_path.exists():
        errors.append(f"manifest missing: {manifest_path}")
    if not chain_path.exists():
        errors.append(f"chain entry missing: {chain_path}")
    if errors:
        return VerifyResult(ok=False, errors=errors)

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest_obj = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        return VerifyResult(ok=False, errors=[f"manifest json invalid: {exc}"])

    canonical_manifest = _canonical_json(manifest_obj)
    actual_manifest_hash = _sha256_hex(canonical_manifest)
    chain_entry = json.loads(chain_path.read_text(encoding="utf-8"))
    expected_manifest_hash = chain_entry.get("manifest_hash")
    if expected_manifest_hash != actual_manifest_hash:
        errors.append(
            f"manifest_hash mismatch: chain says {expected_manifest_hash} but recomputed {actual_manifest_hash}"
        )

    # 2. Walk chain back to genesis. For each entry, prev_hash must equal sha256(canonical(prev_entry)).
    current = chain_entry
    seen = set()
    while True:
        if current["run_id"] in seen:
            errors.append(f"chain cycle detected at {current['run_id']}")
            break
        seen.add(current["run_id"])
        prev_hash = current.get("prev_hash")
        if prev_hash == "genesis":
            break
        # Find the previous run by scanning runs_dir for a chain.json whose canonical hash matches prev_hash.
        prev_run = _find_run_by_chain_hash(runs_dir, prev_hash)
        if prev_run is None:
            errors.append(f"prev_hash {prev_hash} for run {current['run_id']} does not match any known chain entry")
            break
        prev_entry = json.loads((prev_run / "chain.json").read_text(encoding="utf-8"))
        recomputed = _sha256_hex(_canonical_json(prev_entry))
        if recomputed != prev_hash:
            errors.append(f"chain prev_hash mismatch at {prev_run.name}: stored {prev_hash} vs recomputed {recomputed}")
            break
        current = prev_entry

    return VerifyResult(ok=not errors, errors=errors)


def _find_run_by_chain_hash(runs_dir, target_hash):
    for child in runs_dir.iterdir():
        chain_path = child / "chain.json"
        if not chain_path.exists():
            continue
        try:
            entry = json.loads(chain_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if _sha256_hex(_canonical_json(entry)) == target_hash:
            return child
    return None


def record_remote_run(
    *,
    scenario_id,
    scenario_yaml,
    target,
    runtime,
    profile_id,
    profile_resolved,
    command_result,
    actor=DEFAULT_ACTOR,
    seed=0,
    framework_version=FRAMEWORK_VERSION,
    framework_commit="unknown",
    runs_dir,
    state_dir,
    start_resource_snapshot=None,
    end_resource_snapshot=None,
    extra_log_payload=None,
):
    """Record an existing remote/SSH-executed CLI run as a forensic bundle.

    This is the integration helper used by the legacy fuzz/smoke/evidence/package
    flows in Slice 2. Native scenario runs (Slice 8) call start_run + RunHandle
    directly instead.
    """
    handle = start_run(
        scenario_id=scenario_id,
        scenario_yaml=scenario_yaml,
        target=target,
        runtime=runtime,
        profile_id=profile_id,
        profile_resolved=profile_resolved,
        framework_version=framework_version,
        framework_commit=framework_commit,
        seed=seed,
        actor=actor,
        runs_dir=runs_dir,
        state_dir=state_dir,
        start_resource_snapshot=start_resource_snapshot,
    )

    rendered = getattr(command_result, "rendered_command", "")
    rc = int(getattr(command_result, "returncode", 1) or 0)
    stdout = getattr(command_result, "stdout", "") or ""
    stderr = getattr(command_result, "stderr", "") or ""

    payload = {"exit_code": rc, "rendered_command": rendered}
    if extra_log_payload:
        payload.update(extra_log_payload)
    handle.log(phase="load", primitive="legacy_remote_command", level="info", event="executed", payload=payload)

    outputs = handle.run_dir / "outputs"
    (outputs / "stdout.log").write_text(stdout, encoding="utf-8")
    (outputs / "stderr.log").write_text(stderr, encoding="utf-8")
    (outputs / "exit_status.txt").write_text(f"{rc}\n", encoding="utf-8")
    (outputs / "command.txt").write_text(rendered + "\n", encoding="utf-8")

    exit_status = "pass" if rc == 0 else "fail"
    handle.end(exit_status=exit_status, end_resource_snapshot=end_resource_snapshot)
    return handle


def list_recent_runs(*, runs_dir, limit=20):
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    entries = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        try:
            mtime = manifest_path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        entries.append({
            "run_id": child.name,
            "scenario_id": manifest.get("scenario", {}).get("id"),
            "runtime": manifest.get("runtime"),
            "exit_status": manifest.get("exit_status"),
            "started_at": manifest.get("started_at"),
            "ended_at": manifest.get("ended_at"),
            "actor": manifest.get("actor"),
            "assertion_summary": manifest.get("assertion_summary"),
            "resource_snapshot": manifest.get("resource_snapshot"),
            "_mtime_ns": mtime,
        })
    entries.sort(key=lambda e: (e.get("ended_at") or "", e["_mtime_ns"], e["run_id"]), reverse=True)
    for e in entries:
        e.pop("_mtime_ns", None)
    return entries[:limit]


def export_bundle(run_id, *, runs_dir, bundles_dir):
    runs_dir = Path(runs_dir)
    bundles_dir = Path(bundles_dir)
    bundles_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run dir missing: {run_dir}")
    bundle_path = bundles_dir / f"{run_id}.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        tar.add(run_dir, arcname=run_id)
    return bundle_path
