# Profile Templates

This directory is the scaffold library behind:

```bash
./cardano-profile profile new --template <template> --name <profile-id>
```

The same immutable sources are browsable and downloadable under
`/operate/profile-templates`; `/learn/profile-templates` documents their
substitutions, schema reuse, and safe authoring lifecycle. “Use template” opens
the existing `/operate/profiles/new` structured/raw builder with the selected
source preloaded. It creates a separate profile and never edits this library.

Template validation proves that a representative in-memory render satisfies
the profile contract. Deployment, bootstrap, node health, and convergence still
require runtime verification.

## Coverage map

- `haskell-peersharing-disabled` → profile A
- `haskell-peersharing-enabled` → profile B
- `mixed-minimal` → profile C
- `amaru-preview` → profile D
- `haskell-preview` → profile E
- `amaru-preview2` → profile F
- `haskell-preview2` → profile G
- `generated-mixed` → profile H
- `generated-haskell` → profile I

## Collapse policy

No current production profile archetype is omitted from the template library.
When a future profile differs only by operator-local paths or runtime labels, it
should stay collapsed into the nearest archetypal template instead of adding a
near-duplicate file here.
