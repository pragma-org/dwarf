from __future__ import annotations


class AmaruRuntimeInterfaceError(ValueError):
    """The requested Amaru runtime interface is unsupported."""


def uses_modern_amaru_runtime_interface(
    image: str, runtime_interface: str | None = None
) -> bool:
    """Return whether the proven lifecycle must extract the Amaru binary."""

    if runtime_interface is not None:
        if runtime_interface not in {"extracted-binary", "legacy-wrapper"}:
            raise AmaruRuntimeInterfaceError(
                "amaru_runtime_interface must be extracted-binary or legacy-wrapper"
            )
        return runtime_interface == "extracted-binary"
    repository = str(image or "").split("@", 1)[0]
    last_slash = repository.rfind("/")
    if repository.rfind(":") > last_slash:
        repository = repository[: repository.rfind(":")]
    return repository == "ghcr.io/pragma-org/amaru"
