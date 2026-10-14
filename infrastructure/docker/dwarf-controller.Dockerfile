ARG DWARF_FRAMEWORK_IMAGE=dwarf/framework:current
FROM ${DWARF_FRAMEWORK_IMAGE} AS framework

FROM framework AS cardano-support

# The retained local-deployment adapter uses cardano-testnet to create an
# isolated devnet.  Package the official, checksum-pinned tools in the
# controller so a container-only installation never falls back to binaries in
# a particular host user's home directory.
USER root
ARG TARGETARCH
ARG CARDANO_SUPPORT_VERSION=11.1.2
ARG CARDANO_SUPPORT_AMD64_SHA256=fd872fbeb9cc663e67088ca47ed01aa01d18a77d7dbee694e2444bb23d833759
ARG CARDANO_SUPPORT_ARM64_SHA256=24565d796441111b509a8ddd8be2ebf7d5d6ff363bb9fc447af1f4e9e71a3246
RUN python3 - "${TARGETARCH}" "${CARDANO_SUPPORT_VERSION}" \
      "${CARDANO_SUPPORT_AMD64_SHA256}" "${CARDANO_SUPPORT_ARM64_SHA256}" <<'PY'
import hashlib
import io
import os
import sys
import tarfile
import urllib.request

architecture, version, amd64_sha256, arm64_sha256 = sys.argv[1:]
expected_by_arch = {"amd64": amd64_sha256, "arm64": arm64_sha256}
if architecture not in expected_by_arch:
    raise SystemExit(f"unsupported controller architecture: {architecture}")
archive_name = f"cardano-node-{version}-linux-{architecture}.tar.gz"
url = f"https://github.com/IntersectMBO/cardano-node/releases/download/{version}/{archive_name}"
with urllib.request.urlopen(url, timeout=120) as response:
    archive = response.read()
observed_sha256 = hashlib.sha256(archive).hexdigest()
if observed_sha256 != expected_by_arch[architecture]:
    raise SystemExit(
        f"Cardano support archive checksum mismatch: expected "
        f"{expected_by_arch[architecture]}, observed {observed_sha256}"
    )
os.makedirs("/out", mode=0o755, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
    for binary in ("cardano-testnet", "cardano-cli", "cardano-node"):
        member = bundle.getmember(f"./bin/{binary}")
        source = bundle.extractfile(member)
        if source is None or not member.isfile():
            raise SystemExit(f"missing regular support binary: {binary}")
        destination = f"/out/{binary}"
        with open(destination, "wb") as output:
            output.write(source.read())
        os.chmod(destination, 0o755)
PY

# Start a fresh metadata stage so the dashboard image's persistent VOLUME
# declarations are not inherited by the Docker-socket controller.  The
# filesystem is identical; runtime mounts are declared explicitly in Compose.
FROM scratch
COPY --from=framework / /
COPY --from=cardano-support /out/ /usr/local/bin/

ARG DWARF_SOURCE_REVISION=unknown
ARG DWARF_CATALOG_REVISION=unknown

ENV PYTHONPATH=/home/dwarf/dwarf-fw/dwarf \
    DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}

LABEL org.opencontainers.image.title="DWARF local controller" \
      org.opencontainers.image.source="https://github.com/pragma-org/dwarf" \
      org.opencontainers.image.revision=${DWARF_SOURCE_REVISION} \
      io.dwarf.component.role="controller" \
      io.dwarf.catalog.revision=${DWARF_CATALOG_REVISION}

USER root
RUN PYTHONPATH=/home/dwarf/dwarf-fw/dwarf \
    python3 -m profile_manager.release_identity generate \
      --dwarf-root /home/dwarf/dwarf-fw/dwarf \
      --output /home/dwarf/dwarf-fw/dwarf/controller-release-identity.json \
      --component-role controller \
      --release-revision "${DWARF_SOURCE_REVISION}" \
      --catalog-revision "${DWARF_CATALOG_REVISION}" \
    && chown dwarf:dwarf /home/dwarf/dwarf-fw/dwarf/controller-release-identity.json

USER dwarf
EXPOSE 8790

ENTRYPOINT ["python3", "-m", "controller.service"]
CMD ["--bind", "0.0.0.0", "--port", "8790"]
