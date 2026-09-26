# Trigger-class: which encoding-form re-encodings crash Amaru

All served to a single-target Amaru 10.11.20260918 consumer on profile-zb; each is a
re-encoding of a REAL live-tip pool1 Praos header (same logical header, differs only in CBOR
encoding). raw-wire hash = the hash of the served bytes (what Amaru keys on); canonical hash =
the re-canonicalized header hash (what Amaru integrity-checks against, and cardano-node accepts).

| run | form | slot | raw-wire key (stored) | canonical (hashes to) | result |
|---|---|---|---|---|---|
| reverify-A | noncanonical-int | 3186 | 1a1af19b… | a81f5f80… | CRASH (panic types.rs:88) |
| repro1     | noncanonical-int | 1908 | e680b039… | 3cf95e3f… | CRASH |
| repro2     | noncanonical-int | 2681 | 84ca08f5… | b0c56d77… | CRASH |
| repro3     | indefinite-array | 2730 | 67094c34… | 7cfd784c… | CRASH |
| repro2 (same tip) | trailing-bytes | 2681 | (outer-envelope append; inner stays canonical) | b0c56d77… | NO CRASH |

Trigger class = ANY inner-header non-canonical CBOR re-encoding (noncanonical-int AND
indefinite-array crash). trailing-bytes appends at the OUTER envelope, leaving the inner header
canonical, so it does NOT crash — confirming the trigger is inner-header canonicity, not int-specific.
