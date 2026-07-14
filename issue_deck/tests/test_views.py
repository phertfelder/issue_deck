"""Tests for saved views: round-trip, management, and credential-free storage."""

from __future__ import annotations

import json

from issue_deck.models import FieldFilter, SavedView, SearchFilters
from issue_deck.views import SavedViewStore, view_from_dict, view_to_dict


def _view(name="My work", text="crash", **fkw):
    filters = SearchFilters(
        assigned_to_me=True, unresolved=True, updated_days=90,
        projects=["ABC"], text=text,
        field_filters=[FieldFilter(field="labels", op="=", value="settlement")],
        **fkw,
    )
    return SavedView(name=name, filters=filters, sort_column="priority",
                     sort_desc=False, visible_columns=["key", "summary", "priority"])


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def test_view_round_trip_preserves_filters_and_layout():
    view = _view()
    restored = view_from_dict(view_to_dict(view))
    assert restored.name == "My work"
    assert restored.filters.projects == ["ABC"]
    assert restored.filters.unresolved is True
    assert restored.filters.field_filters[0].field == "labels"
    assert restored.filters.field_filters[0].value == "settlement"
    assert restored.sort_column == "priority"
    assert restored.sort_desc is False
    assert restored.visible_columns == ["key", "summary", "priority"]


def test_view_from_dict_tolerates_missing_keys():
    v = view_from_dict({"name": "bare"})
    assert v.name == "bare"
    assert v.filters == SearchFilters()
    assert v.visible_columns  # defaulted


# --------------------------------------------------------------------------- #
# Store CRUD
# --------------------------------------------------------------------------- #
def test_store_save_load_persists(tmp_path):
    path = tmp_path / "views.json"
    store = SavedViewStore(path)
    store.save(_view("A"))
    store.save(_view("B"))
    assert set(store.names()) == {"A", "B"}

    # A fresh store reads the same file back.
    reloaded = SavedViewStore(path)
    assert set(reloaded.names()) == {"A", "B"}
    assert reloaded.get("A").filters.text == "crash"


def test_store_save_upserts_by_name(tmp_path):
    store = SavedViewStore(tmp_path / "v.json")
    store.save(_view("A", text="one"))
    store.save(_view("A", text="two"))
    assert len(store) == 1
    assert store.get("A").filters.text == "two"


def test_store_rename(tmp_path):
    store = SavedViewStore(tmp_path / "v.json")
    store.save(_view("A"))
    assert store.rename("A", "A2")
    assert "A2" in store and "A" not in store


def test_store_duplicate_is_deep_copy(tmp_path):
    store = SavedViewStore(tmp_path / "v.json")
    store.save(_view("A"))
    clone = store.duplicate("A", "A copy")
    assert clone is not None and clone.name == "A copy"
    # Mutating the clone's filters must not touch the original.
    clone.filters.projects.append("XYZ")
    assert store.get("A").filters.projects == ["ABC"]


def test_store_delete(tmp_path):
    store = SavedViewStore(tmp_path / "v.json")
    store.save(_view("A"))
    assert store.delete("A")
    assert not store.delete("missing")
    assert len(store) == 0


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SavedViewStore(path)  # must not raise
    assert store.names() == []


def test_saved_view_file_has_no_credentials(tmp_path):
    path = tmp_path / "v.json"
    store = SavedViewStore(path)
    store.save(_view("A"))
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "token" not in text and "password" not in text
    assert "load_token" not in text
    # Only view/query fields are present.
    assert data["views"][0]["name"] == "A"
