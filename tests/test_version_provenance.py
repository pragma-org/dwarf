"""Version-provenance gate: a label must agree with the commit/binary it names.

Regression anchor: an Amaru binary reporting git_commit ea1f34e4 (upstream tag
v10.11.20260903) was published as "10.11.20260918" (whose commit is aedfe797).
Every layer here must reject that claim and accept the correct 20260903 label.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from profile_manager.version_catalog import CatalogError, validate_version_catalog
from profile_manager.version_provenance import (
    ProvenanceError,
    ProvenanceIndex,
    build_provenance,
    check_id_labels,
    check_image_refs,
    check_prose,
    check_structured,
    observed_identity_matches,
    parse_binary_identity,
    require_label_matches,
    revisions_agree,
    scan_run_identities,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "dwarf" / "versions" / "catalog.json"
sys.path.insert(0, str(REPO_ROOT / "dwarf" / "scripts"))

EA1F = "ea1f34e42c7a1806d8ee60b3f512e58daae7ccc1"  # tag v10.11.20260903
AEDF = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"  # tag v10.11.20260918


@pytest.fixture(scope="module")
def index():
    return ProvenanceIndex.load(CATALOG_PATH)


# -- the committed map itself ---------------------------------------------------

def test_catalog_maps_the_two_tags_to_their_true_commits(index):
    assert index.expected_revision("amaru", "10.11.20260903") == EA1F
    assert index.expected_revision("amaru", "v10.11.20260918") == AEDF


# -- the historical mislabel -----------------------------------------------------

def test_historical_mislabel_fails_and_correct_label_passes(index):
    status, message = index.check_pair("amaru", "10.11.20260918", "ea1f34e4")
    assert status == "mismatch"
    assert "10.11.20260903" in message  # names what the commit really is
    with pytest.raises(ProvenanceError):
        require_label_matches(index, "amaru", "10.11.20260918", "ea1f34e4")
    assert index.check_pair("amaru", "10.11.20260903", "ea1f34e4")[0] == "ok"
    assert require_label_matches(index, "amaru", "v10.11.20260903", EA1F)["version"] == "10.11.20260903"


@pytest.mark.parametrize(
    "text",
    [
        "**Update — FIXED in amaru 10.11.20260918 (ea1f34e4): the divergence is gone.**",
        "all cases AGREE (amaru 0918 ea1f34e4 conformant with cardano-node 11.1.2)",
        "| amaru 0918 (ea1f34e4) |",
        "> **amaru 10.11.20260918** (commit `ea1f34e4`), which now matches",
        "commit ea1f34e4 = v10.11.20260918",
    ],
)
def test_prose_mislabel_forms_fail(index, text):
    problems = check_prose(index, text)
    assert problems, text
    assert "ea1f34e4" in problems[0][1]


@pytest.mark.parametrize(
    "text",
    [
        "**Update — FIXED in amaru 10.11.20260903 (ea1f34e4): the divergence is gone.**",
        "all cases AGREE (amaru 0903 ea1f34e4 conformant with cardano-node 11.1.2)",
        "commit ea1f34e4 = v10.11.20260903",
        "| amaru 807 (493bffba) |",
        # a patched build reports the crate version, not a tag: never judged
        "Amaru 10.11.0 (aedfe797a+dirty)",
        # a nearby non-revision hex string is not a build commit
        "amaru 0918 spent UTxO 9708b921 on the relay",
    ],
)
def test_prose_correct_or_unrelated_forms_pass(index, text):
    assert check_prose(index, text) == []


def test_structured_pair_mislabel_fails(index):
    bad = {"target": {"implementation": "amaru", "version": "10.11.20260918", "source_revision": EA1F}}
    good = {"target": {"implementation": "amaru", "version": "10.11.20260903", "source_revision": EA1F}}
    assert check_structured(index, bad)
    assert check_structured(index, good) == []


def test_id_token_must_name_the_target(index):
    document = {
        "id": "opcert-header-cases-amaru-20260918",
        "target": {"implementation": "amaru", "version": "10.11.20260903", "source_revision": EA1F},
    }
    assert check_id_labels(index, document)
    document["id"] = "opcert-header-cases-amaru-20260903"
    assert check_id_labels(index, document) == []


# -- binary identity (truth source) ------------------------------------------------

AMARU_BUILD_LOG = (
    '{"timestamp":"2026-09-24T12:42:26.925048Z","level":"INFO","fields":{"arch":"x86_64",'
    f'"git_commit":"{EA1F}","git_dirty":false,"message":"build.version","os":"linux",'
    '"version":"10.11.0"},"target":"amaru::setup"}'
)


def test_parse_binary_identity_forms():
    assert parse_binary_identity("Amaru 10.11.20260903 (ea1f34e)") == {
        "implementation": "amaru", "version": "10.11.20260903", "git_commit": "ea1f34e", "dirty": False,
    }
    assert parse_binary_identity("Amaru 10.11.0 (aedfe797a+dirty)")["dirty"] is True
    log = parse_binary_identity(AMARU_BUILD_LOG)
    assert log["git_commit"] == EA1F and log["version"] == "10.11.0"
    node = parse_binary_identity(
        "cardano-node 10.7.1 - linux-x86_64 - ghc-9.6\ngit rev 045bc187a36ef0cbd236db902b85dd8f202fb059"
    )
    assert node["implementation"] == "cardano-node" and node["version"] == "10.7.1"
    assert parse_binary_identity("Amaru 10.11.0") is None


def test_binary_reporting_ea1f_rejects_0918_claim_and_accepts_0903(index):
    assert observed_identity_matches(index, "amaru", "10.11.20260918", AMARU_BUILD_LOG)[0] == "mismatch"
    status, record = observed_identity_matches(index, "amaru", "10.11.20260903", AMARU_BUILD_LOG)
    assert status == "verified" and record["resolved_tag"] == "10.11.20260903"
    assert observed_identity_matches(index, "amaru", "10.11.20260903", "Amaru 10.11.0")[0] == "unobserved"


def test_identity_matches_prefers_commit_equality_over_version_substring():
    from qualify_node_versions import identity_matches

    image = "sha256:" + "1" * 64
    # the version text "contains" the claim but the binary is a different commit
    assert identity_matches(
        expected_version="10.11.20260918",
        reported_version="Amaru 10.11.20260918 (ea1f34e)",
        expected_image_id=image,
        running_image_id=image,
        expected_source_revision=AEDF,
    ) is False
    assert identity_matches(
        expected_version="10.11.20260903",
        reported_version="Amaru 10.11.20260903 (ea1f34e)",
        expected_image_id=image,
        running_image_id=image,
        expected_source_revision=EA1F,
    ) is True


# -- manifest stamp ------------------------------------------------------------------

def test_manifest_provenance_block(index, tmp_path):
    log_dir = tmp_path / "outputs" / "harness"
    log_dir.mkdir(parents=True)
    helper = AMARU_BUILD_LOG.replace("10.11.0", "10.11.0")
    node = AMARU_BUILD_LOG.replace(EA1F, AEDF).replace('"git_dirty":false', '"git_dirty":true')
    (log_dir / "consumer.log").write_text(f"[bootstrap-producer] {helper}\n{node}\n")
    seen = [{**item, "status": "seen"} for item in scan_run_identities(tmp_path)]
    assert {item["git_commit"] for item in seen} == {EA1F, AEDF}

    target = {"implementation": "amaru", "version": "10.11.20260918", "source_revision": AEDF}
    block = build_provenance(target, seen, index=index)
    assert block["status"] == "verified"
    assert block["resolved_tag"] == "10.11.20260918" and block["catalog_revision"] == AEDF

    mislabeled = {"implementation": "amaru", "version": "10.11.20260918", "source_revision": EA1F}
    block = build_provenance(mislabeled, [], index=index)
    assert block["status"] == "mismatch" and block["problems"]

    assert build_provenance(
        {"implementation": "amaru", "version": "10.11.20260903", "source_revision": EA1F}, [], index=index
    )["status"] == "declared"


def test_run_handle_fails_closed_on_binary_mismatch(tmp_path):
    from profile_manager.forensic import RunHandle

    handle = RunHandle.__new__(RunHandle)
    handle._identity_observations = []
    with pytest.raises(ProvenanceError):
        handle.record_binary_identity(
            implementation="amaru", claimed_label="10.11.20260918", reported_text=AMARU_BUILD_LOG
        )
    record = handle.record_binary_identity(
        implementation="amaru", claimed_label="10.11.20260903", reported_text=AMARU_BUILD_LOG
    )
    assert record["status"] == "verified"


# -- catalog + images -------------------------------------------------------------------

def test_derived_image_binary_commit_must_match_its_release():
    catalog = json.loads(CATALOG_PATH.read_text())
    validate_version_catalog(catalog)
    broken = copy.deepcopy(catalog)
    release = next(r for r in broken["releases"] if r["version"] == "10.11.20260918")
    derived = next(a for a in release["artifacts"] if a["kind"] == "oci-derived")
    derived["binary_git_commit"] = EA1F
    with pytest.raises(CatalogError, match="binary_git_commit"):
        validate_version_catalog(broken)


def test_image_tag_contradicting_digest_fails(index):
    digest = "sha256:df7eda777b075545d312255053697a1735c50b93115fa6887dd01dfe2240d1ee"  # v10.11.20260903
    bad, _ = check_image_refs(index, f"  image: ghcr.io/pragma-org/amaru:v10.11.20260918@{digest}\n")
    good, _ = check_image_refs(index, f"  image: ghcr.io/pragma-org/amaru:v10.11.20260903@{digest}\n")
    assert bad and not good
    _, warnings = check_image_refs(index, "  image: ghcr.io/example/amaru@sha256:" + "0" * 64 + "\n")
    assert warnings


# -- the CI gate end to end ---------------------------------------------------------------

def _gate_tree(tmp_path, text):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    doc = tmp_path / "dwarf" / "docs" / "finding.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def test_gate_fails_on_historical_mislabel_and_passes_on_correct_label(tmp_path):
    from validate_scenarios import check_version_provenance

    bad = _gate_tree(tmp_path / "bad", "Result: Amaru 10.11.20260918 (`ea1f34e4`) is CONFORMANT.\n")
    fails, _warns, messages, _ = check_version_provenance(bad, CATALOG_PATH)
    assert fails == 1 and "dwarf/docs/finding.md:1" in messages[0]

    good = _gate_tree(tmp_path / "good", "Result: Amaru 10.11.20260903 (`ea1f34e4`) is CONFORMANT.\n")
    assert check_version_provenance(good, CATALOG_PATH)[0] == 0


def test_gate_is_clean_on_this_repository():
    from validate_scenarios import check_version_provenance

    fails, _warns, messages, _ = check_version_provenance()
    assert fails == 0, messages


def test_revisions_agree_requires_seven_hex():
    assert revisions_agree("ea1f34e", EA1F)
    assert not revisions_agree("ea1f3", EA1F)
    assert not revisions_agree("ea1f34e", AEDF)
