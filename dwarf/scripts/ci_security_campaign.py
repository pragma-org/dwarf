#!/usr/bin/env python3
"""Run a frozen campaign with bounded retries and independently verified archives.

The local catalog is the executable trust boundary. A plan can select catalog
IDs, but cannot supply a scenario body, command, registry or executable path.
Builds are observations supplied by the build lane; this command never builds.
The checkpoint is private operator state, not a publication catalog.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profile_manager.campaign_evidence import evaluate_campaign_evidence
from profile_manager.run_ledger import atomic_write_bytes
from profile_manager.security_campaign import (
    _check_pair, _digest, _freeze_definition, _pair_status, _schema_validate, campaign_evaluation_status,
    digest_security_campaign, serialize_campaign,
)

ROOT = Path(__file__).resolve().parents[2]
HOST_LOCK = Path("/tmp/dwarf-security-campaign.lock")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PROJECT = re.compile(r"^dwarf-substrate-[a-z0-9]+(?:-[a-z0-9]+)*$")


@contextmanager
def host_lock(path=HOST_LOCK):
    """One lock shared by campaigns in every checkout and storage directory."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another security campaign holds the host lock") from exc
        yield
    finally:
        os.close(fd)


def _validate_execution_recipe(manifest, entry, topology):
    """Read-only trusted-recipe gate, before runtime inventory or qualification."""
    from profile_manager.security_campaign import _bound_scenario
    execution = entry.get("execution")
    if execution is None:
        return
    if not execution or entry["topology_id"] != topology["id"]:
        raise ValueError("security execution contract missing or topology differs")
    contract_topology = entry["contract"].get("topology")
    if not isinstance(contract_topology, dict):
        raise ValueError("security execution contract topology must be an object")
    if any(topology.get(field) != contract_topology.get(field)
           for field in ("mode", "deployment_profile")):
        raise ValueError("security execution topology mode or deployment_profile differs from its frozen contract")
    for node in topology["nodes"]:
        if node["implementation"] != node["identity"]["implementation"]:
            raise ValueError(f"security execution node {node['node_id']} implementation differs from its frozen identity")
    # Reconstruct both contracts from the trusted catalog, never from the
    # candidate-supplied body, including the separately executed control.
    for selected_id, frozen in ((entry["contract"]["scenario_id"], execution),
                                (entry["contract"]["normal_control"]["scenario_id"], execution["normal_control"])):
        trusted = _freeze_definition("scenarios", selected_id)
        if (manifest["definitions"]["scenarios"][selected_id] != trusted
                or frozen["scenario_digest"] != trusted["digest"]
                or frozen["scenario"] != _bound_scenario(trusted["document"], topology)):
            raise ValueError("security execution recipe differs from the trusted frozen contract")
        substrate = frozen["scenario"].get("substrate", {})
        if substrate.get("nodes"):
            nodes = {node["node_id"]: node for node in topology["nodes"]}
            if ({node["id"] for node in substrate["nodes"]} != set(nodes)
                    or any(node["impl"] != nodes[node["id"]]["implementation"]
                           or node["role"] != nodes[node["id"]].get("role") for node in substrate["nodes"])
                    or substrate.get("topology", {}).get("edges", []) != topology.get("edges", [])):
                raise ValueError("security execution topology differs from the frozen identities/roles/edges")


def validate_campaign(manifest):
    _schema_validate(manifest)
    if (manifest["campaign_digest"] != digest_security_campaign(manifest)
            or manifest["campaign_id"] != "campaign-" + manifest["campaign_digest"].split(":")[1]):
        raise ValueError("campaign identity/digest mismatch")
    for catalog, definitions in manifest["definitions"].items():
        for definition_id, frozen in definitions.items():
            if not _ID.fullmatch(definition_id):
                raise ValueError("only trusted catalog identifiers are executable")
            if frozen != _freeze_definition(catalog, definition_id):
                raise ValueError(f"frozen definition differs from trusted catalog: {definition_id}")
    # Also validate links, predicates and an empty evidence index before touching
    # Docker. The existing evaluator evaluates security before measurements.
    evaluate_campaign_evidence(manifest, {"schema_version": "v1", "campaign_digest": manifest["campaign_digest"],
                                         "builds": [], "security": [], "measurements": []})
    topologies = {topology["id"]: topology for topology in manifest["topologies"]}
    for entry in manifest["entries"]:
        _validate_execution_recipe(manifest, entry, topologies[entry["topology_id"]])


@contextmanager
def _catalog_environment(binding):
    values = {"ADA2_DWARF_CAMPAIGN_CATALOG": binding["path"],
              "ADA2_DWARF_CAMPAIGN_CATALOG_SHA256": binding["sha256"]}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def receipt_files_valid(receipt):
    try:
        return all(hashlib.sha256(Path(receipt[field]).read_bytes()).hexdigest() == receipt[field + "_sha256"]
                   for field in ("archive", "signature"))
    except (OSError, KeyError, TypeError):
        return False


def _save(path, state):
    state["checkpoint_digest"] = _digest({key: value for key, value in state.items() if key != "checkpoint_digest"})
    atomic_write_bytes(path, (serialize_campaign(state) + "\n").encode())


def _evidence(manifest, state):
    from profile_manager.campaign_evidence import _measurement, _security
    security, security_ranks, measurements, seen = {}, {}, {}, {}
    entries = {entry["id"]: entry for entry in manifest["entries"]}
    topologies = {topology["id"]: topology for topology in manifest["topologies"]}
    builds = {(row["mode"],): row for row in state["builds"]}
    cells = {(cell["topology_id"], cell["node_id"], cell["measurement_id"], mode): cell
             for cell in manifest["required_tap_cells"] for mode in cell["target_modes"]}
    nodes = {(topology["id"], node["node_id"]): node for topology in manifest["topologies"] for node in topology["nodes"]}
    for key in sorted(state["cells"]):
        record = state["cells"][key]
        # A failed/empty retry cannot erase the last classifiable result. Among
        # classifiable results the latest attempt wins, so an older pass cannot
        # hide a new finding. Measurement history is deliberately not revived.
        for ordinal, attempt in enumerate([*record.get("history", []), record]):
            result = attempt.get("result", {})
            if not result.get("security"):
                continue
            row = result["security"]
            entry = entries[row["entry_id"]]
            evaluated = _security(entry, row, manifest, builds, topologies[entry["topology_id"]])
            rank = (evaluated["classification"] in {"pass", "finding"}, ordinal)
            if row["entry_id"] in security_ranks and security_ranks[row["entry_id"]][0] != key:
                raise ValueError("conflicting security entry evidence")
            if row["entry_id"] not in security or rank >= security_ranks[row["entry_id"]][1]:
                security[row["entry_id"]] = row
                security_ranks[row["entry_id"]] = (key, rank)
        result = record.get("result", {})
        for row in result.get("measurements", []):
            binding = tuple(row[field] for field in ("topology_id", "node_id", "measurement_id", "mode"))
            if binding not in cells:
                raise ValueError("measurement evidence does not belong to a frozen cell")
            run = row["result"]["run_id"]
            same_run = (*binding, run)
            if same_run in seen and seen[same_run] != row:
                raise ValueError("conflicting measurement evidence for the same run/cell")
            seen[same_run] = row
            evaluated = _measurement(cells[binding], binding[-1], row, manifest, builds, nodes[binding[:2]])
            # Verification wins over incomplete observations from any lane.
            # Fewer unmet predicates wins among incomplete rows. Independent
            # equally complete runs use stable run IDs; their raw rows remain
            # retained in the checkpoint rather than being numerically combined.
            rank = (evaluated["state"] == "verified", -len(evaluated["reasons"]))
            candidate = (rank, run, row)
            previous = measurements.get(binding)
            if previous is None or rank > previous[0] or rank == previous[0] and run < previous[1]:
                measurements[binding] = candidate
    return {"schema_version": "v1", "campaign_digest": manifest["campaign_digest"], "builds": state["builds"],
            "security": list(security.values()), "measurements": [measurements[key][2] for key in sorted(measurements)]}


def _evaluate(manifest, state):
    state["evidence"] = _evidence(manifest, state)
    state["evaluation"] = evaluate_campaign_evidence(manifest, state["evidence"])
    state["status"] = campaign_evaluation_status(manifest, state["evaluation"])


def _security_mode(topology, fallback="stock"):
    """The heterogeneous adapter currently supports stock artifacts only."""
    modes = {node["identity"].get("mode", fallback) for node in topology["nodes"]}
    heterogeneous = len({node["implementation"] for node in topology["nodes"]}) > 1
    if len(modes) != 1 or (heterogeneous and modes != {"stock"}):
        raise ValueError("unsupported-per-node-mode: heterogeneous security execution requires all-stock nodes")
    return next(iter(modes))


def _execution_compatibility_error(entry, topology, *, qualified=False, allow_pending=False):
    execution = entry.get("execution")
    if execution is None:  # Historical plans predate this execution contract.
        return None
    status = execution.get("compatibility", {}).get("status")
    if status == "pending-prerequisites" and (qualified or allow_pending):
        return None
    if status != "declared-compatible":
        return "security execution compatibility is not declared-compatible"
    try:
        _check_pair(topology["nodes"])
    except ValueError as exc:
        return f"security execution compatibility differs: {exc}"
    if execution["compatibility"] != _pair_status(topology["nodes"]):
        return "security execution compatibility differs from the frozen node prerequisites"
    return None


def _cell_key(cell, mode):
    return "measurement:" + ":".join([cell["topology_id"], cell["node_id"], cell["measurement_id"], mode])


def _measurement_result(state, cell, mode):
    return next(row for row in state["evaluation"]["measurement_completeness"]["cells"]
                if all(row[field] == cell[field] for field in ("topology_id", "node_id", "measurement_id")) and row["mode"] == mode)


def run_campaign(manifest, *, directory, runtime, builds, resume=False, lock_path=HOST_LOCK):
    """Run once, or explicitly retry missing/failed cells of the same snapshot.

    Runtime is the sole side-effect adapter. All decisions use the existing
    evaluator. Each attempt is checkpointed before launch and after archive
    verification. A failure in one scenario never discards earlier evidence.
    """
    validate_campaign(manifest)
    for topology in manifest["topologies"]:
        _security_mode(topology)
    directory = Path(directory)
    with host_lock(lock_path):
        directory.mkdir(parents=True, exist_ok=True)
        checkpoint = directory / "state.json"
        if checkpoint.exists():
            if not resume:
                raise ValueError("campaign already exists; use --resume for missing/failed cells")
            state = json.loads(checkpoint.read_text())
            if state.get("checkpoint_digest") != _digest({k: v for k, v in state.items() if k != "checkpoint_digest"}):
                raise ValueError("campaign checkpoint digest mismatch")
            if state.get("campaign_digest") != manifest["campaign_digest"]:
                raise ValueError("resume requires the same frozen campaign")
            if state.get("builds") != builds:
                raise ValueError("resume requires identical build observations")
        else:
            state = {"schema_version": "v1", "campaign_digest": manifest["campaign_digest"],
                     "builds": deepcopy(builds), "readiness": {"passed": False}, "cells": {}, "cleanup_errors": []}
            atomic_write_bytes(directory / "manifest.json", (serialize_campaign(manifest) + "\n").encode())
        _evaluate(manifest, state)
        topologies = {row["id"]: row for row in manifest["topologies"]}
        blockers = [f"{entry['id']}: {reason}" for entry in manifest["entries"]
                    if (reason := _execution_compatibility_error(
                        entry, topologies[entry["topology_id"]], allow_pending=True))]
        if blockers:
            state["readiness"] = {"passed": False, "executed": False, "status": "blocked", "reasons": blockers}
            _save(checkpoint, state)
            return state
        before = runtime.projects()  # Failure here must start nothing.
        _save(checkpoint, state)
        try:
            # Readiness is a fresh observation even when all cells can be resumed.
            state["readiness"] = runtime.preflight(manifest, builds)
            _save(checkpoint, state)
            if state["readiness"].get("passed") is not True:
                return state
            qualified = set(state["readiness"].get("qualified_topology_ids", []))
            unresolved = [entry["id"] for entry in manifest["entries"]
                          if _execution_compatibility_error(
                              entry, topologies[entry["topology_id"]],
                              qualified=entry["topology_id"] in qualified)]
            if unresolved:
                state["readiness"].update(
                    passed=False, status="blocked",
                    reasons=[f"{entry_id}: runtime qualification did not establish frozen topology compatibility"
                             for entry_id in unresolved])
                _save(checkpoint, state)
                return state

            def valid_previous(key):
                previous = state["cells"].get(key, {})
                receipts = previous.get("archives", [])
                try:
                    return (previous.get("status") == "complete" and bool(receipts)
                            and all(receipt_files_valid(receipt) and runtime.verify_archive(receipt) for receipt in receipts))
                except (RuntimeError, ValueError, OSError):
                    return False

            def attempt(key, scenario_id, *, entry=None, cell=None, mode="stock"):
                previous = state["cells"].get(key, {})
                record = {"status": "running", "attempts": previous.get("attempts", 0) + 1, "archives": [], "history": previous.get("history", [])}
                if previous:
                    record["history"].append({k: v for k, v in previous.items() if k != "history"})
                state["cells"][key] = record
                _save(checkpoint, state)
                try:
                    if entry:
                        topology = next(row for row in manifest["topologies"] if row["id"] == entry["topology_id"])
                        mode = _security_mode(topology, mode)
                    control_id = entry["contract"]["normal_control"]["scenario_id"] if entry else scenario_id
                    control = runtime.run(manifest, control_id, mode=mode, topology_id=(entry or cell)["topology_id"],
                                          **({"execution_entry": entry} if entry else {}))
                    if not control.get("execution_complete"):
                        raise RuntimeError("normal control execution failed")
                    record["control"] = deepcopy(control)
                    record["archives"].append(runtime.archive(control["run_id"]))
                    if not runtime.verify_archive(record["archives"][-1]):
                        raise RuntimeError("normal control archive verification failed")
                    record["control_verified"] = True
                    _save(checkpoint, state)
                    result = runtime.run(manifest, scenario_id, entry=entry, cell=cell, mode=mode, control=control, topology_id=(entry or cell)["topology_id"])
                    record["result"] = result
                    if result.get("security", {}).get("run_id", result["run_id"]) != result["run_id"] or any(
                            row.get("result", {}).get("run_id") != result["run_id"] for row in result.get("measurements", [])):
                        record["rejected_result"] = record.pop("result")
                        raise ValueError("scenario evidence belongs to another run")
                    accepted_measurements, rejected_measurements = [], []
                    for measured in result.get("measurements", []):
                        expected_control = deepcopy(control.get("control"))
                        if expected_control:
                            expected_control["targets"] = {node: target for node, target in expected_control.get("targets", {}).items()
                                                           if node == measured["node_id"]}
                        if (not expected_control or expected_control.get("status") != "pass"
                                or measured.get("normal_control") != expected_control):
                            rejected_measurements.append(measured)
                        else:
                            accepted_measurements.append(measured)
                    result["measurements"] = accepted_measurements
                    if rejected_measurements:
                        record["rejected_measurements"] = rejected_measurements
                        record["error"] = "measurement normal control differs from the exact successful verified campaign control"
                    _save(checkpoint, state)  # Retain native evidence even if export fails.
                    if not result.get("execution_complete"):
                        raise RuntimeError("scenario execution failed")
                    record["archives"].append(runtime.archive(result["run_id"]))
                    if not runtime.verify_archive(record["archives"][-1]):
                        raise RuntimeError("scenario archive verification failed")
                    _evaluate(manifest, state)
                    if entry:
                        verdict = next(row for row in state["evaluation"]["security_validity"]["entries"] if row["entry_id"] == entry["id"])
                        complete = verdict["classification"] in {"pass", "finding"}
                    else:
                        complete = _measurement_result(state, cell, mode)["state"] == "verified"
                    record["status"] = "complete" if complete else "incomplete"
                except Exception as exc:
                    record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                try:
                    _evaluate(manifest, state)
                except (ValueError, TypeError, KeyError) as exc:
                    record["rejected_result"] = record.pop("result", {})
                    record.update(status="failed", error=f"invalid scenario evidence: {exc}")
                    _evaluate(manifest, state)
                _save(checkpoint, state)

            for entry in sorted(manifest["entries"], key=lambda row: row["id"]):
                key = "security:" + entry["id"]
                verdict = next(row for row in state["evaluation"]["security_validity"]["entries"] if row["entry_id"] == entry["id"])
                if valid_previous(key) and verdict["classification"] in {"pass", "finding"}:
                    continue
                attempt(key, entry["contract"]["scenario_id"], entry=entry)
            # All security workloads, controls and assertions have been evaluated
            # before the calibration/fallback lane can start.
            _evaluate(manifest, state)
            for cell in sorted(manifest["required_tap_cells"], key=lambda row: (row["topology_id"], row["node_id"], row["measurement_id"])):
                for mode in sorted(cell["target_modes"]):
                    key = _cell_key(cell, mode)
                    exercise = _measurement_result(state, cell, mode)
                    if exercise["state"] == "verified":
                        # Evidence attached to a security run is covered by its
                        # receipts; dedicated calibration cells have their own.
                        if key not in state["cells"] or valid_previous(key):
                            continue
                    if cell["runtime"]["status"] != "implemented":
                        continue
                    attempt(key, cell["scenario_id"], cell=cell, mode=mode)
                    if _measurement_result(state, cell, mode)["state"] != "verified":
                        fallback = cell.get("fallback", {})
                        if fallback.get("status") == "ready" and fallback["scenario_id"] != cell["scenario_id"]:
                            attempt(key, fallback["scenario_id"], cell=cell, mode=mode)
            return state
        finally:
            # The runtime registers exact projects from canonical runs it starts.
            # An unrelated project appearing concurrently is never ownership proof.
            try:
                runtime.cleanup(set(runtime.owned_projects) - before)
            except Exception as exc:
                state["cleanup_errors"].append(f"{type(exc).__name__}: {exc}")
            _evaluate(manifest, state)
            _save(checkpoint, state)


class LocalRuntime:
    """Trusted local CLI adapter. It never evaluates candidate-supplied commands.

    The existing deployment resolver or frozen library target loader must bind
    the selected artifact before execution. Missing exact registration is an
    explicit failed cell. Native retained events, transcripts and per-tap files
    supply observations; an exit status does not establish a security verdict.
    """
    def __init__(self, directory, *, catalog_path, producer_version, signing_key, actor="ci"):
        self.directory = Path(directory).resolve()
        self.runs = self.directory / "runs"
        self.state = self.directory / "runtime-state"
        self.imports = self.directory / "imported"
        self.catalog_path = Path(catalog_path).resolve()
        self.producer_version = producer_version
        self.signing_key = Path(signing_key).resolve()
        self.actor = actor
        self.owned_projects = set()
        self.builds = []
        self.qualified_topology_ids = set()

    def _command(self, argv, *, env=None, check=True):
        result = subprocess.run(argv, cwd=ROOT, env={**os.environ, **(env or {})},
                                capture_output=True, text=True, timeout=1800)
        if check and result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}): {Path(argv[0]).name}: {result.stderr[-2000:]}")
        return result.stdout

    def projects(self):
        # Both inventories must succeed; an incomplete baseline cannot authorize
        # cleanup. Exactly the smoke helper's complete-label filtering semantics.
        containers = self._command(["docker", "ps", "-a", "--format", '{{.Label "com.docker.compose.project"}}'])
        networks = self._command(["docker", "network", "ls", "--format", '{{.Label "com.docker.compose.project"}}'])
        return {name for name in (containers + "\n" + networks).splitlines() if _PROJECT.fullmatch(name)}

    def cleanup(self, projects):
        if not set(projects) <= self.owned_projects or any(not _PROJECT.fullmatch(project) for project in projects):
            raise ValueError("cleanup requires exact owned campaign projects")
        errors = []
        for project in sorted(projects):
            try:
                label = "label=com.docker.compose.project=" + project
                containers = self._command(["docker", "ps", "-aq", "--filter", label]).split()
                if containers:
                    self._command(["docker", "rm", "-f", *containers])
                networks = self._command(["docker", "network", "ls", "-q", "--filter", label]).split()
                if networks:
                    self._command(["docker", "network", "rm", *networks])
            except Exception as exc:
                errors.append(f"{project}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _catalog_binding(self):
        from profile_manager.version_catalog import load_version_catalog
        try:
            source = self.catalog_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"selected campaign catalog is unavailable: {self.catalog_path}") from exc
        digest = hashlib.sha256(source).hexdigest()
        binding_path = self.directory / "catalog-binding.json"
        frozen_path = self.directory / "inputs/version-catalog.json"
        binding = {"source": str(self.catalog_path), "path": str(frozen_path), "sha256": digest}
        if binding_path.exists():
            if json.loads(binding_path.read_text()) != binding:
                raise ValueError("selected campaign catalog changed since readiness/resolution")
            if not frozen_path.is_file() or frozen_path.read_bytes() != source:
                raise ValueError("frozen campaign catalog changed or is missing")
        else:
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(frozen_path, source)
            atomic_write_bytes(binding_path, (serialize_campaign(binding) + "\n").encode())
        return {**binding, "catalog": load_version_catalog(frozen_path)}

    def preflight(self, manifest, builds):
        from profile_manager.version_catalog import load_version_catalog, resolve_release
        from scripts.qualify_node_versions import BASE_PACKAGE, run_qualification
        self.builds = deepcopy(builds)
        for topology in manifest["topologies"]:
            _security_mode(topology)
        binding = self._catalog_binding()
        catalog = binding["catalog"]
        observations = []
        qualified_topology_ids = []
        qualifications = {}
        for topology in manifest["topologies"]:
            versions = {}
            for node in topology["nodes"]:
                identity = node["identity"]
                release = resolve_release(catalog, node["implementation"], identity["version"])
                artifacts = identity["artifacts"]
                if identity["kind"] == "candidate":
                    build = next((row for row in builds if row["mode"] == identity.get("mode", "stock")), None)
                    if build is None or build.get("source") != manifest["candidate"]:
                        raise ValueError("readiness requires an observed frozen-mode build of the frozen candidate")
                    artifacts = build["artifacts"]
                # Qualification chooses the first available exact OCI artifact.
                # Matching a later artifact would qualify another build mode.
                selected_artifact = next((artifact for artifact in release.get("artifacts", [])
                                          if artifact.get("kind") == "oci" and artifact.get("availability") == "available"
                                          and artifact.get("digest")), {})
                if (release["source_revision"] != identity["source_revision"]
                        or selected_artifact.get("digest") != artifacts["image_digest"]):
                    raise ValueError("readiness catalog differs from the frozen node/build identity")
                if (identity["kind"] == "candidate" and node["implementation"] == "amaru"
                        and build.get("runtime_image_config_id") is not None
                        and build.get("runtime_image_config_id") != selected_artifact.get("config_image_id")):
                    raise ValueError("readiness candidate image config identity differs from the frozen catalog")
                versions[node["implementation"]] = identity["version"]
            scope = "mixed" if len(versions) > 1 else "amaru-only" if "amaru" in versions else "cardano-only"
            producer = versions.get("cardano-node") or self.producer_version
            if not producer:
                raise ValueError("Amaru readiness requires an explicitly selected supporting producer version")
            candidate_build = next((row for row in builds if row.get("mode") == "stock"), {})
            amaru_release = (resolve_release(catalog, "amaru", versions["amaru"])
                             if "amaru" in versions else None)
            amaru_artifact = next((item for item in (amaru_release or {}).get("artifacts", [])
                                   if item.get("kind") == "oci" and item.get("availability") == "available"), {})
            content_id = (candidate_build.get("runtime_image_config_id")
                          if any(node["identity"]["kind"] == "candidate"
                                 and node["implementation"] == "amaru"
                                 for node in topology["nodes"]) else None)
            qualification_key = (scope, producer, versions.get("amaru"),
                                 amaru_artifact.get("runtime_interface"), content_id)
            if qualification_key not in qualifications:
                with _catalog_environment(binding):
                    observation = run_qualification(
                        scope=scope, catalog_path=Path(binding["path"]), base_package=BASE_PACKAGE,
                        state_dir=self.state, cardano_version=producer, amaru_version=versions.get("amaru"),
                        amaru_runtime_interface=amaru_artifact.get("runtime_interface"),
                        operator_stock_content_id=content_id)
                qualifications[qualification_key] = observation
                observations.append(observation)
            else:
                observation = qualifications[qualification_key]
            if observation.get("passed") is True:
                qualified_topology_ids.append(topology["id"])
        self.qualified_topology_ids = set(qualified_topology_ids)
        return {"executed": True, "passed": bool(observations) and all(row.get("passed") is True for row in observations),
                "qualified_topology_ids": qualified_topology_ids, "observations": observations}

    def _plan(self, manifest, scenario_id, topology, mode, entry, cell):
        from profile_manager.run_plan import RunPlanRequest, explicit_no_measurements_context, resolve_run_plan
        if entry:
            mode = _security_mode(topology, mode)
        versions = {node["implementation"]: node["identity"]["version"] for node in topology["nodes"]}
        attachment = next((row for row in (entry or {}).get("measurement_attachments", [])
                           if row["implementation"] == manifest["definitions"]["scenarios"][scenario_id]["document"]["target"]["implementation"]), {})
        # Per-node attachment intent remains in the immutable manifest. The
        # existing single-target runtime cannot claim a second node's samples.
        kwargs = {"scenario_id": scenario_id, "profile_id": topology.get("deployment_profile"),
                  "version_policy": "exact", "cardano_version": versions.get("cardano-node"),
                  "amaru_version": versions.get("amaru"), "acknowledge_unknown_versions": True}
        if attachment:
            kwargs["measurement_profile"] = attachment["measurement_profile"]
            kwargs["measurement_overrides"] = [{"id": tap, "enabled": True} for tap in attachment.get("additional_measurements", [])]
        if entry and "execution" in entry and len({node["implementation"] for node in topology["nodes"]}) > 1:
            # The resolver has one target; attaching just one participant's
            # profile would misrepresent the frozen per-node measurement intent.
            # Keep the independent per-node calibration cells scheduled below.
            kwargs["measurement_profile"] = "none"
            kwargs.pop("measurement_overrides", None)
        if cell:
            kwargs["measurement_profile"] = "none"
            grouped = sorted({required["measurement_id"] for required in manifest["required_tap_cells"]
                              if required["topology_id"] == cell["topology_id"]
                              and required["node_id"] == cell["node_id"]
                              and required["scenario_id"] == cell["scenario_id"]
                              and required.get("runtime", {}).get("status") == "implemented"
                              and mode in required["target_modes"]})
            kwargs["measurement_overrides"] = [{"id": measurement_id, "enabled": True}
                                                for measurement_id in grouped]
        binding = self._catalog_binding()
        from profile_manager.deployment_versions import version_catalog_revision
        plan = resolve_run_plan(RunPlanRequest(**kwargs), catalog=binding["catalog"],
                                catalog_revision=version_catalog_revision(catalog=binding["catalog"]))
        if entry and "execution" in entry and len({node["implementation"] for node in topology["nodes"]}) > 1:
            # Profileless substrate plans otherwise return an unspecified empty
            # measurement context; freeze the explicit opt-out in the launch.
            plan["measurements"] = explicit_no_measurements_context()
        plan["campaign_catalog"] = {key: binding[key] for key in ("path", "sha256")}
        resolved = (plan.get("versions") or {}).get("resolved", {})
        if (not resolved and topology["mode"] == "library"
                and plan.get("scenario", {}).get("runtime") == "library" and not plan.get("profile")):
            from profile_manager.campaign_runtime_evidence import freeze_library_targets
            bindings = freeze_library_targets(manifest, scenario_id, topology, self.builds, mode, self.directory)
            plan["scenario"]["library_target_bindings"] = bindings
            implementation = plan["scenario"]["target"]["implementation"]
            plan["scenario"]["target"]["version"] = versions[implementation]
            if entry and "execution" in entry:
                self._bind_execution(manifest, scenario_id, topology, entry, plan)
            return plan
        target = plan.get("measurements", {}).get("target_identity", {})
        for node in topology["nodes"]:
            identity = node["identity"]
            actual = resolved.get(node["implementation"], {})
            expected_artifacts = identity["artifacts"]
            if identity["kind"] == "candidate":
                build = next((row for row in self.builds if row["mode"] == mode), {})
                if build.get("source") != manifest["candidate"]:
                    raise ValueError(f"missing observed {mode} build of frozen candidate")
                expected_artifacts = build["artifacts"]
            if any(actual.get(field) != expected for field, expected in {
                    "version": identity["version"], "source_revision": identity["source_revision"],
                    "image_digest": expected_artifacts["image_digest"]}.items()):
                raise ValueError("runtime resolver cannot bind the exact frozen candidate/node artifact; registration is required")
        if mode != "stock" and target.get("mode") != mode:
            raise ValueError(f"runtime resolver cannot bind the requested {mode} variant")
        if entry and "execution" in entry:
            self._bind_execution(manifest, scenario_id, topology, entry, plan)
        return plan

    def _bind_execution(self, manifest, scenario_id, topology, entry, plan):
        from profile_manager.launch_store import materialized_scenario
        execution = entry.get("execution")
        if not execution or entry["topology_id"] != topology["id"]:
            raise ValueError("security execution contract missing or topology differs")
        if error := _execution_compatibility_error(
                entry, topology, qualified=topology["id"] in self.qualified_topology_ids):
            raise ValueError(error)
        _validate_execution_recipe(manifest, entry, topology)
        if scenario_id == entry["contract"]["scenario_id"]:
            expected = deepcopy(execution["scenario"])
        elif scenario_id == entry["contract"]["normal_control"]["scenario_id"]:
            expected = deepcopy(execution["normal_control"]["scenario"])
        else:
            raise ValueError("security execution scenario differs from the frozen contract")
        # Candidate image artifacts are observed only after the plan was frozen.
        # _plan has already matched these identities against the build receipts.
        resolved = (plan.get("versions") or {}).get("resolved", {})
        for node in expected.get("substrate", {}).get("nodes", []):
            actual = resolved[node["impl"]]
            if "image" not in node:
                node.update(image=actual["image_reference"], image_digest=actual["image_digest"])
        # Library harness paths are independently frozen and checked by the
        # target loader. They are the only permitted workload-path substitution.
        for binding in plan.get("scenario", {}).get("library_target_bindings", []):
            operation = expected
            for component in binding["location"]:
                operation = operation[component]
            operation["manifests_dir"] = binding["manifests_dir"]
        identity = next(node["identity"] for node in topology["nodes"]
                        if node["implementation"] == expected["target"]["implementation"])
        plan["scenario"]["target"]["source_revision"] = identity["source_revision"]
        materialized = json.loads(materialized_scenario(plan))
        # Tap selection is a separate resolved contract. All execution fields,
        # including identities, roles, edges, workloads and assertions, compare.
        for body in (expected, materialized):
            body.pop("measurement_profile", None)
            body.pop("measurements", None)
        if materialized != expected:
            raise ValueError("materialized security execution differs from the frozen contract")
        plan["campaign_execution_digest"] = _digest(execution)

    def run(self, manifest, scenario_id, *, entry=None, execution_entry=None, cell=None, mode="stock", control=None, topology_id=None):
        from profile_manager.launch_store import create_launch
        if not _ID.fullmatch(scenario_id) or scenario_id not in manifest["definitions"]["scenarios"]:
            raise ValueError("scenario must be a frozen trusted catalog identifier")
        topology = next(row for row in manifest["topologies"] if row["id"] == (topology_id or manifest["topologies"][0]["id"]))
        bound_entry = entry or execution_entry
        if bound_entry:
            mode = _security_mode(topology, mode)
        plan = self._plan(manifest, scenario_id, topology, mode, bound_entry, cell)
        launches = self.directory / "launches"
        launch = create_launch(plan, root=launches)
        binding = self._catalog_binding()
        if plan.get("campaign_catalog", {key: binding[key] for key in ("path", "sha256")}) != {key: binding[key] for key in ("path", "sha256")}:
            raise ValueError("execution catalog differs from the resolved run plan")
        run_root = self.runs
        run_root.mkdir(parents=True, exist_ok=True)
        before = {path.name for path in run_root.iterdir() if path.is_dir()}
        try:
            output = self._command([sys.executable, "dwarf/cardano-profile", "scenario", "run",
                str(launch["path"] / "scenario.yaml"), "--backend", "local-devnet", "--origin", "ci",
                "--runs-dir", str(self.runs), "--state-dir", str(self.state)],
                env={"ADA2_DWARF_LAUNCH_ID": launch["launch_id"], "ADA2_DWARF_LAUNCH_ROOT": str(launches),
                     "ADA2_DWARF_CAMPAIGN_CATALOG": binding["path"],
                     "ADA2_DWARF_CAMPAIGN_CATALOG_SHA256": binding["sha256"]}, check=False)
        finally:
            # This private runs root belongs to this locked campaign. Register
            # canonical run IDs even when the subprocess dies before printing ID.
            for path in run_root.iterdir():
                if path.is_dir() and path.name not in before and _PROJECT.fullmatch("dwarf-substrate-" + path.name.lower()):
                    self.owned_projects.add("dwarf-substrate-" + path.name.lower())
        ids = re.findall(r"^run_id: ([A-Za-z0-9-]+)$", output, re.MULTILINE)
        if len(ids) != 1:
            raise RuntimeError("scenario did not return one canonical run ID")
        run_id = ids[0]
        run_dir = self.runs / run_id
        from profile_manager.campaign_runtime_evidence import read_native_evidence
        return read_native_evidence(manifest, run_dir, scenario_id=scenario_id, topology=topology,
                                    mode=mode, plan=plan, entry=entry, cell=cell, control=control)

    def archive(self, run_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", run_id):
            raise ValueError("invalid canonical run ID")
        base = [sys.executable, "dwarf/cardano-profile", "bundle"]
        env = {"ADA2_DWARF_BUNDLE_SIGNING_KEY": str(self.signing_key)}
        self._command([*base, "sign", run_id, "--runs-dir", str(self.runs), "--signing-actor", self.actor], env=env)
        self._command([*base, "export", run_id, "--runs-dir", str(self.runs), "--signing-actor", self.actor], env=env)
        output = self.runs / run_id / "outputs/export"
        receipt = {"run_id": run_id, "archive": str(output / f"{run_id}-bundle-export.tar.gz"), "signature": str(output / "signature.json")}
        for field in ("archive", "signature"):
            receipt[field + "_sha256"] = hashlib.sha256(Path(receipt[field]).read_bytes()).hexdigest()
        return receipt

    def verify_archive(self, receipt):
        if not receipt_files_valid(receipt):
            return False
        # Fresh import directory per verification avoids treating an existing
        # imported directory as proof of a new archive's contents.
        import tempfile
        self.imports.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="verify-", dir=self.imports) as imported:
            base = [sys.executable, "dwarf/cardano-profile", "bundle"]
            self._command([*base, "import", receipt["archive"], "--signature-path", receipt["signature"], "--runs-dir", imported])
            self._command([*base, "verify", receipt["run_id"], "--runs-dir", imported, "--json"])
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--build-observations", type=Path, required=True, help="Frozen build evidence JSON array; no source build is performed.")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--producer-version", help="Explicit supporting Cardano producer for an Amaru-only readiness check.")
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="Retry only missing/failed cells of this exact snapshot.")
    args = parser.parse_args(argv)
    try:
        if not args.signing_key.is_file():
            raise ValueError("operator signing key is missing")
        manifest = json.loads(args.manifest.read_text())
        builds = json.loads(args.build_observations.read_text())
        if not isinstance(builds, list):
            raise ValueError("build observations must be a JSON array")
        runtime = LocalRuntime(args.directory, catalog_path=args.catalog, producer_version=args.producer_version, signing_key=args.signing_key)
        state = run_campaign(manifest, directory=args.directory, runtime=runtime, builds=builds, resume=args.resume)
        print(serialize_campaign({**state["status"], "readiness_passed": state["readiness"].get("passed") is True,
                                  "cleanup_errors": state["cleanup_errors"]}))
        return 0 if (state["readiness"].get("passed") is True and not state["cleanup_errors"]
                     and state["evaluation"]["security_validity"]["security_valid"]
                     and state["evaluation"]["measurement_completeness"]["measurement_complete"]
                     and all(cell["status"] == "complete" for cell in state["cells"].values())) else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"security campaign: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
