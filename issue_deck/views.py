"""Persistence for named saved views (query + table layout), credential-free.

A :class:`~issue_deck.models.SavedView` bundles a :class:`SearchFilters` with
the table's sort and visible columns. :class:`SavedViewStore` manages a named
collection of them in ``<app dir>/views.json`` — never a token, password, or PAT
(auth lives in :mod:`issue_deck.credentials` and never touches a view).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import constants
from .models import (
    DEFAULT_VISIBLE_COLUMNS,
    FieldFilter,
    SavedView,
    SearchFilters,
)

__all__ = ["view_to_dict", "view_from_dict", "SavedViewStore"]


def _views_path() -> Path:
    # Resolved at call time so tests can redirect constants.APP_DIR.
    return constants.APP_DIR / "views.json"


def view_to_dict(view: SavedView) -> dict:
    """Serialize a view to a plain dict (nested dataclasses included)."""
    return asdict(view)


def view_from_dict(data: dict) -> SavedView:
    """Rebuild a :class:`SavedView`, tolerating unknown/absent keys."""
    raw_filters = data.get("filters", {}) or {}
    known = set(SearchFilters.__dataclass_fields__)
    ff = [
        FieldFilter(**{k: v for k, v in item.items() if k in FieldFilter.__dataclass_fields__})
        for item in raw_filters.get("field_filters", []) or []
    ]
    filters = SearchFilters(
        **{k: v for k, v in raw_filters.items() if k in known and k != "field_filters"}
    )
    filters.field_filters = ff
    return SavedView(
        name=data.get("name", ""),
        filters=filters,
        sort_column=data.get("sort_column", "updated"),
        sort_desc=bool(data.get("sort_desc", True)),
        visible_columns=list(data.get("visible_columns") or DEFAULT_VISIBLE_COLUMNS),
    )


class SavedViewStore:
    """A named collection of saved views, persisted to ``views.json``.

    Names are unique and case-sensitive; :meth:`save` upserts by name. All
    mutations persist immediately. Pass an explicit ``path`` to isolate storage
    (tests); otherwise ``<app dir>/views.json`` is used.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._views: dict[str, SavedView] = {}
        self.load()

    # ---- path / io ----
    @property
    def path(self) -> Path:
        return self._path if self._path is not None else _views_path()

    def load(self) -> None:
        self._views = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for item in data.get("views", []):
                    view = view_from_dict(item)
                    if view.name:
                        self._views[view.name] = view
            except Exception:
                # A corrupt views file must never crash the app; start empty.
                self._views = {}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"views": [view_to_dict(v) for v in self._views.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ---- reads ----
    def names(self) -> list[str]:
        return list(self._views.keys())

    def all(self) -> list[SavedView]:
        return list(self._views.values())

    def get(self, name: str) -> SavedView | None:
        return self._views.get(name)

    def __len__(self) -> int:
        return len(self._views)

    def __contains__(self, name: object) -> bool:
        return name in self._views

    # ---- mutations ----
    def save(self, view: SavedView) -> None:
        """Add or overwrite the view stored under ``view.name``."""
        if not view.name:
            raise ValueError("A saved view needs a name.")
        self._views[view.name] = view
        self._persist()

    def delete(self, name: str) -> bool:
        if name in self._views:
            del self._views[name]
            self._persist()
            return True
        return False

    def rename(self, old: str, new: str) -> bool:
        if old not in self._views or not new:
            return False
        if new in self._views and new != old:
            raise ValueError(f"A view named {new!r} already exists.")
        view = self._views.pop(old)
        view.name = new
        self._views[new] = view
        self._persist()
        return True

    def duplicate(self, name: str, new_name: str) -> SavedView | None:
        src = self._views.get(name)
        if src is None or not new_name:
            return None
        if new_name in self._views:
            raise ValueError(f"A view named {new_name!r} already exists.")
        clone = view_from_dict(view_to_dict(src))  # deep copy via round-trip
        clone.name = new_name
        self._views[new_name] = clone
        self._persist()
        return clone
