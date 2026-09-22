"""Jinja2 environment for the dwarf dashboard.

A single shared Environment rooted at dwarf/dashboard/templates/.
Auto-escapes HTML by default. Exposes a `data_layer` global so
templates can call slice-1 data extractors without importing them.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from profile_manager import data as data_layer

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.globals["data_layer"] = data_layer


import contextvars as _ctxvars

# View toggle (Basic vs Advanced/Bento). Set per-request by the HTTP handler
# from the dwarf_view cookie / ?view= query. Falls back to "bento" when unset
# so non-HTTP renders and tests keep the committed Advanced behaviour.
CURRENT_VIEW = _ctxvars.ContextVar("dwarf_view", default=None)
CURRENT_PATH = _ctxvars.ContextVar("dwarf_path", default="/")


def set_current_view(value):
    CURRENT_VIEW.set(value if value in ("basic", "bento") else None)


def current_view():
    return CURRENT_VIEW.get() or "bento"


def set_current_path(value):
    CURRENT_PATH.set(value or "/")


def current_path():
    return CURRENT_PATH.get() or "/"


def render(template_name: str, **context) -> str:
    """Render a template by name with the given context. Returns HTML string.

    Slice 34: when the caller passes an `active` ("operate" / "learn"),
    the sub-nav catalogue for that section is auto-injected as
    `sub_nav`. Views need only pass `active_sub=<slug>` to get the
    matching chip highlighted; landing pages omit `active_sub` and the
    strip renders without an active chip. Views that don't set `active`
    (legacy chrome flows) get an empty sub_nav and the partial
    suppresses the strip entirely.
    """
    from profile_manager.data.sub_nav import sub_nav_for
    from profile_manager.data.dashboard_theme import current_theme
    context.setdefault("sub_nav", sub_nav_for(context.get("active")))
    context.setdefault("active_sub", None)
    context.setdefault("ui", current_view())
    context.setdefault("request_path", current_path())
    context.setdefault("theme", current_theme())
    return env.get_template(template_name).render(**context)
