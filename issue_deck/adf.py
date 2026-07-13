"""Atlassian Document Format (ADF) -> markdown-ish text flattener.

Deliberately basic: it covers the common node types seen in Cloud issue and
comment bodies and falls back to recursing into unknown nodes' children.
"""

from __future__ import annotations

from typing import Any


def adf_to_text(node: Any, depth: int = 0) -> str:
    """Flatten an ADF doc (dict) to plain markdown-ish text. Passes through str."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return str(node)

    ntype = node.get("type")
    content = node.get("content", []) or []

    def kids(sep: str = "") -> str:
        return sep.join(adf_to_text(c, depth) for c in content)

    if ntype == "doc":
        return "\n\n".join(adf_to_text(c, depth) for c in content).strip()
    if ntype == "paragraph":
        return kids()
    if ntype == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []) or []:
            mt = mark.get("type")
            if mt == "strong":
                text = f"**{text}**"
            elif mt == "em":
                text = f"*{text}*"
            elif mt == "code":
                text = f"`{text}`"
            elif mt == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text
    if ntype == "hardBreak":
        return "\n"
    if ntype == "heading":
        lvl = node.get("attrs", {}).get("level", 1)
        return f"{'#' * min(lvl, 6)} {kids()}"
    if ntype == "bulletList":
        return "\n".join(f"- {adf_to_text(c, depth + 1)}" for c in content)
    if ntype == "orderedList":
        return "\n".join(f"{i + 1}. {adf_to_text(c, depth + 1)}" for i, c in enumerate(content))
    if ntype == "listItem":
        return kids().strip()
    if ntype == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        return f"```{lang}\n{kids()}\n```"
    if ntype == "blockquote":
        inner = kids("\n")
        return "\n".join(f"> {line}" for line in inner.splitlines())
    if ntype == "rule":
        return "---"
    if ntype == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if ntype == "emoji":
        return node.get("attrs", {}).get("text", "")
    if ntype == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    if ntype in ("table", "tableRow", "tableCell", "tableHeader"):
        return kids(" ")
    # default: recurse into children
    return kids()


def body_to_text(value: Any) -> str:
    """Normalize a Jira body field (ADF dict, wiki str, or None) to text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return adf_to_text(value)
    return str(value)
