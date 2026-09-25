"""Evaluation framework (spec 3.15.9, 3.15.15).

An evaluator turns normalized observations into a verdict. The failure
doctrine is strict:

  * PASS        — the rule's condition was **verified** by external data.
  * FAIL        — the rule's condition was **contradicted** by external data.
  * INCONCLUSIVE — the external data was missing, stale, or unusable. This is
                   NEVER minted as PASS or FAIL (3.15.25). Source outages are
                   INCONCLUSIVE, not rule failures, and are surfaced through
                   connector health + workflow notification instead.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.enums import EvaluationResult
from app.monitoring.exceptions import UnknownField, UnknownOperator

ALLOWED_OPERATORS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in", "exists", "matches"}
)

_PATH_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PATH_DEPTH = 6

MISSING_FIELD_DEFAULT = "inconclusive"


@dataclass(frozen=True)
class EvaluationContext:
    """Carries obligation/agreement-side values for cross-checks.

    When a rule's definition declares ``value_source: "AGREEMENT_FIELD"``
    the service resolves the obligation's stored value and passes it here;
    evaluators compare the external observation against ``expected_value``.
    """

    expected_value: Any = None
    expected_value_source: str | None = None
    now: Any = None  # datetime, injected for testability
    obligation: dict | None = None


@dataclass(frozen=True)
class EvaluationOutcome:
    result: EvaluationResult
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @classmethod
    def pass_(cls, **metrics) -> "EvaluationOutcome":
        return cls(result=EvaluationResult.PASS, metrics=metrics)

    @classmethod
    def fail(cls, reason: str, **metrics) -> "EvaluationOutcome":
        return cls(result=EvaluationResult.FAIL, metrics=metrics, reason=reason)

    @classmethod
    def inconclusive(cls, reason: str, **metrics) -> "EvaluationOutcome":
        return cls(result=EvaluationResult.INCONCLUSIVE, metrics=metrics, reason=reason)


def _parse_path_tokens(path: str) -> list[str | int]:
    """Whitelisted tokenization for a rule-supplied field path.

    Only ``key``, ``key.subkey`` and ``key.subkey[index]`` are allowed — no
    ``__dunder`` keys, no code execution, bounded depth (3.15.19).
    """
    if not path:
        raise UnknownField("Empty field path")

    tokens: list[str | int] = []
    for part in path.split("."):
        if not part:
            raise UnknownField(f"Invalid field path '{path}'")
        builtin = part.startswith("__")
        if builtin:
            raise UnknownField(f"Reserved field prefix in '{path}'")
        if _PATH_TOKEN.match(part):
            tokens.append(part)
            continue
        bracket = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$", part)
        if bracket:
            tokens.append(bracket.group(1))
            tokens.append(int(bracket.group(2)))
            continue
        if not _PATH_TOKEN.match(part):
            raise UnknownField(f"Invalid path token '{part}' in '{path}'")
        raise UnknownField(f"Invalid field path '{path}'")

    if len([t for t in tokens if isinstance(t, str)]) > MAX_PATH_DEPTH:
        raise UnknownField(f"Field path exceeds depth limit '{path}'")
    return tokens


def validate_path_grammar(path: str) -> None:
    """Validate path syntax without a payload (eager rule validation)."""
    _parse_path_tokens(path)


def resolve_path(payload: dict | None, path: str) -> Any:
    """Restricted JSON path traversal from a rule (spec 3.15.19).

    Only ``key``, ``key.subkey`` and ``key.subkey[index]`` are allowed and
    traversed on a whitelist grammar — no ``__dunder`` keys, no code
    execution, bounded depth.
    """
    if payload is None:
        raise UnknownField(f"Field '{path}' missing from observation")

    tokens = _parse_path_tokens(path)

    node: Any = payload
    for token in tokens:
        if isinstance(node, dict):
            if token not in node:
                raise UnknownField(f"Field '{path}' missing from observation")
            node = node[token]
        elif isinstance(node, list) and isinstance(token, int):
            try:
                node = node[token]
            except IndexError:
                raise UnknownField(f"Index out of range in '{path}'")
        else:
            raise UnknownField(f"Cannot traverse '{token}' in '{path}'")
    return node


class Evaluator(ABC):
    kind: str

    @abstractmethod
    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        """Verdict for one rule against the fetch's observations."""

    def _check_operator(self, operator: str) -> None:
        if operator not in ALLOWED_OPERATORS:
            raise UnknownOperator(f"Operator '{operator}' is not allowed")


def _missing_field_outcome(reason_detail: str, definition: dict) -> EvaluationOutcome:
    mode = definition.get("on_field_missing", MISSING_FIELD_DEFAULT)
    if mode == "fail":
        return EvaluationOutcome.fail(reason=reason_detail)
    return EvaluationOutcome.inconclusive(reason=reason_detail, field_missing=True)