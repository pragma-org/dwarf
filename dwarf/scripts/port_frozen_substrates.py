#!/usr/bin/env python3
"""Port frozen self-composing scenario substrates to the resolved-plan binding (F57).

A frozen substrate resolves its nodes through host binaries or local image
tags that a dashboard installation does not have.  A resolved-plan substrate
goes through the run plan's version resolver and gets digest-pinned catalog
images.  The port changes exactly three things and nothing else:

* every node gets ``identity_binding: resolved-plan``;
* ``substrate.compose_mode`` becomes ``docker``;
* Amaru versions written with a leading ``v`` take the catalog spelling.

Each node keeps its declared version: the resolver asks for that exact release
when the request pins nothing.  A substrate the resolver cannot express (two
cardano-node versions in one devnet, an Amaru label outside the catalog
series) is skipped with the reason, as are very large meshes unless the
operator raises ``--max-nodes``.  The file's JSON style (single line or two-space
indent) is preserved.  Nothing is written without ``--apply``.

    python3 dwarf/scripts/port_frozen_substrates.py [--apply] [--max-nodes N] [--report PATH] [FILE...]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DWARF_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DWARF_ROOT / "scenarios"
# Any Amaru release label of the catalog shape (<major>.<minor>.<YYYYMMDD>), present or future;
# the resolver decides whether the catalog actually has it.
_AMARU_CATALOG_LABEL = re.compile(r"^\d+\.\d+\.\d{8}$")
_AMARU_PREFIXED_LABEL = re.compile(r"^v(\d+\.\d+\.\d{8})$")
DEFAULT_MAX_NODES = 19


def _catalog_amaru_version(version: str) -> str | None:
    if _AMARU_CATALOG_LABEL.fullmatch(version):
        return version
    match = _AMARU_PREFIXED_LABEL.fullmatch(version)
    return match.group(1) if match else None


def plan_port(document: dict[str, Any], *, max_nodes: int = DEFAULT_MAX_NODES) -> dict[str, Any]:
    """Return ``{"status": "port"|"skip", "reason", "changes", "document"}`` without side effects."""
    substrate = document.get("substrate")
    if not isinstance(substrate, dict) or not isinstance(substrate.get("nodes"), list):
        return {"status": "skip", "reason": "no substrate", "changes": [], "document": document}
    nodes = substrate["nodes"]
    bindings = {str(node.get("identity_binding") or "frozen") for node in nodes}
    if bindings == {"resolved-plan"}:
        # bindings are already catalog-resolved; only a host compose mode (unrunnable on a dashboard
        # install, which has no node binaries) is left to move
        previous_mode = str(substrate.get("compose_mode") or "host")
        if previous_mode == "docker":
            return {"status": "skip", "reason": "already resolved-plan", "changes": [], "document": document}
        if substrate.get("host_strategy") == "explicit" or substrate.get("hosts"):
            return {"status": "skip", "reason": "explicit multi-host substrate", "changes": [], "document": document}
        ported = json.loads(json.dumps(document))
        ported["substrate"]["compose_mode"] = "docker"
        return {"status": "port", "reason": None,
                "changes": [f"substrate.compose_mode: {previous_mode} -> docker"], "document": ported}
    if bindings != {"frozen"}:
        return {"status": "skip", "reason": "mixed node bindings", "changes": [], "document": document}
    if len(nodes) > max_nodes:
        return {"status": "skip", "reason": f"{len(nodes)} nodes exceeds --max-nodes {max_nodes}", "changes": [], "document": document}
    if substrate.get("host_strategy") == "explicit" or substrate.get("hosts"):
        return {"status": "skip", "reason": "explicit multi-host substrate", "changes": [], "document": document}

    ported = json.loads(json.dumps(document))
    new_substrate = ported["substrate"]
    changes: list[str] = []
    versions: dict[str, set[str]] = {}
    for index, node in enumerate(new_substrate["nodes"]):
        impl = str(node.get("impl"))
        version = str(node.get("version"))
        if impl == "amaru":
            catalog_version = _catalog_amaru_version(version)
            if catalog_version is None:
                return {"status": "skip", "reason": f"amaru version {version} is not a catalog release",
                        "changes": [], "document": document}
            if catalog_version != version:
                changes.append(f"substrate.nodes[{index}].version: {version} -> {catalog_version}")
                node["version"] = catalog_version
                version = catalog_version
        versions.setdefault(impl, set()).add(version)
        node["identity_binding"] = "resolved-plan"
        changes.append(f"substrate.nodes[{index}].identity_binding: frozen -> resolved-plan")
    for impl, declared in versions.items():
        if len(declared) > 1:
            return {"status": "skip",
                    "reason": f"two {impl} versions in one devnet ({', '.join(sorted(declared))}); the resolver binds one release per implementation",
                    "changes": [], "document": document}
    previous_mode = str(substrate.get("compose_mode") or "host")
    if previous_mode != "docker":
        changes.append(f"substrate.compose_mode: {previous_mode} -> docker")
        new_substrate["compose_mode"] = "docker"
    return {"status": "port", "reason": None, "changes": changes, "document": ported}


def _render(document: dict[str, Any], original_text: str) -> str:
    if original_text.count("\n") <= 1:
        return json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


PUBLIC_NETWORKS = {"preview", "preprod", "mainnet"}
PRIVATE_DEVNET = "testnet_42"
PRIVATE_DEVNET_MAGIC = 42


def plan_network_move(document: dict[str, Any]) -> dict[str, Any]:
    """Move a single-host public-network smoke substrate onto the private devnet.

    A substrate on ``preview`` starts cold nodes that must download the whole public chain
    before they can serve tip, peer or transaction-submission queries, which a smoke window
    never allows. The private devnet starts in seconds and is the same on every run. The
    scenario's own node queries are aligned with the devnet magic; explicit multi-host
    substrates are left alone. Returns the same shape as :func:`plan_port`.
    """
    substrate = document.get("substrate")
    if not isinstance(substrate, dict):
        return {"status": "skip", "reason": "no substrate", "changes": [], "document": document}
    network = str(substrate.get("network") or "")
    if network not in PUBLIC_NETWORKS:
        return {"status": "skip", "reason": "not a public network", "changes": [], "document": document}
    if substrate.get("host_strategy") == "explicit" or substrate.get("hosts"):
        return {"status": "skip", "reason": "explicit multi-host substrate", "changes": [], "document": document}
    moved = json.loads(json.dumps(document))
    changes = [f"substrate.network: {network} -> {PRIVATE_DEVNET}"]
    moved["substrate"]["network"] = PRIVATE_DEVNET
    for section in ("setup", "load", "probes", "assertions", "teardown"):
        for index, step in enumerate(moved.get(section) or []):
            if not isinstance(step, dict):
                continue
            for key in ("network_magic", "testnet_magic"):
                if key in step and step[key] != PRIVATE_DEVNET_MAGIC:
                    changes.append(f"{section}[{index}].{key}: {step[key]} -> {PRIVATE_DEVNET_MAGIC}")
                    step[key] = PRIVATE_DEVNET_MAGIC
    title = moved.get("title")
    if isinstance(title, str) and f"a {network} substrate" in title:
        moved["title"] = title.replace(f"a {network} substrate", "a private devnet substrate")
        changes.append("title: public network wording -> private devnet")
    return {"status": "port", "reason": None, "changes": changes, "document": moved}


def port_files(paths: list[Path], *, apply: bool, max_nodes: int = DEFAULT_MAX_NODES, planner=None) -> dict[str, Any]:
    rows = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
        plan = planner(document) if planner is not None else plan_port(document, max_nodes=max_nodes)
        row = {"path": str(path), "status": plan["status"], "reason": plan["reason"], "changes": plan["changes"]}
        if plan["status"] == "port" and apply:
            path.write_text(_render(plan["document"], text), encoding="utf-8")
            row["written"] = True
        rows.append(row)
    summary = {
        "ported" if apply else "portable": sum(row["status"] == "port" for row in rows),
        "skipped": sum(row["status"] == "skip" for row in rows),
        "applied": apply,
    }
    return {"rows": rows, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the ported files (default: dry run)")
    parser.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--move-public-to-devnet", action="store_true",
                        help="move single-host public-network substrates to the private devnet instead of porting bindings")
    parser.add_argument("files", nargs="*", type=Path, help="scenario files (default: every catalog scenario)")
    args = parser.parse_args(argv)
    paths = args.files or sorted(SCENARIOS_DIR.glob("*.yaml"))
    report = port_files(paths, apply=args.apply, max_nodes=args.max_nodes,
                        planner=plan_network_move if args.move_public_to_devnet else None)
    for row in report["rows"]:
        if row["status"] == "port":
            print(f"{'PORTED' if args.apply else 'PORT  '} {Path(row['path']).name}: {len(row['changes'])} changes")
        elif row["reason"] not in {"no substrate", "already resolved-plan", "not a public network"}:
            print(f"SKIP   {Path(row['path']).name}: {row['reason']}")
    print(json.dumps(report["summary"]))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
