# DWARF Measurement Program Technical Debrief Design

## Approved outcome

Create one new, standalone, interactive HTML object titled **DWARF Measurement Program — Implementation Overview and Technical Debrief**. Keep the source at `docs/workbench/dwarf-measurement-program-technical-debrief.html`, retain all existing workbench objects, and publish only to the internal `dwarf-latest` workbench.

## Information architecture

The page uses a forensic-noir visual system: near-black surfaces, cyan evidence lines, amber limitations, and green proven states. A sticky navigation rail and a compact mobile navigation bar lead to these sections:

1. Executive summary.
2. Client ask, task, implementation, and evidence traceability.
3. Tap architecture and the Stock, External, Patched, and Reserved sources.
4. Five acceptance-card dossiers.
5. CBOR, Plutus, and fork findings and resolutions.
6. Exact DWARF viewing and export steps.
7. Linked implementation inventory.
8. Proven, partial, unavailable, and deferred coverage.
9. Reproducibility identities.
10. Next work.

Every technical section is followed immediately by a visibly labeled child-friendly explanation. Technical claims come only from the frozen contracts, accepted proof pages, retained run manifests, final documentation, and current deployment record.

## Interaction and accessibility

The document is useful without JavaScript. JavaScript adds:

- Full-text search over section text.
- Status filters for proven, partial, unavailable, and deferred material.
- Expand and collapse controls for dossier details.
- A live result count and an empty-result message.
- Keyboard shortcuts: `/` focuses search, `Escape` clears search, and `Alt+1` through `Alt+4` toggle status filters.

Native controls, labels, focus rings, landmarks, skip navigation, `aria-live`, and semantic `details` elements support keyboard and screen-reader use. Print styles expand all evidence and remove controls. Responsive rules prevent horizontal page overflow while allowing wide evidence tables to scroll inside their own containers.

## Claim boundary

The page says what each accepted run proves and what it does not prove. It preserves Card 01’s historical Amaru security finding beside the fixed-revision pass, Card 02’s on-chain topology evidence, Card 03’s accepted whole-microsecond revision, and Card 04’s canonical-progress semantics. Mixed-node comparison, stable thresholds, weekly automation, presentation walkthrough, Antithesis, and Moog remain deferred or out of scope.

## Verification

Tests lock the required title, sections, child explanations, card run IDs, exact versions and revisions, source taxonomy, interactions, accessible controls, print and responsive rules, inventory links, reproducibility data, and claim boundaries. The published object is read back and compared byte-for-byte with the repository source. Link checks and Chromium desktop/mobile checks verify interaction, keyboard use, no page-level overflow, and print behavior. Screenshots are rendered and inspected at 1440×900 and 390×844.
