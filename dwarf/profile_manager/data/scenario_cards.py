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
    r"\s*[—-]\s*(amaru|cardano-node|cardano node)\s*$"   # trailing "— Amaru"
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
        # Keep only a subtitle that explains something; short fragments such as
        # "Reference responder" stay in the full title (tooltip / detail page).
        sub = (sub[0].upper() + sub[1:]) if len(sub) >= 40 else ""

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


def _humanize(stem: str) -> str:
    words = [w for w in re.split(r"[-_]", stem) if w]
    acr = {"cbor": "CBOR", "n2n": "N2N", "kes": "KES", "plutus": "Plutus", "amaru": "Amaru", "cardano": "Cardano",
           "opcert": "OpCert", "vm": "VM", "v2": "v2", "v3": "v3", "zb": "zb", "za": "za"}
    text = " ".join(acr.get(w, w) for w in words)
    return text[:1].upper() + text[1:]


def run_card(scenario_id: str | None, run_id: str, cards_by_id: dict) -> dict:
    """Card facts for a run/bundle: title by what ran, implementation, runtime and version pills.

    Uses the scenario's short card title when the scenario is in the catalog;
    otherwise names profile deploys, teardowns and client examples plainly.
    """
    sid = scenario_id or ""
    card = cards_by_id.get(sid) if sid else None
    if card:
        return {"title": card["title"], "full_title": card["full_title"], "implementation": card["implementation"],
                "runtime": card["runtime"], "versions": card["versions"]}
    stem = sid or (run_id.split("-20")[0] if "-20" in run_id else run_id)
    probe = f"{run_id} {stem}".lower()

    # Ids compress versions ("1112" = cardano-node 11.1.2, "20260918" = Amaru 10.11.20260918)
    # and repeat implementation words; lift both into pills.
    versions = []
    kept = []
    for token in stem.split("-"):
        if re.fullmatch(r"1\d{3}", token):
            versions.append(f"node {token[:2]}.{token[2]}.{token[3]}")
        elif re.fullmatch(r"20\d{6}", token):
            versions.append(f"amaru 10.11.{token}")
        elif re.fullmatch(r"\d+\.\d+\.\d+", token):
            versions.append(_version_tag(token))
        elif token.lower() not in ("mixed", "amaru", "cardano", "haskell", "node"):
            kept.append(token)
    versions = list(dict.fromkeys(versions))

    if stem == "remove":
        title = "Teardown · remove deployed profile"
    elif stem.startswith("profile-"):
        code = stem.split("-")[1] if stem.count("-") >= 1 else ""
        rest = "-".join(t for t in kept[2:]) if len(kept) > 2 else ""
        title = f"Profile {code} deploy" + (f" · {_humanize(rest)}" if rest else "")
    elif stem.startswith("client-example-"):
        title = "Client example · " + _humanize("-".join(kept[2:]))
    else:
        title = _humanize("-".join(kept)) or _humanize(stem)
    impl = "mixed" if "mixed" in probe else ("amaru" if "amaru" in probe else ("cardano-node" if ("cardano" in probe or "haskell" in probe) else ""))
    return {"title": title, "full_title": sid or run_id, "implementation": impl, "runtime": "", "versions": versions}
