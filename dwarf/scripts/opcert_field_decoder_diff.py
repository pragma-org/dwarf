"""Cross-decoder differential for opcert-field CBOR mutations (opcert family #6).

Family #6 mutates the operational-certificate structure's CBOR at the FIELD level
(negative/oversized counter, type confusion, truncated fixed-width bytes, missing
/ duplicate / extra opcert fields) and asks whether cardano-node and Amaru agree
on decode accept/reject. This is a DECODER-LEVEL differential, not a live soak:
the by-hash live-soak correlation cannot observe malformed/hash-changing opcert
CBOR (a decode error carries no header_hash; a value change breaks the hash), so
the observable, correct home for these mutations is feeding the SAME bytes to
both implementations' header decoders and diffing the result.

Pipeline (all deterministic, no devnet):
1. Start from a valid base Praos block-header CBOR (both decoders accept it).
2. For each named mutation, the forger (``mutate-opcert``) rewrites ONLY the
   opcert sub-structure, preserving every other header byte.
3. Feed the mutated bytes to BOTH header decoders (cardano: the forger's
   ``decode-praos-header``; amaru: ``amaru-cbor-decode-block-header``), each of
   which prints ``OK`` (exit 0) or ``ERR ...`` (exit 1).
4. A mutation where one decoder accepts and the other rejects is a decode-leniency
   DIVERGENCE (a finding); both-accept or both-reject is agreement.

This module's decision layer (classify_pair / build_report) is pure and
fake-testable; the subprocess invocation is separate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

# The opcert-field mutations the forger's mutate-opcert mode knows (kept in sync
# with oPCERT_FIELD_MUTATIONS in DwarfOpcertAdversary.hs).
MUTATIONS = (
    "counter-negative",
    "counter-bignum-oversized",
    "counter-type-text",
    "counter-type-bytes",
    "kesperiod-type-text",
    "kesperiod-negative",
    "hot-vkey-truncated",
    "cold-sig-truncated",
    "opcert-missing-field",
    "opcert-duplicate-field",
    "opcert-extra-field",
)


# --------------------------------------------------------------------------- #
# Pure decision layer (fake-testable, no subprocess).
# --------------------------------------------------------------------------- #

def normalize_outcome(exit_code, stdout):
    """Reduce a decoder's (exit_code, stdout) to ``ok`` | ``error`` | ``crash``.

    Contract shared by the standalone decode binaries: exit 0 + stdout starting
    ``OK`` is a clean decode; exit 1 + stdout starting ``ERR`` is a clean decode
    error; anything else (other exit, signal, unexpected stdout) is a crash.
    """
    text = (stdout or "").strip()
    if exit_code == 0 and text.startswith("OK"):
        return "ok"
    if exit_code == 1 and text.startswith("ERR"):
        return "error"
    return "crash"


def classify_pair(amaru_outcome, cardano_outcome):
    """Classify one mutation's cross-decoder result.

    ``agree`` when both decoders reach the same clean outcome (both ok or both
    error); ``divergence`` when one accepts and the other rejects; ``crash`` when
    either decoder crashed (fail-closed: never scored as agreement).
    """
    if amaru_outcome == "crash" or cardano_outcome == "crash":
        return "crash"
    if amaru_outcome == cardano_outcome:
        return "agree"
    return "divergence"


def build_report(rows):
    """Assemble the differential report from per-mutation ``rows``.

    Each row: ``{mutation, amaru: {outcome,...}, cardano: {outcome,...}}``.
    Returns counts, the per-mutation table, and the divergence list (the finding).
    """
    table = []
    divergences = []
    crashes = []
    for row in rows:
        a = row["amaru"]["outcome"]
        c = row["cardano"]["outcome"]
        verdict = classify_pair(a, c)
        entry = {"mutation": row["mutation"], "amaru": a, "cardano": c, "result": verdict}
        table.append(entry)
        if verdict == "divergence":
            divergences.append(entry)
        elif verdict == "crash":
            crashes.append(entry)
    return {
        "schema_version": "v1",
        "mutations": len(rows),
        "agree": sum(1 for e in table if e["result"] == "agree"),
        "divergences": len(divergences),
        "crashes": len(crashes),
        "table": table,
        "divergence_detail": divergences,
        "crash_detail": crashes,
    }


# --------------------------------------------------------------------------- #
# Subprocess invocation (the real cross-decoder run).
# --------------------------------------------------------------------------- #

def run_decoder(cmd, data: bytes):
    """Run a decode binary command on ``data`` via stdin; return outcome record."""
    try:
        proc = subprocess.run(cmd, input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"outcome": "crash", "exit": None, "stdout": "", "error": str(exc)}
    stdout = proc.stdout.decode("utf-8", "replace")
    return {"outcome": normalize_outcome(proc.returncode, stdout),
            "exit": proc.returncode, "stdout": stdout.strip()[:200]}


def generate_vector(forger_bin, base_bytes: bytes, mutation: str) -> bytes:
    """Produce the mutated header bytes for ``mutation`` via the forger."""
    proc = subprocess.run([forger_bin, "mutate-opcert", mutation],
                          input=base_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"mutate-opcert {mutation} failed: {proc.stderr.decode('utf-8','replace')}")
    return proc.stdout


def run_differential(*, base_path, forger_bin, amaru_bin, cardano_cmd,
                     mutations=MUTATIONS, corpus_dir=None):
    """Generate the corpus from ``base_path`` and diff both decoders over it."""
    base_bytes = Path(base_path).read_bytes()
    rows = []
    for mutation in mutations:
        vec = generate_vector(forger_bin, base_bytes, mutation)
        if corpus_dir is not None:
            Path(corpus_dir).mkdir(parents=True, exist_ok=True)
            (Path(corpus_dir) / f"{mutation}.cbor").write_bytes(vec)
        amaru = run_decoder([amaru_bin], vec)
        cardano = run_decoder(list(cardano_cmd), vec)
        rows.append({"mutation": mutation, "amaru": amaru, "cardano": cardano})
    report = build_report(rows)
    report["base_header"] = str(base_path)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Opcert-field CBOR cross-decoder differential (family #6).")
    p.add_argument("--base", required=True, help="valid base Praos block-header CBOR")
    p.add_argument("--forger-bin", required=True, help="dwarf-opcert-adversary-latest binary (mutate-opcert + decode-praos-header)")
    p.add_argument("--amaru-bin", required=True, help="amaru-cbor-decode-block-header binary")
    p.add_argument("--report", help="write the JSON report here")
    p.add_argument("--corpus-dir", help="write the generated mutated vectors here")
    args = p.parse_args(argv)
    report = run_differential(
        base_path=args.base, forger_bin=args.forger_bin, amaru_bin=args.amaru_bin,
        cardano_cmd=[args.forger_bin, "decode-praos-header"], corpus_dir=args.corpus_dir)
    text = json.dumps(report, indent=2)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["crashes"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
