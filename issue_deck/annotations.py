"""Local, private per-issue annotations — notes and tags, stored off to the side.

An :class:`Annotation` is the user's own working memory about an issue: a free-text
note and any of a small set of workflow tags. It is deliberately **kept separate
from the issue data**:

* Annotations live in their own file (``<app dir>/annotations.json``), keyed by
  issue key — they are never folded into a :class:`~issue_deck.schema.NormalizedIssue`,
  so importing or re-fetching issues can never overwrite or corrupt them, and
  editing a note can never mutate fetched/imported issue data.
* They are **never written back to Jira.** Nothing in this module talks to the
  API; the store only reads/writes the local JSON file.

This mirrors :class:`issue_deck.views.SavedViewStore`: a tolerant, immediately-
persisted, credential-free JSON store that tests can redirect with an explicit
``path`` (or by monkeypatching ``constants.APP_DIR``).
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import constants

__all__ = [
    "ANNOTATION_TAGS",
    "normalize_tags",
    "Annotation",
    "AnnotationStore",
]

# The fixed vocabulary of workflow tags a user can pin to an issue. Order here is
# the canonical display/serialization order.
ANNOTATION_TAGS: list[str] = [
    "follow up",
    "blocker",
    "ask client",
    "needs grooming",
    "ready for closeout",
]
_TAG_SET = {t.lower(): t for t in ANNOTATION_TAGS}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def normalize_tags(tags: object) -> list[str]:
    """Keep only known tags, de-duplicated and in canonical order.

    Tolerant of unknown/misspelled/wrongly-cased input (dropped), so a hand-edited
    or older annotations file can never inject junk tags.
    """
    if not isinstance(tags, (list, tuple, set)):
        return []
    seen = {t.strip().lower() for t in tags if isinstance(t, str)}
    return [canon for low, canon in _TAG_SET.items() if low in seen]


def _annotations_path() -> Path:
    # Resolved at call time so tests can redirect constants.APP_DIR.
    return constants.APP_DIR / "annotations.json"


@dataclass
class Annotation:
    """One issue's local note + tags. Purely local, never sent to Jira."""

    key: str
    note: str = ""
    tags: list[str] = field(default_factory=list)
    updated_at: str = ""

    @property
    def has_content(self) -> bool:
        """True when there is anything worth persisting (a note or a tag)."""
        return bool(self.note.strip() or self.tags)

    @classmethod
    def from_dict(cls, data: dict) -> "Annotation":
        return cls(
            key=str(data.get("key", "")),
            note=str(data.get("note", "") or ""),
            tags=normalize_tags(data.get("tags", [])),
            updated_at=str(data.get("updated_at", "") or ""),
        )


class AnnotationStore:
    """Named collection of :class:`Annotation`, one per issue key.

    All mutations persist immediately. An annotation that becomes empty (no note,
    no tags) is dropped rather than stored, so the file only ever holds real
    content. Pass an explicit ``path`` to isolate storage (tests); otherwise
    ``<app dir>/annotations.json`` is used.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._items: dict[str, Annotation] = {}
        self.load()

    # ---- path / io ----
    @property
    def path(self) -> Path:
        return self._path if self._path is not None else _annotations_path()

    def load(self) -> None:
        self._items = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            # A corrupt annotations file must never crash the app; start empty.
            self._items = {}
            return
        for item in data.get("annotations", []):
            try:
                ann = Annotation.from_dict(item)
            except Exception:
                continue
            if ann.key and ann.has_content:
                self._items[ann.key] = ann

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"annotations": [asdict(a) for a in self._items.values()]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- reads ----
    def get(self, key: str) -> Annotation | None:
        return self._items.get(key)

    def get_or_empty(self, key: str) -> Annotation:
        """The stored annotation, or a fresh empty one (NOT persisted)."""
        return self._items.get(key) or Annotation(key=key)

    def all(self) -> list[Annotation]:
        return list(self._items.values())

    def keys_with_tag(self, tag: str) -> set[str]:
        low = tag.strip().lower()
        return {k for k, a in self._items.items() if any(t.lower() == low for t in a.tags)}

    def tags_in_use(self) -> list[str]:
        """Canonical tags that are currently applied to at least one issue."""
        used = {t.lower() for a in self._items.values() for t in a.tags}
        return [t for t in ANNOTATION_TAGS if t.lower() in used]

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    # ---- mutations ----
    def set(
        self,
        key: str,
        *,
        note: str | None = None,
        tags: list[str] | None = None,
    ) -> Annotation | None:
        """Upsert the note and/or tags for ``key`` and persist.

        ``note``/``tags`` left as ``None`` are preserved from any existing
        annotation. If the result has no content, the entry is deleted instead of
        stored. Returns the stored annotation, or ``None`` when it was cleared.
        """
        if not key:
            raise ValueError("An annotation needs an issue key.")
        current = self._items.get(key)
        if note is not None:
            new_note = note
        else:
            new_note = current.note if current else ""
        if tags is not None:
            new_tags = normalize_tags(tags)
        else:
            new_tags = list(current.tags) if current else []
        result = Annotation(key=key, note=new_note, tags=new_tags, updated_at=_now_iso())
        if not result.has_content:
            self.delete(key)
            return None
        self._items[key] = result
        self._persist()
        return result

    def delete(self, key: str) -> bool:
        if key in self._items:
            del self._items[key]
            self._persist()
            return True
        return False
