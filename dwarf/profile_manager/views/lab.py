"""Execution chooser and independent Antithesis draft wizard."""
from profile_manager.data.antithesis_run_wizard import antithesis_draft_catalog
from profile_manager.data.operate_config import operate_config_payload
from profile_manager.templating import render


def render_lab() -> str:
    return render("operate/lab.j2", page_title="Enter the lab", density="reading",
                  layout="wide", active="operate", active_sub="run", ui="basic")


def render_antithesis_run_wizard(*, token: str | None = None) -> str:
    return render("operate/antithesis_run_wizard.j2", page_title="Antithesis run",
                  density="reading", layout="wide", active="operate", active_sub="run",
                  ui="basic", catalog=antithesis_draft_catalog(), token=token or "",
                  moog_setup=operate_config_payload()["moog_setup"])
