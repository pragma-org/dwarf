# Nanosecond Measurement Revision Design

Date: 2026-09-20

## Purpose

Add finer timing precision before the four remaining client examples. Keep the accepted Card 03 whole-microsecond evidence reproducible and unchanged.

Child explanation: Card 03 used the first measuring ruler. We will not change that ruler or its saved results. The remaining cards will use a new ruler with smaller marks.

## Binding constraints

- Preserve every accepted Card 03 target, profile, scenario, run, bundle, claim, and digest.
- Do not rerun Card 03.
- Keep the existing microsecond fields for compatibility.
- Add integer nanosecond fields from the same monotonic timing boundaries.
- Make collectors prefer nanoseconds when they are present.
- Preserve raw nanoseconds and derive fractional microseconds without discarding the sub-microsecond part.
- Keep legacy microsecond-only evidence valid.
- Use additive, versioned target and profile definitions.
- Do not create a second reporting framework.
- Do not change security assertion semantics.
- Do not launch Antithesis or Moog.

## Measurement revisions

The accepted revision is `whole-microseconds-v1`. Its patch directories, manifests, patch-set identities, target records, profiles, scenarios, and evidence remain byte-for-byte unchanged.

The new revision is `nanoseconds-v2`. Each implementation receives a separate patch directory and manifest under a versioned revision path. Build commands select this path explicitly. Existing build defaults continue to select the v1 path.

Each v2 manifest records:

- the source revision;
- the measurement revision;
- the complete ordered patch list;
- each patch digest;
- the combined patch-set digest;
- the executable digest;
- the image digest; and
- the manifest digest.

New target records and profiles point only to the v2 digests. Existing Card 03 profiles continue to point only to v1.

## Producer data contract

Each relevant Amaru and Cardano patched-node timing boundary reads the monotonic clock once at each endpoint. It computes an integer nanosecond delta. It emits that delta in a nanosecond field and also emits the existing microsecond field for compatibility.

For an existing `elapsed_micros` field, the sibling field is `elapsed_nanos`. For named intervals, such as `decode_micros`, the sibling uses the same stem, such as `decode_nanos`. Cardano events retain their existing `duration_us` field and add `elapsed_nanos` as the precise primary duration. This avoids changing an established field name.

The compatibility microsecond value remains an integer at the producer boundary. The precise value is always recoverable from the integer nanosecond field.

## Collector data contract

A shared conversion rule applies inside the existing collectors:

1. If a valid integer nanosecond value is present, preserve it and compute microseconds as `nanoseconds / 1000`.
2. If nanoseconds are absent, use the legacy microsecond value unchanged.
3. Do not infer fake nanoseconds from a legacy microsecond value.
4. Reject invalid negative, Boolean, or non-numeric timing values through the existing validation path.

Thus, `2184` nanoseconds becomes `2.184` microseconds. Distribution inputs use the precise derived microsecond value. Raw source evidence remains unchanged. Normalized evidence retains the raw nanosecond integer and the derived fractional microsecond value.

## Reports and schemas

The current measurement report remains the only reporting system. Its distribution and raw-evidence sections accept fractional microseconds. Human-facing formatting keeps meaningful decimal digits, including `2.184 us`, and does not coerce values to whole integers.

Schemas accept both contracts:

- v1: existing microsecond field only;
- v2: integer nanosecond field plus the existing compatibility microsecond field.

Revision identity is explicit in target metadata and report evidence. A report cannot silently present a v1 target as v2.

## Future-card pinning

Cards 01, 02, 04, and 05 use `nanoseconds-v2`. Their frozen contracts and documentation record the exact v2 profile, patch-set, executable, image, and manifest digests after the builds complete.

Card 03 remains on `whole-microseconds-v1`. Its accepted proof is not regenerated or relabeled.

## Test and delivery sequence

1. Add red tests for exact conversion, legacy fallback, distributions, raw evidence, schema compatibility, and presentation.
2. Implement the smallest collector, schema, and presentation changes that make those tests pass.
3. Add and verify the Amaru v2 timing patch set.
4. Add and verify the Cardano v2 timing patch set.
5. Build revision-locked executables and images. Record all required digests.
6. Add v2 target records and profiles without editing v1 objects.
7. Update only the four future-card contracts and documentation to pin v2.
8. Resume G3-B and G3-C, then complete Gates 4 and 5 in the frozen order.
9. Review and verify each batch before an internal-only push.

## Failure containment

If a v2 build or validation fails, keep v1 deployed and reproducible. Do not modify accepted Card 03 evidence to make v2 pass. Fix the additive v2 implementation and repeat its own verification.
