"""Operate-side Measurement and Measurement Profile catalogs."""
from __future__ import annotations

from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.templating import render


def _catalog_rows(catalog: str) -> list[dict]:
    rows = []
    for record in list_definitions(catalog):
        data = record.data
        compatibility = data.get("compatibility") or {}
        selections = data.get("measurements") or []
        rows.append(
            {
                "id": record.definition_id,
                "title": data.get("title") or data.get("label") or record.definition_id,
                "description": data.get("description") or "",
                "implementation": (
                    data.get("implementation")
                    or compatibility.get("implementation")
                    or "—"
                ),
                "modes": list(
                    data.get("target_modes")
                    or compatibility.get("target_modes")
                    or []
                ),
                "collection_mode": data.get("collection_mode") or "",
                "overhead_class": data.get("overhead_class") or "",
                "default_enabled": data.get("default_enabled"),
                "measurement_count": len(selections),
                "search": " ".join(
                    str(value)
                    for value in (
                        record.definition_id,
                        data.get("title") or data.get("label") or "",
                        data.get("description") or "",
                        data.get("implementation") or compatibility.get("implementation") or "",
                        data.get("collection_mode") or "",
                        " ".join(data.get("target_modes") or compatibility.get("target_modes") or []),
                    )
                ).lower(),
            }
        )
    return rows


def _render(catalog: str) -> str:
    is_profile = catalog == "measurement-profiles"
    rows = _catalog_rows(catalog)
    return render(
        "operate/measurement_catalog.j2",
        page_title="Measurement profiles" if is_profile else "Measurements",
        density="reading",
        layout="wide",
        active="operate",
        active_sub=catalog,
        catalog=catalog,
        rows=rows,
        empty=not rows,
        heading="Measurement profiles" if is_profile else "Measurements",
        eyebrow="Reusable selections" if is_profile else "Independent taps",
        description=(
            "Profiles select compatible taps without changing scenario semantics. "
            "Threshold gates remain explicit opt-in."
            if is_profile
            else "Measurements are independent, version-aware taps attached to real-node scenarios. "
            "Unavailable boundaries stay unavailable rather than becoming zero."
        ),
        create_label="+ New measurement profile" if is_profile else "+ New measurement",
        other_url="/operate/measurements" if is_profile else "/operate/measurement-profiles",
        other_label="Measurements" if is_profile else "Measurement profiles",
    )


def render_operate_measurements() -> str:
    return _render("measurements")


def render_operate_measurement_profiles() -> str:
    return _render("measurement-profiles")
