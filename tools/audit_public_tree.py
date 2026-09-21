#!/usr/bin/env python3
"""Fail closed when a Git tree contains private or generated publication data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


PRIVATE_TEXT = {
    "private home path": "/home/" + "nigel",
    "private macOS home path": "/Users/" + "nigel",
    "private host": "cardano" + "-box",
    "private secondary host": "bra" + "box",
    "private tertiary host": "sp" + "ark",
    "forbidden publisher name": "gain" + "palfam",
    "internal Git host": "git." + "gain" + "palfam.com",
    "internal repository name": "V7" + "-PRAGMA",
    "private ownership name": "Cyber-" + "Cast" + "ellum",
    "private ownership surname": "Cast" + "ellum",
}

ACCEPTED_FINGERPRINT = "cardano" + "-box-2026-09-19"
ACCEPTED_FINGERPRINT_PATHS = {
    "docs/workbench/dwarf-measurement-program-technical-debrief.html",
    "dwarf/docs/client-examples/contracts/01-cbor-decoding.yaml",
    "dwarf/docs/client-examples/contracts/02-plutus-vm.yaml",
    "dwarf/docs/client-examples/contracts/03-invalid-mini-protocol.yaml",
    "dwarf/docs/client-examples/contracts/04-block-application.yaml",
    "dwarf/docs/client-examples/contracts/05-restart-recovery-sync.yaml",
    "dwarf/spec/v1/client-example-acceptance-card.schema.json",
    "tests/test_client_example_acceptance_cards.py",
}
INTENTIONAL_SAFETY_MATCHERS = {
    "delivery/tests/test_delivery_contract.sh": "/Users/" + "operator",
}

ARCHIVE_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2"
)
GENERATED_SUFFIXES = (".log", ".orig", ".pyc")
GENERATED_PARTS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules",
    "logs", "scratchbook", "tasks", "task", "tmp", "temp",
}

SECRET_PATTERNS = {
    "private key material": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "GitHub token": re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "AWS access key": re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "credential URL": re.compile(
        r"https?://[^/\s:@]+:[^@\s/]+@[^\s/]+", re.IGNORECASE
    ),
}


def tracked_paths(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo, check=True, capture_output=True
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def forbidden_path_reason(path: str) -> str | None:
    item = PurePosixPath(path)
    parts = item.parts
    if item.name == ".DS_Store" or item.name.startswith("._"):
        return "operating-system metadata"
    if any(part in GENERATED_PARTS for part in parts):
        return "generated cache or dependency tree"
    if path.startswith(("dwarf/state/", "dwarf/runs/", "dwarf/bundles/")):
        return "runtime state, run, or evidence bundle"
    if path.endswith(ARCHIVE_SUFFIXES):
        return "archive"
    if path.endswith(GENERATED_SUFFIXES):
        return "generated log, backup, or bytecode"
    return None


def private_findings(path: str, text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for match in re.finditer(r"/Users/[A-Za-z0-9._-]+(?:/|$)", text):
        findings.append(
            {
                "path": path,
                "line": text.count("\n", 0, match.start()) + 1,
                "reason": "machine-specific macOS home path",
            }
        )
    for label, needle in PRIVATE_TEXT.items():
        pattern = re.escape(needle)
        if label == "private tertiary host":
            pattern = rf"(?<![A-Za-z0-9_-]){pattern}(?![A-Za-z0-9_-])"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if (
                label == "private host"
                and path in ACCEPTED_FINGERPRINT_PATHS
                and text[match.start() : match.start() + len(ACCEPTED_FINGERPRINT)]
                == ACCEPTED_FINGERPRINT
            ):
                continue
            findings.append(
                {
                    "path": path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "reason": label,
                }
            )
    return findings


def scan_text(repo: Path, paths: list[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    findings: list[dict[str, object]] = []
    intentional: list[dict[str, object]] = []
    for path in paths:
        data = (repo / path).read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        matcher = INTENTIONAL_SAFETY_MATCHERS.get(path)
        if matcher and matcher in text:
            intentional.append(
                {
                    "path": path,
                    "line": text[: text.index(matcher)].count("\n") + 1,
                    "reason": "public delivery test rejects machine-specific paths",
                }
            )
            text = text.replace(matcher, "__INTENTIONAL_MACHINE_PATH_MATCHER__")
        if path in ACCEPTED_FINGERPRINT_PATHS:
            for match in re.finditer(re.escape(ACCEPTED_FINGERPRINT), text):
                intentional.append(
                    {
                        "path": path,
                        "line": text.count("\n", 0, match.start()) + 1,
                        "reason": "accepted frozen measurement evidence identity",
                    }
                )
        findings.extend(private_findings(path, text))
    return findings, intentional


def secret_findings(path: str, text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for label, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(
                {
                    "path": path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "reason": label,
                }
            )
    return findings


def audit(repo: Path) -> dict[str, object]:
    paths = tracked_paths(repo)
    private_text, intentional_private_text = scan_text(repo, paths)
    secrets: list[dict[str, object]] = []
    for path in paths:
        data = (repo / path).read_bytes()
        if b"\0" not in data:
            secrets.extend(secret_findings(path, data.decode("utf-8", errors="replace")))
    forbidden_paths = [
        {"path": path, "reason": reason}
        for path in paths
        if (reason := forbidden_path_reason(path)) is not None
    ]
    return {
        "tracked_files": len(paths),
        "forbidden_paths": forbidden_paths,
        "private_text": private_text,
        "secrets": secrets,
        "intentional_private_text": intentional_private_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit(Path(args.repo).resolve())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"tracked files: {report['tracked_files']}")
        print(f"forbidden paths: {len(report['forbidden_paths'])}")
        print(f"private text matches: {len(report['private_text'])}")
        print(f"secret matches: {len(report['secrets'])}")
    return 1 if report["forbidden_paths"] or report["private_text"] or report["secrets"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
