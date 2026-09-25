#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

tag="${1:-dwarf-opcert-proxy:local}"
binary="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-opcert-adversary-0.1.0.0/x/dwarf-opcert-adversary/build/dwarf-opcert-adversary/dwarf-opcert-adversary"

if [ ! -x "$binary" ]; then
    echo "binary not found at $binary; run cabal build -w ghc-9.6.7 exe:dwarf-opcert-adversary" >&2
    exit 1
fi

mkdir -p dist
cp -f "$binary" dist/dwarf-opcert-adversary
cp -fL /usr/local/lib/libsodium.so.23 dist/libsodium.so.23
cp -fL /usr/local/lib/libsecp256k1.so.2 dist/libsecp256k1.so.2
docker build -t "$tag" .
