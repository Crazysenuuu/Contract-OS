"""Strict clause variable rendering (spec 1.8.11).

Resolves {{party_a.legal_name}}-style variables against server-built,
database-backed agreement data. Deliberately strict: a missing variable
fails the render instead of inventing a placeholder value like
[COMPANY NAME] - an incomplete legal document must not be produced.
"""

import re
from typing import Any

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


class MissingClauseVariableError(ValueError):
    """Raised when a clause references data the platform does not have."""

    def __init__(self, missing: list[str]):
        self.missing = sorted(set(missing))
        super().__init__(
            "Missing required clause variables: " + ", ".join(self.missing)
        )


class ClauseRenderer:
    def render(self, content: str, data: dict[str, Any]) -> str:
        missing: list[str] = []

        def replace(match: re.Match) -> str:
            path = match.group(1)
            value = self._resolve(data, path)
            if value is None:
                missing.append(path)
                return match.group(0)
            return str(value)

        rendered = VARIABLE_PATTERN.sub(replace, content)

        if missing:
            raise MissingClauseVariableError(missing)

        return rendered

    def _resolve(self, data: dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current
