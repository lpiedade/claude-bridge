#!/usr/bin/env python3
"""Backwards-compatible shim — historical entrypoint for the bridge.

The implementation lives in ``app.main`` (entrypoint), ``service.handlers``
(Telegram commands), ``repositories.session_repository`` (state),
``integrations.claude_client`` (CLI invocation), ``core`` (config/logger),
and ``utils`` (paths/redaction). This shim re-exports the public surface
expected by the existing tests; it will be removed once the test suite is
migrated to the new modules.
"""
from __future__ import annotations

from core.config import (  # noqa: F401  (re-exports for tests)
    ALLOWED_CHAT_IDS,
    ALLOWED_CWD_ROOTS,
    BOT_TOKEN,
    CLAUDE_BIN,
    DEFAULT_CWD,
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    PERMISSION_MODE,
    TIMEOUT_SECONDS,
    VALID_EFFORTS,
    VALID_MODELS,
    parse_effort as _parse_effort,
    parse_model as _parse_model,
)
from core.logger import log  # noqa: F401
from integrations.claude_client import extract_result_text  # noqa: F401
from repositories import session_repository as _state
from repositories.session_repository import (  # noqa: F401
    claim_update as _claim_update,
    get_last_update_id,
    load_state,
    reset_session,
    save_state,
    session_for,
    set_last_update_id,
    update_session,
)
from service.handlers._common import authorized, is_cwd_allowed  # noqa: F401
from service.handlers.cwd import (  # noqa: F401
    LS_MAX_ENTRIES,
    cmd_cd,
    cmd_ls,
    cmd_pwd,
)
from service.handlers.effort import cmd_effort  # noqa: F401
from service.handlers.message import on_message  # noqa: F401
from service.handlers.model import cmd_model  # noqa: F401
from service.handlers.session import cmd_new  # noqa: F401
from service.handlers.start import cmd_start, cmd_status  # noqa: F401
from utils.paths import (  # noqa: F401
    resolve_arg as _resolve_arg,
    safe_resolve as _safe_resolve,
)
from utils.redact import redact as _redact  # noqa: F401

STATE_FILE = _state.STATE_FILE

if not is_cwd_allowed(DEFAULT_CWD):
    raise SystemExit(
        f"DEFAULT_CWD {DEFAULT_CWD!r} is not under any of "
        f"CLAUDE_BRIDGE_CWD_ROOTS={[str(r) for r in ALLOWED_CWD_ROOTS]!r}"
    )


def main() -> None:
    from app.main import main as _main
    _main()


if __name__ == "__main__":
    main()
