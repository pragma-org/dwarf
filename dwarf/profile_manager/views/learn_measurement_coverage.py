"""Learn page for measurement applicability and retained evidence coverage."""
from __future__ import annotations

from profile_manager.data.measurement_coverage import measurement_coverage_payload
from profile_manager.templating import render


def render_learn_measurement_coverage() -> str:
    payload = measurement_coverage_payload()
    labels = {
        "threats": "Threats",
        "risks": "Risks",
        "scenario_families": "Scenario families",
        "surfaces": "Node/protocol surfaces",
        "client_requirements": "Client requirements",
    }
    return render(
        "learn/measurement_coverage.j2",
        page_title="Measurement coverage",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="measurement-coverage",
        payload=payload,
        view_sections=[
            {"id": view_id, "label": label, "rows": payload["views"][view_id]}
            for view_id, label in labels.items()
        ],
    )
