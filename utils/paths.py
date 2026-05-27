"""Path resolution and allowlist enforcement."""
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


def resolve_arg(arg: str, base_cwd: str) -> str:
    """Resolve a user-provided path argument with POSIX `cd` semantics.

    - `~` is expanded against the user's home directory.
    - Absolute paths (post-expansion) are returned unchanged.
    - Relative paths are joined with `base_cwd` and normalized so that
      `..` and `.` segments collapse before any allowlist check runs.
    """
    arg = os.path.expanduser(arg)
    if os.path.isabs(arg):
        return os.path.normpath(arg)
    return os.path.normpath(os.path.join(base_cwd, arg))


def safe_resolve(path: str) -> str:
    """Best-effort resolve for logging; never raises."""
    try:
        return str(Path(os.path.expanduser(path)).resolve(strict=False))
    except (OSError, RuntimeError):
        return path


def is_cwd_allowed(path: str, allowed_roots: Iterable[Path]) -> bool:
    """Check that `path` exists and resolves under one of `allowed_roots`.

    Uses strict resolution so symlinks pointing outside the allowlist are
    rejected (a symlinked dir resolves to its real target before the check).
    """
    try:
        resolved = Path(os.path.expanduser(path)).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return any(
        resolved == root or root in resolved.parents
        for root in allowed_roots
    )
