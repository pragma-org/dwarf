# Amaru custom-devnet bootstrap assets

These loader scripts are bundled so a normal DWARF installation does not
depend on an operator-local Amaru source checkout. They were sourced from the
Pragma Amaru testnet loader flow at revision
`931c6400409b60f0e07c7f1c0534838397a7d367` and are staged—not edited in
place—by `runtime_amaru_bootstrap_synth.py` for the requested node count and
current header-import CLI.

The loader base image is digest-pinned in that module. The selected Amaru node
binary is copied from the exact release image into a per-release loader image,
so bootstrap schema and node identity stay aligned and are reviewable in the
retained runtime evidence.
