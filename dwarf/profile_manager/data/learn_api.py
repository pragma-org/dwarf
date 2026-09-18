"""REST API reference data (slice 2 of dispatch 8).

Curated endpoint catalog rather than runtime route-table introspection
because many routes mix HTML/JSON or wrap dispatchers; a hand-listed
spec is more accurate AND easier to keep correct than reflection over
the dispatcher.
"""
from __future__ import annotations

from typing import Any


# Each endpoint: path, method(s), kind (json/sse/html-only/binary),
# description, parameters, response, example (str).
ENDPOINTS: list[dict[str, Any]] = [
    {
        "path": "/healthz",
        "aliases": ["/health"],
        "method": "GET",
        "kind": "json",
        "description": "Lightweight liveness probe. 200 when every component is ok; 503 otherwise. Designed for load-balancer / supervisor probes — distinct from /api/health which carries the full substrate payload.",
        "parameters": [],
        "response_schema": {
            "status": "ok | fail",
            "components": {"dashboard": "ok|fail", "runs_dir": "ok|fail", "state_dir": "ok|fail"},
            "uptime_seconds": "number",
        },
        "example": '{"status":"ok","components":{"dashboard":"ok","runs_dir":"ok","state_dir":"ok"},"uptime_seconds":42.18}',
    },
    {
        "path": "/metrics",
        "aliases": [],
        "method": "GET",
        "kind": "prometheus",
        "description": "Prometheus text-exposition format (https://prometheus.io/docs/instrumenting/exposition_formats/). Eight metric families: requests_total, runs_total, assertions_pass_total, assertions_fail_total, substrate_compose_total, bundle_size_bytes, bundle_count, dashboard_uptime_seconds.",
        "parameters": [],
        "response_schema": {"_": "Prometheus text format — see https://prometheus.io/docs/concepts/data_model/"},
        "example": (
            "# HELP dwarf_runs_total Count of recorded runs by manifest.exit_status outcome.\n"
            "# TYPE dwarf_runs_total counter\n"
            "dwarf_runs_total{outcome=\"pass\"} 945\n"
            "dwarf_runs_total{outcome=\"fail\"} 135\n"
            "..."
        ),
    },
    {
        "path": "/api/health",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Full substrate-status payload — same shape as /api/status. Carries live SSH-poll output, last-cached evidence, profile metadata. Heavier than /healthz; use this when you need the substrate snapshot, not for probing.",
        "parameters": [],
        "response_schema": {"_": "Full DashboardStatus dict — see profile_manager.dashboard.build_dashboard_status_payload"},
        "example": '{"live": {...}, "profiles": [...], "last_local_health": {...}}',
    },
    {
        "path": "/api/status",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Identical payload to /api/health. Predates the rename; kept as a stable alias for the legacy live-runtime card.",
        "parameters": [],
        "response_schema": {"_": "see /api/health"},
        "example": "(same as /api/health)",
    },
    {
        "path": "/api/runs",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Recent-runs payload over the local + remote run sources. Backs the /operate/runs page.",
        "parameters": [
            {"name": "limit", "kind": "query", "required": False, "type": "integer (1-200)", "default": "50"},
        ],
        "response_schema": {"recent_runs": "[{run_id, ended_at, scenario_id, exit_status, runtime, source}, ...]"},
        "example": '{"recent_runs": [{"run_id": "20260427T154920Z-4bdcb76f", "exit_status": "pass", "scenario_id": "honest-baseline-smoke", ...}]}',
    },
    {
        "path": "/operate/runs/<id>/tail",
        "aliases": [],
        "method": "GET",
        "kind": "sse",
        "description": "Server-Sent Events stream of the run's log.ndjson. Emits a `hello` event with the run-id, then every existing log line as a `log` event, then live `log` events as the file grows. Closes with `end` (reason=manifest) when manifest.json appears or `end` (reason=idle_timeout) after 600s of silence. Heartbeat SSE comments every poll.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "text/event-stream — events: hello, log, end, error"},
        "example": "event: hello\ndata: 20260427T154920Z-4bdcb76f\n\nevent: log\ndata: {\"event\":\"started\",...}\n\nevent: end\ndata: manifest\n",
    },
    {
        "path": "/runs/<id>/bundle",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download a run's forensic bundle as tar.gz. Streams the archive verbatim. Powers the 'Export bundle' button on the run inspector.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "application/gzip — full run-bundle archive"},
        "example": "(binary tar.gz)",
    },
    {
        "path": "/api/bundle/import",
        "aliases": [],
        "method": "POST",
        "kind": "html-result",
        "description": "Multipart upload of a previously-exported bundle.tar.gz. The framework verifies the bundle's hash chain before unpacking it into runs/<run_id>/. Existing run-ids are NOT overwritten.",
        "parameters": [
            {"name": "bundle", "kind": "form-multipart", "required": True, "type": "file", "default": ""},
        ],
        "response_schema": {"_": "HTML result page with helper stdout/stderr"},
        "example": "(HTML)",
    },
    {
        "path": "/runs/<id>/output",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Per-artifact download from a run bundle's outputs/ tree. Path-traversal-guarded: only files under runs/<id>/outputs/ resolve.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
            {"name": "path", "kind": "query", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "the artifact bytes; content-type sniffed from extension"},
        "example": "(binary)",
    },
    {
        "path": "/api/catalog/<catalog>/<id>/download",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download the exact source for one scenario, target, or profile. Catalog names and identifiers are allow-listed and path-traversal guarded.",
        "parameters": [
            {"name": "catalog", "kind": "path", "required": True, "type": "scenarios | targets | profiles", "default": ""},
            {"name": "id", "kind": "path", "required": True, "type": "definition id", "default": ""},
        ],
        "response_schema": {"_": "application/yaml — exact stored definition"},
        "example": "(YAML or JSON-compatible YAML bytes)",
    },
    {
        "path": "/api/catalog/<catalog>/export",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download a deterministic tar.gz containing only the definitions in one allow-listed catalog, using their public repository paths.",
        "parameters": [
            {"name": "catalog", "kind": "path", "required": True, "type": "scenarios | targets | profiles", "default": ""},
        ],
        "response_schema": {"_": "application/gzip — definition-only archive"},
        "example": "(binary tar.gz)",
    },
    {
        "path": "/api/assets/<catalog>/export /api/assets/<catalog>/<id>/export",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download a deterministic tar.gz containing every exact source record in one registered reusable-asset catalog, or one selected object, plus DWARF-EXPORT-MANIFEST.json with source revision, reproducible generation time, object IDs, portable source/export paths, sizes, and SHA-256 hashes. The generic registry currently owns primitives, profile-templates, testcases, and testcase-buckets; corpora, grammars, risk-packages, and plugins use the specialized endpoints below because one object can contain multiple exact-byte sources. Catalog names are explicitly allow-listed; traversal, symlinks, platform metadata, caches, bytecode, and sensitive-named files are excluded.",
        "parameters": [
            {"name": "catalog", "kind": "path", "required": True, "type": "primitives | profile-templates | testcases | testcase-buckets", "default": ""},
            {"name": "id", "kind": "path", "required": False, "type": "asset id", "default": ""},
        ],
        "response_schema": {"_": "application/gzip — source-only asset archive"},
        "example": "(binary tar.gz)",
    },
    {
        "path": "/api/assets/<catalog>/<id>/download",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download the exact retained source for one reusable asset. Catalog and object identifiers are allow-listed and restricted to single safe URL segments.",
        "parameters": [
            {"name": "catalog", "kind": "path", "required": True, "type": "registered asset catalog", "default": ""},
            {"name": "id", "kind": "path", "required": True, "type": "asset id", "default": ""},
        ],
        "response_schema": {"_": "exact source bytes with the catalog-provided content type"},
        "example": "(source file)",
    },
    {
        "path": "/api/assets/testcases/<id>/artifact",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download the exact retained testcase input or run artifact. The lifecycle record must resolve beneath its own retained run directory; absolute paths, traversal, and escaping symlinks are rejected.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "testcase id", "default": ""},
        ],
        "response_schema": {"_": "application/octet-stream — exact retained artifact bytes"},
        "example": "(binary testcase or retained run artifact)",
    },
    {
        "path": "/api/corpora/export /api/corpora/<id>/export /api/corpora/<id>/inputs/<name>/download",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Deterministic exact-byte export of every explicitly configured corpus, one corpus, or one containment-checked input. Archives include DWARF-EXPORT-MANIFEST.json. Shipped and runtime roots are allow-listed; symlinks, traversal, Apple metadata, caches, bytecode, and sensitive-named files are excluded.",
        "parameters": [
            {"name": "id", "kind": "path", "required": False, "type": "corpus id", "default": ""},
            {"name": "name", "kind": "path", "required": False, "type": "safe input filename", "default": ""},
        ],
        "response_schema": {"_": "application/gzip or application/octet-stream"},
        "example": "(binary)",
    },
    {
        "path": "/api/corpora/runtime--overlay/actions",
        "aliases": [],
        "method": "POST",
        "kind": "json",
        "description": "Token-gated runtime-overlay management. Supports bounded atomic import, testcase promotion, SHA-256 deduplication, disable/enable, and recoverable input removal. Shipped corpus IDs reject every mutation.",
        "parameters": [
            {"name": "token", "kind": "query", "required": True, "type": "string", "default": ""},
            {"name": "action", "kind": "JSON body", "required": True, "type": "import | promote | deduplicate | disable | enable | remove", "default": ""},
        ],
        "response_schema": {"ok": "boolean", "result": "string", "error": "string"},
        "example": '{"action":"import","filename":"seed.cbor","content_base64":"…"}',
    },
    {
        "path": "/api/grammars/export /api/grammars/<id>/export /api/grammars/<id>/<source>/download",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Deterministic export of every explicitly configured generation structure and mutation-token set, one definition, or one exact structure.json/dict.txt source. Archives include DWARF-EXPORT-MANIFEST.json. Repository and runtime roots are allow-listed; traversal and symlinks are rejected.",
        "parameters": [
            {"name": "id", "kind": "path", "required": False, "type": "generation grammar id", "default": ""},
            {"name": "source", "kind": "path", "required": False, "type": "structure.json | dict.txt", "default": ""},
        ],
        "response_schema": {"_": "application/gzip, application/json, or text/plain exact source"},
        "example": "(binary or exact source bytes)",
    },
    {
        "path": "/api/grammars/<id>/actions",
        "aliases": [],
        "method": "POST",
        "kind": "json",
        "description": "Token-gated clone/save endpoint. Clone copies an immutable shipped source pair into the explicit runtime root. Save replaces only a runtime clone after the generation-structure schema and finite mutation-token parser both pass.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "generation grammar id", "default": ""},
            {"name": "token", "kind": "query", "required": True, "type": "string", "default": ""},
            {"name": "action", "kind": "JSON body", "required": True, "type": "clone | save", "default": ""},
        ],
        "response_schema": {"ok": "boolean", "result": "cloned | saved", "grammar_id": "runtime id", "error": "validation error"},
        "example": '{"action":"clone"}',
    },
    {
        "path": "/api/risk-packages/export /api/risk-packages/<id>/export /api/risk-packages/<id>/download /api/risk-packages/<id>/evidence/<index>/download",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Read-only deterministic export of the compatible risk work-package source catalog, one exact package source, or an evidence file that actually exists beneath the explicit evidence root. Archives include DWARF-EXPORT-MANIFEST.json. Missing and unsafe historical references never receive fabricated downloads.",
        "parameters": [
            {"name": "id", "kind": "path", "required": False, "type": "risk work-package id", "default": ""},
            {"name": "index", "kind": "path", "required": False, "type": "declared evidence-path index", "default": ""},
        ],
        "response_schema": {"_": "application/gzip, application/yaml, or exact evidence bytes"},
        "example": "(binary or exact source bytes)",
    },
    {
        "path": "/api/plugins/export /api/plugins/<id>/export",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Read-only deterministic export of all inventoried plugin catalog sources or one plugin. Only the contained plugin.json and explicitly declared registry and entrypoint files are selected; inventory never imports or executes an entrypoint. Archives include DWARF-EXPORT-MANIFEST.json and exclude symlinks, traversal, platform metadata, caches, bytecode, and sensitive-named files.",
        "parameters": [
            {"name": "id", "kind": "path", "required": False, "type": "plugin catalog id", "default": ""},
        ],
        "response_schema": {"_": "application/gzip — source-only plugin catalog archive"},
        "example": "(binary tar.gz)",
    },
    {
        "path": "/api/catalog/<catalog>/validate /api/catalog/<catalog>/<id>/save",
        "aliases": [],
        "method": "POST",
        "kind": "json",
        "description": "Token-gated structured-builder endpoints. Validate parses JSON or YAML without writing. Save repeats canonical validation and atomically creates or replaces one definition; failed validation never changes the file.",
        "parameters": [
            {"name": "catalog", "kind": "path", "required": True, "type": "scenarios | targets | profiles", "default": ""},
            {"name": "id", "kind": "path-or-query", "required": False, "type": "immutable definition id", "default": ""},
            {"name": "token", "kind": "query", "required": True, "type": "string", "default": ""},
            {"name": "create", "kind": "query", "required": False, "type": "boolean", "default": "false"},
        ],
        "response_schema": {"ok": "boolean", "id": "string", "data": "validated definition", "url": "detail URL after save", "error": "validation error"},
        "example": '{"ok":true,"id":"my-scenario","url":"/operate/scenarios/my-scenario"}',
    },
    {
        "path": "/api/deploy/preview",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Resolve a profile's node-version policy into exact releases, artifacts, scoped status, compatibility evidence, and acknowledgement/block state without changing runtime state.",
        "parameters": [
            {"name": "profile", "kind": "query", "required": True, "type": "profile id", "default": ""},
        ],
        "response_schema": {"profile_id": "string", "policy": "string", "scope": "string", "status": "confirmed|unknown|incompatible|blocked", "resolved": "implementation-to-release map", "catalog_revision": "sha256"},
        "example": '{"profile_id":"mixed","policy":"latest-confirmed","status":"confirmed","resolved":{"cardano-node":{"version":"10.7.1"},"amaru":{"version":"10.11.0"}}}',
    },
    {
        "path": "/api/deploy /api/remove /api/fuzz/run /api/test/smoke/run /api/scenario/run /api/scenario/compare /api/scenario/paste /api/scenario/promote",
        "aliases": [],
        "method": "POST",
        "kind": "sse-or-text",
        "description": "Mutating action endpoints, each gated behind the dashboard token. Long-running ones (deploy, fuzz, scenario_run) stream their stdout/stderr as SSE; short ones (paste, promote) return text. GET on these paths returns 405. A global mutating-lock serializes them so two operators can't deploy simultaneously.",
        "parameters": [
            {"name": "token", "kind": "query", "required": True, "type": "string", "default": ""},
            {"name": "(see CLI reference for per-endpoint params)", "kind": "form-or-query", "required": False, "type": "varies", "default": ""},
        ],
        "response_schema": {"_": "text/event-stream OR text/plain — see cli docs"},
        "example": "(SSE)",
    },
]


def html_route_groups() -> list[dict[str, Any]]:
    """The HTML-only routes the dashboard surfaces, grouped by namespace
    so the /learn/api page can render one card per group instead of
    one giant bullet list."""
    operate = [
        "/operate", "/operate/runs", "/operate/runs/<id>", "/operate/runs/<id>/live",
        "/operate/scenarios", "/operate/scenarios/new", "/operate/scenarios/<id>",
        "/operate/scenarios/<id>/edit",
        "/operate/compare", "/operate/compare/runs",
        "/operate/profiles", "/operate/profiles/new", "/operate/profiles/<id>",
        "/operate/profiles/<id>/edit", "/operate/versions",
        "/operate/profile-templates", "/operate/profile-templates/<id>",
        "/operate/testcases", "/operate/testcases/<id>",
        "/operate/testcase-buckets", "/operate/testcase-buckets/<id>",
        "/operate/corpora", "/operate/corpora/<id>",
        "/operate/grammars", "/operate/grammars/<id>",
        "/operate/risk-packages", "/operate/risk-packages/<id>",
        "/operate/bundles", "/operate/targets", "/operate/targets/new",
        "/operate/targets/<id>", "/operate/targets/<id>/edit",
        "/operate/status", "/operate/coverage", "/operate/timeline",
        "/operate/static-analysis", "/operate/contract", "/operate/crashes",
        "/operate/audit", "/operate/schedule", "/operate/antithesis",
        "/operate/primitives", "/operate/primitives/<id>",
        "/operate/primitives/new", "/operate/plugins", "/operate/plugins/<id>",
        "/operate/config",
        "/operate/config/edit", "/operate/notifications",
    ]
    learn = [
        "/learn", "/learn/getting-started", "/learn/examples", "/learn/primitives",
        "/learn/profile-templates", "/learn/versions",
        "/learn/testcases",
        "/learn/corpora",
        "/learn/grammars",
        "/learn/risk-packages",
        "/learn/walkthroughs", "/learn/architecture",
        "/learn/concepts", "/learn/glossary",
        "/learn/api", "/learn/faq", "/learn/troubleshooting",
        "/learn/coverage", "/learn/threat-coverage", "/learn/consensus",
        "/learn/status", "/learn/cli", "/learn/overview",
        "/learn/attack-cost", "/learn/operator-runbook",
        "/learn/developer-onboarding", "/learn/plugin-authoring",
    ]
    top_level = ["/"]
    return [
        {"label": "Operate", "routes": operate, "count": len(operate)},
        {"label": "Learn", "routes": learn, "count": len(learn)},
        {"label": "Top-level", "routes": top_level, "count": len(top_level)},
    ]


def api_payload() -> dict[str, Any]:
    groups = html_route_groups()
    html_count = sum(g["count"] for g in groups)
    # Machine-readable count: strict-JSON + Prometheus (treat /metrics as
    # machine-readable text). Excludes SSE streams and binary downloads.
    machine_readable = sum(
        1 for e in ENDPOINTS if e["kind"] in ("json", "prometheus")
    )
    return {
        "endpoints": ENDPOINTS,
        "html_route_groups": groups,
        "html_count": html_count,
        "machine_readable_count": machine_readable,
        "json_count": sum(1 for e in ENDPOINTS if e["kind"] == "json"),
        "sse_count": sum(1 for e in ENDPOINTS if e["kind"] in ("sse", "sse-or-text")),
        "binary_count": sum(1 for e in ENDPOINTS if e["kind"] == "binary"),
    }
