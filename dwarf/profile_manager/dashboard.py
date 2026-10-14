import html
import fcntl
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from profile_manager.templating import set_current_view, set_current_path
from pathlib import Path
from collections import Counter
from urllib.parse import parse_qs, quote, urlsplit

from profile_manager.config import config_exists, config_path, load_config
from profile_manager.data.commands import _command_cards, _command_rows
from profile_manager.data.config import (
    _config_payload,
    _discover_project_root,
    _local_interface_urls,
    _safe_project_file,
    default_dashboard_dir,
)
from profile_manager.data.deliverables import (
    _deliverable_catalog,
    _deliverable_entry,
    _deliverable_rows,
    _doc_links,
    _document_rows,
)
from profile_manager.data.packages import _package_rows
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.health import (
    _extract_health_value,
    _extract_tip_json,
    _health_from_body,
    _latest_profile_health,
    _live_health,
    runtime_identity,
)
from profile_manager.data.bundles import _forensic_bundles_dir, _latest_evidence_rows
from profile_manager.data.fuzz import _fuzz_rows, _smoke_rows
from profile_manager.data.lifecycle import (
    _live_testcase_lifecycle_summary,
    _local_testcase_lifecycle_summary,
    _read_ndjson_rows,
    _summarize_testcase_state,
)
from profile_manager.data.runs import (
    _forensic_runs_dir,
    _ssh_remote_lister,
    humanize_decode_error,
    list_recent_runs_with_remote,
    parse_remote_sources,
    recent_runs_payload,
)
from profile_manager.data.scenarios import (
    _humanize_scenario_id,
    _list_scenarios_for_compare,
    _scenarios_dir,
)
from profile_manager.data.files import (
    _attachment_headers,
    _best_existing,
    _download_url,
    _escape,
    _latest_files,
    _pdf_url,
    _read_text,
)
from profile_manager.evidence_packages import load_evidence_packages
from profile_manager.fuzz import load_fuzz_tests
from profile_manager.inspect import inspect_health_command
from profile_manager.moog import moog_health_summary, query_moog_health
from profile_manager.profiles import load_profiles
from profile_manager.remote import control_shim_enabled, render_ssh_command, ssh_command
from profile_manager.smoke import load_smoke_tests
from profile_manager.wallets import wallet_statuses
from profile_manager.views.concepts import render_learn_concepts
from profile_manager.views.coverage import render_learn_coverage
from profile_manager.views.threat_coverage import render_learn_threat_coverage
from profile_manager.views.learn_cli import render_learn_cli
from profile_manager.views.architecture import render_learn_architecture
from profile_manager.views.compare import render_operate_compare
from profile_manager.views.operate_coverage import render_operate_coverage
from profile_manager.views.operate_crashes import render_operate_crashes
from profile_manager.views.operate_schedule import render_operate_schedule
from profile_manager.views.operate_audit import render_operate_audit
from profile_manager.views.operate_timeline import render_operate_timeline
from profile_manager.views.operate_static_analysis import render_operate_static_analysis
from profile_manager.views.operate_profiles import render_operate_profiles
from profile_manager.views.operate_measurements import (
    render_operate_measurement_profiles,
    render_operate_measurements,
)
from profile_manager.views.operate_versions import render_operate_versions
from profile_manager.views.operate_bundles import render_operate_bundles
from profile_manager.views.operate_plugins import dispatch_plugin_request
from profile_manager.views.operate_primitives import (
    dispatch_primitive_catalog_request,
)
from profile_manager.views.operate_profile_templates import (
    dispatch_profile_template_request,
)
from profile_manager.views.operate_testcases import (
    dispatch_testcase_artifact_request,
    dispatch_testcase_request,
)
from profile_manager.views.operate_corpora import dispatch_corpus_request
from profile_manager.views.operate_grammars import dispatch_grammar_request
from profile_manager.views.operate_risk_packages import dispatch_risk_package_request
from profile_manager.views.operate_config import render_operate_config
from profile_manager.views.operate_notifications import render_operate_notifications
from profile_manager.views.learn_examples import render_learn_examples, render_learn_getting_started
from profile_manager.views.learn_api import render_learn_api
from profile_manager.views.learn_docs import (
    render_learn_glossary, render_learn_faq, render_learn_troubleshooting,
)
from profile_manager.views.learn_runbooks import (
    render_learn_operator_runbook,
    render_learn_developer_onboarding,
    render_learn_plugin_authoring_guide,
)
from profile_manager.views.operate_contract import render_operate_contract
from profile_manager.views.operate_run import (
    render_operate_run,
    render_operate_run_measurements_raw,
    render_operate_run_not_found,
)
from profile_manager.views.operate_runs import render_operate_runs
from profile_manager.views.operate_run_wizard import render_operate_run_wizard
from profile_manager.views.operate_status import render_operate_status
from profile_manager.views.operate_targets import render_operate_targets
from profile_manager.views.status import render_learn_status
from profile_manager.views.walkthroughs import render_learn_walkthroughs
from profile_manager.views.learn_attack_cost import render_learn_attack_cost
from profile_manager.views.learn_overview import render_learn_overview
from profile_manager.views.learn_consensus import render_learn_consensus
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.learn_primitives import render_learn_primitives
from profile_manager.views.learn_profile_templates import render_learn_profile_templates
from profile_manager.views.learn_versions import render_learn_versions
from profile_manager.views.learn_measurements import render_learn_measurements
from profile_manager.views.learn_measurement_coverage import render_learn_measurement_coverage
from profile_manager.views.learn_testcases import render_learn_testcases
from profile_manager.views.learn_corpora import render_learn_corpora
from profile_manager.views.learn_grammars import render_learn_grammars
from profile_manager.views.learn_risk_packages import render_learn_risk_packages
from profile_manager.views.landing import render_landing
from profile_manager.views.operate import render_operate_landing
from profile_manager.views.scenarios import render_operate_scenarios


PROJECT_ROOT = _discover_project_root(Path(__file__).resolve())


def _pick_dashboard_root(project_root: Path) -> Path:
    """Pick the dashboard root that actually contains the dashboard assets.

    Two candidate layouts:
    - ``project_root/dwarf/dashboard``  (local Mac dev checkout: dwarf/ is the
      app subdir under the parent ada2 repo)
    - ``project_root/dashboard``        (cardano-box flattened layout: rsync
      from sync-dwarf-fw.sh strips the dwarf/ wrapper, putting dashboard/ +
      profile_manager/ at top level)

    Pre-slice-19 logic checked only that ``project_root/dwarf`` *existed*,
    which on cardano-box was true but stale -- a leftover ``dwarf/`` directory
    from an old layout containing only an ``index.html``, no ``static/``.
    Result: the picker selected ``project_root/dwarf/dashboard`` (which had
    no ``static/``), so ``/static/css/base.css`` returned 404 and all
    theming broke.

    Hardened: prefer the candidate whose ``static/`` subdir actually exists.
    That's the load-bearing signal that the candidate is a real working
    dashboard root, not a stale leftover.

    Falls through to the first existing candidate if neither has ``static/``,
    so fresh checkouts that haven't materialized assets still resolve.
    """
    candidates = [
        project_root / "dwarf" / "dashboard",
        project_root / "dashboard",
    ]
    for candidate in candidates:
        if (candidate / "static").is_dir():
            return candidate
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DASHBOARD_ROOT = _pick_dashboard_root(PROJECT_ROOT)


@dataclass(frozen=True)
class DashboardResult:
    path: Path
    url: str


def build_dashboard_status_payload(live=True, profile_id=None):
    health_path, health_body = _latest_profile_health()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_identity": runtime_identity(),
        "config": _config_payload(),
        "profiles": _profile_rows(),
        "evidence_packages": _package_rows(),
        "smoke_tests": _smoke_rows(),
        "fuzz_tests": _fuzz_rows(),
        "documents": _document_rows(),
        "deliverables": _deliverable_rows(),
        "commands": _command_rows(),
        "latest_evidence": _latest_evidence_rows(),
        "wallets": _wallet_status_rows_for_payload(),
        "moog": _moog_status_for_payload(),
        "testcase_lifecycle": _live_testcase_lifecycle_summary() if live else _local_testcase_lifecycle_summary(),
        "last_local_health": _health_from_body(health_body, evidence_path=health_path),
        "live": {"enabled": False, "profile_id": profile_id},
    }
    if live:
        payload["live"] = _live_health(profile_id)
    return payload


def _moog_status_for_payload():
    if not config_exists():
        return {
            "state": "unknown",
            "summary": {"state": "unknown", "check_count": 0, "ok_count": 0, "warn_count": 0, "error_count": 0},
            "checks": [],
            "wallets": {},
            "error": "Dwarf config is missing.",
        }
    try:
        health = query_moog_health(load_config(), timeout=10)
        return {
            **health,
            "summary": moog_health_summary(health),
        }
    except Exception as exc:
        return {
            "state": "error",
            "summary": {"state": "error", "check_count": 0, "ok_count": 0, "warn_count": 0, "error_count": 1},
            "checks": [],
            "wallets": {},
            "error": str(exc),
        }


def _wallet_status_rows_for_payload():
    if not config_exists():
        return []
    try:
        return wallet_statuses(load_config(), timeout=10)
    except Exception as exc:
        return [{
            "id": "wallet-config",
            "label": "Wallet config",
            "role": "unknown",
            "network": "unknown",
            "address": "unknown",
            "state": "error",
            "balance_lovelace": None,
            "balance_tada": "unknown",
            "recent_transactions": [],
            "queried_at": None,
            "error": str(exc),
        }]


def dashboard_status_text(output_dir=None):
    output = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    return (
        "Dashboard\n"
        f"Output: {output / 'index.html'}\n"
        f"Config: {config_path()} ({'present' if config_exists() else 'missing'})\n"
        f"Profiles: {len(load_profiles())}\n"
        f"Risk work packages: {len(load_evidence_packages())}\n"
        f"Smoke tests: {len(load_smoke_tests())}\n"
        f"Fuzz tests: {len(load_fuzz_tests())}\n"
    )


def _render_table(headers, rows):
    head = "".join(f"<th>{_escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _convert_project_file_to_pdf(path):
    suffix = path.suffix.lower()
    with tempfile.TemporaryDirectory(prefix="dwarf-pdf-") as tmp:
        tmpdir = Path(tmp)
        html_path = tmpdir / "source.html"
        pdf_path = tmpdir / "output.pdf"
        if suffix in {".md", ".html", ".htm", ".docx"}:
            try:
                html_result = subprocess.run(
                    [
                        "pandoc",
                        str(path),
                        "-s",
                        "--embed-resources",
                        "--standalone",
                        "-t",
                        "html",
                        "-o",
                        str(html_path),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=90,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return None, f"PDF conversion requires pandoc for {suffix} files: {exc}\n"
            if html_result.returncode != 0:
                detail = html_result.stderr.strip() or html_result.stdout.strip() or "pandoc html conversion failed"
                return None, detail + "\n"
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return None, f"could not read file: {exc}\n"
            html_path.write_text(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{font:14px system-ui,sans-serif;margin:36px;}pre{white-space:pre-wrap;}</style>"
                f"</head><body><h1>{_escape(path.name)}</h1><pre>{_escape(text)}</pre></body></html>",
                encoding="utf-8",
            )
        try:
            pdf_result = subprocess.run(
                [
                    "npx",
                    "--yes",
                    "playwright",
                    "pdf",
                    "--paper-format",
                    "Letter",
                    html_path.as_uri(),
                    str(pdf_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"PDF rendering requires Playwright Chromium: {exc}\n"
        if pdf_result.returncode != 0:
            detail = pdf_result.stderr.strip() or pdf_result.stdout.strip() or "playwright pdf rendering failed"
            return None, detail + "\n"
        try:
            return pdf_path.read_bytes(), None
        except OSError as exc:
            return None, f"could not read rendered PDF: {exc}\n"


def _script_json(payload):
    return json.dumps(payload).replace("</", "<\\/")


def _bootstrap_payload():
    return _script_json(build_dashboard_status_payload(live=False))


def _nav(active_route):
    """Slice-21 CP1.5 retains this for legacy callers/tests; the rendered
    chrome now uses _legacy_subnav() instead. Same 8 items, same active-
    matching, but with mono-eyebrow forensic-noir styling applied via the
    .legacy-subnav CSS class."""
    return _legacy_subnav(active_route)


def _legacy_subnav(active_route):
    """Sub-nav row for the 8 preserved-legacy routes. Sits below the
    slice-20 OPERATE/LEARN primary nav so operators can cross-link
    between legacy surfaces (project, deliverables, scenarios, compare,
    architecture, settings, tests, raw) without losing the new shell."""
    items = [
        ("/scenarios", "Scenarios"),
        ("/tests", "Tests & Evidence"),
    ]
    return "".join(
        f'<a class="{"active" if route == active_route else ""}" href="{route}">{label}</a>'
        for route, label in items
    )


def _target_live_cards():
    return """
  <section class="grid basic-only">
    <div class="card notice"><h3>Test environment</h3><div id="target-env-basic">Loading...</div></div>
    <div class="card live"><h3>Status</h3><div id="live-summary-basic">Loading...</div></div>
  </section>
  <section class="grid adv-only">
    <div class="card notice"><h3>Target Environment</h3><div id="target-env">Loading...</div></div>
    <div class="card live"><h3>Live Runtime</h3><div id="live-summary">Waiting for /api/status...</div></div>
  </section>
"""


def _common_script(render_body):
    return f"""
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
const code = (value) => `<code>${{esc(value)}}</code>`;
function table(headers, rows) {{
  const head = headers.map(h => `<th>${{esc(h)}}</th>`).join("");
  const body = rows.map(row => `<tr>${{row.map(cell => `<td>${{cell}}</td>`).join("")}}</tr>`).join("");
  return `<table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table>`;
}}
function liveHealth(payload) {{
  const live = payload.live || {{}};
  return live.health || payload.last_local_health || {{}};
}}
function renderTargetAndLive(payload) {{
  const cfg = payload.config || {{}};
  const adv = document.getElementById("target-env");
  if (adv) adv.innerHTML = cfg.present
    ? `<strong>${{esc(cfg.deployment_name)}}</strong><br>${{esc(cfg.ssh_user)}}@${{esc(cfg.host)}}<br>remote base: ${{code(cfg.remote_base_path)}}<br>config: ${{code(cfg.path)}}`
    : `${{esc(cfg.message || "Config missing")}}<br>${{code(cfg.path || "")}}`;
  const live = payload.live || {{}};
  const health = liveHealth(payload);
  const liveClass = live.enabled && Number(health.returncode) === 0 ? "live" : "stale";
  const advLive = document.getElementById("live-summary");
  if (advLive) {{
    advLive.parentElement.className = `card ${{liveClass}}`;
    advLive.innerHTML =
      `Source: ${{live.enabled ? "live SSH poll" : "local cached evidence"}}<br>` +
      `Profile: ${{code(live.profile_id || "profile-a-haskell-peersharing-disabled")}}<br>` +
      `SSH exit: ${{esc(health.returncode ?? "n/a")}}<br>` +
      `<span class="small">Last refresh: ${{esc(payload.generated_at)}}</span>`;
  }}
  // Basic-mode plain-English summaries
  const basicEnv = document.getElementById("target-env-basic");
  if (basicEnv) basicEnv.innerHTML = cfg.present
    ? `Connected to <strong>${{esc(cfg.deployment_name)}}</strong>.`
    : `<strong>Not connected.</strong> Run <code>cardano-profile intake</code> from a terminal.`;
  const basicLive = document.getElementById("live-summary-basic");
  if (basicLive) {{
    const ok = live.enabled && Number(health.returncode) === 0;
    basicLive.parentElement.className = `card ${{liveClass}}`;
    basicLive.innerHTML = ok
      ? `The test Cardano network is <strong>running</strong>.`
      : (live.enabled
          ? `The test Cardano network is <strong>not responding</strong>.`
          : `Showing the most recent saved status (no live poll).`);
  }}
}}
function activeProfile(payload) {{
  return (payload.profiles || []).find(p => p.id === (payload.live || {{}}).profile_id) || (payload.profiles || [])[0] || {{}};
}}
{render_body}
async function refresh() {{
  try {{
    const response = await fetch("/api/status", {{cache: "no-store"}});
    if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
    render(await response.json());
  }} catch (error) {{
    const fallback = JSON.parse(document.getElementById("bootstrap").textContent);
    fallback.live = fallback.live || {{}};
    fallback.live.error = String(error);
    render(fallback);
  }}
}}
render(JSON.parse(document.getElementById("bootstrap").textContent));
refresh();
setInterval(refresh, 10000);
"""


def _page(title, active_route, body, render_script):
    """Slice-21 CP1.5: legacy chrome re-themed onto the slice-20 forensic-noir
    HUD shell. The remaining preserved-legacy nav routes (/scenarios,
    /tests) flow through this helper alongside the bundle inspector at
    /runs/<id> (which slice 26 ports to /operate/runs/<id>; /runs/<id>
    is kept as a 302-style alias via render_route_html so old links still
    resolve). /compare migrated to /operate/compare in slice 24;
    /architecture re-pointed to /learn/architecture and /status added as
    a /operate/status alias in slice 25 (legacy /architecture used to
    point at /operate/status, which was wrong — architecture content
    lives at /learn/architecture). /settings still maps to
    /operate/status (the legacy settings page WAS the status console);
    /project, /deliverables, /raw retired in slice 26 (redirects only).

    The shell head + body header now match _base.j2 (logo brand mark,
    OPERATE/LEARN primary nav, plex.css/tokens.css/base.css). A secondary
    legacy-subnav row preserves the 8 cross-links between legacy pages and
    keeps the basic/advanced mode toggle.

    The remaining inline <style> block is a dark-theme reskin for legacy
    primitives (.card, .grid, .flow-step, .zone, .viz, table, pre, etc.)
    so the existing per-route HTML bodies render legibly on dark without
    being rewritten. Anti-fabrication rails preserved: every operator data
    field on every legacy page still flows through unchanged.
    """
    payload = build_dashboard_status_payload(live=False)
    bootstrap_json = _script_json(payload)
    common_script = _common_script(render_script) if render_script.strip() else ""
    common_script_tag = f"<script>{common_script}</script>" if common_script else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dwarf — {title}</title>
<link rel="icon" type="image/png" href="/static/dwarf-logo.png">
<link rel="apple-touch-icon" href="/static/dwarf-logo.png">
<meta name="theme-color" content="#06080B">
<link rel="stylesheet" href="/static/css/plex.css">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/base.css">
<style>
/* Slice-21 CP1.5: dark-theme reskin for legacy primitives. The slice-20
   tokens (--obsidian-N, --cyan-*, --ink-*, --crimson-*) come from
   tokens.css; this block maps the legacy class names onto them. */
:root {{
  /* Legacy aliases — point legacy code paths at slice-20 tokens. */
  --bg: var(--obsidian-0);
  --panel: var(--obsidian-2);
  --line: var(--cyan-trace);
  --accent: var(--cyan-primary);
  --muted: var(--ink-secondary);
  --warn: var(--cork);
  --danger: var(--crimson);
}}
/* Legacy main area opens up wider than the operate/learn surfaces; the
   legacy pages have wide tables and grids. */
body[data-shell="legacy"] .shell-main {{
  max-width: 1280px;
  padding-block: var(--space-8) var(--space-24);
}}

/* Legacy section / heading rhythm. */
body[data-shell="legacy"] section {{ margin: 0 0 var(--space-8); }}
body[data-shell="legacy"] section h2 {{
  font-size: 1.4rem; font-weight: 700; color: var(--ink-primary);
  letter-spacing: -0.005em; margin: 0 0 var(--space-4);
}}
body[data-shell="legacy"] section h3 {{
  font-size: 1rem; font-weight: 500; color: var(--ink-primary);
  margin: 0 0 var(--space-2);
}}

/* Card / metric / flow-step / zone / status-tile / node-box —
   surface-2 obsidian + cyan-trace edge. Replaces 1px line + cream paper. */
body[data-shell="legacy"] .card,
body[data-shell="legacy"] .metric,
body[data-shell="legacy"] .flow-step,
body[data-shell="legacy"] .zone,
body[data-shell="legacy"] .status-tile,
body[data-shell="legacy"] .node-box {{
  background: var(--obsidian-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  padding: var(--space-4);
  box-shadow: var(--shadow-card);
}}
body[data-shell="legacy"] .card.live,
body[data-shell="legacy"] .notice {{
  border-left: 3px solid var(--cyan-primary);
}}
body[data-shell="legacy"] .card.bad,
body[data-shell="legacy"] .danger {{
  border-left: 3px solid var(--crimson);
}}
body[data-shell="legacy"] .card.stale {{
  border-left: 3px solid var(--cork);
}}

/* Grid / status-strip / node-lane — tighter on dark, no change in semantics. */
body[data-shell="legacy"] .grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .status-strip {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .node-lane {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .flow {{
  display: flex; flex-wrap: wrap; align-items: stretch; gap: var(--space-3);
}}
body[data-shell="legacy"] .flow-step {{ flex: 1 1 160px; }}

/* Metric / status-tile typography. */
body[data-shell="legacy"] .metric span,
body[data-shell="legacy"] .status-tile span {{
  display: block; color: var(--ink-tertiary);
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
}}
body[data-shell="legacy"] .metric strong,
body[data-shell="legacy"] .status-tile strong {{
  display: block; margin-top: var(--space-1); color: var(--ink-primary);
  font-family: var(--font-mono); word-break: break-word;
}}

/* Tables — match the slice-20 dense-table style. */
body[data-shell="legacy"] table {{
  width: 100%; border-collapse: collapse;
  background: var(--obsidian-2); border: 1px solid transparent;
  border-radius: var(--radius-md); overflow: hidden;
  font-size: 0.95em;
}}
body[data-shell="legacy"] th, body[data-shell="legacy"] td {{
  text-align: left; vertical-align: top;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--rule-soft);
}}
body[data-shell="legacy"] th {{
  background: var(--obsidian-3);
  font-family: var(--font-mono); font-weight: 400;
  font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  color: var(--cyan-primary);
}}
body[data-shell="legacy"] tbody tr:hover {{ background: var(--obsidian-3); }}
body[data-shell="legacy"] .table-wrap {{ overflow-x: auto; border-radius: var(--radius-md); }}
body[data-shell="legacy"] .small {{ color: var(--ink-tertiary); font-size: var(--type-small); }}

/* Pre / code blocks — terminal-grade. */
body[data-shell="legacy"] pre {{
  margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--obsidian-1); color: var(--ink-primary);
  border-left: 2px solid var(--cyan-trace);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  max-height: 420px; overflow: auto;
}}

/* Legacy span.pill — distinct from slice-20 .pill button. Static label. */
body[data-shell="legacy"] span.pill {{
  display: inline-block; border-radius: 999px;
  padding: 2px 8px; font-size: 0.78em; font-family: var(--font-mono);
  background: var(--obsidian-3); color: var(--ink-secondary);
  letter-spacing: 0.04em; text-transform: uppercase;
  border: 1px solid var(--cyan-trace); cursor: default;
}}

/* SVG diagrams: re-fill nodes/zones for dark backgrounds. */
body[data-shell="legacy"] .viz {{
  display: block; width: 100%; max-width: 100%;
  height: clamp(132px, 24vw, 210px);
  border: 1px solid transparent; border-radius: var(--radius-md);
  background: var(--obsidian-2); overflow: hidden;
}}
body[data-shell="legacy"] .viz-compact {{ height: clamp(118px, 20vw, 170px); }}
body[data-shell="legacy"] .viz-standard {{ height: clamp(150px, 28vw, 260px); }}
body[data-shell="legacy"] .viz text {{ font-family: var(--font-sans); fill: var(--ink-primary); }}
body[data-shell="legacy"] .viz .muted {{ fill: var(--ink-tertiary); font-size: 12px; }}
body[data-shell="legacy"] .viz .node {{ fill: var(--obsidian-3); stroke: var(--cyan-trace); stroke-width: 1; }}
body[data-shell="legacy"] .viz .ok {{ fill: rgba(43, 224, 224, 0.10); stroke: var(--cyan-primary); }}
body[data-shell="legacy"] .viz .warn {{ fill: rgba(181, 138, 79, 0.10); stroke: var(--cork); }}
body[data-shell="legacy"] .viz .bad {{ fill: rgba(192, 48, 48, 0.10); stroke: var(--crimson); }}
body[data-shell="legacy"] .viz .edge {{ stroke: var(--cyan-trace); stroke-width: 1.5; marker-end: url(#arrowhead); }}
body[data-shell="legacy"] .viz .zone-fill {{ fill: var(--obsidian-3); stroke: var(--cyan-trace); stroke-width: 1; }}
body[data-shell="legacy"] .viz .gate {{ fill: rgba(181, 138, 79, 0.10); stroke: var(--cork); stroke-width: 1; }}

/* Mode toggle — basic/advanced pill row. */
body[data-shell="legacy"] .mode-toggle {{
  display: inline-flex; gap: 0;
  border: 1px solid var(--cyan-trace); border-radius: 999px; overflow: hidden;
  background: var(--obsidian-2);
}}
body[data-shell="legacy"] .mode-toggle button {{
  background: transparent; color: var(--ink-secondary); border: 0;
  padding: var(--space-1) var(--space-4); font: inherit;
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  cursor: pointer;
}}
body[data-shell="legacy"] .mode-toggle button.active {{
  background: var(--cyan-tint); color: var(--cyan-glow);
}}
[data-mode="basic"] .adv-only {{ display: none !important; }}
[data-mode="advanced"] .basic-only {{ display: none !important; }}

/* Action buttons / download links — cyan-glow on dark. */
body[data-shell="legacy"] .action-row {{ display: flex; flex-wrap: wrap; gap: var(--space-3); margin: var(--space-2) 0 var(--space-4); }}
body[data-shell="legacy"] .action-button,
body[data-shell="legacy"] .download-link {{
  display: inline-block; background: var(--cyan-tint); color: var(--cyan-glow);
  border: 1px solid var(--cyan-primary); border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4); font: inherit;
  font-weight: 500; cursor: pointer; text-decoration: none;
}}
body[data-shell="legacy"] .action-button:hover,
body[data-shell="legacy"] .download-link:hover {{
  background: rgba(43, 224, 224, 0.18);
  border-color: var(--cyan-glow); color: var(--cyan-glow);
}}
body[data-shell="legacy"] .action-button.action-remove {{
  background: rgba(192, 48, 48, 0.10); border-color: var(--crimson); color: var(--crimson-glow);
}}
body[data-shell="legacy"] .action-button:disabled {{ opacity: .5; cursor: not-allowed; }}
body[data-shell="legacy"] .download-link.secondary {{ background: transparent; color: var(--ink-secondary); border-color: var(--ink-tertiary); margin-left: var(--space-2); }}
body[data-shell="legacy"] .download-link.missing {{ background: var(--obsidian-2); color: var(--ink-tertiary); border-color: transparent; cursor: default; }}
body[data-shell="legacy"] .deliverable-path {{ color: var(--ink-tertiary); font-size: var(--type-small); overflow-wrap: anywhere; }}
body[data-shell="legacy"] .milestone-heading {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--space-2); }}
body[data-shell="legacy"] .candidate-warning {{
  border: 1px solid rgba(181, 138, 79, 0.40); border-left: 3px solid var(--cork);
  border-radius: var(--radius-md); padding: var(--space-3) var(--space-4);
  background: rgba(181, 138, 79, 0.08); color: var(--ink-secondary);
}}

/* Modal — dark surface with cyan edge. */
body[data-shell="legacy"] .modal {{
  position: fixed; inset: 0; background: rgba(6, 8, 11, 0.75);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; padding: var(--space-4);
}}
body[data-shell="legacy"] .modal[hidden] {{ display: none; }}
body[data-shell="legacy"] .modal-card {{
  background: var(--obsidian-2); border: 1px solid var(--cyan-trace);
  border-radius: var(--radius-lg); padding: var(--space-6);
  max-width: 520px; width: 100%;
  box-shadow: 0 16px 48px rgba(0,0,0,.55);
}}
body[data-shell="legacy"] .modal-card h3 {{ margin: 0 0 var(--space-2); color: var(--ink-primary); }}
body[data-shell="legacy"] .modal-card p {{ margin: 0 0 var(--space-3); color: var(--ink-secondary); }}
body[data-shell="legacy"] .modal-card input {{
  width: 100%; padding: var(--space-2); border: 1px solid var(--cyan-trace);
  border-radius: var(--radius-sm); font: inherit;
  background: var(--obsidian-1); color: var(--ink-primary);
  margin-top: var(--space-1);
}}
body[data-shell="legacy"] .modal-row {{ display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }}
body[data-shell="legacy"] .modal-row button {{
  padding: var(--space-2) var(--space-4); border-radius: var(--radius-sm);
  border: 1px solid var(--cyan-trace); background: var(--obsidian-3);
  color: var(--ink-secondary); font: inherit; cursor: pointer;
}}
body[data-shell="legacy"] .modal-row button:last-child {{
  background: var(--cyan-tint); color: var(--cyan-glow);
  border-color: var(--cyan-primary);
}}
body[data-shell="legacy"] .modal-row button:disabled {{ opacity: .5; cursor: not-allowed; }}
body[data-shell="legacy"] textarea {{
  width: 100%; min-height: 180px;
  font: var(--type-small)/1.5 var(--font-mono);
  padding: var(--space-2); background: var(--obsidian-1);
  color: var(--ink-primary);
  border: 1px solid var(--cyan-trace); border-radius: var(--radius-sm);
}}

/* Legacy sub-nav — cross-link bar between the 8 legacy pages, sits below
   the slice-20 OPERATE/LEARN nav. Mono-pill row, single-line scroll. */
.legacy-subnav {{
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  align-items: center;
  padding: var(--space-3) var(--space-12);
  background: var(--obsidian-1);
  box-shadow: inset 0 -1px 0 var(--cyan-trace);
}}
.legacy-subnav__label {{
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  color: var(--ink-tertiary); margin-right: var(--space-2);
}}
.legacy-subnav a {{
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--ink-secondary); text-decoration: none;
  padding: 4px var(--space-3);
  border: 1px solid var(--cyan-trace); border-radius: var(--radius-md);
  background: var(--obsidian-2);
  border-bottom: 1px solid var(--cyan-trace);
}}
.legacy-subnav a:hover {{
  color: var(--cyan-glow); border-color: var(--cyan-glow);
  background: var(--obsidian-3);
}}
.legacy-subnav a.active {{
  color: var(--cyan-glow); border-color: var(--cyan-primary);
  background: var(--cyan-tint);
}}
.legacy-subnav__toggle {{ margin-left: auto; }}

.legacy-strapline {{
  margin: var(--space-3) var(--space-12) 0;
  font-family: var(--font-mono);
  font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--ink-tertiary);
  text-align: right;
}}

/* Mobile responsive — narrower padding. */
@media (max-width: 640px) {{
  body[data-shell="legacy"] .shell-main {{ padding-inline: var(--space-3); }}
  body[data-shell="legacy"] .grid {{ grid-template-columns: 1fr; gap: var(--space-2); }}
  body[data-shell="legacy"] .status-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2); }}
  body[data-shell="legacy"] .flow-step {{ flex-basis: 100%; }}
  body[data-shell="legacy"] th, body[data-shell="legacy"] td {{ padding: var(--space-2); }}
  .legacy-subnav {{ padding-inline: var(--space-3); }}
}}
</style>
</head>
<body data-mode="basic" data-shell="legacy" data-density="dense">
<header class="shell-header">
  <a class="brand" href="/operate" aria-label="Dwarf — observability for the Cardano substrate">
    <img src="/static/dwarf-logo.png" alt="Dwarf">
  </a>
  <nav class="shell-nav" aria-label="Primary">
    <a href="/operate">Operate</a>
    <a href="/learn">Learn</a>
  </nav>
</header>
<nav class="legacy-subnav" aria-label="Legacy">
  <span class="legacy-subnav__label">Legacy</span>
  {_legacy_subnav(active_route)}
  <div id="mode-toggle" class="mode-toggle legacy-subnav__toggle" role="group" aria-label="View mode">
    <button type="button" data-target-mode="basic">Basic</button>
    <button type="button" data-target-mode="advanced">Advanced</button>
  </div>
</nav>
<p class="legacy-strapline">Browser mutations are token-gated and serialized; read-only routes remain open.</p>
<main class="shell-main">
{body}
</main>
<script id="bootstrap" type="application/json">{bootstrap_json}</script>
<script>
(function () {{
  var key = "dwarf-ui-mode";
  var saved = null;
  try {{ saved = localStorage.getItem(key); }} catch (e) {{}}
  var initial = (saved === "advanced" || saved === "basic") ? saved : "basic";
  document.body.setAttribute("data-mode", initial);
  function setMode(mode) {{
    document.body.setAttribute("data-mode", mode);
    try {{ localStorage.setItem(key, mode); }} catch (e) {{}}
    document.querySelectorAll("#mode-toggle button").forEach(function (b) {{
      b.classList.toggle("active", b.getAttribute("data-target-mode") === mode);
    }});
  }}
  document.querySelectorAll("#mode-toggle button").forEach(function (b) {{
    b.addEventListener("click", function () {{ setMode(b.getAttribute("data-target-mode")); }});
  }});
  setMode(initial);
}})();
</script>
{common_script_tag}
</body>
</html>
"""


def render_command_center_html():
    intro = """
  <section class="basic-only">
    <div class="card notice">
      <h3>What is this?</h3>
      <p>Dwarf is a forensic Cardano testing framework. It feeds adversarial inputs to Amaru and cardano-node parsers and consensus code, records every test as a tamper-evident evidence bundle, and lets you run the same test against both implementations to find behavioural divergences.</p>
      <p>Every test produces a forensic bundle that is reproducible, signable, and replayable. Nothing here changes the live Cardano network &mdash; this is a read-only, isolated test harness.</p>
      <p><a href="/tests"><strong>Run a test &rarr;</strong></a> &middot; <a href="/operate/compare">Compare implementations</a></p>
    </div>
  </section>
  <section class="adv-only">
    <div class="card notice">
      <h3>Operator surface</h3>
      <p>Library-tier scenarios run via <code>cardano-profile scenario run &lt;path&gt;</code>; cross-impl comparison via <code>cardano-profile compare &lt;path&gt;</code>; bundles verifiable via <code>cardano-profile verify &lt;run-id&gt;</code>. Every mutating endpoint requires the dashboard token and is serialised by a global lock. <a href="/operate/runs">Recent runs</a> &middot; <a href="/operate/compare">Compare</a> &middot; <a href="/operate/status">Substrate status</a>.</p>
    </div>
  </section>
"""
    body = intro + _target_live_cards() + """
  <section>
    <h2>Actions</h2>
    <p class="small basic-only">Start or stop the Cardano test environment. Each action asks for typed confirmation and streams a live log below.</p>
    <p class="small adv-only">Each button runs the equivalent <code>cardano-profile</code> CLI command with <code>--approve</code>. The server serializes mutating actions; a second simultaneous action returns 409. A forensic bundle is produced for every run.</p>
    <div class="action-row">
      <button id="deploy-button" class="action-button action-deploy" data-action="deploy"
              data-profile="profile-a-haskell-peersharing-disabled">
        <span class="basic-only">Start the test environment</span>
        <span class="adv-only">cardano-profile deploy profile-a-haskell-peersharing-disabled --approve</span>
      </button>
      <button id="remove-button" class="action-button action-remove" data-action="remove">
        <span class="basic-only">Stop the test environment</span>
        <span class="adv-only">cardano-profile remove --approve</span>
      </button>
    </div>
    <pre id="action-log" class="action-log">No action running. Output will appear here live.</pre>
  </section>

  <div id="action-modal" class="modal" hidden>
    <div class="modal-card">
      <h3 id="action-modal-title">Confirm</h3>
      <p id="action-modal-body"></p>
      <pre id="action-modal-command" class="adv-only"></pre>
      <label for="action-modal-input">Type the confirmation word to proceed:</label>
      <input id="action-modal-input" type="text" autocomplete="off" spellcheck="false">
      <div class="modal-row">
        <button id="action-modal-cancel" type="button">Cancel</button>
        <button id="action-modal-confirm" type="button" disabled>Confirm</button>
      </div>
    </div>
  </div>

  <section>
    <h2>Command Center</h2>
    <svg class="viz viz-compact" id="status-gauge-svg" viewBox="0 0 860 170" role="img" aria-label="Live runtime gauges"></svg>
    <div class="status-strip" id="status-strip"></div>
  </section>

  <section>
    <h2>Deployment Flow</h2>
    <svg class="viz viz-compact" id="deployment-flow-svg" viewBox="0 0 920 260" role="img" aria-label="Deployment topology map"></svg>
    <div class="flow" id="deployment-flow">
      <div class="flow-step"><strong>Browser / CLI</strong>Local operator view and command entry point.</div>
      <div class="flow-step"><strong>SSH</strong>Read-only health polling and explicit CLI operations.</div>
      <div class="flow-step"><strong>cardano-box</strong>Ubuntu host for local Cardano profile runtime.</div>
      <div class="flow-step"><strong>Profile Runtime</strong>Managed local testnet profile under `/opt/dwarf/cardano-profiles`.</div>
      <div class="flow-step"><strong>node1 / node2 / node3</strong>Loopback node-to-node listeners, sockets, logs, and DB state.</div>
    </div>
  </section>

  <section>
    <h2>Current Deployment</h2>
    <div class="table-wrap" id="deployment-table"></div>
  </section>

  <section>
    <h2>Next Actions</h2>
    <div class="table-wrap" id="next-actions-table"></div>
  </section>
"""
    script = """
const ACTION_CONFIRM_WORDS = { deploy: "START", remove: "STOP" };
function actionModal(action, profile, mode) {
  const modal = document.getElementById("action-modal");
  const title = document.getElementById("action-modal-title");
  const bodyEl = document.getElementById("action-modal-body");
  const cmdEl = document.getElementById("action-modal-command");
  const input = document.getElementById("action-modal-input");
  const confirmBtn = document.getElementById("action-modal-confirm");
  const cancelBtn = document.getElementById("action-modal-cancel");
  const word = ACTION_CONFIRM_WORDS[action];
  if (action === "deploy") {
    title.textContent = "Start the test environment";
    bodyEl.innerHTML = `This will deploy <code>${esc(profile)}</code> to the test host. It can take a few minutes and replaces any active devnet. Type <strong>${word}</strong> to confirm.`;
    cmdEl.textContent = `cardano-profile deploy ${profile} --approve`;
  } else {
    title.textContent = "Stop the test environment";
    bodyEl.innerHTML = `This will stop active Cardano sessions and archive the runtime directory. Type <strong>${word}</strong> to confirm.`;
    cmdEl.textContent = `cardano-profile remove --approve`;
  }
  input.value = "";
  confirmBtn.disabled = true;
  modal.hidden = false;
  input.focus();
  return new Promise((resolve) => {
    function close(ok) {
      modal.hidden = true;
      input.oninput = null;
      confirmBtn.onclick = null;
      cancelBtn.onclick = null;
      resolve(ok);
    }
    input.oninput = () => { confirmBtn.disabled = input.value !== word; };
    confirmBtn.onclick = () => close(true);
    cancelBtn.onclick = () => close(false);
  });
}
function runAction(action, profile) {
  const log = document.getElementById("action-log");
  const deployBtn = document.getElementById("deploy-button");
  const removeBtn = document.getElementById("remove-button");
  deployBtn.disabled = true;
  removeBtn.disabled = true;
  log.textContent = "";
  const url = action === "deploy"
    ? `/api/deploy?token=${encodeURIComponent(getToken())}&profile=${encodeURIComponent(profile)}`
    : `/api/remove?token=${encodeURIComponent(getToken())}`;
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      log.textContent = `HTTP ${response.status}: ${text}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        const lines = part.split("\\n");
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => {
    log.textContent += "\\n[error] " + String(err);
  }).finally(() => {
    deployBtn.disabled = false;
    removeBtn.disabled = false;
    refresh();
  });
}
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function bindActionButtons() {
  document.querySelectorAll(".action-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const profile = btn.dataset.profile || "";
      const ok = await actionModal(action, profile, document.body.getAttribute("data-mode"));
      if (ok) runAction(action, profile);
    });
  });
}
function render(payload) {
  renderTargetAndLive(payload);
  bindActionButtons();
  const health = liveHealth(payload);
  const parsed = health.parsed || {};
  const active = activeProfile(payload);
  drawStatusGauges(parsed);
  drawDeploymentFlow(payload, active, parsed);
  const metrics = [
    ["Profile", active.id],
    ["Node Type", active.node_type],
    ["Node Count", active.node_count],
    ["PeerSharing", active.peer_sharing ? "enabled" : "disabled"],
    ["Node Processes", parsed.cardano_node_processes],
    ["Tip Block", parsed.tip_block],
    ["Sync", parsed.sync_progress],
    ["Loopback Only", parsed.loopback_only],
  ];
  document.getElementById("status-strip").innerHTML = metrics.map(([label, value]) =>
    `<div class="status-tile"><span>${esc(label)}</span><strong>${esc(value ?? "unknown")}</strong></div>`
  ).join("");
  document.getElementById("deployment-table").innerHTML = table(
    ["Field", "Value"],
    [
      ["Runtime Root", code((payload.live || {}).runtime_root || active.remote_runtime_root || "unknown")],
      ["Target", `${esc((payload.config || {}).ssh_user || "unknown")}@${esc((payload.config || {}).host || "unknown")}`],
    ]
  );
  document.getElementById("next-actions-table").innerHTML = table(
    ["Action", "CLI"],
    (payload.commands || []).slice(0, 5).map(c => [esc(c.label), code(c.command)])
  );
}
function stateColor(value, expected) {
  return String(value) === String(expected) ? "ok" : "warn";
}
function drawStatusGauges(parsed) {
  const gauges = [
    ["Processes", parsed.cardano_node_processes, "3", 80],
    ["Sockets", parsed.socket_count, "3", 260],
    ["Listeners", parsed.listener_count, "3", 440],
    ["Sync", parsed.sync_progress, "100.00", 620],
  ];
  document.getElementById("status-gauge-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    ${gauges.map(([label, value, expected, x]) => `<g>
      <circle class="${stateColor(value, expected)}" cx="${x}" cy="70" r="46"></circle>
      <text x="${x}" y="66" text-anchor="middle" font-size="22" font-weight="700">${esc(value ?? "unknown")}</text>
      <text class="muted" x="${x}" y="91" text-anchor="middle">${esc(label)}</text>
      <text class="muted" x="${x}" y="132" text-anchor="middle">expected ${esc(expected)}</text>
    </g>`).join("")}
  `;
}
function drawDeploymentFlow(payload, active, parsed) {
  const processClass = stateColor(parsed.cardano_node_processes, active.node_count || 3);
  document.getElementById("deployment-flow-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    <rect class="node" x="24" y="86" width="130" height="66" rx="8"/><text x="89" y="114" text-anchor="middle" font-weight="700">Browser / CLI</text><text class="muted" x="89" y="136" text-anchor="middle">operator</text>
    <line class="edge" x1="154" y1="119" x2="238" y2="119"/>
    <rect class="node" x="248" y="86" width="110" height="66" rx="8"/><text x="303" y="114" text-anchor="middle" font-weight="700">SSH</text><text class="muted" x="303" y="136" text-anchor="middle">read-only poll</text>
    <line class="edge" x1="358" y1="119" x2="438" y2="119"/>
    <rect class="node" x="448" y="66" width="146" height="106" rx="8"/><text x="521" y="102" text-anchor="middle" font-weight="700">cardano-box</text><text class="muted" x="521" y="126" text-anchor="middle">${esc((payload.config || {}).host || "unknown")}</text><text class="muted" x="521" y="150" text-anchor="middle">Ubuntu host</text>
    <line class="edge" x1="594" y1="119" x2="674" y2="119"/>
    <rect class="node" x="684" y="46" width="190" height="146" rx="8"/><text x="779" y="76" text-anchor="middle" font-weight="700">${esc(active.id || "active profile")}</text>
    <circle class="${processClass}" cx="729" cy="124" r="22"/><text x="729" y="130" text-anchor="middle">n1</text>
    <circle class="${processClass}" cx="779" cy="124" r="22"/><text x="779" y="130" text-anchor="middle">n2</text>
    <circle class="${processClass}" cx="829" cy="124" r="22"/><text x="829" y="130" text-anchor="middle">n3</text>
    <text class="muted" x="779" y="170" text-anchor="middle">${esc(parsed.cardano_node_processes ?? "unknown")} live node processes</text>
  `;
}
"""
    return _page("Command Center", "/", body, script)






def _deliverable_download_cell(relative):
    if not relative:
        return '<span class="download-link missing">Not available yet</span>'
    filename = Path(relative).name
    pdf_filename = f"{Path(relative).stem}.pdf"
    return (
        f'<a class="download-link" href="{_escape(_download_url(relative))}" download="{_escape(filename)}">'
        f'Download {_escape(filename)}</a>'
        f'<a class="download-link secondary" href="{_escape(_pdf_url(relative))}" download="{_escape(pdf_filename)}">'
        f'Download PDF</a>'
        f'<div class="deliverable-path">{_escape(relative)}</div>'
    )




def _recent_runs_table_html():
    payload = recent_runs_payload(limit=20)
    runs = payload["recent_runs"]
    if not runs:
        return '<p class="small">No forensic runs recorded yet. Run a fuzz, smoke, evidence, or package command from the CLI to populate this list.</p>'
    rows = []
    for r in runs:
        rs = r.get("resource_snapshot") or {}
        rss = (rs.get("process_rss") or {}).get("delta_bytes")
        wall = rs.get("wall_time_seconds")
        source = r.get("source") or "local"
        is_local = source == "local"
        run_id = r["run_id"]
        # Local runs link to the inspector; remote runs link only to the source description.
        rid_cell = (
            f"<a href=\"/runs/{_escape(run_id)}\"><code>{_escape(run_id)}</code></a>"
            if is_local
            else f"<code>{_escape(run_id)}</code>"
        )
        actions_cell = (
            f"<a href=\"/runs/{_escape(run_id)}\">view</a> &middot; "
            f"<a href=\"/runs/{_escape(run_id)}/bundle\">bundle</a>"
            if is_local
            else f"<span class=\"small\">on <strong>{_escape(source)}</strong></span>"
        )
        rows.append(
            "<tr>"
            f"<td>{rid_cell}</td>"
            f"<td>{_escape(r.get('scenario_id') or '')}</td>"
            f"<td>{_escape(source)}</td>"
            f"<td>{_escape(r.get('runtime') or '')}</td>"
            f"<td>{_escape(r.get('exit_status') or '')}</td>"
            f"<td>{_escape(r.get('ended_at') or '')}</td>"
            f"<td>{_escape(wall if wall is not None else '')}</td>"
            f"<td>{_escape(rss if rss is not None else '')}</td>"
            f"<td>{actions_cell}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Run ID</th><th>Scenario</th><th>Source</th><th>Runtime</th><th>Result</th>"
        "<th>Ended</th><th>Wall (s)</th><th>RSS Δ (bytes)</th><th>Open</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


DEFAULT_TOKEN = "dwarf"


import re as _re


import threading as _threading

_MUTATING_LOCK = _threading.Lock()


def try_acquire_mutating_lock():
    """Non-blocking attempt to acquire the global mutating-action lock.

    Returns True if acquired (caller must release), False if another mutating action is in progress.
    """
    return _MUTATING_LOCK.acquire(blocking=False)


def release_mutating_lock():
    try:
        _MUTATING_LOCK.release()
    except RuntimeError:
        pass


def stream_subprocess_sse(cmd, *, env=None, cwd=None):
    """Spawn a subprocess and yield SSE-formatted bytes chunks.

    Each stdout line becomes a `data: <line>\\n\\n` event. When the process exits, a final
    `event: done\\ndata: {"exit_code": N}\\n\\n` is emitted. stderr is merged into the stream
    so operators see everything the process said.
    """
    import subprocess as _sub
    try:
        proc = _sub.Popen(
            cmd,
            stdout=_sub.PIPE,
            stderr=_sub.STDOUT,
            env=env,
            cwd=cwd,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        payload = json.dumps({"error": str(exc), "exit_code": 126})
        yield ("event: error\ndata: " + payload + "\n\n").encode("utf-8")
        yield b'event: done\ndata: {"exit_code": 126}\n\n'
        return
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield ("data: " + line.rstrip("\n") + "\n\n").encode("utf-8")
        proc.wait()
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except _sub.TimeoutExpired:
                proc.kill()
    exit_code = proc.returncode if proc.returncode is not None else -1
    yield ("event: done\ndata: " + json.dumps({"exit_code": exit_code}) + "\n\n").encode("utf-8")


def resolve_token(*, token=None):
    """Pick the active token from arg / env / default.

    Precedence: explicit `token=` argument > ADA2_DWARF_TOKEN env var > "dwarf".
    """
    if token:
        return token
    env = os.environ.get("ADA2_DWARF_TOKEN")
    if env:
        return env
    return DEFAULT_TOKEN


def check_token(path, *, expected):
    """Validate that the given request path has ?token=<expected>.

    Returns (ok, error_message_or_None). Constant-time comparison.
    """
    import hmac
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    qs = parse_qs(parts.query, keep_blank_values=True)
    provided = qs.get("token", [None])[0]
    if not provided:
        return (False, "missing token query parameter")
    if not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        return (False, "invalid token")
    return (True, None)


def check_form_token(path, body, *, expected):
    """Prefer a submitted form token while retaining query-client compatibility."""
    import hmac
    from urllib.parse import parse_qs

    form = parse_qs(
        body.decode("utf-8", errors="replace"), keep_blank_values=True
    ) if body else {}
    submitted = (form.get("token") or [None])[0]
    if submitted is None:
        return check_token(path, expected=expected)
    if not submitted or not hmac.compare_digest(
        submitted.encode("utf-8"), expected.encode("utf-8")
    ):
        return (False, "invalid token")
    return (True, None)


def _new_definition_success_location(path, body):
    """Return the clean post/redirect/get destination for a successful create."""
    clean = path.split("?", 1)[0]
    form = parse_qs(
        body.decode("utf-8", errors="replace"), keep_blank_values=True
    )
    if clean == "/operate/primitives/new":
        return "/operate/primitives"
    key = "id" if clean == "/operate/targets/new" else "name"
    definition_id = (form.get(key) or [""])[0].strip()
    catalog = clean.split("/")[2]
    return f"/operate/{catalog}/{quote(definition_id, safe='')}"


def check_multipart_form_token(path, content_type, body, *, expected):
    """Prefer a multipart body token while retaining query-client compatibility."""
    import hmac
    from profile_manager.data.bundle_import import (
        _extract_boundary,
        multipart_text_field,
    )

    boundary = _extract_boundary(content_type)
    submitted = (
        multipart_text_field(body, boundary, "token")
        if boundary is not None
        else None
    )
    if submitted is None:
        return check_token(path, expected=expected)
    if not submitted or not hmac.compare_digest(
        submitted.encode("utf-8"), expected.encode("utf-8")
    ):
        return (False, "invalid token")
    return (True, None)


DEFAULT_CLI_ENTRYPOINT = Path(__file__).resolve().parents[1] / "cardano-profile"


def _cli_command(*args):
    return [sys.executable, str(DEFAULT_CLI_ENTRYPOINT), *args]


def _default_cli_command_builder(
    action,
    *,
    profile=None,
    test_id=None,
    approve=False,
    scenario_path=None,
    run_origin=None,
    acknowledge_unknown_version=False,
):
    if action == "deploy":
        if not profile:
            raise ValueError("deploy requires profile")
        command = _cli_command("deploy", profile, "--approve")
        if acknowledge_unknown_version:
            command.append("--acknowledge-unknown-version")
        return command
    if action == "remove":
        return _cli_command("remove", "--approve")
    if action == "fuzz":
        if not test_id:
            raise ValueError("fuzz requires id")
        cmd = _cli_command("fuzz", "run", test_id)
        if approve:
            cmd.append("--approve")
        return cmd
    if action == "smoke":
        if not test_id:
            raise ValueError("smoke requires id")
        return _cli_command("test", "smoke", "run", test_id)
    if action == "compare":
        if not scenario_path:
            raise ValueError("compare requires path")
        command = _cli_command("compare", scenario_path)
        if run_origin:
            command.extend(("--origin", run_origin))
        return command
    if action == "scenario_run":
        if not scenario_path:
            raise ValueError("scenario_run requires path")
        if control_shim_enabled():
            catalog = _scenarios_dir().resolve()
            candidate = Path(scenario_path).resolve()
            if candidate.suffix != ".yaml" or candidate.parent != catalog:
                raise ValueError("scenario_run path must be a YAML file in the scenario catalog")
            if not candidate.is_file():
                raise ValueError("scenario_run path does not exist in the scenario catalog")
            scenario_id = candidate.stem
            config = load_config()
            verb = "scenario-scheduled" if run_origin == "scheduler" else "scenario"
            return render_ssh_command(
                config,
                f"{verb} {scenario_id}",
                verb=(verb, scenario_id),
            )
        command = _cli_command("scenario", "run", scenario_path)
        if run_origin:
            command.extend(("--origin", run_origin))
        return command
    if action == "coverage":
        if not test_id:
            raise ValueError("coverage requires id")
        return _cli_command("coverage", "run", test_id)
    if action == "backup":
        import os as _os
        from datetime import datetime as _dt, timezone as _tz
        from profile_manager.config import storage_roots as _storage_roots
        state_dir = _storage_roots().state
        backups_dir = Path(_os.environ.get("ADA2_DWARF_BACKUPS_DIR") or (Path(state_dir) / "backups"))
        backups_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = backups_dir / f"dwarf-backup-{ts}.tar.gz"
        return _cli_command("backup", "--to", str(dest))
    raise ValueError(f"unknown action: {action}")


_MUTATING_ROUTES = {
    "/api/deploy": "deploy",
    "/api/remove": "remove",
    "/api/fuzz/run": "fuzz",
    "/api/test/smoke/run": "smoke",
    "/api/scenario/run": "scenario_run",
    "/api/scenario/compare": "compare",
    "/api/backup/create": "backup",
    "/api/coverage/run": "coverage",
}


def dispatch_schedule_request(*, method, path, body, expected_token,
                              cli_command_builder=None):
    """Item #19 — handle POST /api/schedule/* endpoints.

    Routes:
      POST /api/schedule/create        (form body: name, scenario_id, scenario_path, cron)
      POST /api/schedule/<id>/pause
      POST /api/schedule/<id>/resume
      POST /api/schedule/<id>/run-now
      POST /api/schedule/<id>/delete

    Returns (status, content_type, body_bytes) or None when path doesn't
    match. All endpoints are token-gated and POST-only; pause/resume/
    delete are idempotent (return 200 even on a no-op).
    """
    import hmac
    from urllib.parse import urlsplit, parse_qs
    from profile_manager.data import schedule_store
    parts = urlsplit(path)
    clean = parts.path
    if not clean.startswith("/api/schedule"):
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    form = parse_qs(
        body.decode("utf-8", errors="replace"), keep_blank_values=True
    ) if body else {}
    submitted_token = (form.get("token") or [None])[0]
    if submitted_token is not None:
        ok = bool(submitted_token) and hmac.compare_digest(
            submitted_token, expected_token
        )
        err = None if ok else "invalid token"
    else:
        # Preserve compatibility for existing API clients while the Basic UI
        # keeps credentials out of URLs, browser history, and request logs.
        ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (
            401,
            "text/plain; charset=utf-8",
            ((err or "invalid token") + "\n").encode("utf-8"),
        )

    if clean == "/api/schedule/create":
        name = (form.get("name") or [""])[0]
        scenario_id = (form.get("scenario_id") or [""])[0]
        scenario_path = (form.get("scenario_path") or [""])[0]
        cron = (form.get("cron") or [""])[0]
        try:
            entry = schedule_store.create_entry(
                name=name, scenario_id=scenario_id,
                scenario_path=scenario_path, cron=cron,
            )
        except ValueError as exc:
            return (400, "text/plain; charset=utf-8", (str(exc) + "\n").encode("utf-8"))
        return (200, "application/json; charset=utf-8",
                json.dumps({"ok": True, "entry": entry}).encode("utf-8"))

    # /api/schedule/<id>/<action>
    rest = clean[len("/api/schedule/"):]
    if "/" not in rest:
        return (404, "text/plain; charset=utf-8", b"not found\n")
    entry_id, _, action = rest.partition("/")
    if not entry_id or "/" in entry_id or ".." in entry_id:
        return (400, "text/plain; charset=utf-8", b"invalid entry id\n")
    if action == "pause":
        result = schedule_store.pause_entry(entry_id)
    elif action == "resume":
        result = schedule_store.resume_entry(entry_id)
    elif action == "delete":
        result = {"deleted": schedule_store.delete_entry(entry_id)}
    elif action == "run-now":
        entry = schedule_store.get_entry(entry_id)
        if entry is None:
            return (404, "text/plain; charset=utf-8", b"unknown entry\n")
        # Synchronous fire — uses the same builder dispatch_mutating_request
        # would use, with the global mutating lock honored.
        if not try_acquire_mutating_lock():
            return (409, "text/plain; charset=utf-8",
                    b"another mutating action is already in progress\n")
        try:
            from profile_manager.data import scheduler
            builder = cli_command_builder or _default_cli_command_builder
            updated = scheduler.fire_entry(entry, command_builder=builder)
            result = updated
        finally:
            release_mutating_lock()
    else:
        return (404, "text/plain; charset=utf-8", b"unknown action\n")
    if result is None:
        return (404, "text/plain; charset=utf-8", b"unknown entry\n")
    return (200, "application/json; charset=utf-8",
            json.dumps({"ok": True, "result": result}).encode("utf-8"))


def dispatch_mutating_request(
    *, method, path, expected_token, body=b"", cli_command_builder=None
):
    """Dispatch a mutating HTTP request.

    Returns (status, content_type, body_or_generator) or None if the path isn't a mutating route.
    For the SSE path the body is a bytes generator; callers must iterate it.
    """
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    clean = parts.path
    action = _MUTATING_ROUTES.get(clean)
    if action is None:
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    ok, err = check_form_token(path, body, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))
    qs = parse_qs(parts.query, keep_blank_values=True)
    form = (
        parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        if body
        else {}
    )
    profile = (qs.get("profile") or [None])[0]
    test_id = (qs.get("id") or [None])[0]
    scenario_path = (
        (form.get("path") or qs.get("path") or [None])[0]
        if action == "compare"
        else (qs.get("path") or [None])[0]
    )
    approve_raw = (qs.get("approve") or [None])[0]
    approve = approve_raw in ("1", "true", "yes")
    acknowledge_unknown_raw = (qs.get("acknowledge_unknown_version") or [None])[0]
    acknowledge_unknown_version = acknowledge_unknown_raw in ("1", "true", "yes")
    if action == "deploy" and not profile:
        return (400, "text/plain; charset=utf-8", b"missing profile query parameter\n")
    if action in ("fuzz", "smoke", "coverage") and not test_id:
        return (400, "text/plain; charset=utf-8", b"missing id query parameter\n")
    if action in ("compare", "scenario_run") and not scenario_path:
        return (400, "text/plain; charset=utf-8", b"missing path query parameter\n")
    if action == "compare":
        from profile_manager.scenario import compare_preflight

        try:
            preflight = compare_preflight(scenario_path)
        except Exception as exc:
            preflight = {
                "ready": False,
                "code": "compare-preflight-invalid",
                "message": str(exc),
                "requirement": None,
            }
        if not preflight["ready"]:
            return (
                409,
                "application/json; charset=utf-8",
                json.dumps(
                    {
                        "ok": False,
                        "error": preflight["code"],
                        "message": preflight["message"],
                        "requirement": preflight["requirement"],
                    },
                    sort_keys=True,
                ).encode("utf-8"),
            )
    if action == "deploy":
        from profile_manager.deployment_versions import (
            DeploymentVersionGateError,
            enforce_deployment_version_gate,
            profile_deployment_version_preview,
        )

        try:
            preview = profile_deployment_version_preview(profile)
            enforce_deployment_version_gate(
                preview, acknowledge_unknown=acknowledge_unknown_version
            )
        except DeploymentVersionGateError as exc:
            body = json.dumps({"ok": False, "error": exc.code, "message": str(exc)}).encode("utf-8")
            return (409, "application/json; charset=utf-8", body)
        except Exception as exc:
            body = json.dumps(
                {"ok": False, "error": "version-preview-failed", "message": str(exc)}
            ).encode("utf-8")
            return (400, "application/json; charset=utf-8", body)
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is already in progress\n")
    builder = cli_command_builder or _default_cli_command_builder
    try:
        builder_kwargs = {
            "profile": profile,
            "test_id": test_id,
            "approve": approve,
            "scenario_path": scenario_path,
        }
        if action == "deploy":
            builder_kwargs["acknowledge_unknown_version"] = acknowledge_unknown_version
        if action in ("scenario_run", "compare"):
            builder_kwargs["run_origin"] = "dashboard"
        cmd = builder(action, **builder_kwargs)
    except Exception as exc:
        release_mutating_lock()
        return (400, "text/plain; charset=utf-8", (f"bad request: {exc}\n").encode("utf-8"))

    def _gen():
        try:
            yield from stream_subprocess_sse(cmd)
        finally:
            release_mutating_lock()
    return (200, "text/event-stream; charset=utf-8", _gen())


def _sse_event(event: str, payload) -> bytes:
    body = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


_EXPECTED_DESIRED_UNSET = object()


def _desired_topology_reusable(observed: dict, expected: dict) -> bool:
    from profile_manager.profiles import deployment_topologies_reusable

    return deployment_topologies_reusable(observed, expected)


def _observed_desired_with_retained_identity(snapshot: dict) -> dict:
    """Read one verified legacy desired identity from its retained substrate."""
    observed = snapshot.get("desired") or {}
    if not isinstance(observed, dict):
        return {}
    if (
        "deployment_sha256" in observed
        or "catalog_reuse_metadata" in observed
    ):
        return observed
    persisted = snapshot.get("persisted") or {}
    substrate = persisted.get("substrate") if isinstance(persisted, dict) else None
    profile_id = str(snapshot.get("profile_id") or snapshot.get("topology_id") or "")
    if (
        not profile_id
        or not isinstance(substrate, dict)
        or str(persisted.get("profile_id") or "") != profile_id
    ):
        return observed
    try:
        from profile_manager.profiles import verified_desired_topology_from_legacy

        upgraded = verified_desired_topology_from_legacy(
            profile_id, substrate, observed
        )
    except (KeyError, TypeError, ValueError):
        return observed
    return upgraded if isinstance(upgraded, dict) else observed


def _current_run_plan_desired_topology(plan: dict) -> dict | None:
    """Build the desired topology bound to the resolved plan's catalog identity."""
    profile = plan.get("profile") or {}
    profile_snapshot = profile.get("snapshot")
    catalog_revision = str((plan.get("versions") or {}).get("catalog_revision") or "")
    if not profile_snapshot or not catalog_revision:
        return None
    try:
        from profile_manager.deployment_versions import build_deployment_version_preview
        from profile_manager.profiles import Profile, desired_topology_for_profile

        exact_profile = Profile.from_dict(profile_snapshot)
        exact_preview = build_deployment_version_preview(
            vars(exact_profile),
            operator_registration_id=(plan.get("versions") or {}).get(
                "operator_registration_id"
            ),
        )
        frozen_overlay_revision = (plan.get("versions") or {}).get(
            "operator_overlay_revision"
        )
        if exact_preview.get("operator_overlay_revision") != frozen_overlay_revision:
            return None
        if exact_preview.get("catalog_revision") != catalog_revision:
            return None
        desired = desired_topology_for_profile(exact_profile, exact_preview)
        if desired.get("catalog_revision") != catalog_revision:
            return None
        return desired
    except (KeyError, TypeError, ValueError):
        return None


def _refresh_run_plan_after_trusted_discovery(
    plan: dict,
    expected_desired: dict,
    *,
    capability_context: dict | None = None,
) -> tuple[dict, str, dict] | None:
    """Re-resolve one unchanged request after directional catalog enrichment.

    This is deliberately narrower than general plan equivalence.  Everything
    outside resolver-owned version metadata must remain byte-for-byte stable,
    operator selection state must remain exact, and the shared deployment
    predicate must prove that the new catalog view is only trusted enrichment
    of the topology that was just applied.
    """
    from profile_manager.run_plan import (
        RunPlanRequest,
        digest_run_plan,
        resolve_run_plan,
        stable_run_plan,
    )

    try:
        request = RunPlanRequest.from_mapping(plan.get("request"))
        refreshed = resolve_run_plan(
            request, capability_context=capability_context
        )
        refreshed_desired = _current_run_plan_desired_topology(refreshed)
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(refreshed_desired, dict) or not _desired_topology_reusable(
        expected_desired, refreshed_desired
    ):
        return None

    # ``stable_run_plan`` intentionally returns a shallow projection.  Detach
    # it before normalizing duplicated resolver bindings so the frozen plans
    # emitted to the browser and later submitted to Run remain immutable.
    before = json.loads(json.dumps(stable_run_plan(plan)))
    after = json.loads(json.dumps(stable_run_plan(refreshed)))
    before_versions = before.pop("versions", None)
    after_versions = after.pop("versions", None)
    if not isinstance(before_versions, dict) or not isinstance(after_versions, dict):
        return None

    def remove_bound_catalog_revision(
        stable: dict, versions: dict
    ) -> bool:
        measurements = stable.get("measurements")
        identity = (
            measurements.get("target_identity")
            if isinstance(measurements, dict)
            else None
        )
        if not isinstance(identity, dict):
            return True
        present = "version_catalog_revision" in identity
        value = identity.pop("version_catalog_revision", None)
        if not present:
            return True
        return bool(
            isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value)
            and value == versions.get("catalog_revision")
        )

    before_binding_present = bool(
        isinstance((before.get("measurements") or {}).get("target_identity"), dict)
        and "version_catalog_revision"
        in (before.get("measurements") or {})["target_identity"]
    )
    after_binding_present = bool(
        isinstance((after.get("measurements") or {}).get("target_identity"), dict)
        and "version_catalog_revision"
        in (after.get("measurements") or {})["target_identity"]
    )
    if (
        before_binding_present != after_binding_present
        or not remove_bound_catalog_revision(before, before_versions)
        or not remove_bound_catalog_revision(after, after_versions)
        or before != after
    ):
        return None
    resolver_owned_changes = {
        "catalog_revision",
        "topology_plan_sha256",
        "expected_desired_topology",
        "resolved",
        "supporting",
    }
    before_invariants = {
        key: value
        for key, value in before_versions.items()
        if key not in resolver_owned_changes
    }
    after_invariants = {
        key: value
        for key, value in after_versions.items()
        if key not in resolver_owned_changes
    }
    if before_invariants != after_invariants:
        return None
    return refreshed, digest_run_plan(refreshed), refreshed_desired


def _run_plan_preparation_status(
    plan: dict,
    plan_digest: str,
    health: dict | None,
    *,
    expected_desired=_EXPECTED_DESIRED_UNSET,
) -> dict:
    """Return the exact server-owned gates that allow a resolved plan to run."""
    readiness = plan.get("readiness") or {}
    profile = plan.get("profile") or {}
    profile_id = str(readiness.get("profile_id") or profile.get("id") or "")
    topology_id = str(readiness.get("topology_id") or "")
    availability = plan.get("availability") or {}
    availability_state = str(availability.get("state") or "ready")
    availability_ready = availability_state in {"ready", "pullable"}

    measurements = plan.get("measurements") or {}
    requested = [row for row in (measurements.get("requested") or []) if row.get("enabled")]
    resolved = measurements.get("resolved") or []
    incompatible = measurements.get("incompatible") or []
    measurement_compatible = bool(
        not incompatible and (not requested or len(resolved) == len(requested))
    )

    versions = plan.get("versions") or {}
    releases = list((versions.get("resolved") or {}).values())

    def immutable_release(row):
        if not isinstance(row, dict):
            return False
        if row.get("image_digest") or row.get("executable_digest"):
            return True
        registration = row.get("operator_registration")
        if not isinstance(registration, dict):
            return False
        try:
            from profile_manager.deployment_versions import (
                DeploymentVersionGateError,
                operator_registration_immutable_identity,
            )

            identity = operator_registration_immutable_identity(registration)
        except (DeploymentVersionGateError, KeyError, TypeError, ValueError):
            return False
        return bool(
            identity["registration_id"]
            == versions.get("operator_registration_id")
            and row.get("implementation") == identity["implementation"]
            and row.get("version") == identity["version"]
            and row.get("source_revision") == identity["source_revision"]
            and row.get("image_reference") == identity["config_image_id"]
            and row.get("image_config_id") == identity["config_image_id"]
        )

    immutable_targets = all(immutable_release(row) for row in releases)

    current_health = health or {}
    reconciliation = current_health.get("reconciliation") or {}
    current_desired = (
        _current_run_plan_desired_topology(plan)
        if expected_desired is _EXPECTED_DESIRED_UNSET
        else expected_desired
    )
    expected_topology_plan_sha256 = (
        current_desired.get("plan_sha256")
        if isinstance(current_desired, dict)
        else None
    )
    retained_desired = current_health.get("desired") or {}
    observed_desired = _observed_desired_with_retained_identity(current_health)
    observed_topology_plan_sha256 = retained_desired.get("plan_sha256")
    expected_topology_deployment_sha256 = (
        current_desired.get("deployment_sha256")
        if isinstance(current_desired, dict)
        else None
    )
    observed_topology_deployment_sha256 = observed_desired.get(
        "deployment_sha256"
    )
    desired_topology_reusable = bool(
        isinstance(current_desired, dict)
        and _desired_topology_reusable(observed_desired, current_desired)
    )
    if profile_id:
        fresh = (current_health.get("health") or {}).get("fresh") is True
        semantic_health = bool(
            current_health.get("profile_id") == profile_id
            and current_health.get("state") == "healthy"
            and fresh
        )
        topology_identity = bool(
            semantic_health
            and reconciliation.get("state") == "converged"
            and reconciliation.get("persisted_plan_matches") is True
            and not reconciliation.get("missing_node_ids")
            and not reconciliation.get("unexpected_managed_node_ids")
            and not reconciliation.get("mismatched_nodes")
            and not reconciliation.get("not_running_node_ids")
            and not reconciliation.get("duplicate_managed_node_ids")
            and not reconciliation.get("unmanaged_conflict_node_ids")
            and not reconciliation.get("persisted_mismatched_fields")
            and desired_topology_reusable
        )
    elif topology_id:
        fresh = (current_health.get("health") or {}).get("fresh") is True
        semantic_health = current_health.get("state") == "healthy"
        topology_identity = semantic_health
    else:
        fresh = True
        semantic_health = True
        topology_identity = True

    ready = bool(
        availability_ready
        and immutable_targets
        and measurement_compatible
        and semantic_health
        and topology_identity
    )
    reason_code = None
    if not availability_ready:
        reason_code = f"availability_{availability_state}"
    elif not immutable_targets:
        reason_code = "target_identity_not_immutable"
    elif not measurement_compatible:
        reason_code = "measurement_incompatible"
    elif not semantic_health:
        reason_code = current_health.get("reason_code") or "semantic_health_not_ready"
    elif not topology_identity:
        reason_code = reconciliation.get("reason_code") or "topology_identity_mismatch"

    measurement_profile = measurements.get("profile") or {}
    return {
        "state": "ready" if ready else "blocked",
        "plan_digest": plan_digest,
        "profile_id": profile_id or None,
        "topology_id": topology_id or profile_id or None,
        "fresh": fresh,
        "semantic_health": semantic_health,
        "topology_identity": topology_identity,
        "expected_topology_plan_sha256": expected_topology_plan_sha256,
        "observed_topology_plan_sha256": observed_topology_plan_sha256,
        "expected_topology_deployment_sha256": expected_topology_deployment_sha256,
        "observed_topology_deployment_sha256": observed_topology_deployment_sha256,
        "desired_topology_reusable": desired_topology_reusable,
        "measurement_compatible": measurement_compatible,
        "measurement_count": len(resolved),
        "measurement_profile_id": measurement_profile.get("id"),
        "availability_state": availability_state,
        "immutable_targets": immutable_targets,
        "reason_code": reason_code,
        "health": current_health,
    }


def _prepare_run_plan(
    plan: dict,
    plan_digest: str,
    *,
    action_service=None,
    snapshot_loader=None,
    health_requester=None,
    health_waiter=None,
    retry_sleeper=None,
    readiness_timeout: float = 120,
    destructive_confirmation: dict | None = None,
    capability_context: dict | None = None,
):
    """Prepare a resolved run through the reviewed topology action adapter."""
    from profile_manager.data.operate_topology_health import (
        _default_probe,
        request_topology_health_check,
        wait_for_topology_health,
    )
    from profile_manager.topology_actions import TopologyActionService

    readiness = plan.get("readiness") or {}
    profile = plan.get("profile") or {}
    profile_id = str(readiness.get("profile_id") or profile.get("id") or "")
    selected_snapshot = snapshot_loader or _default_probe
    selected_requester = health_requester or request_topology_health_check
    selected_waiter = health_waiter or wait_for_topology_health
    selected_sleeper = retry_sleeper or time.sleep
    actions = action_service or TopologyActionService()
    operator_registration_id = (plan.get("versions") or {}).get(
        "operator_registration_id"
    )

    if not profile_id:
        status = _run_plan_preparation_status(plan, plan_digest, None)
        yield {"stage": status["state"], **status}
        return

    yield {
        "stage": "checking",
        "state": "checking",
        "plan_digest": plan_digest,
        "profile_id": profile_id,
        "detail": "Collecting fresh semantic topology and identity evidence.",
    }
    profile_snapshot = profile.get("snapshot")
    snapshot = (
        selected_snapshot(
            profile_id,
            profile_snapshot=profile_snapshot,
            operator_registration_id=operator_registration_id,
        )
        if snapshot_loader is None
        else selected_snapshot(profile_id)
    )
    current_desired = _current_run_plan_desired_topology(plan)
    status = _run_plan_preparation_status(
        plan,
        plan_digest,
        snapshot,
        expected_desired=current_desired,
    )
    yield {"stage": "readiness", **status}
    desired_plan_sha256 = (
        current_desired.get("plan_sha256")
        if isinstance(current_desired, dict)
        else None
    )
    if not isinstance(desired_plan_sha256, str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", desired_plan_sha256
    ) is None:
        yield {
            "stage": "blocked",
            **status,
            "detail": (
                "The selected run plan has no trustworthy desired topology identity; "
                "no topology action was attempted."
            ),
        }
        return

    def selected_desired_still_current() -> bool:
        latest = _current_run_plan_desired_topology(plan)
        return bool(
            isinstance(latest, dict)
            and latest.get("plan_sha256") == desired_plan_sha256
        )

    if status["state"] == "ready":
        if not selected_desired_still_current():
            yield {
                "stage": "blocked",
                **status,
                "detail": (
                    "The selected version or preference changed during topology "
                    "verification; resolve and Prepare the current selection again."
                ),
            }
            return
        yield {"stage": "ready", **status}
        return

    # A dashboard/controller restart can leave the retained topology identity
    # exact while its last semantic-health receipt has aged out.  Refresh that
    # exact topology before selecting a repair action; scenario or tap changes
    # must not turn stale evidence into an unnecessary mutation.
    if (
        isinstance(current_desired, dict)
        and _desired_topology_reusable(
            _observed_desired_with_retained_identity(snapshot), current_desired
        )
        and (snapshot.get("health") or {}).get("fresh") is not True
    ):
        requested = (
            selected_requester(
                profile_id=profile_id,
                desired_plan_sha256=desired_plan_sha256,
                probe=lambda: _default_probe(
                    profile_id,
                    profile_snapshot=profile_snapshot,
                    operator_registration_id=operator_registration_id,
                ),
            )
            if health_requester is None
            else selected_requester(
                profile_id=profile_id,
                desired_plan_sha256=desired_plan_sha256,
            )
        )
        if requested.get("state") == "conflict":
            yield {
                "stage": "blocked",
                **status,
                "detail": "A different topology health check is already active; retry Prepare after it finishes.",
            }
            return
        refresh_deadline = time.monotonic() + readiness_timeout
        while True:
            remaining = max(0.0, refresh_deadline - time.monotonic())
            refreshed = selected_waiter(
                timeout=min(30.0, max(0.1, remaining)),
                profile_id=profile_id,
                desired_plan_sha256=desired_plan_sha256,
            )
            if refreshed.get("state") == "conflict":
                yield {
                    "stage": "blocked",
                    **status,
                    "detail": (
                        "A topology health result for a different frozen desired "
                        "identity is active or retained; no topology action was attempted."
                    ),
                }
                return
            if refreshed.get("state") != "checking":
                if not selected_desired_still_current():
                    yield {
                        "stage": "blocked",
                        **status,
                        "detail": (
                            "The selected version or preference changed during topology "
                            "verification; resolve and Prepare the current selection again."
                        ),
                    }
                    return
                snapshot = refreshed
                status = _run_plan_preparation_status(
                    plan,
                    plan_digest,
                    snapshot,
                    expected_desired=current_desired,
                )
                yield {"stage": "readiness", **status}
                if status["state"] == "ready":
                    yield {"stage": "ready", **status}
                    return
                break
            if time.monotonic() >= refresh_deadline:
                yield {
                    "stage": "blocked",
                    **status,
                    "detail": "Fresh semantic topology health did not complete before the preparation deadline; no repair was attempted.",
                }
                return
            yield {
                "stage": "waiting",
                "state": "checking",
                "plan_digest": plan_digest,
                "profile_id": profile_id,
                "reason_code": status.get("reason_code"),
                "detail": "The retained topology identity is exact; waiting for fresh semantic health before considering repair.",
            }
            selected_sleeper(
                min(2.0, max(0.0, refresh_deadline - time.monotonic()))
            )

    if not selected_desired_still_current():
        yield {
            "stage": "blocked",
            **status,
            "detail": (
                "The selected version or preference changed before topology action "
                "selection; resolve and Prepare the current selection again."
            ),
        }
        return

    # Topology actions can only repair topology readiness.  If the selected
    # topology is already semantically healthy and identity-exact, preserve
    # the remaining run-plan blocker instead of offering a destructive action
    # that cannot resolve it (for example, an unavailable target artifact).
    if (
        status.get("state") != "ready"
        and status.get("semantic_health") is True
        and status.get("topology_identity") is True
    ):
        reason_code = str(status.get("reason_code") or "remaining_run_plan_gate")
        yield {
            "stage": "blocked",
            **status,
            "detail": (
                "The selected topology is healthy and identity-exact; "
                f"topology mutation cannot resolve {reason_code}."
            ),
        }
        return

    policies = snapshot.get("actions") or {}
    action_candidates = ("deploy", "repair", "redeploy")
    if isinstance(current_desired, dict) and not _desired_topology_reusable(
        _observed_desired_with_retained_identity(snapshot), current_desired
    ):
        # Repair is constrained to the retained deployment contract.  When
        # the selected immutable identity changes, choose the reviewed
        # replacement path even if fresh unhealthy evidence also makes the
        # narrower repair action available.
        action_candidates = ("deploy", "redeploy", "repair")
    action = next(
        (
            candidate
            for candidate in action_candidates
            if (policies.get(candidate) or {}).get("allowed") is True
        ),
        None,
    )
    if action is None:
        yield {
            "stage": "blocked",
            **status,
            "detail": "Preparation cannot safely mutate this topology automatically. Open deployment status for the exact allowed actions.",
            "actions": policies,
        }
        return
    if current_desired is None:
        yield {
            "stage": "blocked",
            **status,
            "detail": "The current resolved plan identity is unavailable; DWARF will not preview a topology mutation.",
            "actions": policies,
        }
        return

    confirmation = destructive_confirmation or {}
    confirmation_matches = bool(
        isinstance(confirmation, dict)
        and confirmation.get("action") == action
        and confirmation.get("profile_id") == profile_id
        and confirmation.get("run_plan_digest") == plan_digest
        and isinstance(confirmation.get("topology_plan_id"), str)
        and isinstance(confirmation.get("topology_plan_digest"), str)
        and isinstance(confirmation.get("execution_id"), str)
    )
    action_snapshot = {**snapshot, "desired": current_desired}
    confirmed_preview = bool(
        confirmation_matches
        and actions.preview_binding_reusable(
            confirmation["topology_plan_id"],
            confirmation["topology_plan_digest"],
            confirmation["execution_id"],
            action=action,
            topology_id=profile_id,
            snapshot=action_snapshot,
        )
    )
    topology_plan = (
        {
            "action": action,
            "plan_id": confirmation["topology_plan_id"],
            "plan_digest": confirmation["topology_plan_digest"],
            "execution_id": confirmation["execution_id"],
            "confirmation_required": True,
        }
        if confirmed_preview
        else actions.preview(action, action_snapshot)
    )
    planned_action = str(topology_plan.get("action") or action)
    confirmation_text = f"{planned_action.upper()} {profile_id}"
    confirmation_binding = {
        "action": planned_action,
        "profile_id": profile_id,
        "run_plan_digest": plan_digest,
        "topology_plan_id": topology_plan.get("plan_id"),
        "topology_plan_digest": topology_plan.get("plan_digest"),
        "execution_id": topology_plan.get("execution_id"),
    }
    if topology_plan.get("confirmation_required") and not confirmed_preview:
        yield {
            "stage": "blocked",
            **status,
            "detail": "The required topology action is destructive and needs explicit confirmation here before DWARF applies it.",
            "action": planned_action,
            "topology_plan_id": topology_plan.get("plan_id"),
            "confirmation_required": True,
            "confirmation_text": confirmation_text,
            "confirmation_binding": confirmation_binding,
        }
        return
    yield {
        "stage": "topology_preview",
        "state": "planned",
        "action": planned_action,
        "profile_id": profile_id,
        "plan_digest": plan_digest,
        "topology_plan_id": topology_plan.get("plan_id"),
        "topology_plan_digest": topology_plan.get("plan_digest"),
        "execution_id": topology_plan.get("execution_id"),
    }

    terminal = None
    apply_kwargs = {"expected_execution_id": topology_plan.get("execution_id")}
    if topology_plan.get("confirmation_required"):
        apply_kwargs["confirmation"] = f"{planned_action.upper()} {profile_id}"
    for event in actions.apply(
        topology_plan["plan_id"],
        topology_plan["plan_digest"],
        **apply_kwargs,
    ):
        terminal = event if event.get("stage") == "completed" else terminal
        yield {
            "stage": "topology",
            "state": event.get("status") or event.get("controller_state") or event.get("stage"),
            "action": planned_action,
            "profile_id": profile_id,
            "plan_digest": plan_digest,
            "topology_plan_id": topology_plan.get("plan_id"),
            "execution_id": event.get("execution_id") or topology_plan.get("execution_id"),
            "audit_receipt": event.get("audit_receipt"),
        }
    if (
        not terminal
        or terminal.get("status") != "applied"
        or not terminal.get("audit_receipt")
    ):
        yield {
            "stage": "blocked",
            **status,
            "detail": "Topology execution ended without a verified applied terminal receipt.",
            "execution_id": topology_plan.get("execution_id"),
        }
        return

    deadline = time.monotonic() + readiness_timeout
    health_check_in_flight = False
    while True:
        if not health_check_in_flight:
            requested = (
                selected_requester(
                    profile_id=profile_id,
                    desired_plan_sha256=desired_plan_sha256,
                    probe=lambda: _default_probe(
                        profile_id,
                        profile_snapshot=profile_snapshot,
                        operator_registration_id=operator_registration_id,
                    ),
                )
                if health_requester is None
                else selected_requester(
                    profile_id=profile_id,
                    desired_plan_sha256=desired_plan_sha256,
                )
            )
            if requested.get("state") == "conflict":
                yield {
                    "stage": "blocked",
                    **status,
                    "detail": "A different topology health check is already active; retry Prepare after it finishes.",
                }
                return
            health_check_in_flight = True
        remaining = max(0.0, deadline - time.monotonic())
        health = selected_waiter(
            timeout=min(30.0, max(0.1, remaining)),
            profile_id=profile_id,
            desired_plan_sha256=desired_plan_sha256,
        )
        if health.get("state") == "conflict":
            yield {
                "stage": "blocked",
                **status,
                "detail": (
                    "Post-action health belongs to a different frozen desired identity; "
                    "the applied action was not accepted as ready."
                ),
                "topology_execution_id": terminal.get("execution_id"),
                "topology_audit_receipt": terminal.get("audit_receipt"),
            }
            return
        if health.get("state") != "checking" and not selected_desired_still_current():
            refreshed = _refresh_run_plan_after_trusted_discovery(
                plan,
                current_desired,
                capability_context=capability_context,
            )
            if refreshed is None:
                yield {
                    "stage": "blocked",
                    **status,
                    "detail": (
                        "The selected version or preference changed during post-action "
                        "health verification; the applied action was not accepted as ready."
                    ),
                    "topology_execution_id": terminal.get("execution_id"),
                    "topology_audit_receipt": terminal.get("audit_receipt"),
                }
                return
            previous_plan_digest = plan_digest
            plan, plan_digest, current_desired = refreshed
            desired_plan_sha256 = current_desired["plan_sha256"]
            operator_registration_id = (plan.get("versions") or {}).get(
                "operator_registration_id"
            )
            yield {
                "stage": "plan_refresh",
                "state": "resolved",
                "plan": plan,
                "previous_plan_digest": previous_plan_digest,
                "plan_digest": plan_digest,
                "profile_id": profile_id,
                "detail": (
                    "Trusted discovery metadata changed while topology health was "
                    "running; the same user selection was re-resolved without mutation."
                ),
            }
        if health.get("state") != "checking":
            # Consume the exact completed result before another check can replace
            # it. A probe may finish in the short gap between wait timeout and
            # this loop's retry sleep; re-requesting first would hide that result.
            health_check_in_flight = False
        final = _run_plan_preparation_status(
            plan,
            plan_digest,
            health,
            expected_desired=current_desired,
        )
        final["topology_action"] = planned_action
        final["topology_execution_id"] = terminal.get("execution_id")
        final["topology_audit_receipt"] = terminal.get("audit_receipt")
        if final["state"] == "ready" or time.monotonic() >= deadline:
            yield {"stage": final["state"], **final}
            return
        yield {
            "stage": "waiting",
            "state": "checking",
            "plan_digest": plan_digest,
            "profile_id": profile_id,
            "reason_code": final.get("reason_code"),
            "detail": "Topology is deployed; waiting for fresh semantic node and chain readiness.",
            "topology_execution_id": terminal.get("execution_id"),
            "topology_audit_receipt": terminal.get("audit_receipt"),
        }
        selected_sleeper(min(2.0, max(0.0, deadline - time.monotonic())))


def _default_run_preflight(launch_id: str, plan: dict) -> dict:
    readiness = plan["readiness"]
    if readiness.get("topology_id"):
        from profile_manager.data.operate_topology_health import (
            request_topology_health_check,
            wait_for_topology_health,
        )

        request_topology_health_check()
        health = wait_for_topology_health(timeout=120)
        return {
            "state": "ready" if health.get("state") == "healthy" else "blocked",
            "kind": "topology",
            "topology_id": readiness["topology_id"],
            "health": health,
            "checks": [
                {
                    "id": f"topology:{readiness['topology_id']}",
                    "passed": health.get("state") == "healthy",
                    "reason": health.get("reason_code"),
                }
            ],
        }
    if plan.get("profile"):
        from profile_manager.data.operate_topology_health import (
            _default_probe,
            request_topology_health_check,
            wait_for_topology_health,
        )

        profile_id = str(readiness.get("profile_id") or plan["profile"].get("id") or "")
        profile_snapshot = plan["profile"].get("snapshot")
        operator_registration_id = (plan.get("versions") or {}).get(
            "operator_registration_id"
        )
        current_desired = _current_run_plan_desired_topology(plan)
        desired_plan_sha256 = (
            current_desired.get("plan_sha256")
            if isinstance(current_desired, dict)
            else None
        )
        if not isinstance(desired_plan_sha256, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", desired_plan_sha256
        ) is None:
            return {
                "state": "blocked",
                "kind": "profile",
                "profile_id": profile_id,
                "health": None,
                "checks": [
                    {
                        "id": "deployed-version-identity",
                        "passed": False,
                        "reason": "current desired topology identity is unavailable",
                    }
                ],
            }
        requested = request_topology_health_check(
            profile_id=profile_id,
            desired_plan_sha256=desired_plan_sha256,
            probe=lambda: _default_probe(
                profile_id,
                profile_snapshot=profile_snapshot,
                operator_registration_id=operator_registration_id,
            ),
        )
        if requested.get("state") == "conflict":
            return {
                "state": "blocked",
                "kind": "profile",
                "profile_id": profile_id,
                "health": requested,
                "checks": [
                    {
                        "id": f"profile:{profile_id}",
                        "passed": False,
                        "reason": requested.get("reason_code"),
                    },
                    {
                        "id": "deployed-version-identity",
                        "passed": False,
                        "reason": "exact profile health was not observed",
                    },
                ],
            }
        health = wait_for_topology_health(
            timeout=120,
            profile_id=profile_id,
            desired_plan_sha256=desired_plan_sha256,
        )
        latest_desired = _current_run_plan_desired_topology(plan)
        if (
            not isinstance(latest_desired, dict)
            or latest_desired.get("plan_sha256") != desired_plan_sha256
        ):
            return {
                "state": "blocked",
                "kind": "profile",
                "profile_id": profile_id,
                "health": health,
                "checks": [
                    {
                        "id": "deployed-version-identity",
                        "passed": False,
                        "reason": (
                            "selected topology identity changed during health verification"
                        ),
                    }
                ],
            }
        from profile_manager.run_plan import digest_run_plan

        preparation = _run_plan_preparation_status(
            plan,
            digest_run_plan(plan),
            health,
            expected_desired=current_desired,
        )
        profile_ready = bool(
            preparation["semantic_health"] and preparation["fresh"]
        )
        identity_ready = preparation["topology_identity"]
        return {
            "state": "ready" if preparation["state"] == "ready" else "blocked",
            "kind": "profile",
            "profile_id": profile_id,
            "health": health,
            "checks": [
                {
                    "id": f"profile:{profile_id}",
                    "passed": profile_ready,
                    "reason": health.get("reason_code"),
                },
                {
                    "id": "deployed-version-identity",
                    "passed": identity_ready,
                    "reason": (health.get("reconciliation") or {}).get("reason_code"),
                },
                {
                    "id": "measurement-compatibility",
                    "passed": preparation["measurement_compatible"],
                    "reason": preparation.get("reason_code"),
                },
            ],
        }
    return {
        "state": "ready",
        "kind": "self-contained",
        "checks": [{"id": "framework", "passed": True}],
    }


def dispatch_run_prepare_request(
    *, method, path, body, expected_token, preparer=None, request_controller=None
):
    """Re-resolve and stream one exact server-owned Prepare operation."""
    if urlsplit(path).path != "/api/run/prepare":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":{"message":"use POST"}}')
    ok, error = check_token(path, expected=expected_token)
    if not ok:
        payload = {"ok": False, "error": {"field": "token", "message": error}}
        return (401, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    if len(body) > 8192:
        payload = {"ok": False, "error": {"field": "request", "message": "request body is too large"}}
        return (413, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    try:
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict) or set(value) - {
            "request", "plan_digest", "destructive_confirmation"
        }:
            raise ValueError(
                "prepare body must contain request, plan_digest, and optional destructive_confirmation only"
            )
        confirmation = value.get("destructive_confirmation")
        confirmation_fields = {
            "action", "profile_id", "run_plan_digest", "topology_plan_id",
            "topology_plan_digest", "execution_id",
        }
        if confirmation is not None and (
            not isinstance(confirmation, dict)
            or set(confirmation) != confirmation_fields
            or any(
                not isinstance(item, str) or not item or len(item) > 256
                for item in confirmation.values()
            )
        ):
            raise ValueError(
                "destructive_confirmation must be an exact bounded preview binding or null"
            )
        from profile_manager.run_plan import (
            RunPlanRequest,
            digest_run_plan,
            resolve_run_plan,
        )
        from profile_manager.data.operate_run_wizard import (
            operator_registration_capability_context,
        )

        request = RunPlanRequest.from_mapping(value.get("request"))
        capability_context = operator_registration_capability_context(
            request, request_controller=request_controller
        )
        plan = resolve_run_plan(
            request, capability_context=capability_context
        )
        current_digest = digest_run_plan(plan)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        field = getattr(exc, "field", "request")
        payload = {"ok": False, "error": {"field": field, "message": str(exc)}}
        return (400, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    if value.get("plan_digest") != current_digest:
        payload = {
            "ok": False,
            "error": {
                "field": "plan_digest",
                "message": "The catalog or selection changed. Resolve the plan again before preparation.",
            },
        }
        return (409, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    selected_preparer = preparer or _prepare_run_plan

    def generate():
        ready = False
        active_digest = current_digest
        lock_acquired = try_acquire_mutating_lock()
        if not lock_acquired:
            yield _sse_event(
                "error",
                {
                    "state": "blocked",
                    "error": "mutation_in_progress",
                    "detail": "another mutating action is already in progress",
                    "plan_digest": current_digest,
                },
            )
            yield _sse_event(
                "done", {"state": "blocked", "plan_digest": current_digest}
            )
            return
        try:
            yield _sse_event(
                "plan",
                {
                    "state": "resolved",
                    "plan_digest": current_digest,
                    "scenario_id": plan["scenario"]["id"],
                    "profile_id": (plan.get("profile") or {}).get("id"),
                },
            )
            try:
                events = (
                    selected_preparer(
                        plan,
                        current_digest,
                        destructive_confirmation=value.get("destructive_confirmation"),
                        capability_context=capability_context,
                    )
                    if preparer is None
                    else selected_preparer(plan, current_digest)
                )
                for event in events:
                    stage = str(event.get("stage") or "progress")
                    if stage == "plan_refresh":
                        refreshed_plan = event.get("plan")
                        candidate_digest = event.get("plan_digest")
                        if (
                            not isinstance(refreshed_plan, dict)
                            or event.get("previous_plan_digest") != active_digest
                            or refreshed_plan.get("request") != plan.get("request")
                            or candidate_digest != digest_run_plan(refreshed_plan)
                        ):
                            raise ValueError(
                                "preparer emitted an invalid authoritative plan refresh"
                            )
                        active_digest = candidate_digest
                    ready = bool(
                        stage == "ready"
                        and event.get("state") == "ready"
                        and event.get("plan_digest") == active_digest
                    )
                    yield _sse_event(stage, event)
            except Exception as exc:  # noqa: BLE001 - retain bounded preparation failure
                ready = False
                yield _sse_event(
                    "error",
                    {
                        "state": "blocked",
                        "error": "prepare_failed",
                        "detail": f"{type(exc).__name__}: {str(exc)[:512]}",
                        "plan_digest": active_digest,
                    },
                )
            yield _sse_event(
                "done",
                {"state": "ready" if ready else "blocked", "plan_digest": active_digest},
            )
        finally:
            if lock_acquired:
                release_mutating_lock()

    return (200, "text/event-stream; charset=utf-8", generate())


def dispatch_run_start_request(
    *,
    method,
    path,
    body,
    expected_token,
    preflight_runner=None,
    command_builder=None,
    streamer=None,
    request_controller=None,
):
    """Re-resolve, preflight, and stream one identifier-only local launch."""
    if urlsplit(path).path != "/api/run/start":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":{"message":"use POST"}}')
    ok, error = check_token(path, expected=expected_token)
    if not ok:
        payload = {"ok": False, "error": {"field": "token", "message": error}}
        return (401, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    if len(body) > 8192:
        payload = {"ok": False, "error": {"field": "request", "message": "request body is too large"}}
        return (413, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    try:
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict) or set(value) - {"request", "plan_digest"}:
            raise ValueError("start body must contain request and plan_digest only")
        from profile_manager.run_plan import (
            RunPlanRequest,
            digest_run_plan,
            resolve_run_plan,
        )
        from profile_manager.data.operate_run_wizard import (
            operator_registration_capability_context,
        )

        request = RunPlanRequest.from_mapping(value.get("request"))
        capability_context = operator_registration_capability_context(
            request, request_controller=request_controller
        )
        plan = resolve_run_plan(
            request, capability_context=capability_context
        )
        current_digest = digest_run_plan(plan)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        field = getattr(exc, "field", "request")
        payload = {"ok": False, "error": {"field": field, "message": str(exc)}}
        return (400, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    if value.get("plan_digest") != current_digest:
        payload = {
            "ok": False,
            "error": {
                "field": "plan_digest",
                "message": "The catalog or selection changed. Resolve the plan again before launch.",
            },
        }
        return (409, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    if _MUTATING_LOCK.locked():
        payload = {"ok": False, "error": {"field": "launch", "message": "another mutating action is already in progress"}}
        return (409, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    selected_preflight = preflight_runner or _default_run_preflight
    selected_streamer = streamer or stream_subprocess_sse
    controller_configured = bool(
        os.environ.get("DWARF_CONTROLLER_URL")
        and os.environ.get("DWARF_CONTROLLER_TOKEN")
    )
    if request_controller is None and controller_configured:
        from profile_manager.topology_actions import controller_request

        request_controller = controller_request

    def default_command(launch_id):
        import shutil as _shutil

        from profile_manager.launch_store import load_launch
        from profile_manager.remote import render_launch_command

        if control_shim_enabled():
            return render_launch_command(load_config(), launch_id)
        stored = load_launch(launch_id)
        return [
            _shutil.which("env") or "/usr/bin/env",
            f"ADA2_DWARF_LAUNCH_ROOT={stored['directory'].parent}",
            f"ADA2_DWARF_LAUNCH_ID={launch_id}",
            *_cli_command(
                "scenario",
                "run",
                str(stored["scenario_path"]),
                "--origin",
                "dashboard",
            ),
        ]

    selected_command = command_builder or default_command

    def generate():
        lock_acquired = try_acquire_mutating_lock()
        if not lock_acquired:
            yield _sse_event(
                "error",
                {
                    "error": "mutation_in_progress",
                    "detail": "another mutating action is already in progress",
                },
            )
            yield _sse_event("done", {"exit_code": 75})
            return
        try:
            try:
                from profile_manager.launch_store import create_launch

                launch = create_launch(plan)
            except Exception as exc:  # noqa: BLE001 - stream bounded launch failure
                yield _sse_event(
                    "error",
                    {
                        "error": "launch_creation_failed",
                        "detail": f"{type(exc).__name__}: {str(exc)[:512]}",
                    },
                )
                yield _sse_event("done", {"exit_code": 74})
                return
            yield _sse_event(
                "plan",
                {"launch_id": launch["launch_id"], "plan_digest": current_digest},
            )
            try:
                preflight = selected_preflight(launch["launch_id"], plan)
            except Exception as exc:  # noqa: BLE001 - retain failed readiness as evidence
                preflight = {
                    "state": "blocked",
                    "checks": [],
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            from profile_manager.launch_store import record_launch_preflight

            record_launch_preflight(launch["launch_id"], preflight)
            yield _sse_event("preflight", preflight)
            if preflight.get("state") != "ready":
                yield _sse_event("error", {"error": "preflight_failed", "preflight": preflight})
                yield _sse_event("done", {"exit_code": 78})
                return
            if request_controller is not None and controller_configured:
                execution_id = "run-execution-" + uuid.uuid4().hex
                yield _sse_event(
                    "launch",
                    {
                        "launch_id": launch["launch_id"],
                        "execution_id": execution_id,
                        "state": "running",
                    },
                )
                try:
                    response = request_controller(
                        "run.execute",
                        {
                            "launch_id": launch["launch_id"],
                            "plan_digest": current_digest,
                            "execution_id": execution_id,
                        },
                        2400,
                        f"run-{launch['launch_id']}",
                    )
                    result = response.get("result") or {}
                    run_id = str(result.get("run_id") or "")
                    exit_code = int(result.get("exit_code", 1))
                    yield _sse_event(
                        "result",
                        {
                            "execution_id": execution_id,
                            "state": result.get("status") or response.get("status"),
                            "run_id": run_id or None,
                            "exit_status": result.get("exit_status"),
                            "security_verdict": result.get("security_verdict"),
                        },
                    )
                    if run_id:
                        yield f"data: run_id: {run_id}\n\n".encode("utf-8")
                    yield _sse_event("done", {"exit_code": exit_code})
                except Exception as exc:  # noqa: BLE001 - stream bounded failure
                    failure = getattr(exc, "failure", None)
                    failure = failure if isinstance(failure, dict) else {}
                    error_payload = {
                        "error": str(
                            failure.get("code") or "controller_run_failed"
                        ),
                        "detail": "controller run failed",
                    }
                    if failure:
                        error_payload["failure"] = failure
                    execution_id = getattr(exc, "execution_id", None)
                    audit_receipt = getattr(exc, "audit_receipt", None)
                    if execution_id:
                        error_payload["execution_id"] = execution_id
                    if audit_receipt:
                        error_payload["audit_receipt"] = audit_receipt
                    yield _sse_event(
                        "error",
                        error_payload,
                    )
                    return_code = failure.get("return_code")
                    exit_code = (
                        return_code
                        if isinstance(return_code, int) and 1 <= return_code <= 255
                        else 126
                    )
                    yield _sse_event("done", {"exit_code": exit_code})
                return
            command = selected_command(launch["launch_id"])
            yield _sse_event("launch", {"launch_id": launch["launch_id"]})
            yield from selected_streamer(command)
        finally:
            if lock_acquired:
                release_mutating_lock()

    return (200, "text/event-stream; charset=utf-8", generate())


def dispatch_deployment_preview_request(*, method, path):
    from urllib.parse import parse_qs, urlsplit
    from profile_manager.deployment_versions import (
        DeploymentVersionGateError,
        profile_deployment_version_preview,
    )

    parts = urlsplit(path)
    if parts.path != "/api/deploy/preview":
        return None
    if method != "GET":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    profile_id = (parse_qs(parts.query).get("profile") or [""])[0]
    if not profile_id:
        return (400, "application/json; charset=utf-8", b'{"ok":false,"error":"missing-profile"}')
    try:
        preview = profile_deployment_version_preview(profile_id)
    except DeploymentVersionGateError as exc:
        body = json.dumps({"ok": False, "error": exc.code, "message": str(exc)}).encode("utf-8")
        return (409, "application/json; charset=utf-8", body)
    except Exception as exc:
        body = json.dumps({"ok": False, "error": "preview-failed", "message": str(exc)}).encode("utf-8")
        return (404, "application/json; charset=utf-8", body)
    return (200, "application/json; charset=utf-8", json.dumps(preview).encode("utf-8"))


def dispatch_topology_configuration_request(
    *,
    method,
    path,
    body,
    expected_token,
    generator=None,
    persister=None,
    request_controller=None,
):
    """Preview, retain, or export a constrained topology configuration."""

    if urlsplit(path).path != "/api/topology/configurations":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"error":"method not allowed"}')
    ok, error = check_token(path, expected=expected_token)
    if not ok:
        return (
            401,
            "application/json; charset=utf-8",
            json.dumps({"error": error}).encode("utf-8"),
        )
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (400, "application/json; charset=utf-8", b'{"error":"invalid JSON body"}')
    if not isinstance(request, dict):
        return (400, "application/json; charset=utf-8", b'{"error":"request body must be an object"}')
    operation = request.get("operation")
    allowed_by_operation = {
        "preview": {
            "operation", "topology_id", "node_count", "implementation", "version",
            "network_magic",
        },
        "preview-shape": {
            "operation", "topology_id", "implementation", "haskell_count",
            "amaru_count", "cardano_version", "amaru_version", "network_magic",
        },
        "save": {"operation", "configuration"},
        "export": {"operation", "profile_id"},
    }
    allowed = allowed_by_operation.get(operation) if isinstance(operation, str) else None
    if allowed is None or set(request) != allowed:
        return (
            400,
            "application/json; charset=utf-8",
            b'{"error":"invalid topology configuration request"}',
        )
    from profile_manager.profiles import persist_topology_configuration
    from profile_manager.topology_actions import TopologyActionError, controller_request
    from profile_manager.topology_export import (
        TopologyExportError,
        generate_topology_configuration,
        generate_topology_shape_configuration,
    )

    selected_generator = generator or generate_topology_configuration
    selected_persister = persister or persist_topology_configuration
    selected_controller = request_controller or controller_request
    try:
        if operation == "preview":
            configuration = selected_generator(
                topology_id=request["topology_id"],
                node_count=request["node_count"],
                implementation=request["implementation"],
                version=request["version"],
                network_magic=request["network_magic"],
                exported_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            return (
                200,
                "application/json; charset=utf-8",
                json.dumps({"ok": True, "configuration": configuration}, sort_keys=True).encode("utf-8"),
            )
        if operation == "preview-shape":
            expected_implementation = (
                "mixed" if request["haskell_count"] and request["amaru_count"]
                else "cardano-node" if request["haskell_count"] else "amaru"
            )
            if request["implementation"] != expected_implementation:
                raise TopologyExportError(
                    "implementation does not match the requested node counts"
                )
            configuration = generate_topology_shape_configuration(
                topology_id=request["topology_id"],
                haskell_count=request["haskell_count"],
                amaru_count=request["amaru_count"],
                cardano_version=request["cardano_version"],
                amaru_version=request["amaru_version"],
                network_magic=request["network_magic"],
                exported_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            return (
                200,
                "application/json; charset=utf-8",
                json.dumps({"ok": True, "configuration": configuration}, sort_keys=True).encode("utf-8"),
            )

        if not try_acquire_mutating_lock():
            return (
                409,
                "application/json; charset=utf-8",
                b'{"error":"another mutating action is already in progress"}',
            )
        try:
            if operation == "save":
                profile = selected_persister(request["configuration"], create=True)
                result = {
                    "ok": True,
                    "status": "saved",
                    "profile_id": profile.id,
                    "next_url": "/operate/status?profile_id=" + quote(profile.id),
                }
            else:
                profile_id = request["profile_id"]
                if not isinstance(profile_id, str) or not re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]|[a-z0-9]", profile_id
                ):
                    raise TopologyExportError("invalid profile identity")
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
                result = selected_controller(
                    "topology.save",
                    {
                        "topology_id": profile_id,
                        "file_name": profile_id + ".json",
                    },
                    30,
                    idempotency_key=f"topology-export-{profile_id}-{stamp}",
                )
                result = {"ok": True, **result}
        finally:
            release_mutating_lock()
        return (
            200,
            "application/json; charset=utf-8",
            json.dumps(result, sort_keys=True).encode("utf-8"),
        )
    except (TopologyExportError, TopologyActionError, KeyError, TypeError, ValueError) as exc:
        return (
            409,
            "application/json; charset=utf-8",
            json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"),
        )


def dispatch_version_refresh_request(*, method, path, expected_token, body=b""):
    """Start one authenticated, serialized official-release refresh."""
    if urlsplit(path).path != "/api/versions/refresh":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":"use POST"}')
    ok, error = check_form_token(path, body, expected=expected_token)
    if not ok:
        return (
            401,
            "application/json; charset=utf-8",
            json.dumps({"ok": False, "error": error}).encode("utf-8"),
        )
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    implementation = (form.get("implementation") or [""])[0]
    if implementation not in {"amaru", "cardano-node"}:
        return (
            409,
            "application/json; charset=utf-8",
            b'{"ok":false,"error":"select amaru or cardano-node"}',
        )
    from profile_manager.version_discovery import start_release_refresh

    result = start_release_refresh(implementation=implementation, manual=True)
    status = 202 if result.get("started") else 409
    return (
        status,
        "application/json; charset=utf-8",
        json.dumps({"ok": bool(result.get("started")), **result}).encode("utf-8"),
    )


def dispatch_version_preference_request(
    *, method, path, expected_token, body=b"", ledger=None,
):
    """Set or clear one exact local preference with server-derived scope."""
    if urlsplit(path).path != "/api/versions/preferences":
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"use POST\n", {})
    ok, error = check_form_token(path, body, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (error + "\n").encode(), {})
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    action = (form.get("action") or [""])[0]
    expected_revision = (form.get("expected_revision") or [""])[0]
    if action not in {"set", "clear"} or not expected_revision:
        return (409, "text/plain; charset=utf-8", b"invalid preference request\n", {})
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is in progress\n", {})
    try:
        from profile_manager.operator_version_decisions import (
            OperatorVersionDecisionLedger,
            StaleOperatorVersionState,
            active_decisions,
            active_preferences,
            preference_scope_key,
        )
        selected_ledger = ledger or OperatorVersionDecisionLedger()
        state = selected_ledger.read()
        if expected_revision != state["revision"]:
            raise StaleOperatorVersionState("operator version state revision is stale")
        if action == "set":
            decision_id = (form.get("decision_id") or [""])[0]
            decision = next(
                (
                    item for item in active_decisions(state).values()
                    if item["decision_id"] == decision_id
                    and item["decision"] == "supported"
                ),
                None,
            )
            if decision is None:
                raise ValueError("preference requires an active supported decision")
            scope_key = preference_scope_key(decision["subject"])
            referenced_decision = decision_id
        else:
            preference_id = (form.get("preference_id") or [""])[0]
            preference = next(
                (
                    item for item in active_preferences(state).values()
                    if item["preference_id"] == preference_id
                ),
                None,
            )
            if preference is None:
                raise ValueError("clear requires an active preference")
            scope_key = preference["scope_key"]
            referenced_decision = None
        event = {
            "preference_id": "preference-" + uuid.uuid4().hex,
            "scope_key": scope_key,
            "action": action,
            "decision_id": referenced_decision,
            "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        selected_ledger.append_preference(event, expected_revision=expected_revision)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return (409, "text/plain; charset=utf-8", (str(exc) + "\n").encode(), {})
    finally:
        release_mutating_lock()
    return (
        303,
        "text/plain; charset=utf-8",
        b"",
        {"Location": "/operate/versions"},
    )


def dispatch_run_version_decision_request(
    *, method, path, expected_token, body=b"", ledger=None, runs_dir=None,
):
    """Record an explicit operator judgment derived from one canonical run."""
    if urlsplit(path).path != "/api/runs/operator-version-decision":
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"use POST\n", {})
    ok, error = check_form_token(path, body, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (error + "\n").encode(), {})
    try:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return (409, "text/plain; charset=utf-8", b"invalid decision request\n", {})
    allowed = {"token", "run_id", "decision", "reason", "prefer", "expected_revision"}
    if set(form) != allowed or any(len(values) != 1 for values in form.values()):
        return (409, "text/plain; charset=utf-8", b"invalid decision request\n", {})
    run_id = form["run_id"][0]
    decision = form["decision"][0]
    reason = form["reason"][0].strip()
    prefer_text = form["prefer"][0]
    expected_revision = form["expected_revision"][0]
    if (
        not run_id or "/" in run_id or ".." in run_id
        or decision not in {"supported", "unsupported"}
        or not reason or len(reason) > 4096
        or prefer_text not in {"true", "false"}
        or (prefer_text == "true" and decision != "supported")
        or not expected_revision
    ):
        return (409, "text/plain; charset=utf-8", b"invalid decision request\n", {})
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is in progress\n", {})
    try:
        from profile_manager.data.operate_run import operate_run_detail
        from profile_manager.operator_version_decisions import (
            OperatorVersionDecisionLedger,
            StaleOperatorVersionState,
            preference_scope_key,
            subject_key,
        )

        detail = operate_run_detail(
            run_id, **({"runs_dir": Path(runs_dir)} if runs_dir is not None else {})
        )
        if detail is None:
            raise ValueError("canonical run evidence is unavailable")
        operator_version = detail.get("operator_version") or {}
        subject = operator_version.get("subject")
        if operator_version.get("available") is not True or not isinstance(subject, dict):
            raise ValueError(
                operator_version.get("reason")
                or "canonical operator-version evidence is unavailable"
            )
        selected_ledger = ledger or OperatorVersionDecisionLedger()
        state = selected_ledger.read()
        if expected_revision != state["revision"]:
            raise StaleOperatorVersionState("operator version state revision is stale")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        decision_id = "decision-" + uuid.uuid4().hex
        event = {
            "decision_id": decision_id,
            "subject_key": subject_key(subject),
            "subject": subject,
            "decision": decision,
            "reason": reason,
            "actor": "local-operator",
            "decided_at": now,
            "evidence": {
                "run_id": run_id,
                "manifest_sha256": detail["chain"]["manifest_hash"],
                "operational_outcome": detail["operational_outcome"],
                "assertion_verdict": str(
                    detail["security_execution"].get("security_verdict") or "unknown"
                ),
            },
        }
        prefer = prefer_text == "true"
        preference = (
            {
                "preference_id": "preference-" + uuid.uuid4().hex,
                "scope_key": preference_scope_key(subject),
                "action": "set",
                "decision_id": decision_id,
                "recorded_at": now,
            }
            if prefer
            else None
        )
        selected_ledger.append_decision_and_optional_preference(
            event,
            expected_revision=expected_revision,
            prefer=prefer,
            preference_event=preference,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return (409, "text/plain; charset=utf-8", (str(exc) + "\n").encode(), {})
    finally:
        release_mutating_lock()
    return (
        303,
        "text/plain; charset=utf-8",
        b"",
        {"Location": f"/operate/runs/{run_id}"},
    )


def dispatch_version_registration_request(
    *, method, path, expected_token, body=b"", ledger=None, request_controller=None,
):
    """Register only an immutable identity inspected by the local controller."""
    if urlsplit(path).path != "/api/versions/register":
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"use POST\n", {})
    ok, error = check_form_token(path, body, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (error + "\n").encode(), {})
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    allowed = {
        "token", "expected_revision", "implementation", "version", "image",
        "declared_source_revision",
    }
    if set(form) - allowed:
        return (409, "text/plain; charset=utf-8", b"browser identity claims are not accepted\n", {})
    expected_revision = (form.get("expected_revision") or [""])[0]
    implementation = (form.get("implementation") or [""])[0]
    version = (form.get("version") or [""])[0]
    image = (form.get("image") or [""])[0]
    source_revision = (form.get("declared_source_revision") or [""])[0] or None
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is in progress\n", {})
    try:
        from profile_manager.operator_version_decisions import (
            OperatorVersionDecisionLedger,
            StaleOperatorVersionState,
        )
        from profile_manager.topology_actions import TopologyActionError, controller_request

        selected_ledger = ledger or OperatorVersionDecisionLedger()
        state = selected_ledger.read()
        if expected_revision != state["revision"]:
            raise StaleOperatorVersionState("operator version state revision is stale")
        parameters = {
            "implementation": implementation,
            "version": version,
            "image": image,
        }
        if source_revision is not None:
            parameters["declared_source_revision"] = source_revision
        inspect = request_controller or controller_request
        result = inspect("image.inspect", parameters, 20)
        expected_fields = {
            "schema_version", "implementation", "version", "requested_image",
            "config_image_id", "manifest_references", "platform",
            "runtime_interface",
            "declared_source_revision", "provenance", "installation_id", "inspected_at",
        }
        if not isinstance(result, dict) or set(result) != expected_fields:
            raise ValueError("controller image inspection response is invalid")
        digest_pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
        reference_pattern = re.compile(
            r"^[a-z0-9][a-z0-9._:-]*(?:/[a-z0-9][a-z0-9._-]*)*@sha256:[0-9a-f]{64}$"
        )
        config_id = result.get("config_image_id")
        manifests = result.get("manifest_references")
        provenance = result.get("provenance")
        if (
            result.get("schema_version") != "v1"
            or result.get("implementation") != implementation
            or result.get("version") != version
            or result.get("requested_image") != image
            or result.get("declared_source_revision") != source_revision
            or not isinstance(config_id, str)
            or not digest_pattern.fullmatch(config_id)
            or not isinstance(manifests, list)
            or any(not isinstance(item, str) or not reference_pattern.fullmatch(item) for item in manifests)
            or result.get("platform") not in {"linux/amd64", "linux/arm64"}
            or result.get("runtime_interface") not in {None, "extracted-binary"}
            or provenance != {
                "source_revision": "unverified",
                "public": None,
                "qualification": "unverified",
            }
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(result.get("installation_id") or ""))
        ):
            raise ValueError("controller image inspection identity is invalid")
        unique_manifests = sorted(set(manifests))
        if len(unique_manifests) == 1:
            immutable_reference = unique_manifests[0]
            manifest_digest = "sha256:" + immutable_reference.rsplit("@sha256:", 1)[1]
        else:
            # An ambiguous mutable lookup freezes to the inspected config ID,
            # never to the tag or an arbitrarily selected repository digest.
            immutable_reference = config_id
            manifest_digest = None
        registration = {
            "registration_id": "registration-" + uuid.uuid4().hex,
            "implementation": implementation,
            "version": version,
            "image_reference": immutable_reference,
            "manifest_digest": manifest_digest,
            "config_image_id": config_id,
            "platform": result["platform"],
            "runtime_interface": result["runtime_interface"],
            "controller_installation_id": result["installation_id"],
            "inspected_at": result["inspected_at"],
            "provenance": {
                "source_revision": source_revision,
                "public": None,
                "qualification": "unverified",
            },
        }
        selected_ledger.append_registration(
            registration, expected_revision=expected_revision
        )
    except (OSError, RuntimeError, TopologyActionError, TypeError, ValueError) as exc:
        return (409, "text/plain; charset=utf-8", (str(exc) + "\n").encode(), {})
    finally:
        release_mutating_lock()
    return (303, "text/plain; charset=utf-8", b"", {"Location": "/operate/versions"})


def dispatch_binary_import_request(
    *, method, path, expected_token, content_type="", body=b"",
):
    clean = urlsplit(path).path
    if clean not in {
        "/api/versions/import-binary",
        "/api/versions/import-binary/status",
    }:
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":"use POST"}')
    from profile_manager.binary_imports import (
        BinaryImportError,
        begin_binary_import,
        poll_binary_import,
    )

    try:
        if clean.endswith("/status"):
            status, payload = poll_binary_import(
                body, expected_token=expected_token
            )
        else:
            status, payload = begin_binary_import(
                content_type,
                body,
                expected_token=expected_token,
                host_arch=os.environ.get("DWARF_HOST_ARCH", ""),
            )
    except BinaryImportError as exc:
        status = 401 if str(exc) == "authentication failed" else 409
        payload = {"ok": False, "error": str(exc)}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        status = 409
        payload = {"ok": False, "error": str(exc)}
    return (
        status,
        "application/json; charset=utf-8",
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def dispatch_topology_action_request(
    *,
    method,
    path,
    body,
    expected_token,
    action_service=None,
    snapshot_loader=None,
):
    """Dispatch typed topology previews, exact-plan applies, and cancellation."""
    if urlsplit(path).path != "/api/topology/actions":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"error":"method not allowed"}')
    ok, error = check_token(path, expected=expected_token)
    if not ok:
        return (
            401,
            "application/json; charset=utf-8",
            json.dumps({"error": error}).encode("utf-8"),
        )
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (400, "application/json; charset=utf-8", b'{"error":"invalid JSON body"}')
    if not isinstance(request, dict):
        return (
            400,
            "application/json; charset=utf-8",
            b'{"error":"request body must be an object"}',
        )
    raw_operation = request.get("operation")
    operation = raw_operation if isinstance(raw_operation, str) else ""
    fields = {
        "preview": {"operation", "profile_id", "action"},
        "apply": {"operation", "plan_id", "plan_digest", "execution_id", "confirmation"},
        "cancel": {"operation", "execution_id", "reason"},
        "status": {"operation", "execution_id"},
    }
    allowed = fields.get(operation)
    required = {
        "preview": {"operation", "profile_id", "action"},
        "apply": {"operation", "plan_id", "plan_digest"},
        "cancel": {"operation", "execution_id", "reason"},
        "status": {"operation", "execution_id"},
    }.get(operation)
    if allowed is None or required is None or set(request) - allowed or not required <= set(request):
        return (
            400,
            "application/json; charset=utf-8",
            b'{"error":"invalid topology action request"}',
        )
    from profile_manager.topology_actions import TopologyActionError, TopologyActionService

    actions = action_service or TopologyActionService()
    try:
        if operation == "preview":
            profile_id = request["profile_id"]
            if not isinstance(profile_id, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]|[a-z0-9]", profile_id
            ):
                raise ValueError("invalid profile identity")
            if snapshot_loader is None:
                from profile_manager.data.operate_topology_health import (
                    topology_action_snapshot,
                )

                snapshot_loader = topology_action_snapshot
            plan = actions.preview(request["action"], snapshot_loader(profile_id))
            return (
                200,
                "application/json; charset=utf-8",
                json.dumps(plan, sort_keys=True).encode("utf-8"),
            )
        if operation == "cancel":
            result = actions.cancel(request["execution_id"], request["reason"])
            return (
                200,
                "application/json; charset=utf-8",
                json.dumps(result, sort_keys=True).encode("utf-8"),
            )
        if operation == "status":
            result = actions.status(request["execution_id"])
            return (
                200,
                "application/json; charset=utf-8",
                json.dumps(result, sort_keys=True).encode("utf-8"),
            )
    except (TopologyActionError, KeyError, TypeError, ValueError) as exc:
        return (
            409 if isinstance(exc, TopologyActionError) else 400,
            "application/json; charset=utf-8",
            json.dumps({"error": str(exc)}).encode("utf-8"),
        )

    if not try_acquire_mutating_lock():
        return (
            409,
            "application/json; charset=utf-8",
            b'{"error":"another mutating action is already in progress"}',
        )

    def _gen():
        try:
            try:
                apply_kwargs = {"confirmation": request.get("confirmation")}
                if request.get("execution_id") is not None:
                    apply_kwargs["expected_execution_id"] = request["execution_id"]
                for event in actions.apply(
                    request["plan_id"], request["plan_digest"], **apply_kwargs
                ):
                    yield _sse_event(str(event.get("stage") or "progress"), event)
            except TopologyActionError as exc:
                payload: dict[str, object] = {"stage": "error", "error": str(exc)}
                if exc.terminal and exc.execution_id:
                    payload.update(
                        {"terminal": True, "execution_id": exc.execution_id}
                    )
                yield _sse_event("error", payload)
        finally:
            release_mutating_lock()

    return (200, "text/event-stream; charset=utf-8", _gen())


_ANTITHESIS_ROUTES = {
    "/api/antithesis/build",
    "/api/moog/validate",
    "/api/moog/preflight",
    "/api/moog/create-test",
    "/api/moog/test-status",
}


def _build_antithesis_command(action, qs):
    """Build the cardano-profile command for an /operate/antithesis GUI action."""

    def g(key):
        return (qs.get(key) or [None])[0]

    def need(key):
        val = g(key)
        if not val:
            raise ValueError(f"missing required field: {key}")
        return val

    if action == "/api/antithesis/build":
        cmd = _cli_command("antithesis", "build", need("profile"))
        for flag, key in (("--scenario", "scenario"), ("--out", "out"),
                          ("--registry", "registry"), ("--tag", "tag")):
            val = g(key)
            if val:
                cmd += [flag, val]
        cmd.append("--json")
        return cmd
    if action == "/api/moog/validate":
        return _cli_command("moog", "asset", "validate", "--asset-dir", need("asset_dir"), "--json")
    if action == "/api/moog/preflight":
        cmd = _cli_command("moog", "preflight", "--json")
        for flag, key in (("--asset-dir", "asset_dir"), ("--repo", "repo"),
                          ("--github-user", "github_user"), ("--directory", "directory"),
                          ("--commit", "commit")):
            val = g(key)
            if val:
                cmd += [flag, val]
        return cmd
    if action == "/api/moog/create-test":
        cmd = _cli_command("moog", "create-test", "--json")
        for flag, key in (("--repo", "repo"), ("--github-user", "github_user"),
                          ("--directory", "directory"), ("--commit", "commit"),
                          ("--duration", "duration")):
            val = g(key)
            if val:
                cmd += [flag, val]
        if g("no_faults") in ("1", "true", "yes"):
            cmd.append("--no-faults")
        # --approve = live on-chain submit; omit for a dry-run (prints the command).
        if g("approve") in ("1", "true", "yes"):
            cmd.append("--approve")
        return cmd
    if action == "/api/moog/test-status":
        return _cli_command("moog", "test-status", need("test_id"), "--json")
    raise ValueError(f"unknown antithesis action: {action}")


def dispatch_antithesis_request(*, method, path, expected_token):
    """Dispatch POST /api/antithesis/* and /api/moog/* GUI actions.

    Token-gated, serialized by the global mutating lock, streamed as SSE — the
    same guarantees as the other mutating endpoints. Returns
    (status, content_type, body_or_generator) or None if the path isn't a match.
    """
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    clean = parts.path
    if clean not in _ANTITHESIS_ROUTES:
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))
    qs = parse_qs(parts.query, keep_blank_values=True)
    try:
        cmd = _build_antithesis_command(clean, qs)
    except ValueError as exc:
        return (400, "text/plain; charset=utf-8", (f"bad request: {exc}\n").encode("utf-8"))
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is already in progress\n")

    def _gen():
        try:
            yield from stream_subprocess_sse(cmd)
        finally:
            release_mutating_lock()
    return (200, "text/event-stream; charset=utf-8", _gen())


_STATIC_CONTENT_TYPES = {
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
    ".png":   "image/png",
    ".svg":   "image/svg+xml",
    ".ico":   "image/x-icon",
    ".txt":   "text/plain; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
}


def dispatch_static_request(path):
    """Dispatch a /static/<subpath> GET. Returns (status, ctype, body) or None.

    Pure function over the request path so it is unit-testable without a server.
    Rejects traversal and absolute-path subpaths with 400 before resolving;
    follows up with a containment check after resolving symlinks. Unknown
    file suffixes fall through to application/octet-stream.
    """
    if not path.startswith("/static/"):
        return None
    # Ignore the query string (templates append ?v=<revision> for cache busting).
    path = path.split("?", 1)[0]
    subpath = path[len("/static/"):]
    if not subpath or ".." in subpath or subpath.startswith("/"):
        return (400, "text/plain; charset=utf-8", b"invalid path\n")
    static_root = (DASHBOARD_ROOT / "static").resolve()
    target = (static_root / subpath).resolve()
    if static_root not in target.parents and target != static_root:
        return (400, "text/plain; charset=utf-8", b"invalid path\n")
    if not target.is_file():
        return (404, "text/plain; charset=utf-8", b"not found\n")
    ctype = _STATIC_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return (200, ctype, target.read_bytes())


def dispatch_scenario_request(*, method, path, body, expected_token, scenarios_dir=None):
    """Handle /api/scenario/paste and /api/scenario/promote.

    Pure function over (method, path, body) so it's unit-testable without a server.
    Returns (status, content_type, body_bytes) or None when path doesn't match.
    """
    from urllib.parse import urlsplit, parse_qs
    from profile_manager import scenario as scen
    parts = urlsplit(path)
    clean = parts.path
    if clean not in ("/api/scenario/paste", "/api/scenario/promote",
                     "/api/scenario/validate", "/api/scenario/save"):
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    paste_body = body or b""
    if clean == "/api/scenario/paste":
        form = parse_qs(
            paste_body.decode("utf-8", errors="replace"), keep_blank_values=True
        )
        wrapped = "token" in form or "scenario_yaml" in form
        if wrapped:
            ok, err = check_form_token(path, paste_body, expected=expected_token)
            payloads = form.get("scenario_yaml") or []
            if ok and len(payloads) != 1:
                return (
                    400,
                    "application/json; charset=utf-8",
                    b'{"ok":false,"error":"missing scenario_yaml form field"}',
                )
            if ok:
                paste_body = payloads[0].encode("utf-8")
        else:
            ok, err = check_token(path, expected=expected_token)
    else:
        ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))
    scenarios_dir = Path(scenarios_dir) if scenarios_dir is not None else _scenarios_dir()
    if clean == "/api/scenario/validate":
        # Item #15 — read-only validation endpoint for the editor.
        report = scen.validate_scenario_body(body or b"")
        # Always return 200 — the body carries the structured errors,
        # so the client can render them inline without juggling 4xx.
        return (200, "application/json; charset=utf-8",
                json.dumps(report).encode("utf-8"))
    if clean == "/api/scenario/save":
        # Item #15 — validate, then persist to <scenarios_dir>/<id>.yaml.
        report = scen.save_scenario_body(body or b"", scenarios_dir=scenarios_dir)
        status = 200 if report.get("ok") else 400
        return (status, "application/json; charset=utf-8",
                json.dumps(report).encode("utf-8"))
    if clean == "/api/scenario/paste":
        report = scen.handle_paste(paste_body, scenarios_dir=scenarios_dir)
        status = 200 if report.get("ok") else 400
        return (status, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))
    qs = parse_qs(parts.query, keep_blank_values=True)
    sid = (qs.get("id") or [None])[0]
    report = scen.handle_promote(sid or "", scenarios_dir=scenarios_dir)
    if not report.get("ok"):
        return (404, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))
    return (200, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))


def dispatch_api_request(path, *, runs_dir=None, bundles_dir=None):
    """Return (status, content_type, body_bytes) for API routes, or None if path isn't an API route.

    Separated from the HTTP handler so it can be unit-tested without starting a server.
    """
    from profile_manager import forensic
    runs_dir = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    bundles_dir = Path(bundles_dir) if bundles_dir is not None else _forensic_bundles_dir()
    parsed = urlsplit(path)

    if parsed.path == "/api/topology/health":
        from profile_manager.data.operate_topology_health import (
            request_topology_health_check,
            topology_health_snapshot,
        )

        topology_query = parse_qs(parsed.query)
        fresh = (topology_query.get("fresh") or ["0"])[0].lower()
        profile_id = (topology_query.get("profile_id") or [None])[0]
        if profile_id is not None and not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]|[a-z0-9]", profile_id
        ):
            return (
                400,
                "application/json; charset=utf-8",
                b'{"error":"invalid profile_id"}',
            )
        if profile_id is not None:
            from profile_manager.profiles import find_profile

            try:
                find_profile(profile_id)
            except KeyError:
                return (
                    404,
                    "application/json; charset=utf-8",
                    b'{"error":"unknown profile_id"}',
                )
        payload = (
            (
                request_topology_health_check(profile_id=profile_id)
                if profile_id
                else request_topology_health_check()
            )
            if fresh in {"1", "true", "yes"}
            else topology_health_snapshot(profile_id=profile_id)
        )
        return (
            409 if payload.get("state") == "conflict" else 200,
            "application/json; charset=utf-8",
            json.dumps(payload).encode("utf-8"),
        )

    if parsed.path == "/api/topology/health/evidence":
        from profile_manager.data.operate_topology_health import (
            topology_health_evidence,
        )

        payload = topology_health_evidence()
        if payload is None:
            return (
                404,
                "application/json; charset=utf-8",
                b'{"error":"no completed topology health evidence"}',
            )
        return (
            200,
            "application/json; charset=utf-8",
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    if parsed.path.startswith("/api/corpora/"):
        from profile_manager.data.operate_corpora import dispatch_corpus_api_request

        corpus_result = dispatch_corpus_api_request(path)
        if corpus_result is not None:
            return corpus_result

    if parsed.path.startswith("/api/grammars/"):
        from profile_manager.data.operate_grammars import dispatch_grammar_api_request

        grammar_result = dispatch_grammar_api_request(path)
        if grammar_result is not None:
            return grammar_result

    if parsed.path.startswith("/api/risk-packages/"):
        from profile_manager.data.operate_risk_packages import dispatch_risk_package_api_request

        risk_package_result = dispatch_risk_package_api_request(path)
        if risk_package_result is not None:
            return risk_package_result

    if parsed.path.startswith("/api/plugins/"):
        from profile_manager.data.operate_plugins import dispatch_plugin_api_request

        plugin_result = dispatch_plugin_api_request(path)
        if plugin_result is not None:
            return plugin_result

    if parsed.path.startswith("/api/assets/"):
        testcase_artifact = dispatch_testcase_artifact_request(path)
        if testcase_artifact is not None:
            return testcase_artifact
        from profile_manager.views.operate_asset_catalog import (
            dispatch_asset_download_request,
        )

        return dispatch_asset_download_request(path)

    if parsed.path.startswith("/api/catalog/"):
        from urllib.parse import unquote
        from profile_manager.data.catalog_definitions import (
            CatalogError,
            DefinitionNotFoundError,
            UnknownCatalogError,
            UnsafeDefinitionIdError,
            archive_filename,
            deterministic_catalog_archive,
            load_definition,
        )

        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        try:
            if len(parts) == 4 and parts[:2] == ["api", "catalog"] and parts[3] == "export":
                catalog = parts[2]
                return (
                    200,
                    "application/gzip",
                    deterministic_catalog_archive(catalog),
                    _attachment_headers(archive_filename(catalog)),
                )
            if (
                len(parts) == 5
                and parts[:2] == ["api", "catalog"]
                and parts[4] == "download"
            ):
                catalog, definition_id = parts[2], parts[3]
                record = load_definition(catalog, definition_id)
                return (
                    200,
                    "application/yaml; charset=utf-8",
                    record.source,
                    _attachment_headers(record.download_filename),
                )
        except UnsafeDefinitionIdError:
            return (400, "text/plain; charset=utf-8", b"invalid definition id\n")
        except (UnknownCatalogError, DefinitionNotFoundError):
            return (404, "text/plain; charset=utf-8", b"not found\n")
        except CatalogError:
            return (422, "text/plain; charset=utf-8", b"invalid definition\n")
        if len(parts) >= 3 and parts[2] not in {
            "scenarios", "targets", "profiles", "measurements", "measurement-profiles"
        }:
            return (404, "text/plain; charset=utf-8", b"not found\n")
        return (400, "text/plain; charset=utf-8", b"invalid catalog request\n")

    if parsed.path == "/deliverables/download":
        values = parse_qs(parsed.query).get("path", [])
        if not values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        target, error = _safe_project_file(values[0])
        if error:
            return error
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.lower() == ".docx":
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        body = target.read_bytes()
        return (200, content_type, body, _attachment_headers(target.name))

    if parsed.path == "/deliverables/pdf":
        values = parse_qs(parsed.query).get("path", [])
        if not values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        target, error = _safe_project_file(values[0])
        if error:
            return error
        body, conversion_error = _convert_project_file_to_pdf(target)
        if conversion_error:
            return (503, "text/plain; charset=utf-8", conversion_error.encode("utf-8"))
        return (200, "application/pdf", body, _attachment_headers(f"{target.stem}.pdf"))

    if parsed.path == "/api/runs":
        raw_limit = (parse_qs(parsed.query).get("limit") or ["50"])[0]
        cursor = (parse_qs(parsed.query).get("cursor") or [None])[0]
        scope = (parse_qs(parsed.query).get("scope") or ["scenario"])[0]
        outcome = (parse_qs(parsed.query).get("outcome") or [""])[0]
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 0
        if not 1 <= limit <= 200:
            body = json.dumps({"error": "limit must be an integer from 1 to 200"}).encode("utf-8")
            return (400, "application/json; charset=utf-8", body)
        if scope not in {"scenario", "ci", "external", "operations", "all"}:
            body = json.dumps({"error": "scope must be scenario, ci, external, operations, or all"}).encode("utf-8")
            return (400, "application/json; charset=utf-8", body)
        if outcome not in {"", "pass", "fail", "error", "inconclusive"}:
            body = json.dumps(
                {
                    "error": (
                        "outcome must be pass, fail, error, inconclusive, or empty"
                    )
                }
            ).encode("utf-8")
            return (400, "application/json; charset=utf-8", body)
        from profile_manager.data.runs import InvalidRunHistoryCursor, recent_runs_payload
        try:
            payload = recent_runs_payload(
                runs_dir=runs_dir,
                limit=limit,
                cursor=cursor,
                scope=scope,
                outcome=outcome,
            )
        except InvalidRunHistoryCursor:
            body = json.dumps({"error": "invalid run-history cursor"}).encode("utf-8")
            return (400, "application/json; charset=utf-8", body)
        body = json.dumps(payload, indent=2).encode("utf-8")
        return (200, "application/json; charset=utf-8", body)

    bundle_suffix = None
    if parsed.path.startswith("/runs/") and parsed.path.endswith("/bundle/signature"):
        bundle_suffix = "/bundle/signature"
    elif parsed.path.startswith("/runs/") and parsed.path.endswith("/bundle"):
        bundle_suffix = "/bundle"
    if bundle_suffix is not None:
        run_id = parsed.path[len("/runs/"):-len(bundle_suffix)]
        if not run_id or "/" in run_id or ".." in run_id:
            return (400, "text/plain; charset=utf-8", b"invalid run id\n")
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        try:
            from scripts import runtime_bundle_export

            export_dir = run_dir / "outputs" / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            lock_path = export_dir / ".dashboard-export.lock"
            with lock_path.open("a+b") as lock_stream:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
                tarball_path = export_dir / f"{run_id}-bundle-export.tar.gz"
                signature_path = export_dir / "signature.json"
                reusable = False
                if tarball_path.is_file() and signature_path.is_file():
                    try:
                        reusable = (
                            runtime_bundle_export.verify_export_signature(export_dir).get("verdict")
                            == "verified"
                        )
                    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                        reusable = False
                if not reusable:
                    configured_key = os.environ.get("ADA2_DWARF_BUNDLE_SIGNING_KEY")
                    key_path = Path(configured_key) if configured_key else None
                    runtime_bundle_export.run_export(
                        run_dir=run_dir,
                        output_dir=export_dir,
                        target_run_id=run_id,
                        signing_actor=os.environ.get("USER", "dwarf"),
                        key_path=key_path,
                    )
                    if (
                        runtime_bundle_export.verify_export_signature(export_dir).get("verdict")
                        != "verified"
                    ):
                        raise RuntimeError("generated bundle export signature did not verify")
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            bundles_dir.mkdir(parents=True, exist_ok=True)
            preserved_path = bundles_dir / f"{run_id}.tar.gz"
            if not preserved_path.exists():
                shutil.copy2(tarball_path, preserved_path)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            return (
                503,
                "text/plain; charset=utf-8",
                f"signed bundle export unavailable: {type(exc).__name__}\n".encode("utf-8"),
            )
        if bundle_suffix == "/bundle/signature":
            return (
                200,
                "application/json",
                signature_path.read_bytes(),
                _attachment_headers("signature.json"),
            )
        return (
            200,
            "application/gzip",
            tarball_path.read_bytes(),
            _attachment_headers(tarball_path.name),
        )

    # Slice 30: per-artifact download. /runs/<id>/output?path=outputs/...
    # serves a single file from inside the bundle so SARIF / attestation
    # JSON / replay result.json can be linked from the inspector page.
    if parsed.path.startswith("/runs/") and parsed.path.endswith("/output"):
        prefix = "/runs/"
        run_id = parsed.path[len(prefix):-len("/output")]
        if not run_id or "/" in run_id or ".." in run_id:
            return (400, "text/plain; charset=utf-8", b"invalid run id\n")
        relpath_values = parse_qs(parsed.query).get("path", [])
        if not relpath_values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        relpath = relpath_values[0]
        if not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
            return (400, "text/plain; charset=utf-8", b"invalid path\n")
        run_dir = (runs_dir / run_id).resolve()
        if not run_dir.is_dir():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        target = (run_dir / relpath).resolve()
        if run_dir != target and run_dir not in target.parents:
            return (400, "text/plain; charset=utf-8", b"path escapes run dir\n")
        if not target.is_file():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.lower() == ".sarif":
            ctype = "application/sarif+json"
        body = target.read_bytes()
        return (200, ctype, body, _attachment_headers(target.name))

    return None


def _topology_profile_delete_blocker(profile_id):
    """Fail closed unless controller inventory proves no managed container uses the profile.

    Deleting a profile definition never stops or removes a topology.
    """
    from profile_manager.data import health as _health

    try:
        inventory = _health.controller_inventory()
    except Exception:  # noqa: BLE001 - any failure means ownership is unverified
        inventory = None
    if not isinstance(inventory, dict) or inventory.get("available") is not True:
        return "controller inventory is unavailable; cannot verify the profile is unused"
    managed = inventory.get("managed")
    if not isinstance(managed, list) or not all(isinstance(row, dict) for row in managed):
        return "controller inventory is invalid; cannot verify the profile is unused"
    owned_resources = inventory.get("owned_resources")
    if not isinstance(owned_resources, list) or not all(
        isinstance(row, dict) for row in owned_resources
    ):
        return "controller inventory is invalid; cannot verify the profile is unused"
    if any(
        str(row.get("topology_id") or "") == profile_id
        for row in [*managed, *owned_resources]
    ):
        return "profile owns managed topology containers; stop the topology before deleting"
    return None


def dispatch_catalog_mutating_request(*, method, path, body, expected_token):
    """Validate or atomically save one allow-listed catalog definition."""
    import hmac
    from urllib.parse import unquote
    from profile_manager.data.catalog_definitions import (
        CatalogError,
        DefinitionConflictError,
        DefinitionNotFoundError,
        InvalidDefinitionError,
        delete_user_definition,
        parse_and_validate_definition,
        save_definition,
    )

    parsed = urlsplit(path)
    if not parsed.path.startswith("/api/catalog/"):
        return None
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    is_validate = len(parts) == 4 and parts[:2] == ["api", "catalog"] and parts[3] == "validate"
    is_save = len(parts) == 5 and parts[:2] == ["api", "catalog"] and parts[4] == "save"
    is_delete = (
        len(parts) == 5
        and parts[:2] == ["api", "catalog"]
        and parts[2]
        in {"scenarios", "measurements", "measurement-profiles", "profiles"}
        and parts[4] == "delete"
    )
    if not (is_validate or is_save or is_delete):
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":"use POST"}')
    delete_form = (
        parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        if is_delete and body
        else {}
    )
    if is_delete:
        submitted_token = (delete_form.get("token") or [None])[0]
        ok = bool(submitted_token) and hmac.compare_digest(
            submitted_token, expected_token
        )
        error = None if ok else "invalid token"
    else:
        ok, error = check_token(path, expected=expected_token)
    if not ok:
        payload = json.dumps({"ok": False, "error": error}).encode("utf-8")
        return (403, "application/json; charset=utf-8", payload)

    query = parse_qs(parsed.query, keep_blank_values=True)
    catalog = parts[2]
    try:
        if is_validate:
            expected_id = (query.get("id") or [None])[0] or None
            data = parse_and_validate_definition(catalog, body or b"", expected_id=expected_id)
            payload = {"ok": True, "id": data["id"], "data": data}
        elif is_save:
            definition_id = parts[3]
            create = (query.get("create") or [""])[0].lower() in {"1", "true", "yes"}
            record = save_definition(catalog, definition_id, body or b"", create=create)
            payload = {"ok": True, "id": record.definition_id, "url": f"/operate/{catalog}/{record.definition_id}"}
        else:
            definition_id = parts[3]
            expected_sha256 = (delete_form.get("expected_sha256") or [""])[0]
            delete_user_definition(
                catalog,
                definition_id,
                expected_sha256=expected_sha256,
                blocker=(
                    _topology_profile_delete_blocker
                    if catalog == "profiles"
                    else None
                ),
            )
            payload = {
                "ok": True,
                "id": definition_id,
                "url": f"/operate/{catalog}",
            }
        return (200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
    except DefinitionNotFoundError as exc:
        status, message = 404, str(exc) or "definition not found"
    except InvalidDefinitionError as exc:
        status, message = (409 if "already exists" in str(exc) else 422), str(exc)
    except DefinitionConflictError as exc:
        status, message = 409, str(exc)
    except CatalogError as exc:
        status, message = 400, str(exc) or "invalid catalog request"
    payload = json.dumps({"ok": False, "error": message}).encode("utf-8")
    return (status, "application/json; charset=utf-8", payload)


def _recent_runs_summary_basic():
    payload = recent_runs_payload(limit=20)
    runs = payload["recent_runs"]
    if not runs:
        return ('<p>No tests have been run yet. Open <a href="/tests">Tests &amp; Evidence</a> '
                'to run one.</p>')
    total = len(runs)
    passed = sum(1 for r in runs if r.get("exit_status") == "pass")
    failed = sum(1 for r in runs if r.get("exit_status") == "fail")
    last = runs[0]
    summary = _basic_run_summary(last)
    headline = (
        f'<p>{passed} of {total} recent tests passed'
        + (f', {failed} failed' if failed else '')
        + '. ' + ('No crashes or panics in any of them. ' if failed == 0 else '')
        + f'<a href="/runs/{_escape(last.get("run_id"))}">Latest test &rarr;</a></p>'
    )
    detail = f'<p>{summary}</p>' if summary else ''
    return (
        headline + detail +
        '<p class="small">Switch to Advanced view to see the full evidence table and download bundles.</p>'
    )


def _basic_run_summary(run_entry):
    """One-sentence plain-English summary of a single run for non-experts."""
    scenario_id = run_entry.get("scenario_id") or ""
    runtime = run_entry.get("runtime") or ""
    exit_status = run_entry.get("exit_status")
    impl_human, parser_human = _humanize_scenario_id(scenario_id)
    target_phrase = (
        f"<strong>{_escape(impl_human)}</strong>'s <strong>{_escape(parser_human)}</strong> parser"
        if impl_human and parser_human
        else f"scenario <code>{_escape(scenario_id)}</code>"
    )
    outcome_phrase = {
        "pass": "all inputs were rejected cleanly with no crashes",
        "fail": "at least one input crashed or behaved unexpectedly &mdash; see the bundle",
        "error": "the test runner errored before producing a verdict",
    }.get(exit_status, "outcome unknown")
    return (
        f"Latest run tested {target_phrase} (runtime: <code>{_escape(runtime)}</code>) &mdash; "
        f"{outcome_phrase}."
    )


def render_tests_html():
    runs_section = (
        '<section class="basic-only" id="recent-runs-summary">'
        '<h2>Recent Tests</h2>' + _recent_runs_summary_basic() +
        '</section>'
        '<section class="adv-only" id="recent-runs-detailed">'
        '<h2>Recent Runs</h2>'
        '<p class="small">Forensic bundles produced by every fuzz, smoke, evidence, and package run. '
        'Each row is a self-contained tamper-evident evidence bundle; click <em>bundle</em> to download a tar.gz.</p>'
        + _recent_runs_table_html() +
        '</section>'
    )
    body = '<h1>Tests &amp; Evidence</h1>' + _target_live_cards() + runs_section + """
  <section class="adv-only">
    <h2>Tests & Evidence</h2>
    <svg class="viz viz-compact" id="test-pipeline-svg" viewBox="0 0 940 270" role="img" aria-label="Test and evidence pipeline"></svg>
    <div class="flow" id="test-pipeline">
      <div class="flow-step"><strong>Risk Work Packages</strong>A/B/C/D candidate coverage and blockers.</div>
      <div class="flow-step"><strong>Smoke Tests</strong>Bounded repeatable checks.</div>
      <div class="flow-step"><strong>Fuzz Tests</strong>Safe offline and approval-required live tests.</div>
      <div class="flow-step"><strong>Review Gate</strong>No accepted risks or findings without human approval.</div>
    </div>
    <div class="table-wrap" id="packages-table"></div>
  </section>

  <section class="adv-only">
    <h2>Smoke Tests</h2>
    <div class="table-wrap" id="smoke-table"></div>
  </section>

  <section class="adv-only">
    <h2>Fuzz Tests</h2>
    <div class="table-wrap" id="fuzz-table"></div>
  </section>

  <section class="adv-only">
    <h2>Run Output</h2>
    <pre id="run-log" class="action-log">No run in progress.</pre>
  </section>

  <section class="adv-only">
    <h2>Issue Families</h2>
    <div class="status-strip" id="lifecycle-summary-strip"></div>
    <h3>Runtime Anomalies</h3>
    <div class="table-wrap" id="lifecycle-runtime-buckets-table"></div>
    <div class="table-wrap" id="lifecycle-runtime-cases-table"></div>
    <h3>Fuzz Queue Families</h3>
    <div class="table-wrap" id="lifecycle-fuzz-buckets-table"></div>
    <div class="table-wrap" id="lifecycle-fuzz-cases-table"></div>
  </section>

  <section>
    <h2>Paste a scenario</h2>
    <p class="small basic-only">Paste a scenario YAML to add it to the framework. The server will check it for you and (if valid) you can promote it into the runnable corpus.</p>
    <p class="small adv-only">POST /api/scenario/paste writes to dwarf/scenarios/pending/&lt;id&gt;.yaml and re-validates against spec/v1/schema.json. POST /api/scenario/promote moves a pending scenario into dwarf/scenarios/.</p>
    <label for="paste-textarea">Scenario YAML</label>
    <textarea id="paste-textarea" rows="14" placeholder='{ "spec_version": "v1", "id": "your-id", ... }'></textarea>
    <div class="action-row">
      <button id="paste-validate-button" class="action-button">Validate</button>
      <button id="paste-promote-button" class="action-button" disabled>Promote</button>
    </div>
    <pre id="paste-report" class="action-log">No paste in progress.</pre>
  </section>

  <section class="adv-only">
    <h2>Latest Evidence Feed</h2>
    <div class="table-wrap" id="evidence-table"></div>
    <p class="small">Evidence remains candidate-only unless a separate human-approved promotion/scoring pass says otherwise.</p>
  </section>
"""
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); })
    .finally(() => { refresh(); });
}
let _pastedScenarioId = null;
function bindPasteButtons() {
  const validateBtn = document.getElementById("paste-validate-button");
  const promoteBtn = document.getElementById("paste-promote-button");
  const textarea = document.getElementById("paste-textarea");
  const report = document.getElementById("paste-report");
  if (!validateBtn || validateBtn.dataset.bound) return;
  validateBtn.dataset.bound = "1";
  validateBtn.addEventListener("click", async () => {
    promoteBtn.disabled = true;
    _pastedScenarioId = null;
    report.textContent = "Validating...";
    try {
      const res = await fetch(`/api/scenario/paste?token=${encodeURIComponent(getToken())}`,
                              { method: "POST", body: textarea.value });
      const data = await res.json();
      report.textContent = JSON.stringify(data, null, 2);
      if (data.ok) {
        _pastedScenarioId = data.scenario_id;
        promoteBtn.disabled = false;
      }
    } catch (err) {
      report.textContent = "[error] " + String(err);
    }
  });
  promoteBtn.addEventListener("click", async () => {
    if (!_pastedScenarioId) return;
    promoteBtn.disabled = true;
    report.textContent = "Promoting " + _pastedScenarioId + "...";
    try {
      const res = await fetch(`/api/scenario/promote?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(_pastedScenarioId)}`,
                              { method: "POST" });
      const data = await res.json();
      report.textContent = JSON.stringify(data, null, 2);
      if (data.ok) {
        textarea.value = "";
        _pastedScenarioId = null;
        refresh();
      }
    } catch (err) {
      report.textContent = "[error] " + String(err);
    }
  });
}
function bindRunButtons() {
  const log = document.getElementById("run-log");
  document.querySelectorAll(".run-fuzz-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const needsApproval = btn.dataset.needsApproval === "1";
      if (needsApproval) {
        const ok = window.prompt(`Fuzz ${id} requires --approve. Type APPROVE to proceed.`) === "APPROVE";
        if (!ok) return;
      }
      const url = `/api/fuzz/run?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(id)}` + (needsApproval ? "&approve=1" : "");
      streamRun(url, log);
    });
  });
  document.querySelectorAll(".run-smoke-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const url = `/api/test/smoke/run?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(id)}`;
      streamRun(url, log);
    });
  });
}
function render(payload) {
  renderTargetAndLive(payload);
  drawTestPipeline(payload);
  document.getElementById("packages-table").innerHTML = table(
    ["Package", "Label", "Run State", "Status", "Candidates", "Blockers"],
    (payload.evidence_packages || []).map(p => [code(p.id), esc(p.label), esc(p.run_state), esc(p.status), (p.candidate_ids || []).map(code).join("<br>"), (p.blockers || []).map(esc).join("<br>") || "none"])
  );
  document.getElementById("smoke-table").innerHTML = table(
    ["Smoke ID", "Label", "Category", "Working Directory", "Timeout", "Run"],
    (payload.smoke_tests || []).map(s => [
      code(s.id), esc(s.label), esc(s.category), esc(s.working_directory), esc(s.timeout_seconds),
      `<button class="run-smoke-button" data-id="${esc(s.id)}">Run smoke</button>`
    ])
  );
  document.getElementById("fuzz-table").innerHTML = table(
    ["Fuzz ID", "Label", "Category", "Package", "Safety", "Needs Testnet", "Candidates", "Run"],
    (payload.fuzz_tests || []).map(f => [
      code(f.id), esc(f.label), esc(f.category), esc(f.target_package), esc(f.safety_level),
      f.requires_deployed_testnet ? "yes" : "no", (f.related_candidates || []).map(code).join("<br>"),
      `<button class="run-fuzz-button" data-id="${esc(f.id)}" data-needs-approval="${f.requires_deployed_testnet ? "1" : "0"}">Run fuzz</button>`
    ])
  );
  bindRunButtons();
  bindPasteButtons();
  document.getElementById("evidence-table").innerHTML = table(
    ["Evidence File", "Size"],
    (payload.latest_evidence || []).map(e => [code(e.path), `${esc(e.size_kib)} KiB`])
  );
  const lifecycle = payload.testcase_lifecycle || {};
  const lifecycleSummary = [
    `Source: ${esc(lifecycle.source || "none")}`,
    `Cases: ${esc(lifecycle.case_count || 0)}`,
    `Buckets: ${esc(lifecycle.bucket_count || 0)}`,
    `Runtime anomalies: ${esc(lifecycle.runtime_anomaly_count || 0)}`,
    `Pending replay: ${esc(lifecycle.pending_replay_count || 0)}`,
    `Pending compare: ${esc(lifecycle.pending_compare_count || 0)}`
  ];
  document.getElementById("lifecycle-summary-strip").innerHTML = lifecycleSummary.map(item => `<span class="status-pill">${item}</span>`).join("");
  document.getElementById("lifecycle-runtime-buckets-table").innerHTML = table(
    ["Bucket", "Cases", "Reason", "Target", "Pending Replay", "Pending Compare", "Minimized"],
    (lifecycle.runtime_buckets || []).map(b => [
      code(b.bucket_id), esc(b.case_count), esc(b.triage_reason), esc(b.target_implementation), esc(b.pending_replay_count || 0), esc(b.pending_compare_count || 0), esc(b.complete_minimization_count || 0)
    ])
  );
  document.getElementById("lifecycle-runtime-cases-table").innerHTML = table(
    ["Case", "Run", "Scenario", "Reason", "Target"],
    (lifecycle.recent_runtime_cases || []).map(c => [
      code(c.case_id), code(c.source_run_id || "-"), esc(c.scenario_id || "-"), esc(c.triage_reason), esc(c.target_implementation)
    ])
  );
  document.getElementById("lifecycle-fuzz-buckets-table").innerHTML = table(
    ["Bucket", "Cases", "Classification", "Reason", "Target", "Pending Replay", "Pending Compare", "Minimized"],
    (lifecycle.fuzz_buckets || []).map(b => [
      code(b.bucket_id), esc(b.case_count), esc(b.classification), esc(b.triage_reason), esc(b.target_implementation), esc(b.pending_replay_count || 0), esc(b.pending_compare_count || 0), esc(b.complete_minimization_count || 0)
    ])
  );
  document.getElementById("lifecycle-fuzz-cases-table").innerHTML = table(
    ["Case", "Run", "Classification", "Reason", "Target"],
    (lifecycle.recent_fuzz_cases || []).map(c => [
      code(c.case_id), code(c.source_run_id || "-"), esc(c.classification), esc(c.triage_reason), esc(c.target_implementation)
    ])
  );
}
function drawTestPipeline(payload) {
  const packages = payload.evidence_packages || [];
  const packageNodes = packages.map((p, idx) => {
    const x = 70 + idx * 150;
    const klass = p.run_state === "runnable" ? "ok" : "warn";
    return `<g><rect class="${klass}" x="${x}" y="42" width="118" height="54" rx="8"/><text x="${x + 59}" y="65" text-anchor="middle" font-weight="700">${esc(p.id)}</text><text class="muted" x="${x + 59}" y="84" text-anchor="middle">${esc(p.run_state)}</text><line class="edge" x1="${x + 59}" y1="96" x2="${x + 59}" y2="140"/></g>`;
  }).join("");
  document.getElementById("test-pipeline-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    ${packageNodes}
    <rect class="node" x="110" y="144" width="180" height="54" rx="8"/><text x="200" y="166" text-anchor="middle" font-weight="700">Smoke Tests</text><text class="muted" x="200" y="185" text-anchor="middle">${esc((payload.smoke_tests || []).length)} registered</text>
    <rect class="warn" x="370" y="144" width="180" height="54" rx="8"/><text x="460" y="166" text-anchor="middle" font-weight="700">Fuzz Tests</text><text class="muted" x="460" y="185" text-anchor="middle">${esc((payload.fuzz_tests || []).length)} registered</text>
    <rect class="gate" x="650" y="132" width="190" height="78" rx="8"/><text x="745" y="160" text-anchor="middle" font-weight="700">Review Gate</text><text class="muted" x="745" y="182" text-anchor="middle">candidate-only</text>
    <line class="edge" x1="290" y1="171" x2="370" y2="171"/><line class="edge" x1="550" y1="171" x2="650" y2="171"/>
  `;
}
"""
    return _page("Tests & Evidence", "/tests", body, script)




def render_dashboard_html():
    return render_command_center_html()


def _scenario_trace_summary(row):
    parts = []
    milestones = row.get("related_milestones") or []
    if milestones:
        parts.append("Milestones: " + ", ".join(_escape(x) for x in milestones))
    intent = row.get("evidence_intent")
    if intent:
        parts.append("Intent: " + _escape(intent))
    trace = row.get("m1_trace") or {}
    for key in ("threat_ids", "gap_ids", "architecture_ids", "risk_candidate_ids"):
        values = trace.get(key) or []
        if values:
            parts.append(_escape(key) + ": " + ", ".join(f"<code>{_escape(v)}</code>" for v in values))
    blockers = row.get("promotion_blockers") or []
    if blockers:
        parts.append("Blockers: " + "; ".join(_escape(v) for v in blockers))
    return "<br>".join(parts) if parts else '<span class="small">No M1 trace metadata.</span>'


def render_scenarios_html():
    rows = _list_scenarios_for_compare()
    rows_html = "".join(
        "<tr>"
        f"<td><code>{_escape(row['id'])}</code><br><span class=\"small\">{_escape(row.get('runtime') or '')}</span></td>"
        f"<td>{_escape(row['title'])}<br><span class=\"small\">{_scenario_trace_summary(row)}</span></td>"
        f"<td><button class=\"run-scenario-button action-button\" data-path=\"{_escape(row['path'])}\">Run</button> "
        f"<a href=\"/operate/compare\" class=\"small\">compare both</a></td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="3"><em>No scenarios in dwarf/scenarios/. Paste one on the Tests page.</em></td></tr>'
    body = (
        '<h1>Scenarios</h1>'
        '<section class="basic-only">'
        '<div class="card notice">'
        '<h3>Scenarios</h3>'
        '<p>Each scenario is a single test recipe &mdash; what to feed which parser, how many times, what to check. Click <strong>Run</strong> to execute one against a single implementation. The dashboard streams the live output and writes a forensic bundle you can inspect from <a href="/tests">Recent Runs</a>.</p>'
        '<p>Want to run the same scenario against both implementations and diff the outcomes? Use <a href="/operate/compare">Compare</a>.</p>'
        '</div>'
        '</section>'
        '<section>'
        '<h2>Scenarios</h2>'
        '<p class="adv-only small">POST /api/scenario/run?token=&amp;path=&lt;scenario&gt; runs <code>cardano-profile scenario run</code> end-to-end. Bundle lands in the configured runs directory; chain head updated atomically.</p>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Scenario id</th><th>Title</th><th>Action</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
        '<pre id="scenario-log" class="action-log">No run in progress.</pre>'
        '</section>'
    )
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); });
}
function render(payload) {
  renderTargetAndLive(payload);
  const log = document.getElementById("scenario-log");
  document.querySelectorAll(".run-scenario-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const path = btn.dataset.path;
      const url = `/api/scenario/run?token=${encodeURIComponent(getToken())}&path=${encodeURIComponent(path)}`;
      streamRun(url, log);
    });
  });
}
"""
    return _page("Scenarios", "/scenarios", body, script)


def render_compare_html():
    """DEPRECATED — slice 24 retired this from the dispatch table; the
    canonical compare surface lives at /operate/compare via
    profile_manager.views.compare.render_operate_compare. This function
    is unreachable from any live route but retained because
    test_forensic.test_scenarios_route_in_navigation iterates it as a
    render-coverage rail. Slated for removal once that test moves to
    the modern view's HTML output."""
    rows = _list_scenarios_for_compare()
    rows_html = "".join(
        "<tr>"
        f"<td><code>{_escape(row['id'])}</code></td>"
        f"<td>{_escape(row['title'])}</td>"
        f"<td><button class=\"run-compare-button action-button\" data-path=\"{_escape(row['path'])}\">Run compare</button></td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="3"><em>No scenarios in dwarf/scenarios/.</em></td></tr>'
    body = (
        '<section class="basic-only">'
        '<div class="card notice">'
        '<h3>Compare implementations</h3>'
        '<p>Run the same test against both Amaru and cardano-node, side by side. The framework feeds identical inputs to both implementations using the same random seed, then compares the outcomes. If the two parsers ever disagree &mdash; one accepts what the other rejects &mdash; that&rsquo;s a candidate finding.</p>'
        '<p>Pick a scenario below and click <strong>Run compare</strong>. When it finishes you&rsquo;ll see the comparison report on each run\'s page.</p>'
        '</div>'
        '</section>'
        '<section>'
        '<h2>Compare implementations</h2>'
        '<p class="adv-only small">POST /api/scenario/compare?token=&amp;path=&lt;scenario&gt; runs <code>cardano-profile compare</code> end-to-end and emits cross-impl-comparison.md inside the second run\'s bundle.</p>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Scenario id</th><th>Title</th><th>Action</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
        '<pre id="compare-log" class="action-log">No compare in progress.</pre>'
        '</section>'
    )
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); });
}
function render(payload) {
  renderTargetAndLive(payload);
  const log = document.getElementById("compare-log");
  document.querySelectorAll(".run-compare-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const path = btn.dataset.path;
      const url = `/api/scenario/compare?token=${encodeURIComponent(getToken())}&path=${encodeURIComponent(path)}`;
      streamRun(url, log);
    });
  });
}
"""
    return _page("Compare", "/compare", body, script)


_PROJECT_MAP_PATH = PROJECT_ROOT / "user" / "PROJECT-MAP.md"


def _md_inline(text):
    import html as _html
    import re as _re
    out = _html.escape(text)
    out = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: f'<a href="{_html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
                  out)
    out = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = _re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def _md_to_html(md_text):
    lines = md_text.splitlines()
    out = []
    i = 0
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            close_list()
            i += 1
            continue

        if stripped.startswith("### "):
            close_list()
            out.append(f"<h3>{_md_inline(stripped[4:])}</h3>")
            i += 1
            continue
        if stripped.startswith("## "):
            close_list()
            out.append(f"<h2>{_md_inline(stripped[3:])}</h2>")
            i += 1
            continue
        if stripped.startswith("# "):
            close_list()
            out.append(f"<h1>{_md_inline(stripped[2:])}</h1>")
            i += 1
            continue
        if stripped == "---":
            close_list()
            out.append("<hr>")
            i += 1
            continue

        # Table: header row | sep row | body rows
        if stripped.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].strip().replace("|", "").replace(":", "").replace("-", "").replace(" ", "")) == set():
            close_list()
            header_cells = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row_cells)
                i += 1
            tbl = ['<div class="table-wrap"><table>']
            tbl.append("<thead><tr>" + "".join(f"<th>{_md_inline(c)}</th>" for c in header_cells) + "</tr></thead>")
            tbl.append("<tbody>")
            for r in rows:
                tbl.append("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>")
            tbl.append("</tbody></table></div>")
            out.append("".join(tbl))
            continue

        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_md_inline(stripped[2:])}</li>")
            i += 1
            continue

        close_list()
        # Paragraph: gather consecutive non-empty, non-special lines
        para = [stripped]
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt or nxt.startswith(("#", "-", "|", "---")):
                break
            para.append(nxt)
            j += 1
        out.append(f"<p>{_md_inline(' '.join(para))}</p>")
        i = j

    close_list()
    return "\n".join(out)




# Slice-15: cutover from legacy / to /operate. Single source of truth for
# all redirects. Keys are paths (post query-string strip); values are
# Location header values.
REDIRECTS = {
    "/index.html": "/operate",
    "/tests": "/run",
    "/scenarios": "/operate/scenarios",
    "/compare": "/operate/compare",
    # Slice 25: legacy /architecture targeted /operate/status (substrate
    # health) — wrong destination because /learn/architecture carries the
    # actual architecture diagram + prose. Re-pointed to the semantic
    # match. /status added (was 404) so the operator-typed shorthand
    # also lands on the live console. /settings still maps to
    # /operate/status because the legacy "settings" page WAS the live
    # status console (config + serving info).
    "/architecture": "/learn/architecture",
    "/status": "/operate/status",
    "/settings": "/operate/status",
    # Slice 26(a): /cli + /cli-docs alias the operator CLI reference.
    # Both were 404 before — operators typing the shorthand form would
    # land on a not-found page. The canonical surface is /learn/cli.
    "/cli": "/learn/cli",
    "/cli-docs": "/learn/cli",
    # Slice 26(b): bundle / inspector aliases. The bundles catalog
    # lives at /operate/bundles; the per-run inspector lives at
    # /operate/runs/<id> (which requires a run-id). Operators typing
    # /bundle, /bundles, /inspect, or /inspector should land
    # somewhere useful — the catalog is the natural starting point.
    "/bundle": "/operate/bundles",
    "/bundles": "/operate/bundles",
    "/inspect": "/operate/runs",
    "/inspector": "/operate/runs",
    "/project": "/operate",
    "/deliverables": "/operate/contract",
    "/raw": "/api/health",
}


def _status_profile_id(route: str) -> str | None:
    parsed = urlsplit(route)
    profile_id = (parse_qs(parsed.query).get("profile_id") or [None])[0]
    if profile_id is None or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]|[a-z0-9]", profile_id
    ):
        return None
    return profile_id


def render_route_html(route, *, token=None):
    # Slice 42: /operate/compare/runs is the only path that consumes query
    # args (left=<id>&right=<id>). Branch off before stripping the query.
    if route.startswith("/operate/compare/runs"):
        from urllib.parse import urlsplit, parse_qs
        from profile_manager.views.operate_run_compare import render_operate_run_compare
        parts = urlsplit(route)
        qs = parse_qs(parts.query, keep_blank_values=True)
        left = (qs.get("left") or [""])[0]
        right = (qs.get("right") or [""])[0]
        return render_operate_run_compare(left, right)
    path_only = route.split("?", 1)[0].rstrip("/")
    if path_only in {
        "/operate/profiles/new",
        "/operate/targets/new",
        "/operate/scenarios/new",
        "/operate/measurements/new",
        "/operate/measurement-profiles/new",
    }:
        from urllib.parse import parse_qs, urlsplit
        from profile_manager.views.operate_definition_edit import render_operate_definition_edit

        query = parse_qs(urlsplit(route).query)
        catalog = path_only.split("/")[2]
        template = (query.get("template") or [None])[0]
        request_token = (query.get("token") or [None])[0] or token
        return render_operate_definition_edit(catalog, None, token=request_token, template=template)
    edit_parts = path_only.strip("/").split("/")
    if (
        len(edit_parts) == 4
        and edit_parts[0] == "operate"
        and edit_parts[1] in {
            "scenarios", "targets", "profiles", "measurements", "measurement-profiles"
        }
        and edit_parts[3] == "edit"
    ):
        from profile_manager.views.operate_definition_edit import render_operate_definition_edit

        return render_operate_definition_edit(edit_parts[1], edit_parts[2], token=token)
    definition_parts = path_only.strip("/").split("/")
    if (
        len(definition_parts) == 3
        and definition_parts[0] == "operate"
        and definition_parts[1] in {
            "scenarios", "targets", "profiles", "measurements", "measurement-profiles"
        }
    ):
        from profile_manager.data.catalog_definitions import DefinitionNotFoundError
        from profile_manager.views.operate_definition import render_operate_definition

        try:
            return render_operate_definition(
                definition_parts[1], definition_parts[2], token=token
            )
        except DefinitionNotFoundError:
            return None
    # Item #15 — scenario editor. /operate/scenarios/edit/<id> opens an
    # existing scenario for editing; /operate/scenarios/edit (no id)
    # opens a blank-state authoring buffer. Pre-empts the routes-dict
    # lookup so the trailing path-segment is preserved.
    if route.split("?", 1)[0].startswith("/operate/scenarios/edit"):
        from profile_manager.views.operate_scenarios_edit import render_operate_scenarios_edit
        path_only = route.split("?", 1)[0]
        rest = path_only[len("/operate/scenarios/edit"):]
        sid = rest.lstrip("/").rstrip("/") if rest else ""
        return render_operate_scenarios_edit(sid)
    # Item E (Phase 4.3 D-1) — /operate/audit accepts
    # ?classification=&family= server-side filters.
    if route.split("?", 1)[0] == "/operate/audit":
        from urllib.parse import urlsplit
        return render_operate_audit(urlsplit(route).query)
    # Ledger-backed history: scope and cursor are bookmarkable alongside filters.
    if route.split("?", 1)[0] == "/operate/runs":
        from urllib.parse import urlsplit, parse_qs
        parts = urlsplit(route)
        qs = parse_qs(parts.query, keep_blank_values=True)
        outcome = (qs.get("outcome") or [""])[0]
        q = (qs.get("q") or [""])[0]
        scope = (qs.get("scope") or ["scenario"])[0]
        cursor = (qs.get("cursor") or [None])[0]
        if outcome not in ("", "pass", "fail", "error", "inconclusive"):
            outcome = ""
        if scope not in {"scenario", "ci", "external", "operations", "all"}:
            scope = "scenario"
        from profile_manager.data.runs import InvalidRunHistoryCursor
        try:
            return render_operate_runs(
                outcome=outcome,
                q=q[:128],
                scope=scope,
                cursor=cursor,
            )
        except InvalidRunHistoryCursor:
            return render_operate_runs(outcome=outcome, q=q[:128], scope=scope)
    if route.split("?", 1)[0] == "/operate/versions":
        return render_operate_versions(token=token)
    if route.split("?", 1)[0] == "/operate/status":
        return render_operate_status(
            token=token,
            profile_id=_status_profile_id(route),
        )
    profile_template_html = dispatch_profile_template_request(route)
    if profile_template_html is not None:
        return profile_template_html
    testcase_html = dispatch_testcase_request(route, token=token)
    if testcase_html is not None:
        return testcase_html
    corpus_html = dispatch_corpus_request(route, token=token)
    if corpus_html is not None:
        return corpus_html
    grammar_html = dispatch_grammar_request(route, token=token)
    if grammar_html is not None:
        return grammar_html
    risk_package_html = dispatch_risk_package_request(route)
    if risk_package_html is not None:
        return risk_package_html
    plugin_html = dispatch_plugin_request(route)
    if plugin_html is not None:
        return plugin_html
    primitive_html = dispatch_primitive_catalog_request(route)
    if primitive_html is not None:
        return primitive_html
    from profile_manager.views.operate_asset_catalog import (
        dispatch_asset_catalog_request,
    )

    asset_html = dispatch_asset_catalog_request(route)
    if asset_html is not None:
        return asset_html
    from urllib.parse import parse_qs as _parse_qs, urlsplit as _urlsplit

    run_scenario = None
    if _urlsplit(route).path.rstrip("/") == "/run":
        run_scenario = (
            _parse_qs(_urlsplit(route).query).get("scenario") or [None]
        )[0]
    # Strip query string before lookup; routes are paths only.
    route = route.split("?", 1)[0]
    routes = {
        "/": render_landing,
        "/run": lambda: render_operate_run_wizard(
            token=token, requested_scenario_id=run_scenario
        ),
        "/index.html": render_command_center_html,
        "/tests": render_tests_html,
        "/scenarios": render_scenarios_html,
        "/operate": render_operate_landing,
        "/operate/scenarios": lambda: render_operate_scenarios(token=token),
        "/learn": render_learn_landing,
        "/learn/concepts": render_learn_concepts,
        "/learn/attack-cost": render_learn_attack_cost,
        "/learn/overview": render_learn_overview,
        "/learn/consensus": render_learn_consensus,
        "/learn/coverage": render_learn_coverage,
        "/learn/threat-coverage": render_learn_threat_coverage,
        "/learn/status": render_learn_status,
        "/operate/compare": lambda: render_operate_compare(token=token),
        "/learn/architecture": render_learn_architecture,
        "/operate/profiles": render_operate_profiles,
        "/operate/measurements": render_operate_measurements,
        "/operate/measurement-profiles": render_operate_measurement_profiles,
        "/operate/runs": render_operate_runs,
        "/operate/status": render_operate_status,
        "/operate/targets": lambda: render_operate_targets(token=token),
        "/operate/bundles": lambda: render_operate_bundles(token=token),
        "/operate/config": render_operate_config,
        "/operate/notifications": render_operate_notifications,
        "/learn/getting-started": render_learn_getting_started,
        "/learn/examples": render_learn_examples,
        "/learn/api": render_learn_api,
        "/learn/primitives": render_learn_primitives,
        "/learn/profile-templates": render_learn_profile_templates,
        "/learn/versions": render_learn_versions,
        "/learn/measurements": render_learn_measurements,
        "/learn/measurement-coverage": render_learn_measurement_coverage,
        "/learn/testcases": render_learn_testcases,
        "/learn/corpora": render_learn_corpora,
        "/learn/grammars": render_learn_grammars,
        "/learn/risk-packages": render_learn_risk_packages,
        "/learn/glossary": render_learn_glossary,
        "/learn/faq": render_learn_faq,
        "/learn/troubleshooting": render_learn_troubleshooting,
        "/learn/operator-runbook": render_learn_operator_runbook,
        "/learn/developer-onboarding": render_learn_developer_onboarding,
        "/learn/plugin-authoring": render_learn_plugin_authoring_guide,
        "/operate/contract": render_operate_contract,
        "/operate/coverage": render_operate_coverage,
        "/operate/crashes": render_operate_crashes,
        "/operate/schedule": render_operate_schedule,
        "/operate/timeline": render_operate_timeline,
        "/operate/static-analysis": render_operate_static_analysis,
        "/learn/walkthroughs": render_learn_walkthroughs,
        "/learn/cli": render_learn_cli,
    }
    renderer = routes.get(route)
    if renderer is not None:
        return renderer()
    if route.startswith("/operate/runs/") and route.endswith("/measurements/raw"):
        rid = route[len("/operate/runs/"):-len("/measurements/raw")]
        if rid and "/" not in rid and ".." not in rid:
            html = render_operate_run_measurements_raw(rid)
            if html is not None:
                return html
            return render_operate_run_not_found(rid)
    # Slice 47 — /operate/runs/<id>/live serves the streaming HTML view.
    # The /tail SSE endpoint is dispatched from do_GET (needs streaming),
    # not from this HTML-only route function.
    if route.startswith("/operate/runs/") and route.endswith("/live"):
        rid = route[len("/operate/runs/"):-len("/live")]
        if rid and "/" not in rid and ".." not in rid:
            from profile_manager.views.operate_run_live import render_operate_run_live
            return render_operate_run_live(rid)
    # /operate/runs/<id> — slice-26 current-gen bundle inspector.
    if route.startswith("/operate/runs/"):
        rid = route[len("/operate/runs/"):]
        if rid and "/" not in rid and ".." not in rid:
            html = render_operate_run(rid, token=token)
            if html is not None:
                return html
            return render_operate_run_not_found(rid)
    # /runs/<id> kept as a legacy alias serving the same current-gen
    # inspector body (avoids breaking already-shared run links).
    if route.startswith("/runs/") and not route.endswith("/bundle"):
        rid = route[len("/runs/"):]
        if rid and "/" not in rid and ".." not in rid:
            html = render_operate_run(rid, token=token)
            if html is not None:
                return html
            return render_operate_run_not_found(rid)
    return None


def _read_ndjson_lines(path, limit=None):
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit is not None and len(out) >= limit:
                break
    return out




def generate_dashboard(output_dir=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.html"
    path.write_text(render_dashboard_html(), encoding="utf-8")
    return DashboardResult(path=path, url=f"file://{path}")


def dashboard_serve_text(output_dir=None, port=8787, bind="0.0.0.0", token=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    urls = "\n".join(f"URL: {url}" for url in _local_interface_urls(port))
    token_active = resolve_token(token=token)
    token_source = (
        "explicit --token"
        if token
        else ("ADA2_DWARF_TOKEN env var" if os.environ.get("ADA2_DWARF_TOKEN") else "default")
    )
    return (
        f"Serving live dashboard from: {directory}\n"
        f"Bind: {bind}:{port}\n"
        f"{urls}\n"
        "Live API: /api/status\n"
        f"Token gate active for mutating endpoints (source: {token_source}; length: {len(token_active)} chars).\n"
        "Read-only routes do not require a token.\n"
        "Browser mutations are token-gated and serialized.\n"
    )


def serve_dashboard_handler_factory(expected_token, *, serving_port=None, serving_bind=None):
    """Build a DashboardHandler class bound to expected_token. Extracted from
    serve_dashboard so tests can spin up their own ThreadingHTTPServer
    without invoking the full serve flow (which prints, generates HTML, and
    blocks on serve_forever).

    serving_port/serving_bind are passed through to /operate/status so the
    "Dashboard serving" tile reflects the live process. Optional — tests
    that don't care can omit them and the tile renders dashes.
    """

    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            safe_args = list(args)
            if safe_args and isinstance(safe_args[0], str):
                request_line = safe_args[0]
                request_parts = request_line.split()
                if len(request_parts) >= 2 and "?" in request_parts[1]:
                    request_parts[1] = request_parts[1].split("?", 1)[0] + "?[query-redacted]"
                    safe_args[0] = " ".join(request_parts)
            print(f"{self.address_string()} - {format % tuple(safe_args)}")

        def _send(self, status, content_type, body, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
            # Slice 44: feed the Prometheus request counter. Path-bucketing
            # bounds cardinality (see data/health_metrics._path_bucket).
            try:
                from profile_manager.data.health_metrics import record_request
                record_request(self.command, self.path, status)
            except Exception:  # noqa: BLE001 — telemetry never breaks responses
                pass

        def _send_stream(self, status, content_type, generator):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for chunk in generator:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                close = getattr(generator, "close", None)
                if callable(close):
                    close()

        def _send_redirect(self, location):
            """Slice-15: 302 with Location header and empty body. Reuses
            _send so cache headers and Content-Length stay consistent."""
            self._send(302, "text/plain; charset=utf-8", b"", extra_headers={"Location": location})

        def do_POST(self):
            clean_path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, "text/plain; charset=utf-8", b"invalid content length\n")
                return
            if clean_path == "/api/versions/import-binary":
                from profile_manager.binary_imports import MAX_IMPORT_REQUEST_BYTES

                if length > MAX_IMPORT_REQUEST_BYTES:
                    self._send(
                        413,
                        "text/plain; charset=utf-8",
                        b"binary import request is too large\n",
                    )
                    return
                if length <= 0:
                    self._send(
                        400,
                        "text/plain; charset=utf-8",
                        b"binary import request is empty\n",
                    )
                    return
                self.connection.settimeout(120)
            body_bytes = self.rfile.read(length) if length > 0 else b""
            if clean_path == "/api/versions/import-binary" and len(body_bytes) != length:
                self._send(
                    400,
                    "text/plain; charset=utf-8",
                    b"binary import request is incomplete\n",
                )
                return
            if self.path.split("?", 1)[0] == "/operate/testcases/ingest":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(
                        403,
                        "text/plain; charset=utf-8",
                        f"{err}\n".encode("utf-8"),
                    )
                    return
                from profile_manager.views.operate_testcases import (
                    dispatch_testcase_ingest_request,
                )

                if not try_acquire_mutating_lock():
                    self._send(
                        409,
                        "text/plain; charset=utf-8",
                        b"another mutating action is already in progress\n",
                    )
                    return
                try:
                    status, message, result = dispatch_testcase_ingest_request(
                        method="POST", path=self.path, body=body_bytes
                    )
                finally:
                    release_mutating_lock()
                if status == 200:
                    self._send(
                        303,
                        "text/plain; charset=utf-8",
                        b"",
                        extra_headers={
                            "Location": f"/operate/testcases/{result['case_id']}"
                        },
                    )
                    return
                self._send(
                    status,
                    "text/plain; charset=utf-8",
                    message.encode("utf-8"),
                )
                return
            from profile_manager.data.operate_run_wizard import (
                dispatch_run_resolve_request,
            )

            run_resolve = dispatch_run_resolve_request(
                method="POST", path=self.path, body=body_bytes
            )
            if run_resolve is not None:
                status, ctype, response_body = run_resolve
                self._send(status, ctype, response_body)
                return
            run_prepare = dispatch_run_prepare_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if run_prepare is not None:
                status, ctype, response_body = run_prepare
                if status == 200 and "event-stream" in ctype:
                    self._send_stream(status, ctype, response_body)
                else:
                    self._send(status, ctype, response_body)
                return
            run_start = dispatch_run_start_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if run_start is not None:
                status, ctype, response_body = run_start
                if status == 200 and "event-stream" in ctype:
                    self._send_stream(status, ctype, response_body)
                else:
                    self._send(status, ctype, response_body)
                return
            binary_import = dispatch_binary_import_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                content_type=self.headers.get("Content-Type") or "",
                expected_token=expected_token,
            )
            if binary_import is not None:
                status, ctype, response_body = binary_import
                self._send(status, ctype, response_body)
                return
            version_refresh = dispatch_version_refresh_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if version_refresh is not None:
                status, ctype, response_body = version_refresh
                self._send(status, ctype, response_body)
                return
            version_preference = dispatch_version_preference_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if version_preference is not None:
                status, ctype, response_body, headers = version_preference
                self._send(status, ctype, response_body, extra_headers=headers)
                return
            run_version_decision = dispatch_run_version_decision_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if run_version_decision is not None:
                status, ctype, response_body, headers = run_version_decision
                self._send(status, ctype, response_body, extra_headers=headers)
                return
            version_registration = dispatch_version_registration_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if version_registration is not None:
                status, ctype, response_body, headers = version_registration
                self._send(status, ctype, response_body, extra_headers=headers)
                return
            catalog_result = dispatch_catalog_mutating_request(
                method="POST", path=self.path, body=body_bytes, expected_token=expected_token
            )
            if catalog_result is not None:
                status, ctype, response_body = catalog_result
                self._send(status, ctype, response_body)
                return
            if self.path.split("?", 1)[0].startswith("/api/corpora/"):
                from profile_manager.data.operate_corpora import dispatch_corpus_mutating_request

                if not try_acquire_mutating_lock():
                    self._send(409, "application/json; charset=utf-8", b'{"ok":false,"error":"another mutating action is already in progress"}')
                    return
                try:
                    corpus_result = dispatch_corpus_mutating_request(
                        method="POST",
                        path=self.path,
                        body=body_bytes,
                        expected_token=expected_token,
                    )
                finally:
                    release_mutating_lock()
                if corpus_result is not None:
                    status, ctype, response_body = corpus_result
                    self._send(status, ctype, response_body)
                    return
            if self.path.split("?", 1)[0].startswith("/api/grammars/"):
                from profile_manager.data.operate_grammars import dispatch_grammar_mutating_request

                if not try_acquire_mutating_lock():
                    self._send(409, "application/json; charset=utf-8", b'{"ok":false,"error":"another mutating action is already in progress"}')
                    return
                try:
                    grammar_result = dispatch_grammar_mutating_request(
                        method="POST",
                        path=self.path,
                        body=body_bytes,
                        expected_token=expected_token,
                    )
                finally:
                    release_mutating_lock()
                if grammar_result is not None:
                    status, ctype, response_body = grammar_result
                    self._send(status, ctype, response_body)
                    return
            if self.path.split("?", 1)[0] == "/api/moog/setup":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.config import DeploymentConfig, config_exists, load_config, save_config
                from profile_manager.data.operate_config import apply_moog_setup_form
                form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
                try:
                    current_config = load_config() if config_exists() else DeploymentConfig.from_dict({})
                    updated = apply_moog_setup_form(current_config, form)
                    save_config(updated)
                except Exception as exc:
                    self._send(500, "text/plain; charset=utf-8", f"failed to save Moog setup: {exc}\n".encode("utf-8"))
                    return
                self._send(303, "text/plain; charset=utf-8", b"", extra_headers={"Location": "/operate/config?saved=moog"})
                return
            # Slice 3 of dispatch 7 — multipart bundle import.
            if self.path.split("?", 1)[0] == "/api/bundle/import":
                ctype = self.headers.get("Content-Type") or ""
                ok, err = check_multipart_form_token(
                    self.path, ctype, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(
                        401,
                        "text/plain; charset=utf-8",
                        f"{err}\n".encode("utf-8"),
                    )
                    return
                from profile_manager.data.bundle_import import handle_bundle_import_post
                status, body = handle_bundle_import_post(ctype, body_bytes)
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            # Slice 7 of dispatch 7 — create scenario from template.
            if self.path.split("?", 1)[0] == "/operate/scenarios/new":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.data.operate_scenarios_new import handle_create_post
                status, body = handle_create_post(body_bytes)
                if status == 200:
                    self._send(
                        303,
                        "text/plain; charset=utf-8",
                        b"",
                        extra_headers={
                            "Location": _new_definition_success_location(
                                self.path, body_bytes
                            )
                        },
                    )
                    return
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            if self.path.split("?", 1)[0] == "/operate/targets/new":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.data.operate_targets_new import handle_register_post
                status, body = handle_register_post(body_bytes)
                if status == 200:
                    self._send(
                        303,
                        "text/plain; charset=utf-8",
                        b"",
                        extra_headers={
                            "Location": _new_definition_success_location(
                                self.path, body_bytes
                            )
                        },
                    )
                    return
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            if self.path.split("?", 1)[0] == "/operate/profiles/new":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.data.operate_profiles_new import handle_create_post
                status, body = handle_create_post(body_bytes)
                if status == 200:
                    self._send(
                        303,
                        "text/plain; charset=utf-8",
                        b"",
                        extra_headers={
                            "Location": _new_definition_success_location(
                                self.path, body_bytes
                            )
                        },
                    )
                    return
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            if self.path.split("?", 1)[0] == "/operate/primitives/new":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.data.operate_primitives_new import handle_create_post as _prim_create
                status, body = _prim_create(body_bytes)
                if status == 200:
                    self._send(
                        303,
                        "text/plain; charset=utf-8",
                        b"",
                        extra_headers={
                            "Location": _new_definition_success_location(
                                self.path, body_bytes
                            )
                        },
                    )
                    return
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            if self.path.split("?", 1)[0] == "/operate/config/save":
                ok, err = check_form_token(
                    self.path, body_bytes, expected=expected_token
                )
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.data.operate_config_edit import handle_config_save
                status, body = handle_config_save(body_bytes)
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            sched_result = dispatch_schedule_request(
                method="POST", path=self.path, body=body_bytes,
                expected_token=expected_token,
            )
            if sched_result is not None:
                status, ctype, body = sched_result
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
                return
            topology_result = dispatch_topology_action_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if topology_result is not None:
                status, ctype, body = topology_result
                if status == 200 and "event-stream" in ctype:
                    self._send_stream(status, ctype, body)
                else:
                    self._send(
                        status,
                        ctype,
                        body
                        if isinstance(body, (bytes, bytearray))
                        else b"".join(body),
                    )
                return
            topology_configuration_result = dispatch_topology_configuration_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if topology_configuration_result is not None:
                status, ctype, response_body = topology_configuration_result
                self._send(status, ctype, response_body)
                return
            scen_result = dispatch_scenario_request(
                method="POST", path=self.path, body=body_bytes,
                expected_token=expected_token,
            )
            if scen_result is not None:
                status, ctype, body = scen_result
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
                return
            anti_result = dispatch_antithesis_request(
                method="POST", path=self.path, expected_token=expected_token,
            )
            if anti_result is not None:
                status, ctype, body = anti_result
                if status == 200 and "event-stream" in ctype:
                    self._send_stream(status, ctype, body)
                else:
                    self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
                return
            result = dispatch_mutating_request(
                method="POST",
                path=self.path,
                body=body_bytes,
                expected_token=expected_token,
            )
            if result is None:
                self._send(404, "text/plain; charset=utf-8", b"not found\n")
                return
            status, ctype, body = result
            if status == 200 and "event-stream" in ctype:
                self._send_stream(status, ctype, body)
            else:
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
        def _resolve_view(self):
            """Basic on every request unless the URL explicitly selects Advanced."""
            from urllib.parse import urlsplit, parse_qs
            q = parse_qs(urlsplit(self.path).query)
            requested = q.get("view", ["basic"])[0].strip().lower()
            view = "bento" if requested == "advanced" else "basic"
            _path = urlsplit(self.path).path
            set_current_path(_path)
            # Every route renders the chosen view; Basic layouts are provided
            # for all routes (generic reducer + per-family templates).
            set_current_view(view)
            return view


        # Raw-HTML reference pages get a concise Basic stub (full page in Advanced).
        BASIC_REFERENCE_STUBS = {
            "/learn/overview": ("Learn", "Overview",
                "The complete DWARF reference on one page — DSL, primitive catalogue, "
                "Antithesis pipeline, coverage, evidence and attack-cost. It is long by design."),
            "/learn/consensus": ("Learn", "Consensus differential",
                "How the Haskell cardano-node and Amaru compare on the same chain under fault "
                "— forks, k-recovery, epoch boundaries."),
        }

        # Basic "Evidence Deck" binder replaces the two very large coverage pages.
        BASIC_BINDER_PAGES = {
            "/learn/threat-coverage": ("threats", "Threat & risk binder"),
            "/learn/measurement-coverage": ("measurement", "Measurement coverage"),
        }

        def _basic_reference_stub(self, path):
            from profile_manager.templating import render, current_view
            if current_view() != "basic":
                return None
            binder = self.BASIC_BINDER_PAGES.get(path)
            if binder:
                from profile_manager.data.basic_deck import deck_binder
                mode, title = binder
                return render("learn/basic_binder.j2", page_title=title, active="learn",
                              mode=mode, binder=deck_binder())
            spec = self.BASIC_REFERENCE_STUBS.get(path)
            if not spec:
                return None
            eyebrow, title, summary = spec
            return render("_basic_reference_stub.j2", page_title=title, eyebrow=eyebrow,
                          summary=summary, active="learn")

        def do_GET(self):
            self._resolve_view()
            _stub = self._basic_reference_stub(self.path.split("?",1)[0])
            if _stub is not None:
                self._send(200, "text/html; charset=utf-8", _stub.encode("utf-8")); return
            # Allow GET to show a 405 for every mutating endpoint (more informative than 404).
            mutating_paths = {
                "/api/deploy", "/api/remove", "/api/fuzz/run", "/api/test/smoke/run",
                "/api/scenario/paste", "/api/scenario/promote", "/api/scenario/compare",
                "/api/scenario/run", "/api/backup/create", "/api/coverage/run",
                "/api/topology/actions",
                "/api/topology/configurations",
                "/api/run/resolve", "/api/run/prepare", "/api/run/start",
                "/api/versions/refresh",
                "/api/versions/preferences",
                "/api/versions/register",
                "/operate/config/save",
            }
            path_only = self.path.split("?", 1)[0]
            deployment_preview = dispatch_deployment_preview_request(
                method="GET", path=self.path
            )
            if deployment_preview is not None:
                status, ctype, response_body = deployment_preview
                self._send(status, ctype, response_body)
                return
            if path_only.startswith("/api/corpora/") and path_only.endswith("/actions"):
                self._send(405, "text/plain; charset=utf-8", b"use POST for mutating endpoints\n")
                return
            if path_only.startswith("/api/grammars/") and path_only.endswith("/actions"):
                self._send(405, "text/plain; charset=utf-8", b"use POST for mutating endpoints\n")
                return
            if path_only in mutating_paths:
                self._send(405, "text/plain; charset=utf-8", b"use POST for mutating endpoints\n")
                return
            target = REDIRECTS.get(path_only)
            if target is not None:
                self._send_redirect(target)
                return
            if path_only == "/operate/status":
                html_body = render_operate_status(
                    port=serving_port,
                    bind=serving_bind,
                    token=expected_token,
                    profile_id=_status_profile_id(self.path),
                )
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            if path_only == "/operate/config":
                html_body = render_operate_config(token=expected_token)
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            if path_only == "/operate/config/edit":
                from profile_manager.views.operate_config_edit import render_operate_config_edit
                self._send(200, "text/html; charset=utf-8", render_operate_config_edit(token=expected_token).encode("utf-8"))
                return
            if path_only == "/operate/antithesis":
                from profile_manager.views.operate_antithesis import render_operate_antithesis
                html_body = render_operate_antithesis(token=expected_token)
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            if path_only == "/operate/targets":
                from profile_manager.views.operate_targets import render_operate_targets
                self._send(200, "text/html; charset=utf-8", render_operate_targets(token=expected_token).encode("utf-8"))
                return
            if path_only == "/operate/primitives/new":
                from profile_manager.views.operate_primitives_new import render_operate_primitives_new
                self._send(200, "text/html; charset=utf-8", render_operate_primitives_new(token=expected_token).encode("utf-8"))
                return
            if path_only == "/operate/scenarios":
                from profile_manager.views.scenarios import render_operate_scenarios
                self._send(200, "text/html; charset=utf-8", render_operate_scenarios(token=expected_token).encode("utf-8"))
                return
            html_body = render_route_html(self.path, token=expected_token)
            if html_body is not None:
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            from profile_manager.views.operate_asset_catalog import (
                render_asset_not_found,
            )

            not_found_html = render_asset_not_found(self.path)
            if not_found_html is not None:
                self._send(
                    404,
                    "text/html; charset=utf-8",
                    not_found_html.encode("utf-8"),
                )
                return
            static_result = dispatch_static_request(self.path)
            if static_result is not None:
                status, ctype, body = static_result
                self._send(status, ctype, body)
                return
            if self.path == "/favicon.ico":
                self._send(204, "image/x-icon", b"")
                return
            if self.path == "/api/status":
                payload = build_dashboard_status_payload(live=True)
                body = json.dumps(payload, indent=2).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if self.path == "/api/health":
                # Slice 26: legacy /raw HTML page retired; /api/health is
                # the JSON-only successor. Identical payload to /api/status
                # so existing tooling can pivot without re-shaping callers.
                payload = build_dashboard_status_payload(live=True)
                body = json.dumps(payload, indent=2).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if path_only in ("/healthz", "/health"):
                # Slice 44: light-weight liveness probe. Distinct from
                # /api/health (full substrate payload) — designed for
                # load-balancer / supervisor probes where the 200-or-503
                # signal is all that's needed.
                from profile_manager.data.health_metrics import healthz_payload
                payload = healthz_payload()
                body = json.dumps(payload, indent=2).encode("utf-8")
                status_code = 200 if payload.get("status") == "ok" else 503
                self._send(status_code, "application/json; charset=utf-8", body)
                return
            if path_only == "/metrics":
                # Slice 44: Prometheus text-exposition endpoint.
                from profile_manager.data.health_metrics import prometheus_exposition
                body = prometheus_exposition()
                self._send(200, "text/plain; version=0.0.4; charset=utf-8", body)
                return
            # Slice 47 — /operate/runs/<id>/tail SSE stream of log.ndjson.
            if path_only.startswith("/operate/runs/") and path_only.endswith("/tail"):
                rid = path_only[len("/operate/runs/"):-len("/tail")]
                if rid and "/" not in rid and ".." not in rid:
                    from profile_manager.data.operate_run_tail import stream_run_tail
                    self._send_stream(200, "text/event-stream; charset=utf-8", stream_run_tail(rid))
                    return
            api = dispatch_api_request(self.path)
            if api is not None:
                status, ctype, body = api[:3]
                extra_headers = api[3] if len(api) > 3 else None
                self._send(status, ctype, body, extra_headers=extra_headers)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

    return DashboardHandler


_SCHEDULER_THREAD_STARTED = False


def _start_scheduler_thread():
    """Item #19 — fire-and-forget daemon thread that polls the schedule
    store every 30 seconds, runs due entries via dispatch_mutating_request's
    builder, and honors the global mutating lock.

    Idempotent: a process-wide flag ensures the thread is started once
    even if serve_dashboard is invoked multiple times in tests.
    """
    global _SCHEDULER_THREAD_STARTED
    if _SCHEDULER_THREAD_STARTED:
        return
    _SCHEDULER_THREAD_STARTED = True

    import threading
    import time as _time

    def _loop():
        from profile_manager.data import scheduler
        while True:
            try:
                scheduler.tick(
                    command_builder=_default_cli_command_builder,
                    lock_acquire=try_acquire_mutating_lock,
                    lock_release=release_mutating_lock,
                )
            except Exception:  # noqa: BLE001
                # Never let an error kill the loop — the dashboard
                # process must keep serving HTTP regardless of the
                # scheduler's health.
                pass
            _time.sleep(30)

    t = threading.Thread(target=_loop, name="dwarf-scheduler", daemon=True)
    t.start()


def serve_dashboard(output_dir=None, port=8787, bind="0.0.0.0", token=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    expected_token = resolve_token(token=token)
    if not (directory / "index.html").exists():
        generate_dashboard(directory)

    DashboardHandler = serve_dashboard_handler_factory(
        expected_token,
        serving_port=int(port),
        serving_bind=bind,
    )

    print(dashboard_serve_text(directory, port, bind, token=token), end="")
    _start_scheduler_thread()
    server = ThreadingHTTPServer((bind, int(port)), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


def port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", int(port))) != 0
