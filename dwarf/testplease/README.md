# Source intake

Place an ordinary **uncompiled upstream source tree** directly in either location:

```text
dwarf/testplease/amaru/<candidate-name>/
dwarf/testplease/cardano-node/<candidate-name>/
```

The parent directory selects the implementation. Multiple candidates, including
both implementations, can be identified in one scan. No `candidate.yaml`, client
workflow, scenario configuration, or client build command is required. Exact node
and scenario versions remain run-selected; this intake does not pin them.

A same-repository push that changes this directory starts the GitHub-hosted
security-campaign workflow. The current executable lane accepts one changed Amaru
candidate, builds its stock artifact, qualifies the exact mixed-node pair, and runs
the frozen security and stock-measurement campaign. A complete verified campaign is
published as content-addressed release assets and becomes eligible for the bounded
GitHub Pages defaults. Cardano candidate builds and patched taps remain separate
follow-on lanes.

The Amaru stock build packages the verified binary in a minimal, digest-pinned
POSIX runtime so qualification can atomically transfer those exact bytes into
the proven relay wrapper. Retained receipts bind the builder, runtime base,
binary hash, and final runtime-image identity.

## Recognized source

Amaru must have a parseable workspace `Cargo.toml` and
`crates/amaru/Cargo.toml` declaring package `amaru`. The package must contain
the default `src/main.rs` binary or declare an `amaru` binary whose relative
`path` exists inside that package. These identify the Cargo workspace/package
used by DWARF's Amaru build helpers.

Cardano-node must have `cabal.project`,
`cardano-node/cardano-node.cabal` declaring package `cardano-node`, and at least
one `.hs` source file under `cardano-node/src/`. These identify the ordinary Cabal
project/package surface used by DWARF's cardano-node build helpers.

These are conservative source markers, not a buildability or dependency check.
Unsupported layouts raise a precise `CandidateIntakeError`. A wrapper directory
around the source, a second recognizable source root anywhere inside a candidate,
and a root matching both implementations are rejected. Names must be 1–128 ASCII
letters, digits, dots, underscores, or hyphens, starting with a letter or digit.

Candidate roots and their intake parents must be real directories. Within a
candidate, symlinks are allowed only when their entire resolution stays inside
that candidate and reaches an existing target. Directory symlinks are recorded
without recursion; escaping, dangling, and cyclic links are rejected. Special
files such as sockets, devices, and FIFOs are rejected. Outside candidate trees,
only this README, the two implementation directories, and their `.gitkeep`
placeholders belong to the intake layout.

## Identity contract

```python
from pathlib import Path
from profile_manager.testplease_candidates import discover_candidates

snapshots = discover_candidates(
    Path("/path/to/dwarf-checkout"),
    repository_commit=trusted_repository_commit,
)
```

The trusted caller supplies the current repository's full lowercase 40-hex Git
object ID, such as the workflow's `GITHUB_SHA`. Intake does not obtain provenance
from candidate metadata or invoke Git. It returns a deterministically ordered
tuple of frozen `CandidateSnapshot` records with `implementation`,
`candidate_name`, `repository_commit`, `source_tree_digest`, and absolute
`source_root`. An absent or empty intake returns an empty tuple. An invalid
candidate rejects the complete scan without returning partial results.

The SHA-256 source-tree digest covers **all** entries, including hidden,
untracked, ignored, Git metadata if present, ordinary upstream build support,
and workflow files inside the source. Nothing is excluded by `.gitignore`.
It binds relative paths, entry types, regular-file bytes and executable status,
symlink target text, and empty directories. It excludes absolute location,
creation order, timestamps, ownership, and other permission bits.

Digest format v1 starts with `dwarf-testplease-source-tree-v1` followed by a NUL.
Entries sort by their relative POSIX path. Each record is compact ASCII JSON
(with non-ASCII characters escaped), prefixed by its byte length as an unsigned
8-byte big-endian integer:

- Directory: `[path, "directory"]`
- Symlink: `[path, "symlink", target_text]`
- Regular file: `[path, "file", executable_boolean, byte_length, content_sha256]`

Executable status means any of the three executable permission bits is set.
`content_sha256` is a lowercase hex SHA-256 of the file's bytes.

The digest identifies actual candidate contents even when they differ from the
repository commit. Callers must scan a stable checkout and freeze/reverify those
same contents before later use. Frozen records do not copy or lock source files.
Intake never executes candidate code, scripts, build files, workflows, hooks, or
commands. Legitimate upstream build-support files are retained and hashed; this
contract does not authorize executing them or add client-defined build recipes.
