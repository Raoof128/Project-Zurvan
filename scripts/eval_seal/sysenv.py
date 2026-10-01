"""Clean process environment and caller identity for sealed code (contract s3.11).

clean_env inherits NOTHING from os.environ. `extra` is an allow-by-exception channel:
it may add new variables (the intended use is TMPDIR) but can never set or override a
variable the base env pins, nor anything that changes dynamic loading, interpreter
startup, or git configuration. Violations raise ValueError (a programming error, not a
SealRefusal: callers pass literals, never user input).
"""
from __future__ import annotations

import os
import pwd
import re
from pathlib import Path

from .codes import SealRefusal

_BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_BASE_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_TERMINAL_PROMPT", "GIT_ATTR_NOSYSTEM",
})
_FORBIDDEN_PREFIXES = ("GIT_", "LC_", "DYLD_", "LD_", "PYTHON")
_FORBIDDEN_KEYS = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4", "IFS", "CDPATH"})
_UID_RE = re.compile(r"[0-9]+")
_UID_MAX = 2 ** 31 - 1


def clean_env(home: Path, extra=None) -> dict:
    home = Path(home)
    if not home.is_absolute():
        raise ValueError("home must be absolute")
    env = {
        "PATH": _BASE_PATH,
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ATTR_NOSYSTEM": "1",
    }
    for key, val in (extra or {}).items():
        if type(key) is not str or type(val) is not str:
            raise ValueError("env entries must be str")
        if not key or "=" in key or "\x00" in key or "\x00" in val:
            raise ValueError("malformed env entry")
        if key in _BASE_KEYS or key in _FORBIDDEN_KEYS or key.startswith(_FORBIDDEN_PREFIXES):
            raise ValueError("reserved env key")
        env[key] = val
    return env


def home_of_euid() -> Path:
    """Home of the effective uid from the passwd database; NEVER $HOME (macOS sudo keeps HOME)."""
    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def sudo_uid() -> int:
    """Validated SUDO_UID. Canonical decimal only, 1..2**31-1, must have a passwd entry.

    uid 0 is refused: a root caller is not a human operator and sudo-from-root proves nothing.
    """
    raw = os.environ.get("SUDO_UID")
    if raw is None or not _UID_RE.fullmatch(raw) or (len(raw) > 1 and raw[0] == "0"):
        raise SealRefusal("BAD_ARGS")
    uid = int(raw)
    if uid < 1 or uid > _UID_MAX:
        raise SealRefusal("BAD_ARGS")
    try:
        pwd.getpwuid(uid)
    except KeyError:
        raise SealRefusal("BAD_ARGS") from None
    return uid
