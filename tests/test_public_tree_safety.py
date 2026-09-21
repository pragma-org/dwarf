from pathlib import Path
import importlib.util
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_public_tree", ROOT / "tools" / "audit_public_tree.py"
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_public_tree_has_no_generated_or_private_tracked_content():
    report = AUDIT.audit(ROOT)
    assert report["forbidden_paths"] == []
    assert report["private_text"] == []
    assert report["secrets"] == []
    assert len(report["intentional_private_text"]) == 9
    assert {
        item["reason"] for item in report["intentional_private_text"]
    } == {
        "accepted frozen measurement evidence identity",
        "public delivery test rejects machine-specific paths",
    }


def test_forbidden_path_policy_covers_runtime_and_generated_outputs():
    cases = {
        "dwarf/state/chain-head.json": "runtime",
        "dwarf/runs/example/manifest.json": "runtime",
        "dwarf/bundles/example.tar.gz": "runtime",
        "reports/example/run.log": "generated",
        "output/result.zip": "archive",
        "reports/example/logs/raw.json": "generated",
        "notes/tasks/private.md": "generated",
        "reports/example/raw.json.gz": "archive",
        "cache/__pycache__/module.pyc": "generated",
        "docs/._README.md": "operating-system",
    }
    for path, expected in cases.items():
        assert expected in AUDIT.forbidden_path_reason(path)


def test_source_and_documentation_paths_are_allowed():
    assert AUDIT.forbidden_path_reason("dwarf/scenarios/example.yaml") is None
    assert AUDIT.forbidden_path_reason("docs/measurements.md") is None


def test_runtime_output_roots_are_ignored():
    samples = [
        "dwarf/state/example.json",
        "dwarf/runs/example/manifest.json",
        "dwarf/bundles/example.tar.gz",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT,
        input="\n".join(samples) + "\n",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == samples


def test_secret_signatures_are_rejected_without_embedding_real_secrets():
    samples = {
        "private key": "-----BEGIN " + "PRIVATE KEY-----",
        "GitHub token": "github_" + "pat_" + "a" * 24,
        "AWS access key": "AK" + "IA" + "A" * 16,
        "credential URL": "https://operator:" + "not-a-real-password@example.invalid/repo",
    }
    for expected, sample in samples.items():
        findings = AUDIT.secret_findings("fixture.txt", sample)
        assert any(expected in item["reason"] for item in findings)


def test_tracked_tree_has_no_forbidden_publisher_references():
    forbidden_name = "gain" + "palfam"
    result = subprocess.run(
        ["git", "grep", "-ni", forbidden_name],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1, result.stdout


def test_independent_scanner_rejects_forbidden_publisher_name():
    forbidden_name = "gain" + "palfam"
    findings, _ = AUDIT.scan_text(ROOT, [])
    assert findings == []
    assert any(
        item["reason"] == "forbidden publisher name"
        for item in AUDIT.private_findings("fixture.txt", forbidden_name)
    )
