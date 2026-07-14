"""Tests for the deterministic role → field mapping suggestions."""

from __future__ import annotations

from issue_deck.models import JiraField
from issue_deck.services.field_mapping_service import (
    ROLES_BY_KEY,
    suggest_all,
    suggest_role,
)


def _f(fid, name):
    return JiraField(id=fid, name=name, custom=fid.startswith("customfield_"))


def _suggest(role_key, fields, samples=None):
    return suggest_role(ROLES_BY_KEY[role_key], fields, samples)


# --------------------------------------------------------------------------- #
# Matching quality
# --------------------------------------------------------------------------- #
def test_exact_name_match_is_high_confidence():
    s = _suggest("client", [_f("customfield_10050", "Client")])
    assert s.field_id == "customfield_10050"
    assert s.confidence >= 90 and s.band == "high"
    assert s.reason == "Exact name match"


def test_synonym_match_is_confident():
    s = _suggest("client", [_f("customfield_10051", "Customer")])
    assert s.field_id == "customfield_10051"
    assert s.confidence >= 70                      # a real synonym, amber+ badge
    assert "Customer" in s.reason


def test_story_points_synonym_estimate_field():
    s = _suggest("story_points", [_f("customfield_10016", "Story point estimate")])
    assert s.field_id == "customfield_10016"
    assert s.confidence >= 70


def test_low_confidence_generic_term_is_flagged():
    # "Parent" is a weak/generic synonym for epic → capped + verify note.
    s = _suggest("epic", [_f("parent", "Parent")])
    assert s.field_id == "parent"
    assert s.confidence < 90
    assert "verify" in s.reason.lower()


def test_no_matching_field_returns_empty_suggestion():
    s = _suggest("sprint", [_f("customfield_9", "Colour"), _f("customfield_8", "Team")])
    assert s.has_suggestion is False
    assert s.field_id == ""
    assert "No matching" in s.reason


def test_ambiguous_when_two_strong_candidates():
    s = _suggest("client", [
        _f("customfield_1", "Client"),
        _f("customfield_2", "Customer"),   # both strong → ambiguous
    ])
    assert s.ambiguous is True
    assert "ambiguous" in s.reason.lower()


def test_role_specific_scoring_does_not_bleed():
    # A severity field must not be chosen for the client role.
    fields = [_f("customfield_10060", "Severity")]
    assert _suggest("client", fields).has_suggestion is False
    assert _suggest("severity", fields).confidence >= 90


def test_customfield_id_is_preserved_verbatim():
    s = _suggest("sprint", [_f("customfield_10020", "Sprint")])
    assert s.field_id == "customfield_10020"


def test_sample_value_is_attached_when_provided():
    s = _suggest("sprint", [_f("customfield_10020", "Sprint")],
                 samples={"customfield_10020": "Sprint 24"})
    assert s.sample == "Sprint 24"


# --------------------------------------------------------------------------- #
# suggest_all
# --------------------------------------------------------------------------- #
def test_suggest_all_covers_every_role():
    fields = [
        _f("customfield_10050", "Client"),
        _f("customfield_10060", "Severity"),
        _f("customfield_10016", "Story Points"),
        _f("customfield_10020", "Sprint"),
        _f("customfield_10014", "Epic Link"),
    ]
    by_role = {s.role: s for s in suggest_all(fields)}
    assert set(by_role) == {"client", "severity", "story_points", "sprint", "epic"}
    assert all(by_role[r].has_suggestion for r in by_role)
    assert by_role["epic"].field_id == "customfield_10014"
