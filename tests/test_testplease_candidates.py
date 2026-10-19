"""Source intake is a pure identification boundary, never a build boundary."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import os

import pytest

from profile_manager import testplease_candidates as intake


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def write(root: Path, name: str, content: str = "source\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def upstream(root: Path, implementation: str) -> Path:
    if implementation == "amaru":
        write(root, "Cargo.toml", '[workspace]\nmembers = ["crates/amaru"]\n')
        write(root, "crates/amaru/Cargo.toml", '[package]\nname = "amaru"\nversion = "0.1.0"\n')
        write(root, "crates/amaru/src/main.rs", "fn main() {}\n")
    else:
        write(root, "cabal.project", "packages: cardano-node\n")
        write(root, "cardano-node/cardano-node.cabal", "name: cardano-node\nversion: 0.1.0\n")
        write(root, "cardano-node/src/Cardano/Node.hs", "module Cardano.Node where\n")
    return root


def candidate(repo: Path, implementation: str = "amaru", name: str = "experiment") -> Path:
    return upstream(repo / "dwarf/testplease" / implementation / name, implementation)


def scan(repo: Path):
    return intake.discover_candidates(repo, repository_commit=COMMIT)


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_detects_uncompiled_upstream_without_client_configuration(tmp_path, implementation):
    source = candidate(tmp_path, implementation)
    records = scan(tmp_path)
    assert isinstance(records, tuple)
    assert len(records) == 1
    record = records[0]
    assert record.implementation == implementation
    assert record.candidate_name == "experiment"
    assert record.repository_commit == COMMIT
    assert record.source_root == source.resolve()
    assert len(record.source_tree_digest) == 64
    assert all(char in "0123456789abcdef" for char in record.source_tree_digest)
    with pytest.raises(FrozenInstanceError):
        record.candidate_name = "changed"


def test_scans_both_implementations_in_stable_name_order(tmp_path):
    candidate(tmp_path, "cardano-node", "second")
    candidate(tmp_path, "amaru", "z-last")
    candidate(tmp_path, "amaru", "a-first")
    assert [(item.implementation, item.candidate_name) for item in scan(tmp_path)] == [
        ("amaru", "a-first"), ("amaru", "z-last"), ("cardano-node", "second")
    ]


def test_empty_intake_and_repository_placeholders_are_not_candidates(tmp_path):
    assert scan(tmp_path) == ()
    write(tmp_path, "dwarf/testplease/README.md")
    write(tmp_path, "dwarf/testplease/amaru/.gitkeep", "")
    write(tmp_path, "dwarf/testplease/cardano-node/.gitkeep", "")
    assert scan(tmp_path) == ()


@pytest.mark.parametrize("commit", ["", "HEAD", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "a" * 64])
def test_requires_full_trusted_repository_commit(tmp_path, commit):
    with pytest.raises(intake.CandidateIntakeError, match="repository commit"):
        intake.discover_candidates(tmp_path, repository_commit=commit)


@pytest.mark.parametrize("name", ["bad name", "-option", ".hidden", "bad;command", "bad\nname", "éxperiment", "a" * 129])
def test_rejects_unsafe_candidate_names(tmp_path, name):
    candidate(tmp_path, name=name)
    with pytest.raises(intake.CandidateIntakeError, match="unsafe candidate name"):
        scan(tmp_path)


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_rejects_missing_source_markers_with_precise_layout_error(tmp_path, implementation):
    source = candidate(tmp_path, implementation)
    marker = "crates/amaru/src/main.rs" if implementation == "amaru" else "cardano-node/src/Cardano/Node.hs"
    (source / marker).unlink()
    with pytest.raises(intake.CandidateIntakeError, match=rf"unsupported layout.*{implementation}.*experiment"):
        scan(tmp_path)


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_manifests_alone_are_not_recognizable_source(tmp_path, implementation):
    root = tmp_path / "dwarf/testplease" / implementation / "empty"
    write(root, "Cargo.toml" if implementation == "amaru" else "cabal.project")
    with pytest.raises(intake.CandidateIntakeError, match="unsupported layout"):
        scan(tmp_path)


def test_generic_rust_workspace_is_not_amaru(tmp_path):
    source = candidate(tmp_path)
    (source / "crates/amaru/Cargo.toml").write_text('[package]\nname = "unrelated"\n')
    with pytest.raises(intake.CandidateIntakeError, match="unsupported layout"):
        scan(tmp_path)


def test_malformed_manifest_is_an_unsupported_layout(tmp_path):
    source = candidate(tmp_path)
    (source / "Cargo.toml").write_text("invalid TOML [\n")
    with pytest.raises(intake.CandidateIntakeError, match="unsupported layout"):
        scan(tmp_path)


def test_parent_directory_selects_implementation(tmp_path):
    upstream(tmp_path / "dwarf/testplease/amaru/wrong-parent", "cardano-node")
    with pytest.raises(intake.CandidateIntakeError, match="unsupported layout.*amaru"):
        scan(tmp_path)


def test_rejects_ambiguous_root(tmp_path):
    upstream(candidate(tmp_path), "cardano-node")
    with pytest.raises(intake.CandidateIntakeError, match="ambiguous layout"):
        scan(tmp_path)


@pytest.mark.parametrize("nested_implementation", ["amaru", "cardano-node"])
def test_rejects_nested_candidate_roots(tmp_path, nested_implementation):
    source = candidate(tmp_path)
    upstream(source / "vendor/second", nested_implementation)
    with pytest.raises(intake.CandidateIntakeError, match="nested candidate root"):
        scan(tmp_path)


def test_rejects_wrapper_directory_around_upstream(tmp_path):
    source = tmp_path / "dwarf/testplease/amaru/wrapped"
    upstream(source / "amaru", "amaru")
    with pytest.raises(intake.CandidateIntakeError, match="nested candidate root"):
        scan(tmp_path)


@pytest.mark.parametrize("level", ["intake", "implementation", "candidate"])
def test_rejects_symlinked_intake_directories(tmp_path, level):
    outside = upstream(tmp_path / "outside", "amaru")
    base = tmp_path / "dwarf/testplease"
    link = {"intake": base, "implementation": base / "amaru", "candidate": base / "amaru/alias"}[level]
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(intake.CandidateIntakeError, match="symlink"):
        scan(tmp_path)


@pytest.mark.parametrize("target_kind", ["file", "directory", "missing", "chain"])
def test_rejects_escaping_symlinks_before_reading_targets(tmp_path, monkeypatch, target_kind):
    source = candidate(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = write(outside, "secret")
    if target_kind == "file":
        target = secret
    elif target_kind == "directory":
        target = outside
    elif target_kind == "chain":
        (source / "inner").symlink_to(secret)
        target = source / "inner"
    else:
        target = outside / "missing"
    (source / "escape").symlink_to(target)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.is_relative_to(source), "intake opened an escaping target"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(intake.CandidateIntakeError, match="symlink.*escapes"):
        scan(tmp_path)


def test_accepts_internal_links_without_recursing_directory_aliases(tmp_path):
    source = candidate(tmp_path)
    (source / "main-link").symlink_to("crates/amaru/src/main.rs")
    (source / "directory-link").symlink_to("crates", target_is_directory=True)
    first = scan(tmp_path)[0].source_tree_digest
    (source / "main-link").unlink()
    (source / "main-link").symlink_to("./crates/amaru/src/main.rs")
    assert scan(tmp_path)[0].source_tree_digest != first


@pytest.mark.parametrize("target", ["missing", "loop"])
def test_rejects_dangling_and_cyclic_internal_links(tmp_path, target):
    source = candidate(tmp_path)
    (source / "loop").symlink_to(target)
    with pytest.raises(intake.CandidateIntakeError, match="symlink"):
        scan(tmp_path)


def test_rejects_special_files_without_opening_them(tmp_path):
    source = candidate(tmp_path)
    os.mkfifo(source / "pipe")
    with pytest.raises(intake.CandidateIntakeError, match="unsupported file type"):
        scan(tmp_path)


def test_digest_is_location_order_and_timestamp_independent(tmp_path):
    first = candidate(tmp_path / "one")
    second = candidate(tmp_path / "two")
    write(first, "z-extra")
    write(first, "a-extra")
    write(second, "a-extra")
    write(second, "z-extra")
    os.utime(second / "a-extra", (1, 1))
    assert scan(tmp_path / "one")[0].source_tree_digest == scan(tmp_path / "two")[0].source_tree_digest


@pytest.mark.parametrize("change", ["content", "rename", "ignored", "hidden", "executable", "empty-dir", "delete", "git-metadata"])
def test_digest_covers_complete_tree(tmp_path, change):
    source = candidate(tmp_path)
    extra = write(source, "extra")
    before = scan(tmp_path)[0]
    if change == "content":
        extra.write_text("changed")
    elif change == "rename":
        extra.rename(source / "renamed")
    elif change == "ignored":
        write(source, ".gitignore", "ignored\n")
        write(source, "ignored")
    elif change == "hidden":
        write(source, ".hidden")
    elif change == "executable":
        extra.chmod(0o755)
    elif change == "empty-dir":
        (source / "empty").mkdir()
    elif change == "git-metadata":
        write(source, ".git/HEAD", "ref: refs/heads/local\n")
    else:
        extra.unlink()
    after = scan(tmp_path)[0]
    assert after.source_tree_digest != before.source_tree_digest
    assert before.repository_commit == after.repository_commit == COMMIT


def test_build_support_is_hashed_but_never_executed(tmp_path):
    source = candidate(tmp_path)
    sentinel = tmp_path / "executed"
    dangerous = f"touch {sentinel}\n"
    for name in ["scripts/build.sh", "crates/amaru/build.rs", "Makefile", ".github/workflows/ci.yml", "candidate.yaml"]:
        write(source, name, dangerous)
    write(source, ".cargo/config.toml", '[build]\nrustc-wrapper = "scripts/build.sh"\n')
    assert len(scan(tmp_path)) == 1
    assert not sentinel.exists()


def test_rejects_stray_client_files_outside_candidate_source(tmp_path):
    candidate(tmp_path)
    write(tmp_path, "dwarf/testplease/amaru/build.sh", "exit 1\n")
    with pytest.raises(intake.CandidateIntakeError, match="expected candidate directory"):
        scan(tmp_path)


def test_rejects_link_with_regular_file_as_path_component(tmp_path):
    source = candidate(tmp_path)
    write(source, "not-a-directory")
    (source / "broken").symlink_to("not-a-directory/../crates")
    with pytest.raises(intake.CandidateIntakeError, match="symlink"):
        scan(tmp_path)


def test_rejects_escape_that_returns_through_external_link(tmp_path):
    source = candidate(tmp_path)
    outside = tmp_path / "external"
    outside.symlink_to(source, target_is_directory=True)
    (source / "escape").symlink_to(os.path.relpath(outside / "crates", source))
    with pytest.raises(intake.CandidateIntakeError, match="symlink.*escapes"):
        scan(tmp_path)


@pytest.mark.parametrize("name", ["candidate.yaml", "build.sh", ".github/workflows/ci.yml", "unknown/thing"])
def test_rejects_client_configuration_outside_intake_layout(tmp_path, name):
    candidate(tmp_path)
    write(tmp_path, "dwarf/testplease/" + name)
    with pytest.raises(intake.CandidateIntakeError, match="unsupported intake entry"):
        scan(tmp_path)


@pytest.mark.parametrize("suffix", ["/", "/."])
@pytest.mark.parametrize("absolute", [False, True])
def test_rejects_link_requiring_regular_file_to_be_directory(tmp_path, suffix, absolute):
    source = candidate(tmp_path)
    target = "crates/amaru/src/main.rs" + suffix
    if absolute:
        target = str(source) + "/" + target
    link = source / "broken"
    link.symlink_to(target)
    with pytest.raises(NotADirectoryError):
        link.stat()
    with pytest.raises(intake.CandidateIntakeError, match="symlink"):
        scan(tmp_path)


@pytest.mark.parametrize("suffix", ["/", "/."])
@pytest.mark.parametrize("absolute", [False, True])
def test_accepts_link_requiring_real_directory(tmp_path, suffix, absolute):
    source = candidate(tmp_path)
    target = "crates/amaru/src" + suffix
    if absolute:
        target = str(source) + "/" + target
    (source / "directory-link").symlink_to(target)
    assert len(scan(tmp_path)) == 1
