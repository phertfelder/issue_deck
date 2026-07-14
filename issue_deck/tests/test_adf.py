"""Characterization tests for ADF flattening and body_to_text.

Locks in the current (deliberately basic) behavior.
"""

from __future__ import annotations

from issue_deck.adf import adf_to_text, body_to_text


# --------------------------------------------------------------------------- #
# adf_to_text
# --------------------------------------------------------------------------- #
def test_none_returns_empty():
    assert adf_to_text(None) == ""


def test_plain_string_passthrough():
    assert adf_to_text("just text") == "just text"


def test_non_dict_non_str_stringified():
    assert adf_to_text(123) == "123"


def test_paragraph():
    node = {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}
    assert adf_to_text(node) == "hello"


def test_doc_joins_with_blank_lines():
    node = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "a"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "b"}]},
    ]}
    assert adf_to_text(node) == "a\n\nb"


def test_heading():
    node = {"type": "heading", "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Title"}]}
    assert adf_to_text(node) == "## Title"


def test_heading_level_capped_at_six():
    node = {"type": "heading", "attrs": {"level": 9},
            "content": [{"type": "text", "text": "Deep"}]}
    assert adf_to_text(node) == "###### Deep"


def test_strong_mark():
    assert adf_to_text({"type": "text", "text": "bold",
                        "marks": [{"type": "strong"}]}) == "**bold**"


def test_em_mark():
    assert adf_to_text({"type": "text", "text": "italic",
                        "marks": [{"type": "em"}]}) == "*italic*"


def test_code_mark():
    assert adf_to_text({"type": "text", "text": "x = 1",
                        "marks": [{"type": "code"}]}) == "`x = 1`"


def test_link_mark():
    node = {"type": "text", "text": "site",
            "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}]}
    assert adf_to_text(node) == "[site](https://example.com)"


def test_hard_break():
    assert adf_to_text({"type": "hardBreak"}) == "\n"


def test_bullet_list():
    node = {"type": "bulletList", "content": [
        {"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "one"}]}]},
        {"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "two"}]}]},
    ]}
    assert adf_to_text(node) == "- one\n- two"


def test_ordered_list():
    node = {"type": "orderedList", "content": [
        {"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "first"}]}]},
        {"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "second"}]}]},
    ]}
    assert adf_to_text(node) == "1. first\n2. second"


def test_list_item_strips():
    node = {"type": "listItem", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "  padded  "}]}]}
    assert adf_to_text(node) == "padded"


def test_code_block():
    node = {"type": "codeBlock", "attrs": {"language": "python"},
            "content": [{"type": "text", "text": "print(1)"}]}
    assert adf_to_text(node) == "```python\nprint(1)\n```"


def test_blockquote():
    node = {"type": "blockquote", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "quoted"}]}]}
    assert adf_to_text(node) == "> quoted"


def test_rule():
    assert adf_to_text({"type": "rule"}) == "---"


def test_mention():
    assert adf_to_text({"type": "mention", "attrs": {"text": "@ada"}}) == "@ada"


def test_emoji():
    assert adf_to_text({"type": "emoji", "attrs": {"text": ":smile:"}}) == ":smile:"


def test_inline_card():
    node = {"type": "inlineCard", "attrs": {"url": "https://example.com/card"}}
    assert adf_to_text(node) == "https://example.com/card"


def test_table_flattening_current_behavior():
    node = {"type": "table", "content": [
        {"type": "tableRow", "content": [
            {"type": "tableHeader", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "H1"}]}]},
            {"type": "tableCell", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "C1"}]}]},
        ]},
    ]}
    assert adf_to_text(node) == "H1 C1"


def test_unknown_node_recurses_into_children():
    node = {"type": "someFutureNode", "content": [
        {"type": "text", "text": "kept"}, {"type": "text", "text": "-here"}]}
    assert adf_to_text(node) == "kept-here"


# --------------------------------------------------------------------------- #
# body_to_text
# --------------------------------------------------------------------------- #
def test_body_none_returns_empty():
    assert body_to_text(None) == ""


def test_body_string_passthrough():
    assert body_to_text("wiki text") == "wiki text"


def test_body_adf_dict_flattened():
    node = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}
    assert body_to_text(node) == "hi"


def test_body_number_stringified():
    assert body_to_text(7) == "7"


def test_body_list_stringified():
    assert body_to_text(["a", "b"]) == "['a', 'b']"
