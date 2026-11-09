ARG PYTHON_BASE=python@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3
FROM ${PYTHON_BASE}

ARG DWARF_SOURCE_REVISION=unknown
ARG DWARF_COMPONENT_ROLE=dashboard
ARG DWARF_CATALOG_REVISION=unknown
ARG SOURCE_DATE_EPOCH=0
ARG DEBIAN_SNAPSHOT=20260429T000000Z
ARG BASH_VERSION=5.2.37-2+b8
ARG CA_CERTIFICATES_VERSION=20250419
# Docker CLI and Compose plugin come from Docker's own apt repository (F45):
# Debian trixie's 26.1 / 2.26 packages lack `compose config --no-env-resolution`
# and `image inspect --platform`, which the Antithesis package validator uses.
# The container never runs an engine; it talks to the host daemon's socket.
ARG DOCKER_CLI_VERSION=5:29.9.2-1~debian.13~trixie
ARG DOCKER_COMPOSE_VERSION=5.6.1-1~debian.13~trixie
ARG JQ_VERSION=1.7.1-6+deb13u1
ARG LIBSODIUM_VERSION=1.0.18-1+deb13u1
ARG LIBSECP256K1_VERSION=0.5.0-2+b1
ARG OPENSSH_CLIENT_VERSION=1:10.0p1-7+deb13u2
ARG RSYNC_VERSION=3.4.1+ds1-5+deb13u1
ARG TMUX_VERSION=3.5a-3
# procps gives the controller `ps` for node pid resolution (F79)
ARG PROCPS_VERSION=2:4.0.4-9
# iptables and iproute2 run in a node container's network namespace for port faults (F95)
ARG IPTABLES_VERSION=1.8.11-2
ARG IPROUTE2_VERSION=6.15.0-1
# libfaketime: runtime_time_skew copies the .so into a Docker-mode substrate's /env and preloads it
# into the target node container (the controller itself never preloads it)
ARG LIBFAKETIME_VERSION=0.9.10+2024-06-05+gba9ed5b2-0.6

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/dwarf \
    ADA2_DWARF_RUNS_DIR=/var/dwarf/runs \
    ADA2_DWARF_STATE_DIR=/var/dwarf/state \
    ADA2_DWARF_BUNDLES_DIR=/var/dwarf/bundles \
    ADA2_PROFILE_MANAGER_CONFIG=/var/dwarf/state/config.yaml \
    DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION} \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

LABEL org.opencontainers.image.title="DWARF dashboard" \
      org.opencontainers.image.source="https://github.com/pragma-org/dwarf" \
      org.opencontainers.image.revision=${DWARF_SOURCE_REVISION} \
      io.dwarf.component.role=${DWARF_COMPONENT_ROLE} \
      io.dwarf.catalog.revision=${DWARF_CATALOG_REVISION}

RUN printf 'Acquire::Check-Valid-Until "false";\nAcquire::Check-Date "false";\nAcquire::Retries "5";\nAcquire::http::Timeout "120";\n' >/etc/apt/apt.conf.d/90snapshot \
    && cat >/etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

# Docker's release key (Docker Release (CE deb) <docker@docker.com>,
# 9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88) is vendored, so the build
# never trusts a key fetched at build time; the pinned package versions above
# select exact .deb files from Docker's pool.
COPY infrastructure/docker/docker-archive-keyring.asc /usr/share/keyrings/docker-archive-keyring.asc
RUN cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Signed-By: /usr/share/keyrings/docker-archive-keyring.asc
EOF

# snapshot.debian.org intermittently answers 504 for single pinned .deb files; a
# fresh install must survive that without changing the pinned versions below.
RUN for attempt in 1 2 3 4 5; do \
        if apt-get update \
            && apt-get install -y --no-install-recommends \
                bash=${BASH_VERSION} \
                ca-certificates=${CA_CERTIFICATES_VERSION} \
                docker-ce-cli=${DOCKER_CLI_VERSION} \
                docker-compose-plugin=${DOCKER_COMPOSE_VERSION} \
                jq=${JQ_VERSION} \
                libsodium23=${LIBSODIUM_VERSION} \
                libsecp256k1-2=${LIBSECP256K1_VERSION} \
                libfaketime=${LIBFAKETIME_VERSION} \
                openssh-client=${OPENSSH_CLIENT_VERSION} \
                iproute2=${IPROUTE2_VERSION} \
                iptables=${IPTABLES_VERSION} \
                procps=${PROCPS_VERSION} \
                rsync=${RSYNC_VERSION} \
                tmux=${TMUX_VERSION}; then \
            break; \
        fi; \
        if [ "${attempt}" -eq 5 ]; then \
            echo "apt install failed after ${attempt} attempts" >&2; exit 1; \
        fi; \
        echo "apt install failed (attempt ${attempt}); retrying in 20s" >&2; \
        sleep 20; \
    done \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 dwarf \
    && mkdir -p /home/dwarf/.ssh /home/dwarf/dwarf-fw /var/dwarf/runs /var/dwarf/state /var/dwarf/bundles \
    && chmod 755 /home/dwarf \
    && chmod 700 /home/dwarf/.ssh \
    && chown -R dwarf:dwarf /home/dwarf /var/dwarf

WORKDIR /home/dwarf/dwarf-fw

COPY dwarf/ /home/dwarf/dwarf-fw/dwarf/
COPY antithesis/cardano_amaru_relay_bootstrap_control/ /home/dwarf/dwarf-fw/antithesis/cardano_amaru_relay_bootstrap_control/
# The wizard-supported scenario pins this curated workload package by content
# (antithesis_workload_binding.SOURCE); without it every installed dashboard
# reports workload-source-contract-changed.
COPY antithesis/cardano_amaru_miniprotocol_security/ /home/dwarf/dwarf-fw/antithesis/cardano_amaru_miniprotocol_security/
# Fixture transactions the submit/block-apply scenarios name (fixture/... or
# antithesis/cardano_amaru_adversarial/fixture/...); staged into each run dir.
COPY antithesis/cardano_amaru_adversarial/fixture/ /home/dwarf/dwarf-fw/antithesis/cardano_amaru_adversarial/fixture/
COPY antithesis/components/dwarf-adversary/coverage-docker/Dockerfile /home/dwarf/dwarf-fw/antithesis/components/dwarf-adversary/coverage-docker/Dockerfile
COPY infrastructure/docker/dwarf-fw-entrypoint.sh /usr/local/bin/dwarf-fw-entrypoint
COPY infrastructure/docker/requirements-framework.txt /tmp/requirements-framework.txt

RUN PYTHONPATH=/home/dwarf/dwarf-fw/dwarf \
    python3 -m profile_manager.release_identity generate \
      --dwarf-root /home/dwarf/dwarf-fw/dwarf \
      --output /home/dwarf/dwarf-fw/dwarf/release-identity.json \
      --component-role "${DWARF_COMPONENT_ROLE}" \
      --release-revision "${DWARF_SOURCE_REVISION}" \
      --catalog-revision "${DWARF_CATALOG_REVISION}"

RUN chmod +x /usr/local/bin/dwarf-fw-entrypoint \
    && python3 -m pip install --no-compile --require-hashes -r /tmp/requirements-framework.txt \
    && rm /tmp/requirements-framework.txt

USER dwarf

VOLUME ["/var/dwarf/runs", "/var/dwarf/state", "/var/dwarf/bundles"]
EXPOSE 8787

ENTRYPOINT ["/usr/local/bin/dwarf-fw-entrypoint"]
CMD ["dashboard", "serve", "--bind", "0.0.0.0", "--port", "8787"]
