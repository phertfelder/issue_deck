"""Token storage tests: keyring vs plaintext fallback, and no-persist behavior.

Uses a fake keyring and a redirected APP_DIR; never touches the real keychain
or home directory.
"""

from __future__ import annotations

import os
import stat

import pytest

from issue_deck import constants, credentials
from issue_deck.config import AppConfig


@pytest.fixture
def redirect_appdir(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


class FakeKeyring:
    def __init__(self, fail=False):
        self.store: dict[tuple[str, str], str] = {}
        self.fail = fail

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.store[(service, name)] = value

    def get_password(self, service, name):
        if self.fail:
            raise RuntimeError("keyring unavailable")
        return self.store.get((service, name))

    def delete_password(self, service, name):
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.store.pop((service, name), None)


def _use_keyring(monkeypatch, fake):
    monkeypatch.setattr(credentials, "_HAS_KEYRING", True)
    monkeypatch.setattr(credentials, "keyring", fake, raising=False)


def _no_keyring(monkeypatch):
    monkeypatch.setattr(credentials, "_HAS_KEYRING", False)


# --------------------------------------------------------------------------- #
# via AppConfig (delegation)
# --------------------------------------------------------------------------- #
def test_save_token_noop_when_remember_false(redirect_appdir, monkeypatch):
    _no_keyring(monkeypatch)
    AppConfig(remember_token=False).save_token("do-not-persist")
    assert not (redirect_appdir / "token.txt").exists()


def test_save_token_plaintext_when_remember_true_no_keyring(redirect_appdir, monkeypatch):
    _no_keyring(monkeypatch)
    AppConfig(base_url="https://example.atlassian.net",
              remember_token=True).save_token("plain-token")
    tok = redirect_appdir / "token.txt"
    assert tok.exists()
    assert tok.read_text(encoding="utf-8") == "plain-token"


def test_clear_token_removes_plaintext_file(redirect_appdir, monkeypatch):
    _no_keyring(monkeypatch)
    (redirect_appdir / "token.txt").write_text("stale", encoding="utf-8")
    AppConfig().clear_token()
    assert not (redirect_appdir / "token.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
def test_plaintext_token_permissions_0600(redirect_appdir, monkeypatch):
    _no_keyring(monkeypatch)
    AppConfig(remember_token=True).save_token("perm-token")
    mode = stat.S_IMODE(os.stat(redirect_appdir / "token.txt").st_mode)
    assert mode == 0o600


# --------------------------------------------------------------------------- #
# keyring paths (credentials module directly)
# --------------------------------------------------------------------------- #
def test_keyring_available_path(redirect_appdir, monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    credentials.save_token("https://example.atlassian.net", "kr-token", remember=True)
    assert not (redirect_appdir / "token.txt").exists()
    assert credentials.load_token("https://example.atlassian.net") == "kr-token"


def test_keyring_failure_falls_back_to_plaintext(redirect_appdir, monkeypatch):
    fake = FakeKeyring(fail=True)
    _use_keyring(monkeypatch, fake)
    credentials.save_token("https://example.atlassian.net", "fallback-token", remember=True)
    tok = redirect_appdir / "token.txt"
    assert tok.exists()
    assert tok.read_text(encoding="utf-8") == "fallback-token"


def test_no_keyring_plaintext_load(redirect_appdir, monkeypatch):
    _no_keyring(monkeypatch)
    (redirect_appdir / "token.txt").write_text("disk-token", encoding="utf-8")
    assert credentials.load_token("https://example.atlassian.net") == "disk-token"


def test_token_keyed_by_base_url(redirect_appdir, monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    credentials.save_token("https://a.example.net", "token-a", remember=True)
    credentials.save_token("https://b.example.net", "token-b", remember=True)
    assert credentials.load_token("https://a.example.net") == "token-a"
    assert credentials.load_token("https://b.example.net") == "token-b"
