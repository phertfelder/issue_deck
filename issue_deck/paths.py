"""Centralized, platform-native filesystem locations for application data.

This module is the **single** place that constructs the per-user data directory.
Every other module reads :data:`issue_deck.constants.APP_DIR` (derived from
:func:`resolve_app_dir`) at call time, so path logic is never scattered.

Layout (config + data share one directory — this is a small single-user tool):

* **Windows**: ``%APPDATA%\\IssueDeck``
* **macOS**: ``~/Library/Application Support/IssueDeck``
* **Linux / other**: ``${XDG_CONFIG_HOME:-~/.config}/issue-deck``

Earlier versions stored everything in ``~/.issue_deck`` (:func:`legacy_app_dir`)
and, on Windows/macOS, under a ``JiraPuller`` directory (the app's former name).
:func:`migrate_legacy` performs a one-time, **non-destructive** copy from either
into the native location on startup; the old directories are never deleted.

Resolution order:

1. ``ISSUE_DECK_HOME`` environment variable — an explicit data dir (power users
   and tests). The former ``JIRA_PULLER_HOME`` is still honored as a fallback.
2. The platform-native path above.

The legacy directories are intentionally *not* part of resolution: the app always
converges on the native path, and migration copies data forward once.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Human-facing directory name on Windows / macOS.
_APP_NAME = "IssueDeck"
# The app's former name, kept only so existing data can be migrated forward.
_OLD_APP_NAME = "JiraPuller"
# XDG convention on Linux: lowercase, dashed.
_XDG_DIR_NAME = "issue-deck"
# Plaintext token fallback filename (migrated only under credential rules).
_TOKEN_FILE_NAME = "token.txt"

# Environment variable that overrides the resolved location entirely.
HOME_ENV_VAR = "ISSUE_DECK_HOME"
# The former override variable, still honored as a fallback for continuity.
LEGACY_HOME_ENV_VAR = "JIRA_PULLER_HOME"


def _home() -> Path:
    """Indirection over :meth:`Path.home` so tests can redirect it."""
    return Path.home()


def legacy_app_dir() -> Path:
    """The pre-migration data directory used by every earlier version."""
    return _home() / ".issue_deck"


def _native_dir_for(app_name: str) -> Path | None:
    """The platform-native dir for a given app name (``None`` when N/A on Linux)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / app_name
    # Linux / other POSIX use the XDG name, which never changed on rename.
    return None


def _native_app_dir() -> Path:
    """The platform-native data directory (ignoring override/migration)."""
    native = _native_dir_for(_APP_NAME)
    if native is not None:
        return native
    # Linux / other POSIX: follow the XDG Base Directory spec.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (_home() / ".config")
    return base / _XDG_DIR_NAME


def _legacy_candidates() -> list[Path]:
    """Pre-rename data dirs to migrate forward, most-preferred first.

    Always the ``~/.issue_deck`` dotfolder; on Windows/macOS also the former
    ``JiraPuller`` native dir (the Linux XDG name never changed on rename).
    """
    candidates = [legacy_app_dir()]
    old_native = _native_dir_for(_OLD_APP_NAME)
    if old_native is not None:
        candidates.append(old_native)
    return candidates


def resolve_app_dir() -> Path:
    """The active data directory: an explicit override, else the native path."""
    override = os.environ.get(HOME_ENV_VAR) or os.environ.get(LEGACY_HOME_ENV_VAR)
    if override:
        return Path(override)
    return _native_app_dir()


# --------------------------------------------------------------------------- #
# Legacy migration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MigrationResult:
    """Outcome of :func:`migrate_legacy` (used for logging and tests)."""

    performed: bool           # any file was actually migrated
    migrated_config: bool
    migrated_token: bool
    token_skip_reason: str    # non-empty when a token existed but was left behind
    legacy_dir: Path
    target_dir: Path

    def summary(self) -> str:
        """A clear, user-facing one-line notice; empty when nothing happened."""
        if not (self.performed or self.token_skip_reason):
            return ""
        parts = [f"Migrated settings from legacy {self.legacy_dir} to {self.target_dir}."]
        if self.migrated_token:
            parts.append("A remembered token was moved (plaintext fallback).")
        elif self.token_skip_reason:
            parts.append(f"Token was not migrated: {self.token_skip_reason}.")
        parts.append(
            "The old folder was left in place and can be removed manually once you "
            "have verified the app works.")
        return " ".join(parts)


def _harden_dir(path: Path) -> None:
    try:  # POSIX only; harmless no-op on Windows.
        os.chmod(path, 0o700)
    except OSError:
        pass


def _harden_file(path: Path) -> None:
    try:  # POSIX only; harmless no-op on Windows.
        os.chmod(path, 0o600)
    except OSError:
        pass


def migrate_legacy(*, keyring_available: bool | None = None) -> MigrationResult:
    """Copy legacy ``~/.issue_deck`` data into the native dir, once, safely.

    Rules (see module docstring / README):

    * If the native ``config.json`` already exists, the native dir wins — no
      migration (existing native config is never overwritten).
    * Otherwise, when a legacy ``config.json`` exists, copy the legacy data tree
      (config, saved views, profiles, …) into the native dir. Existing native
      files are never clobbered.
    * ``token.txt`` is migrated **only** when keyring is unavailable — i.e. when
      plaintext is still the sanctioned fallback. When keyring is available the
      token is left behind so the user re-enters it into the keychain; storage is
      never silently weakened.
    * The legacy directory is never deleted.

    ``keyring_available`` is resolved from :mod:`issue_deck.credentials` when not
    passed explicitly (tests pass it directly).
    """
    target = resolve_app_dir()
    legacy = legacy_app_dir()
    nothing = MigrationResult(False, False, False, "", legacy, target)

    # Native already set up: the native dir always wins, no migration.
    if (target / "config.json").exists():
        return nothing
    # Pick the first legacy dir that actually holds a config and isn't the target.
    legacy = next(
        (c for c in _legacy_candidates()
         if (c / "config.json").exists() and c.resolve() != target.resolve()),
        None,
    )
    if legacy is None:
        return MigrationResult(False, False, False, "", legacy_app_dir(), target)

    if keyring_available is None:
        from . import credentials  # local import avoids an import cycle
        keyring_available = credentials.keyring_available()

    target.mkdir(parents=True, exist_ok=True)
    _harden_dir(target)

    migrated_config = False
    for entry in sorted(legacy.iterdir()):
        if entry.name == _TOKEN_FILE_NAME:
            continue  # handled separately, under credential rules
        dest = target / entry.name
        if dest.exists():
            continue  # never overwrite anything already native
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)
        if entry.name == "config.json":
            migrated_config = True

    migrated_token = False
    token_skip_reason = ""
    legacy_token = legacy / _TOKEN_FILE_NAME
    if legacy_token.exists():
        if keyring_available:
            token_skip_reason = (
                "keyring is available, so re-enter your token to store it in the OS "
                "keychain instead of a plaintext file")
        else:
            dest_token = target / _TOKEN_FILE_NAME
            if not dest_token.exists():
                shutil.copy2(legacy_token, dest_token)
                _harden_file(dest_token)
            migrated_token = True

    return MigrationResult(
        performed=migrated_config or migrated_token,
        migrated_config=migrated_config,
        migrated_token=migrated_token,
        token_skip_reason=token_skip_reason,
        legacy_dir=legacy,
        target_dir=target,
    )
