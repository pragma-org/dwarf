# Measurement Profiles Card and Progress Workbench Design

**Status:** approved
**Date:** 2026-09-19

## Outcome

DWARF's `/operate` landing page will present Measurements and Measurement
Profiles as two distinct first-class concepts. The existing Measurements card
will continue to link to the tap catalog and will no longer use its subtitle to
stand in for profile navigation. A new adjacent Measurement Profiles card will
show the profile count and link directly to `/operate/measurement-profiles`.

The `dwarf-latest` workbench will also gain a new responsive HTML object named
`measurement-first-progress`. It will explain, without overstating completion:

1. what the client requested in the conformance and non-functional guidance;
2. how DWARF separated reusable measurements from security scenarios;
3. what was implemented and proven independently for Amaru and Cardano-node;
4. what remains partial, deferred, or not started, including automatic default
   attachment, full functional scoreboards, transcripts, examples, automation,
   and mixed-node comparison.

## UI design

The new card reuses the existing `.tile` component and responsive grid. It does
not introduce new CSS or a nested control. The two cards use these contracts:

- **Independent measurements** — numeric tap count; “real-node taps · retained
  reports · honest unavailable values”; link `/operate/measurements`.
- **Measurement profiles** — numeric profile count; “reusable stock and patched
  selections”; link `/operate/measurement-profiles`.

## Workbench design

The HTML object uses the current dark DWARF identity, a compact status legend,
side-by-side Amaru/Cardano proof cards, a client-request status matrix, retained
run links, and a clearly separated next-work section. Desktop tables become
horizontally scrollable on narrow screens; cards collapse to one column.

## Verification

- A regression test must fail before the card is added and pass afterward.
- The focused frontend tests and full established test suite must pass.
- `/operate` must show both cards with the expected counts and links.
- Desktop and mobile browser checks must show no page-level overflow.
- The workbench content must round-trip byte-for-byte through the Bench API.
- Deployment revision, health, and key routes must be verified after redeploy.

