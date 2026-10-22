from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pages.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_pages_remains_independent_of_campaign_execution():
    data = workflow()
    triggers = data[True]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert "if" not in data["jobs"]["deploy"]
    assert data["permissions"] == {"contents": "read", "actions": "read", "pages": "write", "id-token": "write"}


def test_pages_downloads_only_catalog_bound_release_assets_then_stages_same_origin():
    steps = {step.get("name"): step for step in workflow()["jobs"]["deploy"]["steps"]}
    download = steps["Download verified rolling campaign defaults"]["run"]
    assert "dwarf-campaign-results" in download
    assert "ci_stage_pages_campaigns.py list-assets" in download
    assert "gh release download" in download
    assemble = steps["Assemble static site"]["run"]
    assert "ci_stage_pages_campaigns.py stage" in assemble
    assert "--trusted-publishers docs/evidence/trusted-publishers.json" in assemble
    assert "_site/docs/evidence" in assemble
    assert "docs/antithesis-import.js" in assemble


def test_pages_shell_steps_parse():
    for step in workflow()["jobs"]["deploy"]["steps"]:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True, capture_output=True)
