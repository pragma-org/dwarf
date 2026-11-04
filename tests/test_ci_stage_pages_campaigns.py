import hashlib
import json
from pathlib import Path

import pytest

from scripts import ci_stage_pages_campaigns as api

ROOT = Path(__file__).resolve().parents[1]


def test_committed_default_archives_stage_with_validated_empty_campaign_catalog(tmp_path):
    output = tmp_path / "evidence"
    api.stage(committed=ROOT / "docs/evidence",
        defaults_path=ROOT / "docs/evidence/default-runs.json",
        catalog_path=ROOT / "docs/evidence/campaigns.json",
        downloads=tmp_path / "downloads", output=output,
        trusted_publishers_path=ROOT / "docs/evidence/trusted-publishers.json")
    assert json.loads((output / "default-runs.json").read_text())["archives"]
    assert (output / "campaigns.json").is_file()
    assert len(list((output / "runs").glob("*.tar.gz"))) == 5   # four coverage-rule workload reruns + the Antithesis snapshot


def test_rolling_defaults_retain_committed_antithesis_archive(tmp_path):
    committed = ROOT / "docs/evidence"
    defaults = json.loads((committed / "default-runs.json").read_text())
    defaults["archives"] = defaults["archives"][:1]
    rolling = tmp_path / "rolling.json"
    rolling.write_text(json.dumps(defaults))
    output = tmp_path / "output"
    api.stage(committed=committed, defaults_path=rolling,
        catalog_path=committed / "campaigns.json", downloads=tmp_path / "downloads",
        output=output, trusted_publishers_path=committed / "trusted-publishers.json")
    rows = json.loads((output / "default-runs.json").read_text())["archives"]
    assert len(rows) == 2
    assert rows[-1]["format"] == "dwarf-antithesis-snapshots"
    assert (output / rows[-1]["url"]).read_bytes() == (committed / rows[-1]["url"]).read_bytes()


def test_release_asset_listing_is_bounded_and_path_safe():
    row = {"url": "runs/run-1-" + "a" * 64 + ".tar.gz", "size_bytes": 10,
           "sha256": "b" * 64, "run_id": "run-1", "implementation": "amaru",
           "version": "v1", "release_tag": "campaign-" + "c" * 64,
           "asset_name": "run-1-" + "a" * 64 + ".tar.gz"}
    assert api.release_assets({"schema_version": api._SCHEMA, "archives": [row]}) == [
        ("campaign-" + "c" * 64, row["asset_name"])]
    changed = dict(row, asset_name="../escape.tar.gz")
    with pytest.raises(ValueError, match="binding"):
        api.release_assets({"schema_version": api._SCHEMA, "archives": [changed]})


def test_stage_rejects_release_default_missing_from_trusted_catalog(tmp_path):
    source = ROOT / "docs/evidence/runs/20261104T161325Z-5eb100bf-bundle-export.tar.gz"
    tag = "campaign-" + "c" * 64
    asset = "run-1-" + hashlib.sha256(source.read_bytes()).hexdigest() + ".tar.gz"
    downloads = tmp_path / "downloads" / tag
    downloads.mkdir(parents=True)
    (downloads / asset).write_bytes(source.read_bytes())
    defaults = {"schema_version": api._SCHEMA, "archives": [{"url": "runs/" + asset,
        "size_bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "run_id": "run-1", "implementation": "amaru", "version": "v1",
        "release_tag": tag, "asset_name": asset}]}
    defaults_path = tmp_path / "defaults.json"
    defaults_path.write_text(json.dumps(defaults))
    with pytest.raises(ValueError, match="trusted campaign catalog"):
        api.stage(committed=ROOT / "docs/evidence", defaults_path=defaults_path,
            catalog_path=ROOT / "docs/evidence/campaigns.json", downloads=tmp_path / "downloads",
            output=tmp_path / "output",
            trusted_publishers_path=ROOT / "docs/evidence/trusted-publishers.json")


def test_pages_caps_allow_full_workload_bundles_and_match_viewer():
    assert api._MAX_ARCHIVE_BYTES == 95 * 1024 * 1024
    assert api._MAX_TOTAL_BYTES == 950 * 1024 * 1024
    assert api._MAX_ARCHIVES == 10
    viewer = (ROOT / "docs/portable-defaults.js").read_text(encoding="utf-8")
    assert "var MAX_ARCHIVE_BYTES = 95 * 1024 * 1024;" in viewer
    assert "var MAX_TOTAL_BYTES = 950 * 1024 * 1024;" in viewer
    assert "var MAX_ARCHIVES = 10;" in viewer


def test_pages_rejects_archive_above_github_file_limit():
    row = {"url": "runs/run-1-" + "a" * 64 + ".tar.gz", "size_bytes": 95 * 1024 * 1024 + 1,
           "sha256": "b" * 64, "run_id": "run-1", "implementation": "amaru", "version": "v1"}
    with pytest.raises(ValueError, match="archive size"):
        api.validate_defaults({"schema_version": api._SCHEMA, "archives": [row]})
