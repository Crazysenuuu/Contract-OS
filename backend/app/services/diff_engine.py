"""Diff engine for clause-level comparison.

Compares two versions of an agreement and produces structured diffs.
Used for redline view and negotiation UI.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ClauseDiff:
    """A single clause difference between two versions."""

    clause_identifier: str
    old_content: str
    new_content: str
    change_type: str  # 'added', 'removed', 'modified', 'unchanged'


@dataclass
class VersionDiff:
    """Complete diff between two agreement versions."""

    base_version: int
    compared_version: int
    clauses: list[ClauseDiff] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def extract_clauses(text: str) -> dict[str, str]:
    """Extract clauses from agreement text.

    Attempts to parse numbered sections (e.g., '1. Title', '2. Title').
    Falls back to paragraph-based extraction if no sections found.
    """
    clauses = {}

    # Try to match numbered sections: "1. Title" or "1) Title" or "1. Title\n"
    section_pattern = re.compile(
        r"^(?:\d{1,3})[.)]\s+(.+?)(?:\n|$)",
        re.MULTILINE,
    )

    matches = list(section_pattern.finditer(text))

    if matches:
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

            section_title = match.group(1).strip()
            section_content = text[start:end].strip()

            # Create identifier from section number
            clause_id = f"section.{i + 1}"
            clauses[clause_id] = section_content
    else:
        # Fallback: split by double newlines (paragraphs)
        paragraphs = re.split(r"\n\s*\n", text)
        for i, para in enumerate(paragraphs):
            if para.strip():
                clauses[f"paragraph.{i + 1}"] = para.strip()

    return clauses


def compute_clause_diff(
    old_content: str,
    new_content: str,
) -> str:
    """Compute the type of change between old and new content.

    Returns: 'added', 'removed', 'modified', or 'unchanged'
    """
    if not old_content and new_content:
        return "added"
    if old_content and not new_content:
        return "removed"
    if old_content == new_content:
        return "unchanged"
    return "modified"


def diff_versions(
    base_text: str,
    compared_text: str,
    base_version: int,
    compared_version: int,
) -> VersionDiff:
    """Compute clause-level diff between two agreement versions.

    Args:
        base_text: Full text of the base version.
        compared_text: Full text of the version being compared.
        base_version: Version number of the base.
        compared_version: Version number being compared.

    Returns:
        VersionDiff with clause-level changes.
    """
    base_clauses = extract_clauses(base_text)
    compared_clauses = extract_clauses(compared_text)

    diffs = []
    all_clause_ids = sorted(
        set(list(base_clauses.keys()) + list(compared_clauses.keys())),
        key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0,
    )

    for clause_id in all_clause_ids:
        old = base_clauses.get(clause_id, "")
        new = compared_clauses.get(clause_id, "")

        change_type = compute_clause_diff(old, new)

        diffs.append(
            ClauseDiff(
                clause_identifier=clause_id,
                old_content=old,
                new_content=new,
                change_type=change_type,
            )
        )

    # Compute summary
    summary = {
        "total_clauses": len(all_clause_ids),
        "added": sum(1 for d in diffs if d.change_type == "added"),
        "removed": sum(1 for d in diffs if d.change_type == "removed"),
        "modified": sum(1 for d in diffs if d.change_type == "modified"),
        "unchanged": sum(1 for d in diffs if d.change_type == "unchanged"),
    }

    return VersionDiff(
        base_version=base_version,
        compared_version=compared_version,
        clauses=diffs,
        summary=summary,
    )


def highlight_diff_html(
    old_text: str,
    new_text: str,
) -> str:
    """Generate HTML with redline highlights showing additions and deletions.

    Simple word-level diff for display purposes.
    """
    old_words = old_text.split()
    new_words = new_text.split()

    # Simple approach: show removed words in red, added in green
    # For production, use a proper diff library

    html_parts = []

    # Mark removed words
    for word in old_words:
        if word not in new_words:
            html_parts.append(
                f'<span class="text-red-600 line-through">{word}</span>'
            )
        else:
            html_parts.append(word)

    # Mark added words
    for word in new_words:
        if word not in old_words:
            html_parts.append(
                f'<span class="text-green-600 font-bold">{word}</span>'
            )

    return " ".join(html_parts)


def generate_redline_view(
    base_text: str,
    proposed_text: str,
) -> str:
    """Generate a full redline view HTML for comparison.

    Returns HTML showing all changes between two versions.
    """
    base_clauses = extract_clauses(base_text)
    proposed_clauses = extract_clauses(proposed_text)

    all_clause_ids = sorted(
        set(list(base_clauses.keys()) + list(proposed_clauses.keys())),
        key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0,
    )

    html_parts = []

    for clause_id in all_clause_ids:
        old = base_clauses.get(clause_id, "")
        new = proposed_clauses.get(clause_id, "")

        if old == new:
            # Unchanged - show normally
            html_parts.append(f'<div class="clause mb-4 p-3 bg-gray-50">{old}</div>')
        else:
            # Changed - show diff
            html_parts.append(f'<div class="clause mb-4 p-3 border-l-4 border-yellow-400 bg-yellow-50">')
            if old:
                html_parts.append(
                    f'<div class="old-content text-red-600 line-through mb-2">{old}</div>'
                )
            if new:
                html_parts.append(
                    f'<div class="new-content text-green-600 font-bold">{new}</div>'
                )
            html_parts.append("</div>")

    return "\n".join(html_parts)
