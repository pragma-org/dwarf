import json
from pathlib import Path

from profile_manager.profiles import Profile


AMARU_V1 = "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af"
AMARU_V2 = "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0"
CARDANO_V1 = "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
CARDANO_V2 = "1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c"


def _profile(name):
    path = Path("dwarf/profiles") / name / "profile.yaml"
    return json.loads(path.read_text(encoding="utf-8"))


def test_nanosecond_profiles_are_additive_and_v1_profiles_are_unchanged():
    amaru_v1 = _profile("profile-q-amaru-measurement-patched")
    cardano_v1 = _profile("profile-t-cardano-measurement-patched")
    amaru_v2 = _profile("profile-u-amaru-measurement-nanoseconds-v2")
    cardano_v2 = _profile("profile-v-cardano-measurement-nanoseconds-v2")

    assert amaru_v1["measurement_patch_set_sha256"] == AMARU_V1
    assert cardano_v1["measurement_patch_set_sha256"] == CARDANO_V1
    assert "measurement_revision" not in amaru_v1
    assert "measurement_revision" not in cardano_v1
    assert amaru_v2["measurement_revision"] == "nanoseconds-v2"
    assert amaru_v2["measurement_patch_set_sha256"] == AMARU_V2
    assert cardano_v2["measurement_revision"] == "nanoseconds-v2"
    assert cardano_v2["measurement_patch_set_sha256"] == CARDANO_V2


def test_profile_model_retains_measurement_revision():
    body = _profile("profile-u-amaru-measurement-nanoseconds-v2")
    profile = Profile.from_dict(body)

    assert profile.measurement_revision == "nanoseconds-v2"
