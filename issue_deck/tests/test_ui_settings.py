"""Headless tests for the Settings dialog: persistence + view/profile management."""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from issue_deck import constants
from issue_deck.comments import CommentsMode
from issue_deck.config import AppConfig
from issue_deck.models import SavedView
from issue_deck.ui.settings_dialog import SettingsDialog
from issue_deck.views import SavedViewStore


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _dialog(qapp, tmp_path, cfg=None, views=None):
    cfg = cfg or AppConfig()
    views = views or SavedViewStore(tmp_path / "views.json")
    return SettingsDialog(None, cfg=cfg, views=views, profiles_dir=tmp_path / "csv_profiles")


def test_loads_config_into_widgets(qapp, tmp_path):
    cfg = AppConfig(request_timeout=90, max_issues=250, default_export_folder="/out",
                    comments_mode="latest", comments_latest_n=7, export_redact_keys=True)
    dlg = _dialog(qapp, tmp_path, cfg=cfg)
    assert dlg.sp_timeout.value() == 90
    assert dlg.sp_cap.value() == 250
    assert dlg.ed_export_folder.text() == "/out"
    assert dlg.cmb_comments.currentData() == CommentsMode.LATEST
    assert dlg.sp_latest.value() == 7
    assert dlg.cb_redact_keys.isChecked() is True


def test_apply_and_save_persists(qapp, tmp_path):
    cfg = AppConfig()
    dlg = _dialog(qapp, tmp_path, cfg=cfg)
    dlg.sp_cap.setValue(500)
    dlg.ed_export_folder.setText("/exports")
    dlg.cmb_comments.setCurrentIndex(dlg.cmb_comments.findData(CommentsMode.NONE))
    dlg.cb_redact_people.setChecked(True)
    dlg.cmb_authoring.setCurrentIndex(dlg.cmb_authoring.findData("raw"))
    dlg._on_accept()

    assert cfg.max_issues == 500
    assert cfg.default_export_folder == "/exports"
    assert cfg.comments_mode == "none"
    assert cfg.export_redact_people is True
    assert cfg.default_query_authoring_mode == "raw"
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["max_issues"] == 500
    assert "token" not in saved


def test_delete_saved_view(qapp, tmp_path, monkeypatch):
    store = SavedViewStore(tmp_path / "views.json")
    store.save(SavedView(name="Mine"))
    store.save(SavedView(name="Other"))
    dlg = _dialog(qapp, tmp_path, views=store)
    assert dlg.lst_views.count() == 2
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dlg.lst_views.setCurrentRow(0)
    dlg._delete_view()
    assert "Mine" not in store.names()
    assert dlg.lst_views.count() == 1


def test_rename_saved_view(qapp, tmp_path, monkeypatch):
    store = SavedViewStore(tmp_path / "views.json")
    store.save(SavedView(name="Old"))
    dlg = _dialog(qapp, tmp_path, views=store)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("New", True)))
    dlg.lst_views.setCurrentRow(0)
    dlg._rename_view()
    assert store.names() == ["New"]


def test_delete_import_profile(qapp, tmp_path, monkeypatch):
    prof_dir = tmp_path / "csv_profiles"
    prof_dir.mkdir()
    (prof_dir / "my_profile.json").write_text("{}", encoding="utf-8")
    dlg = _dialog(qapp, tmp_path)
    assert dlg.lst_profiles.count() == 1
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dlg.lst_profiles.setCurrentRow(0)
    dlg._delete_profile()
    assert not (prof_dir / "my_profile.json").exists()
    assert dlg.lst_profiles.count() == 0


def test_settings_dialog_has_no_connection_tab(qapp, tmp_path):
    # Credentials were consolidated into the single Settings-page surface; the
    # dialog no longer carries a Connection tab or a credentials editor.
    dlg = _dialog(qapp, tmp_path)
    assert not hasattr(dlg, "creds")
    from PyQt6.QtWidgets import QTabWidget
    tabs = dlg.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Connection" not in titles
