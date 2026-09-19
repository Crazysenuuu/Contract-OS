// Declarative conditional-field evaluation (spec ¶4.2 "Dynamic questionnaire").
//
// Mirrors the backend shape: conditions are data, never code:
//
//   { field: "personal_data_processed", operator: "equals", value: true }
//
// Supported operators: equals, not_equals, in, not_in, contains, gt, gte,
// lt, lte, is_set, is_empty, matches.

export type Condition = {
  field?: string;
  operator?: string;
  value?: unknown;
  depends_on?: { field?: string; value?: unknown };
};

const TRUE_STRINGS = new Set(["yes", "true", "1", "on", "y"]);

function normalise(value: unknown): unknown {
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    if (TRUE_STRINGS.has(lowered)) return true;
    if (lowered === "" || lowered === "no" || lowered === "false" || lowered === "off" || lowered === "n")
      return false;
    return value;
  }
  return value;
}

function toList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined) return [];
  return [value];
}

function isSet(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value) || typeof value === "object")
    return Object.keys(value as object).length > 0;
  return true;
}

export function evaluateCondition(
  condition: Condition | undefined | null,
  answers: Record<string, unknown>
): boolean {
  if (!condition) return true;

  let cond = condition;
  // Legacy depends_on shorthand.
  if (cond.depends_on && !cond.field) {
    cond = cond.depends_on;
  }
  const field = cond.field;
  if (!field) return true;

  const actual = answers[field];
  const op = cond.operator ?? "equals";
  const expected = cond.value;

  switch (op) {
    case "equals":
    case "eq":
      return normalise(actual) === normalise(expected);
    case "not_equals":
    case "neq":
      return normalise(actual) !== normalise(expected);
    case "in":
    case "one_of":
      return toList(expected).some((v) => normalise(v) === normalise(actual));
    case "not_in":
      return !toList(expected).some((v) => normalise(v) === normalise(actual));
    case "contains":
    case "has":
      return toList(actual).some((v) => normalise(v) === normalise(expected));
    case "gt":
      return Number(actual) > Number(expected);
    case "gte":
      return Number(actual) >= Number(expected);
    case "lt":
      return Number(actual) < Number(expected);
    case "lte":
      return Number(actual) <= Number(expected);
    case "is_set":
      return isSet(actual);
    case "is_empty":
      return !isSet(actual);
    case "matches":
      return typeof actual === "string" && typeof expected === "string"
        ? actual.toLowerCase().includes(expected.toLowerCase())
        : false;
    default:
      return true;
  }
}

export function filterVisibleQuestions<T extends { condition?: Condition }>(
  questions: T[],
  answers: Record<string, unknown>
): T[] {
  return questions.filter((q) => evaluateCondition(q.condition, answers));
}