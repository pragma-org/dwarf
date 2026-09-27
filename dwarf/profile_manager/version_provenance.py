"""Version provenance: a version label must agree with the binary it names.

The version catalog (``versions/catalog.json``) is the committed map of
release tag -> source revision -> OCI digest.  This module is the single place
that answers "does this label really name this commit?", for three callers:

* the CI gate (``scripts/validate_scenarios.py``), which scans structured
  ``{version, source_revision}`` pairs, prose ``label (sha)`` pairs, scenario
  and profile ids, and compose image references;
* runtime identity checks, which compare the git commit a binary *reports*
  (``amaru --version``, Amaru's ``build.version`` log line, ``cardano-node
  --version``) with the catalog revision for the claimed label;
* the forensic run manifest, which stamps the resolved provenance so a result
  can never silently carry a wrong label.

Historical failure this exists for: an Amaru build whose binary reported
``git_commit=ea1f34e4`` (tag ``v10.11.20260903``) was described as
``10.11.20260918`` in findings and corpora.  Nothing compared the label to the
binary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .version_catalog import DEFAULT_CATALOG_PATH, load_version_catalog


HEX_REVISION = re.compile(r"^[0-9a-f]{7,40}$")
MIN_REVISION_PREFIX = 7

# ``Amaru 10.11.20260903 (ea1f34e)`` / ``Amaru 10.11.0 (aedfe797a+dirty)``
_AMARU_VERSION_LINE = re.compile(
    r"\bAmaru\s+(?P<version>v?[0-9][0-9A-Za-z.\-]*)\s+\((?P<commit>[0-9a-f]{7,40})(?P<dirty>\+dirty)?\)"
)
# Amaru structured startup log: {"fields":{..."git_commit":"<sha>","git_dirty":false,
# "message":"build.version",...,"version":"10.11.0"}}  (also key=value text form)
_AMARU_LOG_COMMIT = re.compile(r"git_commit\"?\s*[:=]\s*\"?(?P<commit>[0-9a-f]{7,40})")
_AMARU_LOG_DIRTY = re.compile(r"git_dirty\"?\s*[:=]\s*\"?(?P<dirty>true|false)")
_AMARU_LOG_VERSION = re.compile(r"\"version\"\s*:\s*\"(?P<version>[^\"]+)\"|\bversion=(?P<kv>\S+)")
# ``cardano-node 11.1.2 - linux-x86_64 - ghc-9.6`` + ``git rev <sha>``
_CARDANO_VERSION_LINE = re.compile(r"\bcardano-node\s+(?P<version>[0-9][0-9A-Za-z.\-]*)")
_CARDANO_GIT_REV = re.compile(r"\bgit rev\s+(?P<commit>[0-9a-f]{7,40})")


class ProvenanceError(ValueError):
    """A version label disagrees with the revision or binary it names."""


def _norm_version(implementation: str, version: str) -> str:
    text = str(version).strip()
    if implementation == "amaru" and text.startswith("v") and text[1:2].isdigit():
        return text[1:]
    return text


def revisions_agree(a: str | None, b: str | None) -> bool:
    """Two git revisions name the same commit (prefix match, >= 7 hex)."""
    left = str(a or "").strip().lower()
    right = str(b or "").strip().lower()
    if len(left) < MIN_REVISION_PREFIX or len(right) < MIN_REVISION_PREFIX:
        return False
    if not (HEX_REVISION.match(left[:40]) and HEX_REVISION.match(right[:40])):
        return False
    return left.startswith(right) or right.startswith(left)


# ---------------------------------------------------------------------------
# Catalog index
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceIndex:
    """Label/commit/digest lookups over a loaded version catalog."""

    catalog: dict[str, Any]
    by_label: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    by_digest: dict[str, tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=dict)
    exempt_digests: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_catalog(cls, catalog: dict[str, Any]) -> "ProvenanceIndex":
        index = cls(catalog=catalog)
        for release in catalog.get("releases", []):
            implementation = release.get("implementation")
            version = _norm_version(implementation, release.get("version", ""))
            index.by_label[(implementation, version)] = release
            for artifact in release.get("artifacts", []) or []:
                digest = artifact.get("digest")
                if isinstance(digest, str) and digest:
                    index.by_digest.setdefault(digest, (release, artifact))
        for exemption in catalog.get("identity_exemptions", []) or []:
            index.exempt_digests[exemption["digest"]] = exemption
        return index

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG_PATH) -> "ProvenanceIndex":
        return cls.from_catalog(load_version_catalog(path))

    # -- label resolution ------------------------------------------------

    def resolve_label(self, implementation: str | None, label: str) -> dict[str, Any] | None:
        """Resolve a full or short version label to one catalog release.

        Accepts ``10.11.20260903``, ``v10.11.20260903`` and, for Amaru, the
        date-only short forms ``20260903`` / ``0903`` / ``903`` used in prose.
        Returns None when the label is unknown or ambiguous.
        """
        text = str(label or "").strip()
        if not text:
            return None
        implementations = [implementation] if implementation else ["amaru", "cardano-node"]
        hits: list[dict[str, Any]] = []
        for impl in implementations:
            release = self.by_label.get((impl, _norm_version(impl, text)))
            if release is not None:
                hits.append(release)
        if not hits and (implementation in (None, "amaru")) and text.isdigit() and len(text) in (3, 4, 8):
            suffix = text.zfill(4) if len(text) < 8 else text
            hits = [
                release
                for (impl, version), release in self.by_label.items()
                if impl == "amaru" and re.fullmatch(r"\d+\.\d+\.(\d{8})", version)
                and version.rsplit(".", 1)[1].endswith(suffix)
            ]
        return hits[0] if len(hits) == 1 else None

    def release_for_revision(self, implementation: str | None, revision: str) -> list[dict[str, Any]]:
        """Every catalog release whose source revision matches ``revision``."""
        return [
            release
            for (impl, _version), release in self.by_label.items()
            if (implementation is None or impl == implementation)
            and revisions_agree(release.get("source_revision"), revision)
        ]

    def expected_revision(self, implementation: str, label: str) -> str | None:
        release = self.resolve_label(implementation, label)
        return release.get("source_revision") if release else None

    # -- checks ------------------------------------------------------------

    def check_pair(
        self, implementation: str | None, label: str, revision: str
    ) -> tuple[str, str]:
        """Return (status, message); status is ``ok``, ``mismatch`` or ``unknown``.

        ``mismatch`` means the label resolves to a catalog release whose source
        revision is NOT ``revision`` - the historical mislabel class.
        """
        release = self.resolve_label(implementation, label)
        if release is None:
            return "unknown", f"label {label!r} is not in the version catalog"
        expected = release.get("source_revision", "")
        if revisions_agree(expected, revision):
            return "ok", f"{release['implementation']} {release['version']} = {expected[:12]}"
        actual = self.release_for_revision(release["implementation"], revision)
        actual_text = (
            " (that commit is " + ", ".join(sorted(r["version"] for r in actual)) + ")"
            if actual
            else ""
        )
        return "mismatch", (
            f"label {label!r} names {release['implementation']} {release['version']} "
            f"= {expected[:12]}, but the revision given is {revision[:12]}{actual_text}"
        )


def require_label_matches(
    index: ProvenanceIndex, implementation: str | None, label: str, revision: str
) -> dict[str, Any]:
    """Fail closed unless ``label`` names ``revision``; return the release."""
    status, message = index.check_pair(implementation, label, revision)
    if status != "ok":
        raise ProvenanceError(message)
    release = index.resolve_label(implementation, label)
    assert release is not None
    return release


# ---------------------------------------------------------------------------
# Binary identity parsing (the truth source)
# ---------------------------------------------------------------------------


def parse_binary_identity(text: str) -> dict[str, Any] | None:
    """Extract ``{implementation, version, git_commit, dirty}`` from binary output.

    Understands ``amaru --version``, Amaru's ``build.version`` startup log line
    and ``cardano-node --version``.  Returns None if no commit is reported.
    For a log with several Amaru processes the LAST ``build.version`` line wins
    (the node process runs after any bootstrap helper).
    """
    body = str(text or "")
    match = _CARDANO_GIT_REV.search(body)
    if match:
        version = _CARDANO_VERSION_LINE.search(body)
        return {
            "implementation": "cardano-node",
            "version": version.group("version") if version else None,
            "git_commit": match.group("commit"),
            "dirty": False,
        }
    matches = list(_AMARU_VERSION_LINE.finditer(body))
    if matches:
        last = matches[-1]
        return {
            "implementation": "amaru",
            "version": last.group("version"),
            "git_commit": last.group("commit"),
            "dirty": bool(last.group("dirty")),
        }
    build_lines = [line for line in body.splitlines() if "build.version" in line and "git_commit" in line]
    if build_lines:
        line = build_lines[-1]
        commit = _AMARU_LOG_COMMIT.search(line)
        dirty = _AMARU_LOG_DIRTY.search(line)
        version = _AMARU_LOG_VERSION.search(line)
        return {
            "implementation": "amaru",
            "version": (version.group("version") or version.group("kv")) if version else None,
            "git_commit": commit.group("commit") if commit else None,
            "dirty": bool(dirty and dirty.group("dirty") == "true"),
        }
    return None


def iter_identity_lines(text: str) -> Iterator[dict[str, Any]]:
    """Yield every binary identity a text reports, one per matching line."""
    for line in str(text or "").splitlines():
        if "git rev" in line or "Amaru" in line or ("build.version" in line and "git_commit" in line):
            identity = parse_binary_identity(line)
            if identity and identity.get("git_commit"):
                yield identity


RUN_LOG_SUFFIXES = {".log", ".txt", ".ndjson"}
RUN_LOG_MAX_BYTES = 64_000_000


def scan_run_identities(run_dir: str | Path) -> list[dict[str, Any]]:
    """Distinct binary identities reported in a run's retained outputs/logs.

    Each item: ``{implementation, git_commit, version, dirty, files}``.  A run
    may legitimately contain several (e.g. an Amaru bootstrap helper and the
    node under test); the caller decides which one the target claim names.
    """
    root = Path(run_dir)
    found: dict[tuple[str, str, bool], dict[str, Any]] = {}
    for directory in (root / "outputs", root):
        if not directory.is_dir():
            continue
        paths = directory.rglob("*") if directory.name == "outputs" else directory.glob("*")
        for path in paths:
            if not path.is_file() or path.suffix not in RUN_LOG_SUFFIXES:
                continue
            try:
                if path.stat().st_size > RUN_LOG_MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for identity in iter_identity_lines(text):
                key = (identity["implementation"], identity["git_commit"], bool(identity.get("dirty")))
                entry = found.setdefault(key, {**identity, "files": []})
                rel = path.relative_to(root).as_posix()
                if rel not in entry["files"]:
                    entry["files"].append(rel)
    return sorted(found.values(), key=lambda item: (item["implementation"], item["git_commit"]))


def observed_identity_matches(
    index: ProvenanceIndex,
    implementation: str,
    claimed_label: str,
    reported_text: str,
) -> tuple[str, dict[str, Any]]:
    """Compare a claimed label with what the binary itself reports.

    Returns (status, record) with status ``verified`` (commit equality),
    ``mismatch``, ``unobserved`` (binary reported no commit) or ``unknown``
    (label not in catalog).
    """
    observed = parse_binary_identity(reported_text)
    release = index.resolve_label(implementation, claimed_label)
    record: dict[str, Any] = {
        "implementation": implementation,
        "claimed_label": claimed_label,
        "resolved_tag": release.get("version") if release else None,
        "catalog_revision": release.get("source_revision") if release else None,
        "observed_git_commit": observed.get("git_commit") if observed else None,
        "observed_version": observed.get("version") if observed else None,
        "observed_dirty": observed.get("dirty") if observed else None,
    }
    if release is None:
        return "unknown", record
    if not observed or not observed.get("git_commit"):
        return "unobserved", record
    if revisions_agree(release.get("source_revision"), observed["git_commit"]):
        return "verified", record
    actual = index.release_for_revision(implementation, observed["git_commit"])
    record["observed_release"] = sorted(r["version"] for r in actual)
    return "mismatch", record


def build_provenance(
    target: dict[str, Any] | None,
    observations: Iterable[dict[str, Any]] = (),
    *,
    index: ProvenanceIndex | None = None,
) -> dict[str, Any]:
    """Build the manifest ``provenance`` block for a run.

    ``target`` is the scenario target (``implementation``, ``version``,
    ``source_revision``).  ``observations`` are records produced by
    :func:`observed_identity_matches` (plus optional ``image_digest``/``component``).
    Overall status: ``mismatch`` if any claim disagrees, else ``verified`` if a
    binary commit was observed for the target, else ``declared`` (label and
    declared revision agree in the catalog but no binary was observed), else
    ``unobserved``/``unknown``.  Observations with status ``seen`` come from
    :func:`scan_run_identities`; the claim is verified when the claimed commit
    is among them (other commits, e.g. bootstrap helpers, are listed only).
    """
    target = dict(target or {})
    try:
        index = index or ProvenanceIndex.load()
    except Exception as exc:  # noqa: BLE001 - provenance must never crash a run
        return {"schema_version": "v1", "status": "unknown", "error": f"catalog unavailable: {exc}"}
    implementation = target.get("implementation")
    label = str(target.get("version") or "")
    declared = str(target.get("source_revision") or "")
    release = index.resolve_label(implementation, label) if label else None
    block: dict[str, Any] = {
        "schema_version": "v1",
        "implementation": implementation,
        "claimed_label": label or None,
        "resolved_tag": release.get("version") if release else None,
        "catalog_revision": release.get("source_revision") if release else None,
        "declared_source_revision": declared or None,
        "observations": [dict(item) for item in observations],
    }
    problems: list[str] = []
    if release and declared and not revisions_agree(release.get("source_revision"), declared):
        problems.append(
            f"declared source_revision {declared[:12]} is not {release['version']} "
            f"({release['source_revision'][:12]})"
        )
    statuses = [item.get("status") for item in block["observations"]]
    seen = [item for item in block["observations"] if item.get("status") == "seen"]
    if release and seen:
        for item in seen:
            item["matches_target"] = bool(
                item.get("implementation") == release.get("implementation")
                and revisions_agree(release.get("source_revision"), item.get("git_commit"))
            )
            matched = index.release_for_revision(item.get("implementation"), item.get("git_commit", ""))
            item["catalog_releases"] = sorted(r["version"] for r in matched)
        if any(item["matches_target"] for item in seen):
            statuses.append("verified")
    problems.extend(
        f"{item.get('component') or item.get('implementation')}: claimed "
        f"{item.get('claimed_label')} but binary reports {item.get('observed_git_commit')}"
        for item in block["observations"]
        if item.get("status") == "mismatch"
    )
    if problems:
        status = "mismatch"
    elif "verified" in statuses:
        status = "verified"
    elif release and declared:
        status = "declared"
    elif release:
        status = "unobserved"
    else:
        status = "unknown"
    block["status"] = status
    if problems:
        block["problems"] = problems
    return block


# ---------------------------------------------------------------------------
# Repository scanning (used by the CI gate)
# ---------------------------------------------------------------------------

_SHA_TOKEN = r"(?<![0-9a-z:/@_.-])([0-9a-f]{7,40})(?![0-9a-z_])"
_AMARU_LABEL = r"(v?\d+\.\d+\.\d{8}|20\d{6}|\d{3,4})"
_CARDANO_LABEL = r"(\d+\.\d+\.\d+(?:-pre)?)"
_GAP = r"[^\n]{0,24}?"
# label then sha: "amaru 10.11.20260903 (ea1f34e4)", "Amaru 0903 ea1f34e4", "amaru 807 (493bffba)"
_PROSE_PATTERNS = (
    ("amaru", re.compile(r"(?i:\bamaru\b)[^\n]{0,12}?(?<![\w.])" + _AMARU_LABEL + r"(?![\w.])" + _GAP + _SHA_TOKEN)),
    ("cardano-node", re.compile(r"(?i:\bcardano-node\b)[^\n]{0,4}?(?<![\w.])" + _CARDANO_LABEL + r"(?![\w.])" + _GAP + _SHA_TOKEN)),
    # sha then label: "ea1f34e4 = v10.11.20260903", "`ea1f34e4` (amaru 0903)"
    ("amaru", re.compile(_SHA_TOKEN + r"`?\s*(?:=|is|\(|,)\s*(?i:amaru\s+)?(?:tag\s+)?(v?\d+\.\d+\.\d{8})(?![\w.])")),
)
_ID_DATE_TOKEN = re.compile(r"\bamaru-(20\d{6})\b")


def _is_revision_like(token: str) -> bool:
    return bool(re.search(r"[a-f]", token)) and bool(re.search(r"\d", token))


def iter_prose_pairs(text: str) -> Iterator[tuple[int, str, str, str]]:
    """Yield (line, implementation, label, sha) pairs found in free text."""
    for implementation, pattern in _PROSE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            if pattern.pattern.startswith(_SHA_TOKEN):
                sha, label = groups[0], groups[1]
            else:
                label, sha = groups[0], groups[1]
            if not _is_revision_like(sha):
                continue
            line = text.count("\n", 0, match.start()) + 1
            yield line, implementation, label, sha


def check_prose(index: ProvenanceIndex, text: str) -> list[tuple[int, str]]:
    """Return (line, message) for every prose label/sha pair that disagrees.

    Only pairs whose sha is a catalog commit of the same implementation are
    judged: an arbitrary nearby hex string (tx id, UTxO, digest fragment) is
    never mistaken for a build revision.
    """
    problems: list[tuple[int, str]] = []
    seen: set[tuple[int, str, str]] = set()
    for line, implementation, label, sha in iter_prose_pairs(text):
        key = (line, label, sha)
        if key in seen:
            continue
        seen.add(key)
        if not index.release_for_revision(implementation, sha):
            continue
        status, message = index.check_pair(implementation, label, sha)
        if status == "mismatch":
            problems.append((line, message))
    return problems


def iter_structured_pairs(value: Any, path: str = "") -> Iterator[tuple[str, str | None, str, str]]:
    """Yield (json_path, implementation, version, source_revision) from a document."""
    if isinstance(value, dict):
        version = value.get("version")
        revision = value.get("source_revision")
        if isinstance(version, str) and isinstance(revision, str) and version and revision:
            implementation = value.get("implementation")
            yield path or "$", implementation if isinstance(implementation, str) else None, version, revision
        for key, child in value.items():
            yield from iter_structured_pairs(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for position, child in enumerate(value):
            yield from iter_structured_pairs(child, f"{path}[{position}]")


def check_structured(index: ProvenanceIndex, document: Any) -> list[str]:
    problems: list[str] = []
    for path, implementation, version, revision in iter_structured_pairs(document):
        status, message = index.check_pair(implementation, version, revision)
        if status == "mismatch":
            problems.append(f"{path}: {message}")
    return problems


def check_id_labels(index: ProvenanceIndex, document: Any) -> list[str]:
    """An ``amaru-YYYYMMDD`` token in a scenario/profile id must name the target."""
    if not isinstance(document, dict):
        return []
    target = document.get("target") if isinstance(document.get("target"), dict) else {}
    problems: list[str] = []
    for key in ("id", "profile"):
        ident = document.get(key)
        if not isinstance(ident, str):
            continue
        for token in _ID_DATE_TOKEN.findall(ident):
            release = index.resolve_label("amaru", token)
            if release is None:
                continue
            if target.get("implementation") == "amaru" and target.get("version"):
                claimed = index.resolve_label("amaru", str(target["version"]))
                if claimed is not None and claimed is not release:
                    problems.append(
                        f"{key} {ident!r} says amaru {release['version']} but target.version "
                        f"is {claimed['version']}"
                    )
            revision = target.get("source_revision") if target.get("implementation") == "amaru" else None
            if revision and not revisions_agree(release.get("source_revision"), revision):
                problems.append(
                    f"{key} {ident!r} says amaru {release['version']} "
                    f"({release['source_revision'][:12]}) but target.source_revision is {str(revision)[:12]}"
                )
    return problems


_IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?image:\s*[\"']?(?P<ref>[^\s\"'#]+)", re.MULTILINE)
_FROM_LINE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(?P<ref>\S+)", re.MULTILINE)
_NODE_IMAGE = re.compile(r"(amaru|cardano-node)(?![\w-]*(?:tracer|submit|antithesis))", re.IGNORECASE)


def check_image_refs(index: ProvenanceIndex, text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Check compose/Dockerfile node image references against the catalog.

    Returns (failures, warnings).  A pinned digest that the catalog knows must
    not carry a tag naming a different release (fail).  A node image pinned by
    digest that the catalog does not know has no recorded binary identity
    (warning: register it as a catalog artifact with ``binary_git_commit``).
    """
    failures: list[tuple[int, str]] = []
    warnings: list[tuple[int, str]] = []
    for pattern in (_IMAGE_LINE, _FROM_LINE):
        for match in pattern.finditer(text):
            reference = match.group("ref")
            if not _NODE_IMAGE.search(reference.split("@", 1)[0].rsplit("/", 1)[-1]):
                continue
            line = text.count("\n", 0, match.start()) + 1
            name, _, digest = reference.partition("@")
            tag = name.rsplit(":", 1)[1] if ":" in name.rsplit("/", 1)[-1] else ""
            if not digest:
                continue
            hit = index.by_digest.get(digest)
            if hit is None and digest in index.exempt_digests:
                continue
            if hit is None:
                warnings.append((line, f"{reference}: node image digest is not in the version catalog (no recorded binary identity)"))
                continue
            release, _artifact = hit
            tagged = index.resolve_label(release["implementation"], tag) if tag else None
            if tag and tagged is not None and tagged is not release:
                failures.append((line, f"{reference}: tag says {tagged['version']} but the digest is {release['implementation']} {release['version']}"))
    return failures, warnings


_SUBMIT_API_IMAGE = re.compile(r"cardano-submit-api", re.IGNORECASE)


def _default_cardano_node_versions(index) -> list[str]:
    """Cardano-node versions the catalog marks default in any verification profile."""
    defaults: list[str] = []
    for release in index.catalog.get("releases", []):
        if release.get("implementation") != "cardano-node":
            continue
        verification = release.get("verification") or {}
        if any(isinstance(p, dict) and p.get("default") for p in verification.values()):
            version = release.get("version")
            if version:
                defaults.append(version)
    return defaults


def check_submit_api_refs(index, text):
    """cardano-submit-api image pins must match the cardano-node release under test.

    The submit-api ships its own CBOR decoder.  A submit-api pinned to a
    different release than the node decodes submitted tx bytes with a different
    codec, which silently changes decode-layer differential verdicts -- found
    2026-09-27: submit-api 10.7.1 forwarding to an 11.1.2 node accepted 65-byte
    metadata the node's codec rejects, masking cases as INCONCLUSIVE.  The
    submit-api is excluded from ``_NODE_IMAGE`` binary provenance (it is not a
    binary-under-test), so its version is pinned-checked here instead.  Keyed on
    ``image:`` YAML lines only, so prose that merely *describes* a bad version
    does not trip the gate.
    """
    failures: list[tuple[int, str]] = []
    warnings: list[tuple[int, str]] = []
    defaults = set(_default_cardano_node_versions(index))
    for match in _IMAGE_LINE.finditer(text):
        reference = match.group("ref")
        base = reference.split("@", 1)[0]
        if not _SUBMIT_API_IMAGE.search(base.rsplit("/", 1)[-1]):
            continue
        line = text.count("\n", 0, match.start()) + 1
        tag = base.rsplit(":", 1)[1] if ":" in base.rsplit("/", 1)[-1] else ""
        if not tag:
            warnings.append((line, f"{reference}: cardano-submit-api pinned without a version tag"))
            continue
        known = index.resolve_label("cardano-node", tag)
        if known is None:
            failures.append((line, f"{reference}: cardano-submit-api tag {tag} is not a known cardano-node release (decoder-skew risk); pin it to the cardano-node version under test"))
        elif defaults and known.get("version") not in defaults:
            failures.append((line, f"{reference}: cardano-submit-api {tag} != default cardano-node release {sorted(defaults)} under test (decode-layer skew)"))
    return failures, warnings
