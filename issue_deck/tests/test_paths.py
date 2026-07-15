"""Platform-native path resolution and one-time legacy migration.

Never touches the real per-user directory: home is redirected to ``tmp_path``,
``sys.platform`` and the relevant env vars are monkeypatched, and the session's
``ISSUE_DECK_HOME`` override is removed per-test so native resolution is tested.
"""

from __future__ import annotations

import json

import pytest

from issue_deck import paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect Path.home() and drop the session home overrides."""
    monkeypatch.setattr(paths, "_home", lambda: tmp_path)
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.delenv(paths.LEGACY_HOME_ENV_VAR, raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# Native resolution per platform
# --------------------------------------------------------------------------- #
def test_windows_native_path_uses_appdata(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(home / "Roaming"))
    assert paths.resolve_app_dir() == home / "Roaming" / "IssueDeck"


def test_windows_native_path_falls_back_without_appdata(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    assert paths.resolve_app_dir() == home / "AppData" / "Roaming" / "IssueDeck"


def test_macos_native_path(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.resolve_app_dir() == (
        home / "Library" / "Application Support" / "IssueDeck")


def test_linux_native_path_defaults_to_dot_config(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert paths.resolve_app_dir() == home / ".config" / "issue-deck"


def test_linux_native_path_honors_xdg_config_home(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    assert paths.resolve_app_dir() == home / "xdg" / "issue-deck"


def test_env_override_wins_over_native(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home / "explicit"))
    assert paths.resolve_app_dir() == home / "explicit"


def test_legacy_env_override_still_honored(home, monkeypatch):
    # The former JIRA_PULLER_HOME keeps working as a fallback.
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv(paths.LEGACY_HOME_ENV_VAR, str(home / "legacy_env"))
    assert paths.resolve_app_dir() == home / "legacy_env"


def test_new_env_override_wins_over_legacy_env(home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home / "new_env"))
    monkeypatch.setenv(paths.LEGACY_HOME_ENV_VAR, str(home / "legacy_env"))
    assert paths.resolve_app_dir() == home / "new_env"


def test_macos_migrates_from_former_jirapuller_dir(home, monkeypatch):
    # A macOS user with data under the old "JiraPuller" native dir keeps it after
    # the rename, copied forward into the new "IssueDeck" native dir.
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    support = home / "Library" / "Application Support"
    old = support / "JiraPuller"
    old.mkdir(parents=True)
    (old / "config.json").write_text(json.dumps({"base_url": "https://old"}), encoding="utf-8")
    (old / "views.json").write_text('{"views": []}', encoding="utf-8")

    result = paths.migrate_legacy(keyring_available=False)

    assert result.performed and result.migrated_config
    new = support / "IssueDeck"
    migrated = json.loads((new / "config.json").read_text(encoding="utf-8"))
    assert migrated["base_url"] == "https://old"
    assert (new / "views.json").exists()
    assert old.exists()  # old dir is never deleted


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
def _write_legacy_config(home, payload=None):
    legacy = home / ".issue_deck"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "config.json").write_text(
        payload if payload is not None else json.dumps({"base_url": "https://x"}),
        encoding="utf-8")
    return legacy


@pytest.fixture
def linux_native(home, monkeypatch):
    """Force a deterministic native dir under tmp for migration tests."""
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home / ".config" / "issue-deck"


def test_fresh_install_migrates_nothing(linux_native):
    result = paths.migrate_legacy(keyring_available=False)
    assert result.performed is False
    assert result.migrated_config is False
    assert not (linux_native / "config.json").exists()


def test_legacy_config_migrates_to_native(home, linux_native):
    _write_legacy_config(home)
    result = paths.migrate_legacy(keyring_available=False)
    assert result.performed and result.migrated_config
    migrated = json.loads((linux_native / "config.json").read_text(encoding="utf-8"))
    assert migrated["base_url"] == "https://x"
    assert result.summary()  # a clear, non-empty notice


def test_sibling_data_files_are_migrated(home, linux_native):
    legacy = _write_legacy_config(home)
    (legacy / "views.json").write_text('{"views": []}', encoding="utf-8")
    (legacy / "csv_profiles").mkdir()
    (legacy / "csv_profiles" / "p.json").write_text("{}", encoding="utf-8")
    paths.migrate_legacy(keyring_available=False)
    assert (linux_native / "views.json").exists()
    assert (linux_native / "csv_profiles" / "p.json").exists()


def test_existing_native_config_wins_over_legacy(home, linux_native):
    _write_legacy_config(home, json.dumps({"base_url": "https://legacy"}))
    linux_native.mkdir(parents=True, exist_ok=True)
    (linux_native / "config.json").write_text(
        json.dumps({"base_url": "https://native"}), encoding="utf-8")
    result = paths.migrate_legacy(keyring_available=False)
    assert result.performed is False
    kept = json.loads((linux_native / "config.json").read_text(encoding="utf-8"))
    assert kept["base_url"] == "https://native"  # never overwritten


def test_malformed_legacy_config_does_not_crash(home, linux_native):
    _write_legacy_config(home, "{not valid json")
    result = paths.migrate_legacy(keyring_available=False)  # must not raise
    assert result.migrated_config
    # Copied verbatim; the app's tolerant loader handles the bad content later.
    assert (linux_native / "config.json").read_text(encoding="utf-8") == "{not valid json"


def test_migrated_config_never_contains_a_token(home, linux_native):
    _write_legacy_config(home)
    (home / ".issue_deck" / "token.txt").write_text("secret", encoding="utf-8")
    paths.migrate_legacy(keyring_available=False)
    data = json.loads((linux_native / "config.json").read_text(encoding="utf-8"))
    assert "token" not in data


def test_plaintext_token_migrated_only_without_keyring(home, linux_native):
    _write_legacy_config(home)
    (home / ".issue_deck" / "token.txt").write_text("secret", encoding="utf-8")
    result = paths.migrate_legacy(keyring_available=False)
    assert result.migrated_token
    assert (linux_native / "token.txt").read_text(encoding="utf-8") == "secret"


def test_token_not_migrated_when_keyring_available(home, linux_native):
    _write_legacy_config(home)
    (home / ".issue_deck" / "token.txt").write_text("secret", encoding="utf-8")
    result = paths.migrate_legacy(keyring_available=True)
    assert result.migrated_token is False
    assert result.token_skip_reason  # explains the user must re-enter it
    assert not (linux_native / "token.txt").exists()  # never weakened silently


def test_legacy_directory_is_never_deleted(home, linux_native):
    legacy = _write_legacy_config(home)
    (legacy / "token.txt").write_text("secret", encoding="utf-8")
    paths.migrate_legacy(keyring_available=False)
    assert legacy.exists()
    assert (legacy / "config.json").exists()
    assert (legacy / "token.txt").exists()  # original plaintext left in place


@pytest.mark.skipif(paths.sys.platform == "win32", reason="POSIX file permissions only")
def test_migrated_token_gets_restrictive_permissions(home, linux_native):
    import os
    import stat
    _write_legacy_config(home)
    (home / ".issue_deck" / "token.txt").write_text("secret", encoding="utf-8")
    paths.migrate_legacy(keyring_available=False)
    mode = stat.S_IMODE(os.stat(linux_native / "token.txt").st_mode)
    assert mode == 0o600
