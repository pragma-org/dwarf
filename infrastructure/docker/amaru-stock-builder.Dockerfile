# Network acquisition during image creation is intentional, not hermetic.
FROM rust:bookworm@sha256:e70e2eec3d495fd5c8e0be74adda86507dfac7f51a724fbf9813ff59b2b247c7
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv clang libssl-dev pkg-config ca-certificates && rm -rf /var/lib/apt/lists/*
RUN rustup toolchain install nightly-2026-09-04 --profile minimal --target x86_64-unknown-linux-musl \
 && rustup default nightly-2026-09-04 \
 && cargo install cargo-zigbuild --locked --version 0.23.4 \
 && python3 -m venv /opt/zig \
 && /opt/zig/bin/pip install --no-cache-dir ziglang==0.15.2
RUN printf '#!/bin/sh\nexec /opt/zig/bin/python -m ziglang "$@"\n' > /usr/local/bin/python-zig \
 && chmod 755 /usr/local/bin/python-zig
ENV RUSTUP_TOOLCHAIN=nightly-2026-09-04 \
    CARGO_ZIGBUILD_ZIG_COMMAND=/usr/local/bin/python-zig
RUN rustc --version && cargo-zigbuild --version && python-zig version
