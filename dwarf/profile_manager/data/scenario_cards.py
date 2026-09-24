"""Basic-view scenario cards: short titles and pills derived from catalog metadata.

Presentation only. The full title, id and definition are unchanged and remain
available (tooltip, search, detail page). Rules are deliberately conservative:
they strip repeated boilerplate and move facts that are shown as pills
(implementation, runtime, versions, client-card number) out of the title.
"""
from __future__ import annotations

import re

# Amaru releases carry a date component (10.11.20260918); cardano-node does not.
_VERSION = re.compile(r"\b\d+\.\d+\.\d{8}\b|\b\d{1,2}\.\d{1,2}\.\d{1,2}(?:\.\d+)?\b")

# (pattern, replacement) applied in order; case-insensitive.
_REWRITES = [
    (r"^compose a substrate scenario that\s+", ""),
    (r"^compose a substrate topology stress scenario with\s+", "Topology stress: "),
    (r"^compose a host-mode substrate and exercise\s+(the\s+|a\s+|an\s+)?", ""),
    (r"^compose an?\s+", ""),
    (r"^declarative runtime primitive for\s+", "Primitive: "),
    (r"^client example (\d+)\s*[—-]\s*", r"Card \1 · "),
    (r"^library-tier cbor fuzz of\s+", "CBOR fuzz · "),
    (r"^structured-cbor fuzz of\s+", "Structured CBOR fuzz · "),
    (r"^random-byte fuzz of\s+", "Random-byte fuzz · "),
    (r"^phase 3\s+", ""),
    (r"\s*\(conway shape\)", " (Conway)"),
    (r"\s*[—-]\s*patched target\b", ""),
    (r"\s+capability demo\b", ""),
    # Leading implementation word (the pill states it); "patched" is a kicker flag.
    (r"^(haskell\s+)?(amaru|cardano-node)\s+(patched\s+)?(?=\w)", ""),
]

# Implementation words that the implementation pill already states.
_IMPL_WORDS = re.compile(
    r"\s*[—-]\s*(amaru|cardano-node)\s*$"          # trailing "— Amaru"
    r"|\b(the\s+)?haskell\s+cardano-node\s+"       # "Haskell cardano-node "
    r"|(?<=· )(amaru|cardano-node)\s+"              # "CBOR fuzz · amaru block parser"
    , re.IGNORECASE)

# Phrases that only carried a version and are now empty.
_VERSION_PHRASES = re.compile(
    r"\s*[—-]\s*mixed with cardano-node\s*$|\s+across\s+and\s*$|\s+(across|on|with)\s*$|\s*[—-]\s*$",
    re.IGNORECASE)


def _version_tag(version: str) -> str:
    parts = version.split(".")
    return ("amaru " if len(parts) >= 3 and len(parts[2]) >= 8 else "node ") + version


def scenario_card(entry: dict) -> dict:
    title = (entry.get("title") or entry.get("id") or "").strip()
    scenario_id = entry.get("id") or ""
    runtime = (entry.get("runtime") or "").strip()
    impl = (entry.get("target_impl") or "").strip()
    lowered = f"{scenario_id} {title}".lower()

    flags = []
    if re.match(r"(?i)^phase 3\b", title):
        flags.append("phase 3")
    if re.search(r"(?i)[—-]\s*patched target\b|^(amaru|cardano-node)\s+patched\b", title):
        flags.append("patched")
    if re.search(r"(?i)\bhost-mode\b", title):
        flags.append("host-mode")
    if re.search(r"(?i)capability demo", title):
        flags.append("demo")

    versions = []
    for match in _VERSION.findall(title):
        if match not in versions:
            versions.append(match)

    short = _VERSION.sub("", title)
    for pattern, repl in _REWRITES:
        short = re.sub(pattern, repl, short, flags=re.IGNORECASE)
    for _ in range(3):
        short = _VERSION_PHRASES.sub("", short).strip()
        short = _IMPL_WORDS.sub("", short).strip()
    short = re.sub(r"\s{2,}", " ", short).strip(" ,·—-")
    if not short:
        short = title
    short = short[0].upper() + short[1:]
    short = re.sub(r"(Card \d+ · )(\w)", lambda m: m.group(1) + m.group(2).upper(), short)
    # Long titles: headline before the first " — ", the rest as a subtitle.
    sub = ""
    if len(short) > 60 and " — " in short:
        short, sub = (part.strip() for part in short.split(" — ", 1))
        sub = sub[0].upper() + sub[1:] if sub else ""

    implementation = "mixed" if "mixed" in lowered else (impl if impl and impl != "unknown" else "")
    return {
        "title": short,
        "subtitle": sub,
        "full_title": title,
        "implementation": implementation,
        "runtime": runtime,
        "versions": [_version_tag(v) for v in versions],
        "flags": flags,
    }
