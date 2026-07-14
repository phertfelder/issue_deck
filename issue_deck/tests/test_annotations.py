"""Tests for local annotations: the store, tag handling, and note/context rendering.

Covers the acceptance criteria directly: notes persist locally, live in their own
file, and never mutate the issue data they annotate.
"""

from __future__ import annotations

import json

from issue_deck.annotations import (
    ANNOTATION_TAGS,
    Annotation,
    AnnotationStore,
    normalize_tags,
)
from issue_deck.exporters.context import (
    LOCAL_NOTE_HEADING,
    issue_to_llm_context,
    render_note_block,
)
from issue_deck.schema import JiraComment, JiraUser, NormalizedIssue


def _store(tmp_path):
    return AnnotationStore(path=tmp_path / "annotations.json")


# --------------------------------------------------------------------------- #
# Tag normalization
# --------------------------------------------------------------------------- #
def test_normalize_tags_keeps_known_canonical_order():
    assert normalize_tags(["blocker", "follow up", "blocker"]) == ["follow up", "blocker"]
    assert normalize_tags(["FOLLOW UP"]) == ["follow up"]          # case-insensitive
    assert normalize_tags(["bogus", 42, None]) == []               # junk dropped
    assert normalize_tags("not a list") == []


# --------------------------------------------------------------------------- #
# Store CRUD + persistence
# --------------------------------------------------------------------------- #
def test_set_and_get_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", note="check the retry logic", tags=["follow up"])
    got = s.get("A-1")
    assert got.note == "check the retry logic"
    assert got.tags == ["follow up"]
    assert got.updated_at  # stamped


def test_notes_persist_across_reload(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", note="persist me", tags=["blocker"])
    # A fresh store over the same file sees the note (persisted immediately).
    again = AnnotationStore(path=tmp_path / "annotations.json")
    assert again.get("A-1").note == "persist me"
    assert again.get("A-1").tags == ["blocker"]


def test_empty_annotation_is_deleted_not_stored(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", note="temp", tags=["blocker"])
    assert "A-1" in s
    # Clearing both note and tags removes the entry entirely.
    assert s.set("A-1", note="", tags=[]) is None
    assert "A-1" not in s
    assert s.get("A-1") is None


def test_set_preserves_unspecified_field(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", note="original", tags=["blocker"])
    s.set("A-1", tags=["follow up"])        # note not passed -> preserved
    assert s.get("A-1").note == "original"
    assert s.get("A-1").tags == ["follow up"]


def test_keys_with_tag_and_tags_in_use(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", tags=["follow up", "blocker"])
    s.set("A-2", tags=["follow up"])
    s.set("A-3", note="no tags")
    assert s.keys_with_tag("follow up") == {"A-1", "A-2"}
    assert s.keys_with_tag("blocker") == {"A-1"}
    assert s.tags_in_use() == ["follow up", "blocker"]  # canonical order


def test_corrupt_file_loads_empty(tmp_path):
    p = tmp_path / "annotations.json"
    p.write_text("{ not json", encoding="utf-8")
    s = AnnotationStore(path=p)
    assert len(s) == 0  # tolerated, not raised


def test_stored_file_shape_is_separate_and_keyed(tmp_path):
    s = _store(tmp_path)
    s.set("A-1", note="hi")
    data = json.loads((tmp_path / "annotations.json").read_text(encoding="utf-8"))
    assert [a["key"] for a in data["annotations"]] == ["A-1"]


# --------------------------------------------------------------------------- #
# Acceptance: annotations never corrupt issue data
# --------------------------------------------------------------------------- #
def test_annotating_does_not_mutate_issue(tmp_path):
    issue = NormalizedIssue(key="A-1", summary="orig", assignee=JiraUser(display_name="Al"))
    before = (issue.key, issue.summary, issue.assignee.name, dict(issue.raw_field_values))
    s = _store(tmp_path)
    s.set(issue.key, note="a private note", tags=["blocker"])
    # The issue object is untouched — annotations live entirely in the store.
    assert (issue.key, issue.summary, issue.assignee.name,
            dict(issue.raw_field_values)) == before
    assert "note" not in issue.raw_field_values


# --------------------------------------------------------------------------- #
# Note / LLM-context rendering
# --------------------------------------------------------------------------- #
def test_render_note_block_labels_private_and_empty():
    assert render_note_block(None) == ""
    assert render_note_block(Annotation(key="A")) == ""   # no content
    block = render_note_block(Annotation(key="A", note="secret", tags=["blocker"]))
    assert LOCAL_NOTE_HEADING in block
    assert "secret" in block and "blocker" in block


def test_llm_context_includes_fields_and_optional_notes():
    issue = NormalizedIssue(
        key="A-1", summary="Boom", description="it broke", status="Open",
        assignee=JiraUser(display_name="Al"),
        comments=[JiraComment(author="Ada", created="2026-07-01", body="looking")])
    ann = Annotation(key="A-1", note="ping the client", tags=["ask client"])
    # Without notes: no private section.
    plain = issue_to_llm_context(issue, ann, include_notes=False)
    assert "A-1" in plain and "it broke" in plain and "Ada" in plain
    assert LOCAL_NOTE_HEADING not in plain
    # With notes: the private, labelled section is appended.
    withnotes = issue_to_llm_context(issue, ann, include_notes=True)
    assert LOCAL_NOTE_HEADING in withnotes and "ping the client" in withnotes


def test_all_canonical_tags_present():
    assert ANNOTATION_TAGS == [
        "follow up", "blocker", "ask client", "needs grooming", "ready for closeout"]
