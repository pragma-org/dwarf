"""Minimal DWARF product entrance with live readiness signals."""
from __future__ import annotations

from profile_manager.data.operate_topology_health import topology_health_snapshot
from profile_manager.data.operate_versions import version_default_summary
from profile_manager.remote import control_shim_enabled
from profile_manager.templating import current_view, render


def render_landing() -> str:
    deck = None
    if current_view() == "basic":
        from profile_manager.data.basic_deck import deck_gaps, deck_runs

        deck = {"runs": deck_runs(limit=100), "gaps": deck_gaps()}
    return render(
        "landing.j2",
        page_title="DWARF",
        topology_health=topology_health_snapshot(),
        control_enabled=control_shim_enabled(),
        version_defaults=version_default_summary(),
        deck=deck,
    )
