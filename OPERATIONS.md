# Operations Guide

This guide covers normal operation after Dwarf has been installed and the framework image has been built.

## Lifecycle Commands

Full bring-up in one command — seeds the catalog, builds the image, and starts the
container:

```bash
bash delivery/scripts/install.sh
```

When provisioning or refreshing the optional control channel on a deployment
whose substrate host is not loopback, pass the same host and user shown in
`/operate/config`:

```bash
DEPLOY_HOST=<configured-host> DEPLOY_USER=<configured-user> \
  bash delivery/scripts/install.sh --control-channel --no-build
```

`provision-control-channel.sh` defaults to `127.0.0.1` and the current user.
Those defaults are appropriate for a same-host fresh install, but an upgrade
must not use them when the saved DWARF deployment points elsewhere: provisioning
rewrites the staged `known_hosts` file for the selected host.

`install.sh` calls the two scripts below for you; run them individually to rebuild or
restart without re-seeding.

Build the Docker image:

```bash
bash delivery/scripts/build-image.sh
```

Deploy (or restart) the container:

```bash
bash delivery/scripts/deploy.sh
```

Status:

```bash
bash delivery/scripts/status.sh
```

Undeploy while preserving runtime data:

```bash
bash delivery/scripts/undeploy.sh
```

Conservative uninstall:

```bash
bash delivery/scripts/uninstall.sh
```

## Dashboard Routes

After deployment, open:

```text
http://127.0.0.1:8787/operate
http://<host-local-area-network-ip>:8787/operate
```

Useful routes:

| Route | Purpose |
|---|---|
| `/operate` | Operational overview |
| `/operate/scenarios` | Scenario catalog |
| `/operate/targets` | Registered targets |
| `/operate/profiles` | Configured profiles |
| `/operate/status` | Deployment/status view |
| `/operate/bundles` | Evidence bundle browser |
| `/operate/coverage` | Coverage roll-up |
| `/operate/plugins` | Plugin and primitive registry |
| `/operate/config` | Read-only resolved config view |
| `/learn` | Documentation and onboarding pages |
| `/api/status` | JavaScript Object Notation (JSON) status payload |

The root path redirects to `/operate`.

## Definition Catalogs And Builders

DWARF exposes the complete mounted scenario, target, and profile catalogs through
the dashboard. Each catalog supports search, a complete detail view, exact source
download, and a deterministic full-catalog `tar.gz` export:

| Definition | Catalog | Create | Detail / edit |
|---|---|---|---|
| Scenario | `/operate/scenarios` | `/operate/scenarios/new` | `/operate/scenarios/<id>` and `/operate/scenarios/<id>/edit` |
| Target | `/operate/targets` | `/operate/targets/new` | `/operate/targets/<id>` and `/operate/targets/<id>/edit` |
| Profile | `/operate/profiles` | `/operate/profiles/new` | `/operate/profiles/<id>` and `/operate/profiles/<id>/edit` |

The default Structured view presents supported fields, enumerations, and
repeatable values as form controls. Advanced accepts the complete JSON or YAML
definition. Switching modes preserves valid extension fields instead of silently
discarding them. IDs become immutable after creation.

Scenario controls are generated from `dwarf/spec/v1/schema.json`, the current
primitive registry, and each primitive's declared parameter schema. Primitive
choices are filtered by lifecycle phase, runtime, and target implementation.
Profile and target controls likewise cover the supported schema while Advanced
mode remains available for valid extension fields.

Validate before saving. The server repeats syntactic and semantic validation,
rejects unsupported values and unsafe IDs, and replaces the destination file
atomically only after validation succeeds. Existing scenario Run and profile
Deploy controls continue to use the same runtime and deployment paths; the
builders do not introduce a second execution engine.

Read-only catalog endpoints are:

```text
/api/catalog/<catalog>/<id>/download
/api/catalog/<catalog>/export
```

`<catalog>` is one of `scenarios`, `targets`, or `profiles`. Exports retain the
repository layout under `dwarf/` and omit runtime state, bundles, caches, secrets,
and operating-system metadata.

## Graphical User Interface Handoff Documentation

The polished visual handoff page for recipients is:

```text
docs/index.html
```

It is a self-contained HyperText Markup Language (HTML) file covering the V3 delivery scope and the deployed dashboard. Use it when introducing the package to a new operator or when hosting the overview from GitHub Pages.

## Command-Line Interface Inside The Container

The framework lives at:

```text
/home/dwarf/dwarf-fw
```

The command-line interface (CLI) entrypoint is:

```text
python3 dwarf/cardano-profile
```

Examples:

```bash
docker exec dwarf-fw python3 dwarf/cardano-profile dashboard status
docker exec dwarf-fw python3 dwarf/cardano-profile test smoke list
docker exec dwarf-fw python3 dwarf/cardano-profile fuzz list
docker exec dwarf-fw python3 dwarf/cardano-profile scenario validate dwarf/scenarios/throughput-regression-floor-example-smoke.yaml
```

To prove that a profile produced by the builder reaches the existing deployment
control path without changing the active runtime, use a dry run:

```bash
docker exec \
  -e ADA2_PROFILE_MANAGER_CONFIG=/var/dwarf/state/config.yaml \
  dwarf-fw python3 dwarf/cardano-profile deploy <profile-id> --dry-run
```

Open a shell:

```bash
docker exec -it dwarf-fw bash
```

## Running With A Non-Default Port

If another service already uses `8787`, deploy on another host port:

```bash
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/deploy.sh
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/status.sh
```

The container still listens internally on `8787`; only the host port changes.

## Moog Setup Form

Open `/operate/config` to enter Moog, GitHub, target repository, and Antithesis values from the dashboard. The form saves to the Dwarf `moog` config block and reads Docker environment variables as effective overrides. Values from `.env` are available when `delivery/scripts/deploy.sh` starts the container.

Client-prep mode allows saving GitHub PATs, Antithesis passwords/API keys, and agent email passwords in `var/state/config.yaml`. Secret inputs are masked after save, and a blank secret submission preserves the existing saved value.

## Optional Moog Bootstrap And Healthchecks

Dwarf does not set up Moog during normal install or deploy. To keep wallet/secrets/service changes explicit, the deploy script only runs Moog setup when requested:

```bash
DWARF_MOOG_BOOTSTRAP=plan bash delivery/scripts/deploy.sh
```

The plan mode prints the Moog bootstrap plan and changes no remote state. The approved mode requires a second confirmation variable:

```bash
DWARF_MOOG_BOOTSTRAP=approve \
DWARF_MOOG_BOOTSTRAP_APPROVE=1 \
bash delivery/scripts/deploy.sh
```

Approved bootstrap creates only the Moog directory skeleton and a remote operator plan file. It does not fetch binaries, create wallet files, read secrets, write PATs, write Antithesis credentials, enable systemd units, or start Moog services.

Use this healthcheck sequence after bootstrap or manual Moog changes:

```bash
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog bootstrap --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog healthcheck --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile wallet healthcheck moog-requester --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog readiness --repo <org/repo> --github-user <user> --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json
```

## Runtime Data Layout

Host paths:

```text
var/runs
var/state
var/bundles
```

Container paths:

```text
/var/dwarf/runs
/var/dwarf/state
/var/dwarf/bundles
```

Purpose:

| Path | Purpose |
|---|---|
| `var/runs` | Run directories and generated evidence |
| `var/state` | Config, chain head, runtime state |
| `var/bundles` | Exported or preserved bundle archives |

These directories are bind-mounted instead of Docker volumes so operators can inspect and back them up directly.

## Evidence Bundles

Dwarf’s evidence model is documented in:

```text
dwarf/docs/forensic-bundle-format.md
```

Every meaningful run is expected to produce a self-contained bundle with manifest, scenario copy, environment capture, logs, assertions, and provenance-chain metadata.

## Configuration

The default config path inside the container is:

```text
/var/dwarf/state/config.yaml
```

Mapped from:

```text
var/state/config.yaml
```

If no config exists, dashboard status still works but reports:

```text
Config missing. Run intake first.
```

That is acceptable for a clean delivery smoke test. Operators can later create config through the CLI or by writing the config file directly.

## Security Posture

The delivery stack is intentionally conservative:

- dashboard binds to `0.0.0.0` by default for loopback and local area network (LAN) access
- container filesystem is read-only
- Linux capabilities are dropped
- `no-new-privileges` is enabled
- runtime data is limited to explicit bind mounts
- SSH keys are not mounted by default
- Cardano substrate containers are not started by default

## Troubleshooting

### Docker daemon unreachable

Run:

```bash
docker version
```

If this fails, fix host Docker access before running Dwarf scripts.

### Port already in use

Check:

```bash
ss -ltnp | grep 8787
```

Use another port:

```bash
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/deploy.sh
```

### Container exits immediately

Run:

```bash
docker logs dwarf-fw
bash delivery/scripts/status.sh
```

Common causes:

- image was not built
- runtime paths are not writable
- port mapping conflicts
- Docker daemon restarted while container was starting

### Build fails at apt snapshot update

The V3 Dockerfile should include:

```text
Acquire::Check-Valid-Until "false";
Acquire::Check-Date "false";
```

Run:

```bash
bash delivery/tests/test_delivery_contract.sh
```

If the contract test fails, do not ship the package.

### Browser shows a connection reset immediately after deploy

The dashboard process may still be starting. Wait a few seconds and retry:

```bash
curl -fsS http://127.0.0.1:8787/api/status
curl -fsS http://<host-lan-ip>:8787/api/status
```

### `/api/operate/runs` returns 404

This was observed during manual Playwright probing. It does not block the rendered Operate and Scenarios pages, but it should be tracked as an API coverage gap if that endpoint is expected by future UI code.

## Run evidence defaults

Every completed scenario run retains `manifest.json`, `assertions.json`, telemetry,
and `chain.json`. SARIF is generated automatically at
`outputs/sarif-export/dwarf-export.sarif`, including a valid empty-results report
for a clean or assertion-free run. The `runtime_bundle_export_sarif` primitive is
still available to regenerate that file after later replay or diff evidence is
added.

Signed attestation remains optional because it requires an operator-controlled
Ed25519 signing identity. Use `runtime_bundle_attestation` when signed provenance
is required; the automatic manifest hash chain is integrity metadata, not an
identity signature.

The run inspector reports three integrity states:

- `VERIFIED`: the manifest digest matches and retained history reaches a chain root.
- `INCOMPLETE`: this run's digest matches, but a missing retained predecessor prevents full continuity verification.
- `FAIL`: retained evidence does not match its recorded digest or another integrity error occurred.

When the saved chain head cannot be verified, the next run starts a documented
new root instead of inheriting the broken history. Older evidence is never
rewritten; the new `chain.json` records the prior head and re-root reason.

## Structured-builder help

Field names in the scenario, profile, and target builders expose schema-backed
help on pointer hover and keyboard focus. Dropdowns expose a description of the
currently selected supported value and update that description when selection
changes. The visible helper text remains the accessible baseline for touch and
assistive-technology users.
