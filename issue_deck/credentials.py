"""Token storage, isolated from configuration.

Prefers the OS keychain (via ``keyring``) and falls back to a ``chmod 600``
plaintext file only when keyring is unavailable. Tokens are keyed by base URL so
multiple instances can be remembered independently.

Security invariants:

* Tokens are **never** written to ``config.json`` or any export/log — only to the
  OS keychain or the dedicated ``token.txt`` fallback.
* The fallback file is created ``0600`` (owner read/write) inside a ``0700`` app
  directory on platforms that support POSIX permissions.
* Callers can inspect the active :data:`storage backend <storage_backend>` so the
  UI can warn a user before a token is written to plaintext.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import constants

try:
    import keyring  # optional dependency
    _HAS_KEYRING = True
except Exception:  # pragma: no cover - depends on environment
    _HAS_KEYRING = False

# Storage backend identifiers returned by :func:`save_token` / :func:`storage_backend`.
KEYCHAIN = "keychain"
PLAINTEXT = "plaintext"
CLEARED = "cleared"


def keyring_available() -> bool:
    """True when an OS keychain backend is importable (tokens stored encrypted)."""
    return _HAS_KEYRING


def storage_backend() -> str:
    """Where a remembered token *would* be stored: ``keychain`` or ``plaintext``."""
    return KEYCHAIN if _HAS_KEYRING else PLAINTEXT


def token_file_path() -> Path:
    """Absolute path of the plaintext fallback file (whether or not it exists)."""
    return _token_file()


def _token_file() -> Path:
    return constants.APP_DIR / "token.txt"


def _key(base_url: str) -> str:
    return base_url or "default"


def _ensure_app_dir() -> Path:
    """Create the app dir with restrictive perms where supported."""
    constants.APP_DIR.mkdir(parents=True, exist_ok=True)
    try:  # POSIX only; a no-op / harmless failure on Windows.
        os.chmod(constants.APP_DIR, 0o700)
    except Exception:
        pass
    return constants.APP_DIR


def load_token(base_url: str) -> str:
    if _HAS_KEYRING:
        try:
            return keyring.get_password(constants.KEYRING_SERVICE, _key(base_url)) or ""
        except Exception:
            pass
    tok = _token_file()
    if tok.exists():
        try:
            return tok.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def save_token(base_url: str, token: str, remember: bool) -> str:
    """Persist (or clear) ``token`` for ``base_url``; return the backend used.

    Returns one of :data:`KEYCHAIN`, :data:`PLAINTEXT`, or :data:`CLEARED`. When
    ``remember`` is false (or ``token`` is empty) any stored token is removed and
    :data:`CLEARED` is returned — so unchecking "remember" actively forgets.
    """
    if not remember or not token:
        clear_token(base_url)
        return CLEARED
    if _HAS_KEYRING:
        try:
            keyring.set_password(constants.KEYRING_SERVICE, _key(base_url), token)
            return KEYCHAIN
        except Exception:
            pass
    _ensure_app_dir()
    tok = _token_file()
    tok.write_text(token, encoding="utf-8")
    try:
        os.chmod(tok, 0o600)
    except Exception:
        pass
    return PLAINTEXT


def clear_token(base_url: str) -> None:
    """Forget the token for ``base_url`` from both keychain and the plaintext file."""
    if _HAS_KEYRING:
        try:
            keyring.delete_password(constants.KEYRING_SERVICE, _key(base_url))
        except Exception:
            pass
    tok = _token_file()
    if tok.exists():
        try:
            tok.unlink()
        except Exception:
            pass
