#!/usr/bin/env python3
"""Deploy a retained DWARF Amaru/mixed profile from the proven live control.

The Cardano producer cluster stays alive while the bootstrap producer snapshots
its coherent ChainDB.  Amaru starts from that bundle and peers directly with
the same producers.  A deployment is retained only after every runtime and
identity gate passes; partial or failed projects are removed with their volumes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
for import_root in (DWARF_ROOT, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_cardano_amaru_topology import collect_and_classify  # noqa: E402
from qualify_node_versions import (  # noqa: E402
    AMARU_RELAYS,
    BASE_PACKAGE,
    CARDANO_SERVICES,
    PROTECTED_PROJECTS,
    _amaru_log_text,
    _compose,
    _identity_observation,
    _project_containers,
    _project_is_fresh,
    _render_baseline,
    _run,
    classify_log_signals,
    classify_terminal_runtime_failure,
    transform_compose_model,
)
from redeploy_cardano_amaru_topology import (  # noqa: E402
    fresh_readiness_proven,
    remember_bootstrap_evidence,
)


LIFECYCLE = "cardano_amaru_relay_bootstrap_control"
REQUIRED_GATES = {
    "fresh_state",
    "exact_identity",
    "required_services",
    "chain_progress",
    "peer_formation",
    "consumer_amaru_only_path",
    "consumer_converged",
    "no_fatal_signatures",
    "no_restart_loop",
}


class RuntimeControlError(RuntimeError):
    """The retained live-control deployment cannot safely continue."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validate_runtime_request(config: dict[str, Any]) -> None:
    project = str(config.get("compose_project") or "")
    runtime_root = Path(str(config.get("runtime_root") or ""))
    if config.get("lifecycle") != LIFECYCLE:
        raise RuntimeControlError(f"unsupported lifecycle; expected {LIFECYCLE}")
    if config.get("scope") not in {"amaru-only", "mixed"}:
        raise RuntimeControlError("live Amaru control scope must be amaru-only or mixed")
    if project in PROTECTED_PROJECTS:
        raise RuntimeControlError(f"compose project is protected: {project}")
    if not project.startswith("dwarf-profile-"):
        raise RuntimeControlError("retained project must use the dwarf-profile- prefix")
    if not str(config.get("profile_id") or "").startswith("profile-"):
        raise RuntimeControlError("profile_id must identify a DWARF profile")
    if not runtime_root.is_absolute() or runtime_root == Path("/"):
        raise RuntimeControlError("runtime_root must be a specific absolute path")
    for key in ("cardano_image", "amaru_image"):
        if "@sha256:" not in str(config.get(key) or ""):
            raise RuntimeControlError(f"{key} must be pinned by digest")
    for key in ("supporting_cardano_version", "amaru_version"):
        if not str(config.get(key) or "").strip():
            raise RuntimeControlError(f"{key} is required")


def prepare_runtime_model(
    baseline: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    validate_runtime_request(config)
    model = transform_compose_model(
        baseline,
        scope=str(config["scope"]),
        project=str(config["compose_project"]),
        cardano_image=str(config["cardano_image"]),
        amaru_image=str(config["amaru_image"]),
        allowed_project_prefix="dwarf-profile-",
    )
    for service_name, service in model["services"].items():
        labels = service.setdefault("labels", {})
        if not isinstance(labels, dict):
            raise RuntimeControlError(f"{service_name} labels must be a mapping")
        labels.update(
            {
                "ada2.managed": "dwarf",
                "ada2.profile": str(config["profile_id"]),
                "ada2.service": service_name,
            }
        )
    model["x-dwarf-retained-runtime"] = {
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "fresh_state_required": True,
        "retained_only_after_runtime_gates": True,
    }
    return model


def build_runtime_metadata(
    config: dict[str, Any],
    *,
    identity: dict[str, Any],
    observation: dict[str, Any],
    compose_file: str,
) -> dict[str, Any]:
    substrate = dict(config.get("substrate") or {})
    return {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "created_at": _utc_now(),
        "testbed": "local-devnet",
        "target_implementation": config["scope"].replace("-only", ""),
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "compose_project": config["compose_project"],
        "compose_file": compose_file,
        "logical_target_count": int(substrate.get("target_node_count") or 0),
        "declared_support_node_count": int(substrate.get("support_node_count") or 0),
        "actual_topology": {
            "cardano_services": list(CARDANO_SERVICES),
            "amaru_services": list(AMARU_RELAYS),
            "bootstrap_service": "bootstrap-producer",
            "isolated_consumer": "amaru-consumer",
        },
        "versions": {
            "cardano-node": config["supporting_cardano_version"],
            "amaru": config["amaru_version"],
            "policy": substrate.get("version_policy"),
            "status": substrate.get("version_status"),
            "catalog_revision": substrate.get("catalog_revision"),
            "catalog_snapshot": substrate.get("catalog_snapshot"),
        },
        "images": {
            "cardano-node": config["cardano_image"],
            "amaru": config["amaru_image"],
        },
        "identity": identity,
        "readiness": observation,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _compose_logs(project: str, compose_file: Path) -> str:
    result = _run(
        _compose(project, compose_file, "logs", "--no-color", "--timestamps"),
        timeout=300,
    )
    return result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")


def _gate_result(gates: dict[str, bool]) -> dict[str, Any]:
    failed = sorted(name for name in REQUIRED_GATES if gates.get(name) is not True)
    return {
        "passed": not failed,
        "classification": "retained-runtime-ready" if not failed else "runtime-contract-failed",
        "required_gates": sorted(REQUIRED_GATES),
        "failed_gates": failed,
        "gates": {name: gates.get(name) is True for name in sorted(REQUIRED_GATES)},
    }


def deploy(config: dict[str, Any], *, base_package: Path = BASE_PACKAGE) -> dict[str, Any]:
    validate_runtime_request(config)
    runtime_root = Path(config["runtime_root"])
    project = str(config["compose_project"])
    evidence_root = runtime_root / "evidence"
    compose_file = runtime_root / "docker-compose.json"
    report_file = evidence_root / "deployment-report.json"
    runtime_file = runtime_root / "runtime.json"
    if compose_file.exists() or runtime_file.exists():
        raise RuntimeControlError(f"runtime assets already exist under {runtime_root}")
    if not _project_is_fresh(project):
        raise RuntimeControlError(f"compose project is not fresh: {project}")
    evidence_root.mkdir(parents=True, exist_ok=True)
    model = prepare_runtime_model(_render_baseline(base_package), config)
    _write_json(compose_file, model)

    events: list[dict[str, Any]] = []

    def emit(stage: str, **details: Any) -> None:
        event = {"at": _utc_now(), "stage": stage, **details}
        events.append(event)
        print(json.dumps(event, sort_keys=True), flush=True)

    gates: dict[str, bool] = {"fresh_state": True}
    observation: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    signals: dict[str, list[str]] = {"fatal": [], "background": []}
    terminal_failure: str | None = None
    error: str | None = None
    retain = False
    emit("prepared", project=project, runtime_root=str(runtime_root))
    try:
        up = _run(
            _compose(project, compose_file, "up", "-d"),
            timeout=max(900, int(config.get("healthy_timeout_seconds") or 1800)),
        )
        if up.returncode != 0:
            raise RuntimeControlError((up.stderr or up.stdout or "compose startup failed").strip())
        emit("started")
        deadline = time.monotonic() + max(
            1, int(config.get("healthy_timeout_seconds") or 1800)
        )
        bootstrap_evidence: dict[str, Any] = {}
        attempt = 0
        while time.monotonic() <= deadline:
            attempt += 1
            observation = collect_and_classify(
                project=project,
                output=evidence_root / f"health-{attempt:03d}.json",
                sample_seconds=float(config.get("sample_seconds") or 10.0),
            )
            bootstrap_evidence = remember_bootstrap_evidence(
                observation, bootstrap_evidence
            )
            ready = fresh_readiness_proven(observation, bootstrap_evidence)
            emit(
                "readiness",
                attempt=attempt,
                ready=ready,
                state=observation.get("state"),
            )
            if ready:
                break
            if observation.get("state") == "unhealthy":
                terminal_failure = classify_terminal_runtime_failure(
                    _amaru_log_text(project)
                )
                if terminal_failure:
                    raise RuntimeControlError(terminal_failure)
            time.sleep(max(1.0, float(config.get("sample_seconds") or 10.0)))

        identity = _identity_observation(
            project,
            scope=str(config["scope"]),
            cardano_version=str(config["supporting_cardano_version"]),
            amaru_version=str(config["amaru_version"]),
            cardano_image=str(config["cardano_image"]),
            amaru_image=str(config["amaru_image"]),
        )
        _write_json(evidence_root / "identity.json", identity)
        logs = _compose_logs(project, compose_file) + "\n" + _amaru_log_text(project)
        (evidence_root / "compose-logs.txt").write_text(logs, encoding="utf-8")
        ps = _run(_compose(project, compose_file, "ps", "--all"), timeout=60)
        (evidence_root / "compose-ps.txt").write_text(
            ps.stdout + ps.stderr, encoding="utf-8"
        )

        healthy = fresh_readiness_proven(observation, bootstrap_evidence)
        observed = observation.get("observation") or {}
        containers = observed.get("containers") or {}
        gates.update(
            {
                "exact_identity": bool(identity.get("matched")),
                "required_services": observation.get("state") == "healthy",
                "chain_progress": healthy,
                "peer_formation": observation.get("state") == "healthy",
                "consumer_amaru_only_path": (
                    (observed.get("peer_contract") or {}).get(
                        "amaru_consumer_only_amaru_upstreams"
                    )
                    is True
                ),
                "consumer_converged": healthy,
                "no_restart_loop": bool(containers)
                and all(
                    int(item.get("restart_count") or 0) == 0
                    for item in containers.values()
                ),
            }
        )
        signals = classify_log_signals(logs)
        gates["no_fatal_signatures"] = not signals["fatal"]
        classification = _gate_result(gates)
        retain = bool(classification["passed"])
        if not retain:
            raise RuntimeControlError(
                "runtime gates failed: " + ", ".join(classification["failed_gates"])
            )
        metadata = build_runtime_metadata(
            config,
            identity=identity,
            observation=observation,
            compose_file=str(compose_file),
        )
        _write_json(runtime_file, metadata)
        emit("retained", project=project, runtime_json=str(runtime_file))
    except Exception as exc:
        error = str(exc)
        emit("failed", error=error)
    finally:
        clean_failure_teardown = None
        if not retain:
            down = _run(
                _compose(project, compose_file, "down", "-v", "--remove-orphans"),
                timeout=600,
            )
            clean_failure_teardown = down.returncode == 0 and _project_is_fresh(project)
            (evidence_root / "teardown.txt").write_text(
                down.stdout + ("\n[stderr]\n" + down.stderr if down.stderr else ""),
                encoding="utf-8",
            )
            emit("failure_teardown", passed=clean_failure_teardown)

    classified = _gate_result(gates)
    result = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "project": project,
        "runtime_root": str(runtime_root),
        "completed_at": _utc_now(),
        "retained": retain,
        "error": error,
        "terminal_failure": terminal_failure,
        "identity": identity,
        "observation": observation,
        "log_signals": signals,
        "events": events,
        "clean_failure_teardown": clean_failure_teardown,
        **classified,
    }
    _write_json(report_file, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base-package", type=Path, default=BASE_PACKAGE)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = deploy(config, base_package=args.base_package)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("passed") and result.get("retained") else 2


if __name__ == "__main__":
    raise SystemExit(main())
