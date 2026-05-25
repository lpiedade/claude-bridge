"""Per-chat session state persisted as JSON on disk."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from core.config import DEFAULT_CWD, DEFAULT_EFFORT, DEFAULT_MODEL
from core.logger import log

STATE_FILE = Path.home() / ".claude-bridge" / "state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(STATE_FILE.parent, 0o700)

if STATE_FILE.exists():
    _mode = STATE_FILE.stat().st_mode & 0o777
    if _mode != 0o600:
        log.warning("state.json had mode %o; tightening to 0600", _mode)
        os.chmod(STATE_FILE, 0o600)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError as e:
        backup = STATE_FILE.with_suffix(".json.corrupt")
        log.error(
            "state.json is corrupt (%s); moved to %s and starting empty",
            e, backup,
        )
        STATE_FILE.rename(backup)
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


_state_lock = asyncio.Lock()


def _new_session_entry() -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "cwd": DEFAULT_CWD,
        "effort": DEFAULT_EFFORT,
        "model": DEFAULT_MODEL,
        "started": False,
    }


async def session_for(chat_id: int) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        if key not in state:
            state[key] = _new_session_entry()
            save_state(state)
        else:
            defaults = _new_session_entry()
            missing = {k: v for k, v in defaults.items() if k not in state[key]}
            if missing:
                state[key].update(missing)
                save_state(state)
        return dict(state[key])


async def update_session(chat_id: int, **changes) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        if key not in state:
            state[key] = _new_session_entry()
        state[key].update(changes)
        save_state(state)
        return dict(state[key])


async def reset_session(chat_id: int) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        state[key] = _new_session_entry()
        save_state(state)
        return dict(state[key])


async def get_last_update_id() -> int:
    async with _state_lock:
        state = load_state()
        return state.get("_meta", {}).get("last_processed_update_id", 0)


async def set_last_update_id(update_id: int) -> None:
    async with _state_lock:
        state = load_state()
        meta = state.setdefault("_meta", {})
        if update_id > meta.get("last_processed_update_id", 0):
            meta["last_processed_update_id"] = update_id
            save_state(state)


async def claim_update(update) -> bool:
    """True if this update is fresh; False if it was already processed."""
    uid = update.update_id
    last = await get_last_update_id()
    if uid <= last:
        log.info("skipping replayed update_id=%s (last=%s)", uid, last)
        return False
    return True
