# Follow-up: moog SECRET_FILE_PATTERNS "token" substring over-breadth

**Status:** flagged for the moog/antithesis owner (NOT fixed here; scanner intentionally left intact).
**Date:** 2026-10-01

## Issue
`profile_manager/moog.py` `SECRET_FILE_PATTERNS` includes the bare substring `"token"`, and
`_secret_like_asset_files` matches it as a substring of any asset *filename*
(`if any(pattern in lowered for pattern in SECRET_FILE_PATTERNS)`). This false-positives on
legitimately-named native-token fixtures — e.g. the mint/burn fixtures
`minada-token-at-min.tx` / `minada-token-below.tx` (signed Conway Tx fixtures, `"type":"Tx ConwayEra"`,
NOT secrets). It left `validate_moog_asset(antithesis/cardano_amaru_adversarial)` in state `blocked`
(check `secret_files` = error) and the `test_cardano_amaru_adversarial_bundle_passes_moog_asset_validation`
gate red on internal main since the mint/burn family landed (b2bbaea).

## Immediate resolution applied (2026-10-01)
Renamed the two fixtures `minada-token-*` → `minada-asset-*` (git mv + every reference: manifest.py
case_ids, mint_burn_corpus.json case_id+tx_file, graded-2026-09-27.json case_id, build.sh, the two
value/mint-burn docs). `.tx` content (and cbor_sha256) unchanged — the filename is not part of the
signed tx, so fixture validity is preserved. "asset" is accurate for min-ADA + native-asset fixtures.
The secret scanner was deliberately NOT weakened.

## Recommended real fix (owner)
Make the `"token"` heuristic content-aware or scoped so it does not match legitimate `*.tx` /
native-token fixture filenames — e.g. match secret-bearing extensions/paths or whole-name tokens
rather than a bare substring, or exclude `fixture/**/*.tx`. Until then, avoid `token` in fixture
filenames. Do NOT simply drop `"token"` (it must still catch API tokens / PATs).
