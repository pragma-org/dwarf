"""Profile template discovery and rendering."""

from __future__ import annotations

from pathlib import Path
import re


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "profiles" / "templates"
_TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


def list_templates(*, templates_dir: Path | None = None) -> list[str]:
    root = Path(templates_dir or TEMPLATES_DIR)
    return sorted(
        path.stem
        for path in root.glob("*.yaml")
        if path.is_file() and _TEMPLATE_NAME.fullmatch(path.stem)
    )


def template_path(template_name: str, *, templates_dir: Path | None = None) -> Path:
    root = Path(templates_dir or TEMPLATES_DIR).resolve()
    if not _TEMPLATE_NAME.fullmatch(template_name or "") or template_name.startswith("._"):
        raise FileNotFoundError(f"unknown profile template: {template_name}")
    candidate = (root / f"{template_name}.yaml").resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError(f"unknown profile template: {template_name}")
    return candidate


def render_template_source(
    *,
    template_name: str,
    profile_name: str,
    templates_dir: Path | None = None,
) -> str:
    """Render a profile template entirely in memory."""

    source_path = template_path(template_name, templates_dir=templates_dir)
    body = source_path.read_text(encoding="utf-8")
    return (
        body.replace("{{PROFILE_ID}}", profile_name)
        .replace("{{PROFILE_LABEL}}", profile_name)
        .replace("{{REMOTE_RUNTIME_ROOT}}", f"/opt/dwarf/profiles/{profile_name}")
        .replace("{{COMPOSE_PROJECT}}", f"dwarf-{profile_name}")
    )


def render_template(
    *,
    template_name: str,
    profile_name: str,
    output_path: Path,
    templates_dir: Path | None = None,
) -> Path:
    body = render_template_source(
        template_name=template_name,
        profile_name=profile_name,
        templates_dir=templates_dir,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return output_path
