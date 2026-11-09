# Capture tools for container nodes (F113): tcpdump and strace, nothing else added.
#
# The controller never carries capture or tracing tools. A pcap or syscall trace of a container
# node starts this image only for the capture window, joined to the node's (or the controller's)
# network or process namespace with the narrowest capabilities (NET_RAW/NET_ADMIN for tcpdump,
# SYS_PTRACE for strace). It is built from the dashboard framework image, so it reuses the same
# pinned base and Debian snapshot sources; the two packages are pinned to that snapshot.
ARG DWARF_FRAMEWORK_IMAGE=dwarf/framework:current
FROM ${DWARF_FRAMEWORK_IMAGE}

ARG DWARF_SOURCE_REVISION=unknown
ARG TCPDUMP_VERSION=4.99.5-2
ARG STRACE_VERSION=6.13+ds-1

USER root
RUN for attempt in 1 2 3 4 5; do \
        if apt-get update \
            && apt-get install -y --no-install-recommends \
                tcpdump=${TCPDUMP_VERSION} \
                strace=${STRACE_VERSION}; then \
            break; \
        fi; \
        if [ "${attempt}" -eq 5 ]; then \
            echo "apt install failed after ${attempt} attempts" >&2; exit 1; \
        fi; \
        echo "apt install failed (attempt ${attempt}); retrying in 20s" >&2; \
        sleep 20; \
    done \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="DWARF capture tools" \
      org.opencontainers.image.source="https://github.com/pragma-org/dwarf" \
      org.opencontainers.image.revision=${DWARF_SOURCE_REVISION} \
      io.dwarf.component.role="capture-tools"

ENTRYPOINT []
