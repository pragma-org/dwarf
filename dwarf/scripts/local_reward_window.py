"""The local reward-window timing contract shared by Cardano and Amaru devnets.

profile-c (``runtime_amaru_control_substrate``) introduced it and reached epoch 20 live with
Amaru 10.11.20260918: one epoch is 400 slots of 0.5 s (200 s), ``k = 8``, ``f = 1/5`` and
Amaru's ``epoch_length_scale_factor = 10``, so Cardano's reward pulse (after ``4k/f`` slots)
has a non-empty window and Amaru derives the same epoch length as ``inverse(f) * scale * k``.

Self-composed docker devnets with Amaru use the same contract when they set
``substrate.amaru_local_reward_window_timing: true`` (``runtime_compose_substrate``).

This module holds the contract, the generators of the Amaru inputs (global parameters and
era history) and the identity proof.  It does not read or write files.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

LOCAL_REWARD_WINDOW_EPOCH_FACTOR = 10
LOCAL_REWARD_WINDOW_CONTRACT = {
    "active_slot_coeff": Fraction(1, 5),
    "active_slot_coeff_inverse": 5,
    "epoch_length": 400,
    "epoch_length_scale_factor": 10,
    "randomness_stabilisation_window": 160,
    "reward_pulsing_window": 240,
    "security_parameter": 8,
    "slot_length_ms": 500,
    "stability_window": 120,
}
MONETARY_EXPANSION = Fraction(3, 1000)
TREASURY_CUT = Fraction(1, 5)


class LocalRewardWindowError(RuntimeError):
    """Generated Cardano and Amaru timing inputs are not the approved contract."""


def exact_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise LocalRewardWindowError(
            f"local reward-window contract requires exact integer {label}"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise LocalRewardWindowError(
        f"local reward-window contract requires exact integer {label}"
    )


def exact_milliseconds(value: Any, *, label: str) -> int:
    ratio = Fraction(str(value)) * 1000
    if ratio.denominator != 1:
        raise LocalRewardWindowError(
            f"local reward-window contract requires exact integer {label} milliseconds"
        )
    return exact_integer(ratio.numerator, label=f"{label} milliseconds")


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

def shelley_genesis_fields() -> dict[str, Any]:
    """The Shelley genesis fields the contract fixes (JSON values as cardano-node reads them)."""
    contract = LOCAL_REWARD_WINDOW_CONTRACT
    return {
        "epochLength": contract["epoch_length"],
        "slotLength": float(Fraction(contract["slot_length_ms"], 1000)),
        "securityParam": contract["security_parameter"],
        "activeSlotsCoeff": float(contract["active_slot_coeff"]),
        "protocolParams": {
            "rho": float(MONETARY_EXPANSION),
            "tau": float(TREASURY_CUT),
        },
    }


def byron_protocol_k() -> int:
    """Byron ``protocolConsts.k``: the same ``k`` (cardano-testnet keeps both equal)."""
    return LOCAL_REWARD_WINDOW_CONTRACT["security_parameter"]


def amaru_global_parameters(
    *, shelley_genesis: dict[str, Any], byron_genesis: dict[str, Any]
) -> dict[str, Any]:
    """Amaru global parameters for the generated genesis.

    The three timing values come from the contract; the KES, supply and system-start values are
    read from the generated genesis, from the same fields the bootstrap producer reads them
    (``system_start`` = Byron ``startTime`` in milliseconds).
    """
    contract = LOCAL_REWARD_WINDOW_CONTRACT
    try:
        return {
            "consensus_security_param": contract["security_parameter"],
            "active_slot_coeff_inverse": contract["active_slot_coeff_inverse"],
            "epoch_length_scale_factor": contract["epoch_length_scale_factor"],
            "max_lovelace_supply": exact_integer(
                shelley_genesis["maxLovelaceSupply"], label="maxLovelaceSupply"
            ),
            "slots_per_kes_period": exact_integer(
                shelley_genesis["slotsPerKESPeriod"], label="slotsPerKESPeriod"
            ),
            "max_kes_evolution": exact_integer(
                shelley_genesis["maxKESEvolutions"], label="maxKESEvolutions"
            ),
            "system_start": exact_integer(
                byron_genesis["startTime"], label="Byron startTime"
            )
            * 1000,
        }
    except KeyError as error:
        raise LocalRewardWindowError(
            f"local reward-window contract cannot derive Amaru global parameters: missing {error}"
        ) from error


def amaru_era_history() -> dict[str, Any]:
    """One operative Conway era from genesis, the contract's epoch and slot length.

    ``stability_window`` is ``3k/f`` (what Amaru's built-in networks use), not the bootstrap
    producer's ``3 * epochLength``.
    """
    contract = LOCAL_REWARD_WINDOW_CONTRACT
    return {
        "stability_window": contract["stability_window"],
        "eras": [
            {
                "start": {"time": 0, "slot": 0, "epoch": 0},
                "end": None,
                "params": {
                    "epoch_size_slots": contract["epoch_length"],
                    "slot_length": contract["slot_length_ms"],
                    "era_name": "Conway",
                },
            }
        ],
    }


def amaru_environment(globals_doc: dict[str, Any]) -> dict[str, str]:
    """``AMARU_GLOBAL_*`` overrides that ``amaru run`` reads for a custom testnet."""
    names = {
        "consensus_security_param": "AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM",
        "active_slot_coeff_inverse": "AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE",
        "epoch_length_scale_factor": "AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR",
        "max_lovelace_supply": "AMARU_GLOBAL_MAX_LOVELACE_SUPPLY",
        "slots_per_kes_period": "AMARU_GLOBAL_SLOTS_PER_KES_PERIOD",
        "max_kes_evolution": "AMARU_GLOBAL_MAX_KES_EVOLUTION",
        "system_start": "AMARU_GLOBAL_SYSTEM_START",
    }
    return {env: str(exact_integer(globals_doc[key], label=key)) for key, env in names.items()}


def testnet_view(
    *, shelley_genesis: dict[str, Any], byron_genesis: dict[str, Any]
) -> dict[str, Any]:
    """The generated genesis in the testnet.yaml shape the proof reads (Byron ``protocolConsts``)."""
    view = {
        key: shelley_genesis[key]
        for key in ("epochLength", "activeSlotsCoeff", "protocolParams", "securityParam", "slotLength")
        if key in shelley_genesis
    }
    if "protocolConsts" in byron_genesis:
        view["protocolConsts"] = byron_genesis["protocolConsts"]
    return view


# --------------------------------------------------------------------------- #
# Identity proof
# --------------------------------------------------------------------------- #

def prove_timing_identity(
    *,
    testnet: dict[str, Any],
    globals_doc: dict[str, Any],
    era_doc: dict[str, Any],
) -> dict[str, Any]:
    """Prove the generated local Cardano and Amaru timing inputs are identical.

    Cardano starts reward pulsing after ``4k/f`` slots.  The local profile follows the
    standard ``epochLength = 10k/f`` relation so the pulse has a non-empty completion window.
    Amaru models that same relation as ``inverse(f) * epoch_length_scale_factor * k``.
    """
    try:
        epoch_length = exact_integer(testnet["epochLength"], label="epochLength")
        coefficient = Fraction(str(testnet["activeSlotsCoeff"]))
        monetary_expansion = Fraction(str(testnet["protocolParams"]["rho"]))
        treasury_cut = Fraction(str(testnet["protocolParams"]["tau"]))
        security_parameter = exact_integer(
            testnet["securityParam"], label="securityParam"
        )
        protocol_security_parameter = exact_integer(
            testnet["protocolConsts"]["k"], label="protocolConsts.k"
        )
        amaru_security_parameter = exact_integer(
            globals_doc["consensus_security_param"],
            label="Amaru consensus_security_param",
        )
        amaru_coefficient_inverse = exact_integer(
            globals_doc["active_slot_coeff_inverse"],
            label="Amaru active_slot_coeff_inverse",
        )
        scale_factor = exact_integer(
            globals_doc["epoch_length_scale_factor"],
            label="Amaru epoch_length_scale_factor",
        )
        era_stability_window = exact_integer(
            era_doc["stability_window"], label="era stability_window"
        )
        eras = era_doc["eras"]
        if not isinstance(eras, list) or len(eras) != 1:
            raise LocalRewardWindowError(
                "local reward-window contract requires one operative Conway era"
            )
        era = eras[0]
        era_params = era["params"]
        if era_params["era_name"] != "Conway" or era.get("end") is not None:
            raise LocalRewardWindowError(
                "local reward-window contract requires one operative Conway era"
            )
        era_epoch_length = exact_integer(
            era_params["epoch_size_slots"], label="era epoch_size_slots"
        )
        era_slot_length_ms = exact_integer(
            era_params["slot_length"], label="era slot_length"
        )
        cardano_slot_length_ms = exact_milliseconds(testnet["slotLength"], label="slot")
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
        raise LocalRewardWindowError(
            "local reward-window contract is missing exact generated parameters"
        ) from error
    if coefficient <= 0 or coefficient > 1 or coefficient.numerator != 1:
        raise LocalRewardWindowError(
            "local reward-window contract requires activeSlotsCoeff = 1/n"
        )
    contract = LOCAL_REWARD_WINDOW_CONTRACT
    coefficient_inverse = coefficient.denominator
    expected_epoch_length = (
        coefficient_inverse * LOCAL_REWARD_WINDOW_EPOCH_FACTOR * security_parameter
    )
    stability_window = 3 * security_parameter * coefficient_inverse
    randomness_window = 4 * security_parameter * coefficient_inverse
    reward_pulsing_window = epoch_length - randomness_window
    consistent = (
        coefficient == contract["active_slot_coeff"]
        and coefficient_inverse == contract["active_slot_coeff_inverse"]
        and security_parameter == contract["security_parameter"]
        and protocol_security_parameter == contract["security_parameter"]
        and amaru_security_parameter == contract["security_parameter"]
        and amaru_coefficient_inverse == contract["active_slot_coeff_inverse"]
        and scale_factor == contract["epoch_length_scale_factor"]
        and epoch_length == contract["epoch_length"]
        and epoch_length == expected_epoch_length
        and era_epoch_length == contract["epoch_length"]
        and era_stability_window == contract["stability_window"]
        and stability_window == contract["stability_window"]
        and randomness_window == contract["randomness_stabilisation_window"]
        and reward_pulsing_window == contract["reward_pulsing_window"]
        and era_slot_length_ms == contract["slot_length_ms"]
        and cardano_slot_length_ms == contract["slot_length_ms"]
        and monetary_expansion == MONETARY_EXPANSION
        and treasury_cut == TREASURY_CUT
        and reward_pulsing_window > 0
    )
    if not consistent:
        raise LocalRewardWindowError(
            "local reward-window contract is incompatible with the approved tuple: "
            "Cardano and Amaru require epoch=400, f=1/5, k=8, scale=10, "
            "stability=120, and slotLength=500ms"
        )
    return {
        "active_slot_coeff": f"{coefficient.numerator}/{coefficient.denominator}",
        "active_slot_coeff_inverse": coefficient_inverse,
        "epoch_length": epoch_length,
        "epoch_length_scale_factor": scale_factor,
        "monetary_expansion": "3/1000",
        "randomness_stabilisation_window": randomness_window,
        "reward_pulsing_window": reward_pulsing_window,
        "security_parameter": security_parameter,
        "slot_length_ms": cardano_slot_length_ms,
        "stability_window": stability_window,
        "treasury_cut": "1/5",
        "verified": True,
    }
