# Operations Guide

Use this guide after installation.
For prerequisites and first startup, read [INSTALL.md](INSTALL.md).
Commands assume the default container name and a terminal at the checkout root.

## Application lifecycle

To prepare catalogs, build both images, and start the application, run:

```bash
bash delivery/scripts/install.sh
```

The installer also checks readiness and matching source revisions.
You do not need to run `status.sh` to complete installation.

To build images separately, run:

```bash
bash delivery/scripts/build-image.sh
```

To start or replace the application containers, run:

```bash
bash delivery/scripts/deploy.sh
```

This command also seeds catalogs, checks pinned node images, and checks application readiness.
It is not a node-topology preparation command.
Do not run it repeatedly while another deployment is active.

For an optional diagnostic, run:

```bash
bash delivery/scripts/status.sh
```

To stop the application and preserve runtime data, run:

```bash
bash delivery/scripts/undeploy.sh
```

## Dashboard access

Open `http://127.0.0.1:8787/run` on the installation host.
The root path redirects to `/operate`.
Basic is the default dashboard view.
The six-stage `/run` wizard contains the required Prepare → Run → Evidence controls.

The default host bind is `127.0.0.1`, not `0.0.0.0`.
For remote access, use the SSH forwarding procedure in [INSTALL.md](INSTALL.md#4-open-the-dashboard).
Enable a LAN bind only with deliberate network access restrictions.
The dashboard credential does not replace a firewall, authenticated reverse proxy, or HTTPS.

The installer prints the dashboard token file path.
It does not print a tokenized access URL.
The default file is `~/.local/share/dwarf/state/dashboard-token`.
Keep credentials and configuration private.

### Useful routes

| Route | Purpose |
|---|---|
| `/run` | Configure, prepare, and start a run |
| `/operate` | Operations overview |
| `/operate/scenarios` | Scenario catalog |
| `/operate/targets` | Target catalog |
| `/operate/profiles` | Topology profiles |
| `/operate/versions` | Release discovery, imports, defaults, and compatibility evidence |
| `/operate/measurements` | Measurement definitions |
| `/operate/measurement-profiles` | Tap selections |
| `/operate/runs` | Run history |
| `/operate/runs/<run-id>` | Run results and retained evidence |
| `/operate/status` | Deployment status |
| `/operate/bundles` | Evidence bundles |
| `/operate/coverage` | Coverage summary |
| `/operate/plugins` | Plugins and primitives |
| `/operate/config` | Resolved configuration and setup controls |
| `/learn` | Documentation |
| `/api/status` | JSON status response |

## Prepare, run, and inspect evidence

Use this sequence on `/run`:

1. Select the topology.
2. Select node versions.
3. Select the scenario.
4. Select measurement taps.
5. Review the changes, seed, prerequisites, and experimental consent.
6. Select **Prepare topology**.
7. Wait for Ready.
8. Start the run.
9. Inspect the result and evidence.

The logo shows progress, not readiness proof.
A healthy topology with unchanged physical identities can pass revalidation without another deployment.
A changed topology can require explicit replacement confirmation.

Read the framework outcome separately from the security verdict.
A completed run can contain a failed security assertion.
Inspect the assertion and its evidence before you classify the failure.

Check measurement samples, workload correlations, and collection windows.
An available tap or a zero value does not prove that the workload exercised that metric.
Patched taps require matching instrumentation and provenance.
An imported stock binary does not acquire instrumentation hooks.

## Node versions and compatibility

The deployment profile controls runtime node versions.
Scenarios inherit its exact artifacts.
A target's harness version does not override the profile's runtime version.

Open `/operate/versions` to inspect releases, imports, artifact identities, defaults, compatibility decisions, and retained evidence.
Select **Fetch Latest Version** for Amaru or Cardano to request upstream release metadata.
An open page does not repeatedly request release metadata.
A GitHub error or rate limit does not prove that a release is absent.

Metadata discovery does not download or deploy an artifact.
Use the preparation workflow for that step.
For an unreleased build, import a compatible Docker image or supported Linux ELF binary or archive.
The executable must match the Linux container architecture.
DWARF does not compile arbitrary source trees or correct node build errors.

Use this sequence for an experimental version:

1. Discover or import the version.
2. Select its exact artifact.
3. Download or prepare it.
4. Run applicable checks.
5. Inspect retained evidence.
6. Record a compatibility decision for a specific scope.
7. Optionally choose it as your default.

A successful run does not automatically confirm every scenario, tap, or mixed pair.
A standalone Cardano pass does not prove mixed compatibility.
A mixed pass does not prove standalone Amaru block production.
Local-devnet evidence does not establish Preview, Preview2, or Preprod compatibility.

`incompatible` and `blocked` choices stop execution.
Unknown compatibility requires a one-run acknowledgement.
The acknowledgement does not promote catalog support.

Profile policies include `latest-confirmed`, `latest-stable`, and exact selection.
An omitted policy uses `latest-confirmed`.
That policy resolves a scoped default; discovery alone does not create confirmation.
The resolved artifact remains exact and digest-pinned.

Use **Preview deploy** to inspect the resolved profile before a change.
For a container CLI dry run, replace `PROFILE_ID` with the profile identifier:

```bash
docker exec dwarf-fw python3 dwarf/cardano-profile deploy PROFILE_ID --dry-run
```

For an intentional deployment with unknown compatibility, use:

```bash
docker exec dwarf-fw python3 dwarf/cardano-profile deploy PROFILE_ID --approve --acknowledge-unknown-version
```

This second command changes the topology.
Do not run it as a status check.

For a separate metadata candidate, run:

```bash
PYTHONPATH=dwarf python3 dwarf/scripts/refresh_version_catalog.py --output /tmp/dwarf-version-catalog-candidate.json
```

This command requests upstream metadata and writes a candidate file.
It is not a compatibility proof.
Read the [version-qualified devnet guide](dwarf/docs/version-qualified-devnets.md) for detailed requirements.

## Definition catalogs and builders

The scenario, target, and profile catalogs support search, detail views, source downloads, and catalog exports.

| Definition | Catalog | Create | Edit |
|---|---|---|---|
| Scenario | `/operate/scenarios` | `/operate/scenarios/new` | `/operate/scenarios/<id>/edit` |
| Target | `/operate/targets` | `/operate/targets/new` | `/operate/targets/<id>/edit` |
| Profile | `/operate/profiles` | `/operate/profiles/new` | `/operate/profiles/<id>/edit` |

Append `/<id>` to a catalog route to open its detail view.
An identifier becomes fixed after creation.
The structured editor provides controls for supported schema fields.
Optional raw JSON or YAML editing handles extension fields.
The operator workflow does not require a hidden advanced dashboard view.

Scenario controls use `dwarf/spec/v1/schema.json`, the primitive registry, and primitive parameter schemas.
Primitive choices depend on lifecycle phase, runtime, and target implementation.
Field help appears on hover and keyboard focus.
Visible help remains available to touch and assistive-technology users.

Validate the definition before you save it.
The server repeats syntax and semantic validation.
It rejects unsafe identifiers and unsupported values.
It replaces a definition file only after successful validation.
The builders use the existing run and deployment services.

Read-only export endpoints are:

```text
/api/catalog/<catalog>/<id>/download
/api/catalog/<catalog>/export
```

Replace `<catalog>` with `scenarios`, `targets`, or `profiles`.
Exports retain the `dwarf/` definition layout.
They omit runtime state, bundles, caches, secrets, and operating-system metadata.

## CLI inside the container

The framework directory is `/home/dwarf/dwarf-fw`.
The CLI entry point is `python3 dwarf/cardano-profile`.

For status and definition checks, run:

```bash
docker exec dwarf-fw python3 dwarf/cardano-profile dashboard status
docker exec dwarf-fw python3 dwarf/cardano-profile test smoke list
docker exec dwarf-fw python3 dwarf/cardano-profile fuzz list
docker exec dwarf-fw python3 dwarf/cardano-profile scenario validate --semantic dwarf/scenarios/client-example-cbor-decoding-cardano-patched.yaml
```

If you need an interactive container shell, run:

```bash
docker exec -it dwarf-fw bash
```

This shell is inside the dashboard container, not on the host.
The container does not contain every node-build toolchain.

## Host settings and storage

The scripts read the package-local `.env` file.
Explicit exported values take precedence.
Keep the same runtime root and installation identity for later lifecycle commands.

To change only the host port, run:

```bash
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/deploy.sh
```

Open `http://127.0.0.1:8877/run`.
The internal dashboard port remains 8787.

The default host runtime root is `${XDG_DATA_HOME:-$HOME/.local/share}/dwarf`.
Use `DWARF_RUNTIME_ROOT` to select another absolute path.

| Host path relative to the runtime root | Container path | Contents |
|---|---|---|
| `runs/` | `/var/dwarf/runs` | Run evidence |
| `state/` | `/var/dwarf/state` | Configuration and writable catalogs |
| `bundles/` | `/var/dwarf/bundles` | Evidence archives |
| `controller-state/` | `/var/dwarf/controller-state` | Controller state |
| `state/launches/` | `/var/dwarf/launches` | Resolved launch records |
| `state/run-ledger/` | `/var/dwarf/run-ledger` | Execution ledger |

Compose uses named volumes with host bind-backed storage for these paths.
The controller receives only the specified state views and shared runtime paths.
Its Docker socket mount permits host Docker operations.
Treat controller credentials and Docker access as privileged.

The container configuration file is `/var/dwarf/state/config.yaml`.
Its host path is `<runtime-root>/state/config.yaml`.
A missing configuration file is not evidence of a successful run.
Complete the applicable setup before you use dependent operations.

Before an upgrade or purge, back up the runtime root.
Protect credentials in the backup.
Keep a consistent copy of run evidence and integrity history.
Do not remove evidence while a run or collector is active.

## Evidence and integrity

Read the [forensic bundle format](dwarf/docs/forensic-bundle-format.md).
Completed scenario runs retain manifests, assertions, telemetry, and integrity records.

DWARF automatically generates SARIF at `outputs/sarif-export/dwarf-export.sarif`.
A clean or assertion-free run can have an empty-results SARIF report.
Use `runtime_bundle_export_sarif` to regenerate it after additional replay or difference evidence.

Signed attestation is optional.
It requires an operator-controlled Ed25519 identity and `runtime_bundle_attestation`.
A manifest hash chain is integrity metadata, not an identity signature.

The inspector shows these integrity states:

- `VERIFIED`: The manifest digest matches and retained history reaches a chain root.
- `INCOMPLETE`: The digest matches, but a missing predecessor prevents complete continuity verification.
- `FAIL`: Evidence differs from its recorded digest, or another integrity error occurred.

If the saved chain head cannot be verified, a later run starts a documented new root.
DWARF does not rewrite older evidence.
The new `chain.json` records the previous head and reason.

## Optional SSH channel and AFL harness

The local controller does not require the legacy SSH channel.
The normal installer leaves both host-side capabilities disabled.
`install.sh --all` requests both; it does not prepare every DWARF feature.

Read the optional prerequisites in [INSTALL.md](INSTALL.md#optional-host-capabilities).
The AFL build needs GHC 9.6.7, Cabal, compatible AFL, and the documented build dependencies.
Inspect the final summary for optional failures.

For an existing non-loopback SSH deployment, preserve its configured host and user during provisioning.
Set `DEPLOY_HOST` and `DEPLOY_USER` to the values shown in `/operate/config`.
Then request the channel without another image build:

```bash
bash delivery/scripts/install.sh --control-channel --no-build
```

Provisioning defaults to `127.0.0.1` and the current user.
It rewrites staged host-key data for the selected host.
Do not apply those defaults to an existing deployment that uses another host.
Do not mount unrelated SSH keys.

## Optional Moog and Antithesis setup

Normal installation does not configure Moog or start its services.
Use `/operate/config` for supported setup values.
Startup environment values override saved values.
The scripts pass supported `MOOG_*` variables from `.env` to the container.

Saved secrets can include GitHub tokens, Antithesis credentials, and agent email credentials.
Secret fields are masked after save.
A blank secret submission preserves the saved value.
Keep `<runtime-root>/state/config.yaml` private.

To request a bootstrap plan, run:

```bash
DWARF_MOOG_BOOTSTRAP=plan bash delivery/scripts/deploy.sh
```

The plan does not change remote state.
To approve the limited skeleton setup, run:

```bash
DWARF_MOOG_BOOTSTRAP=approve DWARF_MOOG_BOOTSTRAP_APPROVE=1 bash delivery/scripts/deploy.sh
```

Approval creates directory structure and an operator plan file.
It does not download binaries, create wallets, store credentials, enable services, or start oracle or agent processes.
These deployment commands also recreate the application containers.

After applicable manual setup, inspect health:

```bash
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog healthcheck --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile wallet healthcheck moog-requester --json
```

Run the documented Moog readiness and preflight checks before external execution.
Do not treat a directory skeleton as a ready integration.

## Troubleshooting

### Docker is unavailable

Run `docker version`.
Correct Docker access before you repeat DWARF installation.
Do not change container privileges to hide a host access failure.

### A container is unhealthy

Inspect recent logs and application status:

```bash
docker logs --tail 80 dwarf-controller
docker logs --tail 80 dwarf-fw
docker inspect dwarf-controller --format '{{json .State.Health}}'
bash delivery/scripts/status.sh
```

If logs are empty, inspect the controller health response:

```bash
docker exec dwarf-controller python3 -c 'import urllib.request,urllib.error
try:
    response=urllib.request.urlopen("http://127.0.0.1:8790/healthz", timeout=10)
except urllib.error.HTTPError as error:
    response=error
print(response.read().decode())'
```

Check the reported Docker access, writable state, missing images, and revision compatibility.
Read the exact failure before another startup attempt.
Do not start a duplicate job to check whether an existing job is alive.

### The browser cannot connect

Check the installation summary, bind address, host port, and SSH forwarding.
A loopback bind is not directly accessible from another machine.
For the default local port, inspect application status:

```bash
curl -fsS http://127.0.0.1:8787/api/status
```

Use the configured host port if it differs.
If the response fails, inspect recent logs before you restart the application.

### A source image build is refused

Run `git status --short`.
The source builder requires a clean Git checkout with matching source and catalog revisions.
Do not discard unrelated changes to obtain a clean build.
Commit intended changes or use a separate clean checkout.
A source ZIP does not contain the required Git metadata.

### The snapshot package build fails

The image uses Debian snapshot repositories.
The Dockerfile disables expiration and date checks for those snapshots.
For a delivery contract check, run:

```bash
bash delivery/tests/test_delivery_contract.sh
```

A contract pass does not prove a successful image build or runtime deployment.

## Stop and remove DWARF

Back up evidence before removal.
Use the reviewed topology removal action for managed nodes first.
Do not remove unrelated containers, images, networks, or volumes.

To stop the application and preserve runtime data, run:

```bash
bash delivery/scripts/uninstall.sh
```

To also request removal of configured application images, run:

```bash
bash delivery/scripts/uninstall.sh --remove-images
```

The source-checkout path removes only image references explicitly recorded in its `.env`.
It does not perform a global Docker cleanup.

A source-checkout installation cannot use automated purge without a verified release receipt.
The uninstaller refuses that operation.
For a receipt-backed release installation, use the exact recorded path and installation identifier:

```bash
bash delivery/scripts/uninstall.sh --purge /absolute/recorded/runtime/path --confirm 'PURGE DWARF INSTALLATION_ID'
```

Replace both placeholders with values from the verified installation receipt.
Use the configured `DWARF_INSTALL_ROOT` if it is not the default release installation root.
The receipt checks, ownership marker, signed release checks, and managed-container checks must all pass.
A purge permanently removes the selected runtime data.
Optional host tools and provisioned SSH entries require separate, scoped removal.

## Further documentation

- [Install guide](INSTALL.md)
- [Security policy](SECURITY.md)
- [Operator handbook](dwarf/docs/operator-handbook.md)
- [Version-qualified devnets](dwarf/docs/version-qualified-devnets.md)
- [Coverage harness](antithesis/components/dwarf-adversary/COVERAGE-HARNESS.md)
- [Historical delivery overview](docs/index.html)
