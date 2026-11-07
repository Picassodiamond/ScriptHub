"""Path safety helpers."""
import os
from pathlib import Path


def is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` resolves to a location inside ``parent``."""
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        return False


def clean_directory_input(value: str) -> str:
    """Normalise a user-typed directory path; raises ValueError if unusable."""
    value = (value or "").strip().strip('"').strip("'")
    if not value:
        raise ValueError("Enter a directory path.")
    if "\x00" in value or any(ord(c) < 32 for c in value):
        raise ValueError("The path contains invalid characters.")
    return value
