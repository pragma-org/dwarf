# DWARF Framework

This directory contains the DWARF framework code.

The framework provides:

- `cardano-profile`, the command-line interface (CLI) entrypoint.
- The dashboard application rendered under `/operate` and `/learn`.
- The current scenario catalog under `scenarios/`.
- Primitive schemas and registry data under `primitives/`.
- Profile and profile-template examples under `profiles/`.
- Preserved bundle archives under `bundles/`.
- Documentation under `docs/`.

The delivery wrapper at the repository root is the intended operator entrypoint:

```bash
bash delivery/scripts/install.sh
bash delivery/scripts/build-image.sh
bash delivery/scripts/deploy.sh
bash delivery/scripts/status.sh
```

After deployment, open:

```text
http://127.0.0.1:8787/operate
http://<host-lan-ip>:8787/operate
```

The Scenarios, Targets, and Profiles pages are complete definition catalogs.
Operators can search and inspect every mounted definition, download one exact
source file, or export the complete catalog as a deterministic `tar.gz`. New and
Edit open schema-backed Structured builders with an Advanced JSON/YAML mode.
Server-side validation, immutable edit IDs, safe path resolution, and atomic
saves protect the catalog; existing scenario Run and profile Deploy controls use
the established execution and deployment paths.

Operate also exposes first-class catalogs for primitives, immutable profile
templates, testcase lifecycle records and their derived buckets, exact-byte fuzz
corpora, generation grammars, risk work packages, and plugins. Their source of
truth remains the registry, repository asset, or retained runtime record named on
the detail page. These views do not turn derived buckets into files, execute a
plugin while inventorying it, or make immutable shipped assets browser-editable.
The matching Learn pages document each object’s purpose, schema, lifecycle,
mutability, and explicit relationships; `/learn/examples#asset-examples` provides
one concrete example of every catalog.

Reusable-asset exports include `DWARF-EXPORT-MANIFEST.json` with the exact source
revision, reproducible generation time, object IDs, portable source/export paths,
byte sizes, and SHA-256 hashes. Exports are source-only: traversal, symlinks,
platform metadata, caches, bytecode, and sensitive-named paths are excluded.

Scenario fields and primitive controls are derived from the current scenario
schema, primitive registry, and primitive parameter schemas rather than a second
hand-maintained list. See `OPERATIONS.md` for routes, API endpoints, and the
operator workflow.

The framework container is designed for any Docker-capable Linux host with Docker Compose v2. Runtime data is retained under `~/.local/share/dwarf/` by default; set `DWARF_RUNTIME_ROOT` to use another location.

For package-level install and operation instructions, use the root `INSTALL.md` and `OPERATIONS.md`.
