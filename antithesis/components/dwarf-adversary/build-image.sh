#!/usr/bin/env bash
#
# build-image.sh — stage the host-built binary + IOG libs into ./dist and
# build the thin dwarf-adversary runtime image. Run on the build host
# (cardano-box) after building the adversary and one-shot LocalTxSubmission
# executables.
#
# Usage: ./build-image.sh [image-tag]   (default: ghcr.io/j-gainsec/dwarf-adversary:dev)
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:-ghcr.io/j-gainsec/dwarf-adversary:dev}"
DWARF_SOURCE_REVISION="${DWARF_SOURCE_REVISION:-}"
if [[ ! "${DWARF_SOURCE_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "DWARF_SOURCE_REVISION must be the exact 40-character source commit" >&2
    exit 1
fi
BIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-adversary/build/dwarf-adversary/dwarf-adversary"
LOCALTXBIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-localtx-submit-raw/build/dwarf-localtx-submit-raw/dwarf-localtx-submit-raw"

if [ ! -x "$BIN" ]; then
    echo "binary not found at $BIN — run 'cabal build -w ghc-9.6.7 exe:dwarf-adversary' first" >&2
    exit 1
fi
if [ ! -x "$LOCALTXBIN" ]; then
    echo "binary not found at $LOCALTXBIN — run 'cabal build -w ghc-9.6.7 exe:dwarf-localtx-submit-raw' first" >&2
    exit 1
fi

FUZZBIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-decoder-fuzz/build/dwarf-decoder-fuzz/dwarf-decoder-fuzz"
CORPUS_SRC="${2:-/tmp/harvest}"
DWARF_FFI_ROOT="${DWARF_FFI_ROOT:-/home/dwarf/dwarf-ffi-fuzz}"

mkdir -p dist dist/corpus
cp -f "$BIN" dist/dwarf-adversary
cp -f "$LOCALTXBIN" dist/dwarf-localtx-submit-raw
cp -fL /usr/local/lib/libsodium.so.23 dist/libsodium.so.23
cp -fL /usr/local/lib/libsecp256k1.so.2 dist/libsecp256k1.so.2
# Bundle the decoder-fuzz harness + seed corpus into the same image (see Dockerfile).
[ -x "$FUZZBIN" ] && cp -f "$FUZZBIN" dist/dwarf-decoder-fuzz || { echo "decoder-fuzz binary missing — run 'cabal build exe:dwarf-decoder-fuzz'" >&2; exit 1; }
cp -f "$CORPUS_SRC"/*.cbor dist/corpus/ 2>/dev/null || true
# Genesis for the applyblock decoder-fuzz target (DWARF_GENESIS_DIR=/ledger-genesis).
rm -rf dist/ledger-genesis && cp -r ledger-genesis dist/ledger-genesis
# Conway-Tx seed corpus for the applyblock target (it decodes a raw Conway Tx,
# not the wire-GenTx envelope used by the tx target's /corpus).
rm -rf dist/corpus-conway && mkdir -p dist/corpus-conway
cp -f corpora/conwaytx/*.cbor dist/corpus-conway/ 2>/dev/null || true
# native-crypto FFI sanitizer-fuzz workload (FU10 Antithesis bridge): a C/ASan
# driver exercising all 7 crypto parse/verify entrypoints. Baked into this same
# public image (separate package would default private). Built on the host in
# ~/dwarf-ffi-fuzz (clang + ASan + instrumented libsodium/secp256k1/blst).
FFIBIN="${DWARF_FFI_ROOT}/dwarf-ffi-crypto-fuzz"
if [ -x "$FFIBIN" ]; then cp -f "$FFIBIN" dist/dwarf-ffi-crypto-fuzz; else echo "ffi-crypto-fuzz binary missing — build it in ~/dwarf-ffi-fuzz first" >&2; exit 1; fi
rm -rf dist/corpus-ffi && cp -r "${DWARF_FFI_ROOT}/corpus" dist/corpus-ffi

echo "staged dist/: $(ls -1 dist | tr '\n' ' ') (corpus: $(ls -1 dist/corpus | wc -l) files)"
docker build --build-arg "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}" -t "$TAG" .
echo "built $TAG"
