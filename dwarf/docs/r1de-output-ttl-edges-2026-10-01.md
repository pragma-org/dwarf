# R1d/R1e — output/value-size + validity-interval knife-edges (2026-10-01, confirm-negatives)

All 4/4 AGREE (amaru == cardano reject), via submit_differential_run.sh on funded pair1:
| case | both-reject reason | verdict |
| output coin=1 (below min-UTxO) | ValueNotConservedUTxO | AGREE |
| value-too-big (200 assets) | ValueNotConservedUTxO | AGREE |
| TTL in past (slot 1) | OutsideValidityIntervalUTxO | AGREE |
| validity-interval inverted (start 999999 > end 1) | OutsideValidityIntervalUTxO (invalidBefore 999999) | AGREE |

No divergence. (The below-min + value-too-big fixtures tripped value-conservation before the specific
min-UTxO/OutputTooBig error; both-reject regardless. The exact min-UTxO boundary is already covered
conformant by the mint/burn minada-asset-at-min/below cases, 19/19 AGREE.) Builder: build_r1de.py.
