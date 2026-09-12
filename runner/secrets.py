"""Safe, deterministic paths for container-mounted secret files."""
from pathlib import Path
import re

from packages.protocol import SecretReference


_PATTERNS = {
    "environment_variable": re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"),
    "common_parameter": re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$"),
}


def secret_path(
    reference: SecretReference, root: Path | str = Path("/run/secrets")
) -> Path:
    """Return the mounted path for a validated secret reference."""
    category = getattr(reference.category, "value", reference.category)
    if not isinstance(category, str) or not isinstance(reference.name, str):
        raise ValueError("secret reference is invalid")
    pattern = _PATTERNS.get(category)
    if pattern is None or not pattern.fullmatch(reference.name):
        raise ValueError("secret reference is invalid")
    try:
        resolved_root = Path(root).resolve(strict=False)
        resolved = (resolved_root / category / reference.name).resolve(strict=False)
    except (OSError, RuntimeError, TypeError):
        raise ValueError("secret reference is invalid") from None
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("secret reference is invalid")
    return resolved


__all__ = ["secret_path"]