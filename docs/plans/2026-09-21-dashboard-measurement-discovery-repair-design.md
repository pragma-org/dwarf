# Dashboard measurement discovery and coverage repair design

## Scope

Repair the existing DWARF dashboard. Do not add a second measurement or coverage system. Keep the forensic-noir visual language and derive displayed inventory from the same catalogs that Operate already uses.

## Evidence-based causes

1. `/learn/measurements` exists, but `/learn` has no card that links to it.
2. `/operate` has separate measurement-definition and measurement-profile tiles. Neither tile explains the relationship between profiles, definitions, and retained run results.
3. `/run` renders all ten semantic steps, but `setConditionalState()` hides steps 04 and 05 when the resolved plan has no deployment profile. This creates the visible 03-to-06 jump even though version and measurement choices still exist in the form and progress navigation.
4. `/learn/coverage` mixes catalog-backed matrices with one runtime mini-protocol overlay and describes all cells as support. Catalog presence is not runtime proof.
5. `/learn/threat-coverage` refreshes the scenario inventory but uses a vetted, baked RR/TM mapping. Its “covered” totals describe mapped scenario definitions, not retained executions.

## Design

- Add one Learn card for the authoritative `/learn/measurements` page. Show live measurement and profile counts.
- Extend the existing measurement learning page with the four source meanings: Stock, External, Patched, and Reserved. Keep Coverage as a target/build mode, not a measurement source.
- Replace the two ambiguous Operate measurement tiles with one non-nested Measurements hub card. It links to definitions, profiles, and run results and explains that profiles select taps while runs retain results.
- Keep every `/run` stage visible in DOM, visual, and keyboard order. When a scenario has no profile, show the existing honest fallback text instead of hiding version and measurement stages.
- On `/learn/coverage`, label catalog matrices as inventory and distinguish the mini-protocol runtime overlay. Add a five-card runtime ledger whose accepted run IDs and measurement revisions come from one repository-owned data definition.
- On `/learn/threat-coverage`, reconcile mapped scenario references to the current scenario catalog, recompute gap totals, and add clear catalog-versus-runtime language plus the same five-card ledger. Do not infer a threat or risk as runtime-proven merely because a scenario maps to it.

## Claim boundary

“Catalogued” means a definition exists. “Mapped” means a vetted relationship exists. “Accepted evidence” means the frozen client program names retained runs. None of these labels proves a broader implementation, mixed-node benchmark, or unexecuted threat cell.

## Child explanation

The catalog says which tools are in the toolbox. A saved run says which tools were really used. DWARF will show those as two different facts.
