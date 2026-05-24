"""Tests for path resolution and allowlist enforcement."""
from __future__ import annotations

import os
from pathlib import Path


def test_resolve_arg_expands_tilde(bot_module):
    home = os.path.expanduser("~")
    assert bot_module._resolve_arg("~/foo", "/base") == os.path.normpath(f"{home}/foo")


def test_resolve_arg_absolute_path_unchanged(bot_module):
    assert bot_module._resolve_arg("/etc/hosts", "/base") == "/etc/hosts"


def test_resolve_arg_relative_joined_with_base(bot_module):
    assert bot_module._resolve_arg("sub", "/base") == "/base/sub"


def test_resolve_arg_collapses_dotdot(bot_module):
    # Critical for safe allowlist enforcement: `..` must collapse before checks.
    assert bot_module._resolve_arg("../escape", "/base/inner") == "/base/escape"


def test_resolve_arg_normalizes_dot_segments(bot_module):
    assert bot_module._resolve_arg("./a/./b", "/base") == "/base/a/b"


def test_is_cwd_allowed_accepts_root_itself(bot_module, allowed_dirs):
    a, _ = allowed_dirs
    assert bot_module.is_cwd_allowed(str(a)) is True


def test_is_cwd_allowed_accepts_subdirectory(bot_module, allowed_dirs):
    a, _ = allowed_dirs
    sub = a / "nested"
    sub.mkdir(exist_ok=True)
    assert bot_module.is_cwd_allowed(str(sub)) is True


def test_is_cwd_allowed_rejects_outside_path(bot_module, tmp_home):
    outside = tmp_home / "outside"
    outside.mkdir(exist_ok=True)
    assert bot_module.is_cwd_allowed(str(outside)) is False


def test_is_cwd_allowed_rejects_nonexistent(bot_module, allowed_dirs):
    a, _ = allowed_dirs
    assert bot_module.is_cwd_allowed(str(a / "does-not-exist")) is False


def test_is_cwd_allowed_rejects_symlink_escape(bot_module, allowed_dirs, tmp_home):
    a, _ = allowed_dirs
    outside = tmp_home / "symlink_target"
    outside.mkdir(exist_ok=True)
    link = a / "escape-link"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(outside)
    # Strict resolution should follow the symlink to its real target, which is
    # outside the allowlist.
    assert bot_module.is_cwd_allowed(str(link)) is False
