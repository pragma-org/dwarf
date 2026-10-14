# Install Guide

Use this guide to install DWARF from a Git checkout.
The recommended host is Linux with Docker Engine and Docker Compose v2.
For macOS, use Docker Desktop with compatible Linux node images.

## 1. Check prerequisites

Install Git, Bash, Python 3, and Docker first.
Make sure your user can access the Docker daemon.
Run:

```bash
git --version
bash --version
python3 --version
docker version
docker compose version
df -h .
```

Check these results:

- Docker reports a reachable server.
- Compose reports version 2.
- The host has sufficient free storage for builds, node data, and retained evidence.

Plan for at least 20 GB for initial setup.
Large topologies, coverage builds, and retained runs can require much more storage.
Check the runtime drive as well as the checkout drive.

If Docker reports permission denied, correct your host Docker access.
Membership in the Docker group gives substantial host privileges.
Do not make the DWARF containers privileged.

## 2. Get the source

Clone the repository on the installation host:

```bash
git clone https://github.com/pragma-org/dwarf.git
cd dwarf
git status --short
```

Make sure `git status --short` has no output before a source image build.
The builder checks the checkout root, clean state, and exact commit identity.
It builds from a Git archive of that commit.
An extracted source ZIP alone is not a supported source-build input.

If you copy an existing checkout, include its `.git` directory.
Do not overwrite an unrelated destination directory.
Use a path that your deploying user can write.

## 3. Install DWARF

For the normal local workflow, run:

```bash
bash delivery/scripts/install.sh
```

The installer performs these actions:

1. Check the package layout.
2. Create runtime directories and private installation credentials.
3. Seed writable definition catalogs.
4. Build the framework and local-controller images.
5. Download missing digest-pinned node images.
6. Start the local controller and dashboard.
7. Check dashboard readiness and matching source revisions.

An unsuccessful node-image download stops container startup.
The local controller is required and enabled automatically.
It is separate from the optional legacy SSH channel.

The default runtime root is `${XDG_DATA_HOME:-$HOME/.local/share}/dwarf/`.
Installation does not start a node topology.
Prepare a topology from `/run` after installation.

### Optional host capabilities

To request both the legacy SSH channel and AFL harness, run:

```bash
bash delivery/scripts/install.sh --all
```

`--all` is equivalent to `--control-channel --afl`.
It does not prepare every target, scenario, measurement patch, or external service.
Read these prerequisites before you use it:

- **Legacy SSH channel:** OpenSSH tools and an accessible SSH server for the selected host.
  Setup creates a restricted key and changes the deploying user's SSH configuration.
- **AFL harness:** GHC 9.6.7, Cabal, and the documented Cardano build dependencies.
  Setup builds a large host executable and installs a compatible AFL runtime.
  See the [coverage harness guide](antithesis/components/dwarf-adversary/COVERAGE-HARNESS.md).
- **Host directories:** Write access to the selected installation paths.
  Setup can request `sudo` for directory creation.

Check the final `Legacy SSH channel` and `AFL harness` summary lines.
The installer can continue after an optional setup failure.
Do not treat `FAILED` as successful feature installation.
Do not treat a running dashboard as proof of AFL or SSH readiness.

To request only one capability, use its specific option:

```bash
bash delivery/scripts/install.sh --control-channel
bash delivery/scripts/install.sh --afl
```

Choose one command that matches your requirements.
Do not run every alternative in sequence.

Other options:

| Option | Result |
|---|---|
| `--prepare-only` | Seed catalogs without a build or startup |
| `--no-build` | Use existing framework and controller images |
| `--no-up` | Prepare and build without container startup |

Normal installation does not install host packages or change Docker daemon settings.
It does not configure Moog wallets, Antithesis credentials, or external services.

## 4. Open the dashboard

Open this URL on the installation host:

```text
http://127.0.0.1:8787/run
```

The dashboard binds to loopback by default.
For a remote installation, start SSH port forwarding from your local machine:

```bash
ssh -N -L 8877:127.0.0.1:8787 user@host
```

Replace `user@host` with your SSH account and host.
Then open this URL on your local machine:

```text
http://127.0.0.1:8877/run
```

The installer prints the dashboard token file path, not the token value.
The default file is `~/.local/share/dwarf/state/dashboard-token`.
Its permissions allow only the owner to read or write it.
If you set `ADA2_DWARF_TOKEN`, that supplied value replaces the generated dashboard credential.
Keep token files, private URLs, and screenshots of credentials private.

### Deliberate LAN access

Before you enable LAN access, restrict access with a firewall or authenticated reverse proxy.
To install with a LAN bind, run:

```bash
DWARF_DASHBOARD_BIND=0.0.0.0 bash delivery/scripts/install.sh
```

Open `http://<host-lan-ip>:8787/run` from an authorized machine.
Replace `<host-lan-ip>` with the installation host's LAN address.
The token does not provide network isolation or HTTPS.

### Another host port

If port 8787 is occupied, use another host port:

```bash
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/install.sh
```

Open `http://127.0.0.1:8877/run` on that host.
The container still listens internally on port 8787.

## 5. Prepare and run a topology

Basic is the default dashboard view.
Use the six stages on `/run`:

1. Select or configure a topology.
2. Select node versions.
3. Select a scenario.
4. Select measurement taps.
5. Review the changes and select **Prepare topology**.
6. Wait for Ready before you start the run.

After the run, inspect `/operate/runs/<run-id>`.
Check the security verdict, assertions, measurements, logs, and evidence.
Installation readiness does not prove node readiness or scenario success.

## 6. Optional diagnostics

`install.sh` already checks application readiness.
`status.sh` is not required to complete installation.
To inspect the deployment later, run:

```bash
bash delivery/scripts/status.sh
```

For a custom host port, use the same setting:

```bash
DWARF_DASHBOARD_PORT=8877 bash delivery/scripts/status.sh
```

This command reports delivery settings, image identity, Compose status, container status, mapped ports, and an in-container CLI check.
It does not replace a real Prepare → Run → Evidence check.
Catalog counts do not prove readiness or compatibility.

## Separate build and startup commands

The installer already calls these commands.
Use them separately only when you need another build or application startup:

```bash
bash delivery/scripts/build-image.sh
bash delivery/scripts/deploy.sh
```

The build produces `dwarf/framework:current` and `dwarf/controller:current`.
The deployment script seeds catalogs, checks pinned images, and recreates the application containers.
It checks readiness and matching revisions.
Do not use application startup as a substitute for topology preparation.

## Configuration

The scripts read the package-local `.env` file.
Explicit exported environment values take precedence.
Use the same settings for later lifecycle commands.
Keep `.env` private.

| Variable | Default or purpose |
|---|---|
| `DWARF_IMAGE` | `dwarf/framework:current` |
| `DWARF_CONTROLLER_IMAGE` | `dwarf/controller:current` |
| `DWARF_CONTAINER_NAME` | `dwarf-fw` |
| `DWARF_DASHBOARD_BIND` | `127.0.0.1` |
| `DWARF_DASHBOARD_PORT` | `8787` |
| `DWARF_RUNTIME_ROOT` | `${XDG_DATA_HOME:-$HOME/.local/share}/dwarf` |

Use an absolute path for a custom runtime root.
Read [OPERATIONS.md](OPERATIONS.md) for versions, optional integrations, backups, and troubleshooting.

## Stop or remove DWARF

To stop the application and preserve runtime data, run:

```bash
bash delivery/scripts/uninstall.sh
```

This does not remove every managed node topology, optional host tool, or provisioned SSH file.
Remove a managed topology through its reviewed DWARF action before a complete removal.

The source-checkout uninstall path refuses `--purge` without a verified release receipt.
Do not use a bare `--purge` option.
For a receipt-backed release installation, see the [removal instructions](OPERATIONS.md#stop-and-remove-dwarf).
Back up evidence before any purge.
