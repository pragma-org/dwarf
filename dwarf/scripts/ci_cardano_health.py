"""Observe CI-owned Cardano resource runs without changing the native helper."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from profile_manager.run_ledger import atomic_write_bytes


def process_observation(inspected):
    state = inspected.get("State") or {}
    if (not re.fullmatch(r"[0-9a-f]{64}", str(inspected.get("Id")))
            or any(type(state.get(k)) is not bool for k in ("Running", "OOMKilled"))
            or type(inspected.get("RestartCount")) is not int
            or inspected["RestartCount"] < 0
            or not isinstance(state.get("StartedAt"), str) or not state["StartedAt"]):
        raise ValueError("CI Cardano process observation is incomplete")
    return {"container_id": inspected["Id"], "running": state["Running"],
            "oom_killed": state["OOMKilled"], "restart_count": inspected["RestartCount"],
            "started_at": state["StartedAt"]}


def health_checks(before, after, tip_before, tip_after, signals, records):
    before, after = before or {}, after or {}
    return {
        "target_running_before": before.get("running") is True,
        "target_running_after": after.get("running") is True,
        "target_not_oom_killed": before.get("oom_killed") is False and after.get("oom_killed") is False,
        "target_restart_count_unchanged": (bool(before) and bool(after)
            and before["container_id"] == after["container_id"] and before["started_at"] == after["started_at"]
            and before["restart_count"] == after["restart_count"]),
        "honest_chain_progressed": (type((tip_before or {}).get("block_height")) is int
            and type((tip_after or {}).get("block_height")) is int
            and tip_after["block_height"] > tip_before["block_height"]),
        "no_fatal_signals": records > 0 and isinstance(signals.get("fatal"), list) and not signals["fatal"],
    }


def container_identity(inspected):
    config = inspected.get("Config") or {}
    return {"id": inspected.get("Id"), "name": inspected.get("Name"),
            "image_id": inspected.get("Image"), "image_reference": config.get("Image"),
            "labels": config.get("Labels")}


def begin(manifest, topology, mode, plan, cell, launch_id):
    selected = next((row["identity"] for row in topology["nodes"]
                     if row["node_id"] == (cell or {}).get("node_id")), {})
    scenario_id = (plan.get("scenario") or {}).get("id")
    scenario = manifest["definitions"].get("scenarios", {}).get(scenario_id, {}).get("document", {})
    if (not cell or selected.get("implementation") != "cardano-node" or mode != "stock"
            or not any(row.get("primitive") == "runtime_cardano_measurement_calibration"
                       for row in scenario.get("load", []))):
        return None
    from profile_manager.profiles import runtime_root
    from scripts import runtime_cardano_measurement_calibration as native
    profile = (plan.get("profile") or {}).get("id")
    if profile not in manifest["definitions"]["profiles"]:
        raise ValueError("CI health observer requires a frozen profile")
    runtime = json.loads((runtime_root(profile) / "runtime.json").read_text())
    target, node = native._target(runtime)
    if (any(target.get(k) != selected[k] for k in ("implementation", "version", "source_revision"))
            or target.get("mode") != mode or target.get("image_digest") != selected["artifacts"]["image_digest"]):
        raise ValueError("CI health observer target differs from frozen campaign")
    inspected = native._verify_workload_container(runtime, node)
    installation = os.environ.get("DWARF_INSTALLATION_ID")
    if (not installation or inspected["Config"]["Labels"].get("io.dwarf.installation") != installation
            or runtime.get("compose_project") != f"dwarf-profile-{installation}-{profile}"):
        raise ValueError("CI health observer target is not owned by this campaign")
    started = datetime.now(timezone.utc).isoformat()
    return {"campaign_digest": manifest["campaign_digest"], "launch_id": launch_id,
            "node_id": cell["node_id"], "target": target, "runtime": runtime, "node": node,
            "container_identity": container_identity(inspected),
            "started_at": started, "before": process_observation(inspected),
            "tip_before": native._query_cardano_tip(node["container_id"],
                socket_path=node.get("container_socket_path") or "/state/node.socket",
                network_magic=int(runtime.get("network_magic", 42)))}


def finish(observation, run_dir):
    if observation is None:
        return {}
    from scripts import runtime_cardano_measurement_calibration as native
    from scripts.qualify_node_versions import classify_log_signals
    run_dir = Path(run_dir)
    runtime, node = observation["runtime"], observation["node"]
    after, tip_after, errors = None, None, []
    try:
        inspected = native._inspect_container(node["container_id"])
        if container_identity(inspected) != observation["container_identity"]:
            raise ValueError("CI health observer container identity changed")
        after = process_observation(inspected)
        tip_after = native._query_cardano_tip(node["container_id"],
            socket_path=node.get("container_socket_path") or "/state/node.socket",
            network_magic=int(runtime.get("network_magic", 42)))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    relative = "outputs/ci-cardano-health"
    logs = run_dir / relative / "node.log"
    trace, signals = {"record_count": 0}, {"fatal": []}
    try:
        trace = native._capture_logs(node["container_id"], since=observation["started_at"], destination=logs)
        signals = classify_log_signals(logs.read_text())
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    report = {k: observation[k] for k in ("campaign_digest", "launch_id", "node_id", "target", "started_at", "before", "tip_before", "container_identity")}
    report.update(run_id=run_dir.name, ended_at=datetime.now(timezone.utc).isoformat(), after=after,
        tip_after=tip_after, errors=errors, log_signals=signals, log_artifact=relative + "/node.log",
        checks=health_checks(observation["before"], after, observation["tip_before"], tip_after, signals, trace["record_count"]),
        observer={"implementation": "cardano-node", "container": node["container_id"]},
        artifact=relative + "/result.json")
    if errors:
        report["checks"]["observation_complete"] = False
    atomic_write_bytes(run_dir / report["artifact"], (json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    return {observation["node_id"]: report}
