from profile_manager import scenario


def test_framework_commit_defaults_to_immutable_deployed_revision(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "209fe116ba9d07b39e436e41787323d8f3f57eb9")

    assert scenario._resolve_framework_commit(None) == (
        "209fe116ba9d07b39e436e41787323d8f3f57eb9"
    )


def test_explicit_framework_commit_takes_precedence(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "container-revision")

    assert scenario._resolve_framework_commit("caller-revision") == "caller-revision"


def test_missing_framework_revision_is_retained_as_unknown(monkeypatch):
    monkeypatch.delenv("DWARF_SOURCE_REVISION", raising=False)
    monkeypatch.setattr(scenario, "_git_framework_commit", lambda: None)

    assert scenario._resolve_framework_commit(None) == "unknown"


def test_host_checkout_revision_is_used_when_control_shim_has_no_image_env(
    monkeypatch,
):
    monkeypatch.delenv("DWARF_SOURCE_REVISION", raising=False)
    monkeypatch.setattr(
        scenario,
        "_git_framework_commit",
        lambda: "654fa392e7677ed4d6c1d659d6286c6747147ad4",
    )

    assert scenario._resolve_framework_commit(None) == (
        "654fa392e7677ed4d6c1d659d6286c6747147ad4"
    )
