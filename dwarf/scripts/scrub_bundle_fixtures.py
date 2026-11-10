#!/usr/bin/env python3
"""Scrub host details from recorded fixture bundles and keep them consistent and verified.

Fixture bundles (``dwarf/corpora/bundle-runs``) are real recorded runs, so their logs name the
machine they ran on. This replaces those host details with neutral values, then repairs every
sha256 inside the fixtures that named a changed file and re-signs each attestation with the same
DWARF development key the fixtures were attested with. The attested run id and time are kept.

    python3 dwarf/scripts/scrub_bundle_fixtures.py [--root DIR] [--host NAME]... [--subnet PREFIX]... [--check]

``--check`` changes nothing and exits 1 when any host detail remains (usable as a pre-push gate).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

try:
    from scripts import runtime_bundle_attestation as attestation
    from scripts import runtime_bundle_sign as bundle_sign
except ImportError:  # pragma: no cover - run from the scripts directory
    import runtime_bundle_attestation as attestation  # type: ignore
    import runtime_bundle_sign as bundle_sign  # type: ignore

DEFAULT_ROOT = SCRIPT_DIR.parent / "corpora" / "bundle-runs"

# Home directories of any user other than dwarf become /home/dwarf. Host names and private
# subnets are not written here (that would publish them): pass them with --host / --subnet or
# DWARF_SCRUB_HOSTS / DWARF_SCRUB_SUBNETS (comma separated). Subnets become the documentation-only
# range 192.0.2.0/24 (RFC 5737).
_HOME = re.compile(r"/home/(?!dwarf(?:/|\b))[a-z_][a-z0-9_-]*")
_MAC_HOME = re.compile(r"/Users/(?!Shared(?:/|\b))[A-Za-z0-9._-]+")
# Any RFC 1918 address (10/8, 172.16/12, 192.168/16); the last octet is kept so peers stay distinct.
_PRIVATE_IP = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.(\d{1,3})(?!\.?\d)")
# The key bytes of a cardano-cli signing-key envelope (type ...SigningKey...), in either field order.
# The type may carry any character but a quote: KES keys are "KesSigningKey_ed25519_kes_2^6".
_SIGNING_KEY = re.compile(
    r'("type"\s*:\s*"[^"]*SigningKey[^"]*"[^{}]*?"cborHex"\s*:\s*")[0-9a-fA-F]+(")')
_SIGNING_KEY_REVERSED = re.compile(
    r'("cborHex"\s*:\s*")[0-9a-fA-F]+("[^{}]*?"type"\s*:\s*"[^"]*SigningKey[^"]*")')
NEUTRAL_HOST = "dwarf-host"
NEUTRAL_SUBNET = "192.0.2."
REDACTED_KEY = "redacted-by-dwarf"


def _replacements(hosts=(), subnets=(), *, private_ips=False, signing_keys=False) -> list[tuple[re.Pattern, str]]:
    hosts = [h for h in [*hosts, *os.environ.get("DWARF_SCRUB_HOSTS", "").split(",")] if h.strip()]
    subnets = [n for n in [*subnets, *os.environ.get("DWARF_SCRUB_SUBNETS", "").split(",")] if n.strip()]
    rules = [(_HOME, "/home/dwarf"), (_MAC_HOME, "/home/dwarf")]
    rules += [(re.compile(re.escape(h.strip())), NEUTRAL_HOST) for h in hosts]
    rules += [(re.compile(re.escape(n.strip().rstrip(".") + ".")), NEUTRAL_SUBNET) for n in subnets]
    if private_ips:
        rules.append((_PRIVATE_IP, NEUTRAL_SUBNET + r"\1"))
    if signing_keys:
        rules += [(_SIGNING_KEY, r"\g<1>" + REDACTED_KEY + r"\g<2>"),
                  (_SIGNING_KEY_REVERSED, r"\g<1>" + REDACTED_KEY + r"\g<2>")]
    return rules


_SHA = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_ATTESTATION = Path("outputs") / "attestation" / "attestation.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def remaining_host_details(root: Path, *, hosts=(), subnets=(), private_ips=False, signing_keys=False) -> list[str]:
    rules = _replacements(hosts, subnets, private_ips=private_ips, signing_keys=signing_keys)
    hits = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(pattern.search(text) for pattern, _ in rules):
                hits.append(str(path.relative_to(root)))
    return hits


def sign_attestation(path: Path, *, target_run_id: str, attested_at_utc: str, statement: dict) -> dict:
    """Write an attestation signed with the development key, in runtime_bundle_attestation's layout."""
    private_key, key_source, operator_warning = bundle_sign._load_signing_private_key(None)
    _, serialization, _, _ = bundle_sign._load_crypto()
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    signable = attestation._signable_payload(
        target_run_id=target_run_id, attested_at_utc=attested_at_utc, statement=statement
    )
    payload = {
        **signable,
        "signature": {
            "algorithm": "ed25519",
            "signing_actor": "dwarf",
            "key_source": key_source,
            "operator_warning": operator_warning,
            "signing_unavailable": False,
            "public_key_hex": public_key_bytes.hex(),
            "signing_key_fingerprint": _sha(public_key_bytes),
            "signature_hex": private_key.sign(attestation._canonical_json_bytes(signable)).hex(),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["verification"] = attestation.verify_attestation(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _resign(run_dir: Path) -> bool:
    path = run_dir / _ATTESTATION
    if not path.is_file():
        return False
    body = json.loads(path.read_text(encoding="utf-8"))
    signature = body.get("signature") or {}
    if signature.get("signing_unavailable") or signature.get("key_source") != "development-embedded-key":
        raise RuntimeError(f"{path}: only development-key attestations can be re-signed here")
    statement = dict(body.get("statement") or {})
    scenario = run_dir / str(statement.get("scenario_path") or "scenario.yaml")
    if scenario.is_file():
        statement["scenario_yaml_sha256"] = _sha(scenario.read_bytes())
    profile_id, profile_sha = attestation._parse_profile(run_dir)
    if statement.get("active_profile_sha256") is not None or profile_sha is not None:
        statement["active_profile_sha256"] = profile_sha
    before = path.read_bytes()
    sign_attestation(path, target_run_id=body["target_run_id"], attested_at_utc=body["attested_at_utc"],
                     statement=statement)
    return path.read_bytes() != before


def scrub_tree(root: Path = DEFAULT_ROOT, *, hosts=(), subnets=(), private_ips=False, signing_keys=False) -> dict:
    root = Path(root)
    rules = _replacements(hosts, subnets, private_ips=private_ips, signing_keys=signing_keys)
    files = _text_files(root)
    original = {path: path.read_bytes() for path in files}
    changed: set[Path] = set()
    for path in files:
        text = original[path].decode("utf-8")
        for pattern, new in rules:
            text = pattern.sub(new, text)
        if text.encode("utf-8") != original[path]:
            path.write_bytes(text.encode("utf-8"))
            changed.add(path)
    # Repair digests that named a changed file; repeat, since a repaired file changes its own digest.
    for _ in range(10):
        digest_map = {}
        for path in changed:
            old_digest, new_digest = _sha(original[path]), _sha(path.read_bytes())
            if old_digest != new_digest:
                digest_map[old_digest] = new_digest
        if not digest_map:
            break
        newly = set()
        for path in files:
            text = path.read_text(encoding="utf-8")
            repaired = _SHA.sub(lambda match: digest_map.get(match.group(0), match.group(0)), text)
            if repaired != text:
                path.write_text(repaired, encoding="utf-8")
                if path not in changed:
                    newly.add(path)
        if not newly:
            break
        changed |= newly
    resigned = 0
    for run_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if _resign(run_dir):
            resigned += 1
            changed.add(run_dir / _ATTESTATION)
    return {"files_changed": len(changed), "attestations_resigned": resigned,
            "remaining": remaining_host_details(root, hosts=hosts, subnets=subnets,
                                                private_ips=private_ips, signing_keys=signing_keys)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--host", action="append", default=[], help="host name to neutralise (repeatable)")
    parser.add_argument("--subnet", action="append", default=[], help="subnet prefix to neutralise, e.g. 10.1.2 (repeatable)")
    parser.add_argument("--private-ips", action="store_true", help="mask every RFC 1918 address (last octet kept)")
    parser.add_argument("--signing-keys", action="store_true", help="redact the key bytes of signing-key envelopes")
    parser.add_argument("--check", action="store_true", help="report remaining host details, change nothing")
    args = parser.parse_args(argv)
    extra = {"private_ips": args.private_ips, "signing_keys": args.signing_keys}
    if args.check:
        remaining = remaining_host_details(args.root, hosts=args.host, subnets=args.subnet, **extra)
        print(json.dumps({"remaining": remaining}, indent=2))
        return 1 if remaining else 0
    report = scrub_tree(args.root, hosts=args.host, subnets=args.subnet, **extra)
    print(json.dumps(report, indent=2))
    return 1 if report["remaining"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
