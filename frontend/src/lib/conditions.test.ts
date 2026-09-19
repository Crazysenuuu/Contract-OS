import { describe, expect, it } from "vitest";
import { evaluateCondition, filterVisibleQuestions } from "./conditions";

describe("evaluateCondition (spec §15 dynamic questionnaire)", () => {
  it("returns true when there is no condition", () => {
    expect(evaluateCondition(undefined, {})).toBe(true);
    expect(evaluateCondition(null, {})).toBe(true);
    expect(evaluateCondition({}, {})).toBe(true);
  });

  it("normalises yes/no strings to booleans for equals", () => {
    const cond = { field: "personal_data_processed", operator: "equals", value: true };
    expect(evaluateCondition(cond, { personal_data_processed: "yes" })).toBe(true);
    expect(evaluateCondition(cond, { personal_data_processed: "No" })).toBe(false);
    expect(evaluateCondition(cond, { personal_data_processed: true })).toBe(true);
  });

  it("supports the legacy depends_on shorthand", () => {
    const cond = { depends_on: { field: "dispute_resolution_method", value: "arbitration" } };
    expect(evaluateCondition(cond, { dispute_resolution_method: "arbitration" })).toBe(true);
    expect(evaluateCondition(cond, { dispute_resolution_method: "court" })).toBe(false);
  });

  it("handles list operators", () => {
    expect(evaluateCondition({ field: "k", operator: "in", value: ["a", "b"] }, { k: "b" })).toBe(true);
    expect(evaluateCondition({ field: "k", operator: "not_in", value: ["a"] }, { k: "b" })).toBe(true);
    expect(evaluateCondition({ field: "k", operator: "contains", value: "x" }, { k: ["x", "y"] })).toBe(true);
  });

  it("handles numeric comparisons", () => {
    expect(evaluateCondition({ field: "n", operator: "gt", value: 5 }, { n: "6" })).toBe(true);
    expect(evaluateCondition({ field: "n", operator: "lte", value: 5 }, { n: 5 })).toBe(true);
    expect(evaluateCondition({ field: "n", operator: "lt", value: 5 }, { n: 5 })).toBe(false);
  });

  it("handles is_set / is_empty / matches", () => {
    expect(evaluateCondition({ field: "a", operator: "is_set" }, { a: "  " })).toBe(false);
    expect(evaluateCondition({ field: "a", operator: "is_empty" }, {})).toBe(true);
    expect(evaluateCondition({ field: "a", operator: "matches", value: "sla" }, { a: "Meet the SLA" })).toBe(true);
  });

  it("is permissive for unknown operators (never hides a question by accident)", () => {
    expect(evaluateCondition({ field: "a", operator: "wat", value: 1 }, { a: 2 })).toBe(true);
  });
});

describe("filterVisibleQuestions", () => {
  it("hides questions whose branch condition is not met", () => {
    const questions = [
      { id: "method", condition: undefined },
      { id: "seat", condition: { field: "method", operator: "equals", value: "arbitration" } },
      { id: "court", condition: { field: "method", operator: "equals", value: "court" } },
    ];
    expect(filterVisibleQuestions(questions, { method: "arbitration" }).map((q) => q.id)).toEqual([
      "method",
      "seat",
    ]);
  });
});
