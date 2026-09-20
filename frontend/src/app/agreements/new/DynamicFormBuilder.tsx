import React from "react";
import { filterVisibleQuestions, type Condition } from "@/lib/conditions";

interface Option {
  value: string;
  label: string;
}

export interface Question {
  id: string;
  label: string;
  type: string;
  required: boolean;
  // Options arrive either as {value,label} objects or as plain strings
  // depending on the agreement type's schema.
  options?: Array<Option | string>;
  // Declarative branch rule (spec §4.2): this question is only shown when
  // the condition holds against the collected answers.
  condition?: Condition;
  placeholder?: string;
  helper_text?: string;
  hint?: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  prefix?: string;
  suffix?: string;
  rows?: number;
  multiple?: boolean;
}

interface DynamicFormBuilderProps {
  questions: Question[];
  answers: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
}

const inputClasses =
  "block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500";

const selectClasses =
  "block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 bg-white";

// Types the wizard understands as input[type=...] attributes.
// "text" (and any unrecognised type) falls back to a plain text input via
// the default branch below — previously a plain text question rendered its
// label with NO input, making required fields impossible to fill.
const nativeInputTypes: Record<string, string> = {
  text: "text",
  date: "date",
  email: "email",
  phone: "tel",
  money: "number",
  percentage: "number",
  duration: "number",
  datetime: "datetime-local",
  time: "time",
  country: "text",
  currency: "text",
  clause_selection: "text",
};

const booleanLike: Record<string, string[]> = {
  boolean: ["Yes", "No"],
  radio: [""],
};

function optionEntries(options: Array<Option | string> | undefined): Option[] {
  if (!options) return [];
  return options.map((opt) =>
    typeof opt === "object" && opt !== null
      ? { value: String(opt.value), label: String(opt.label) }
      : { value: String(opt), label: String(opt) }
  );
}

function strVal(value: unknown): string {
  if (Array.isArray(value)) return "";
  return value == null ? "" : String(value);
}

export const DynamicFormBuilder: React.FC<DynamicFormBuilderProps> = ({
  questions,
  answers,
  onChange,
}) => {
  const visible = filterVisibleQuestions(questions, answers);

  if (visible.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No questions for this section.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {visible.map((question) => {
        const fieldId = `question-${question.id}`;
        const value = answers[question.id];
        const hasOptions = Boolean(question.options?.length);

        return (
          <div key={question.id}>
            <label
              htmlFor={fieldId}
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              {question.label}
              {question.required && (
                <span className="text-red-500 ml-1">*</span>
              )}
            </label>

            {question.helper_text && (
              <p className="text-xs text-gray-500 mb-2">
                {question.helper_text}
              </p>
            )}

            {question.type === "textarea" && (
              <>
                <textarea
                  id={fieldId}
                  value={strVal(value)}
                  onChange={(e) => onChange(question.id, e.target.value)}
                  rows={question.rows ?? 3}
                  placeholder={question.placeholder}
                  className={inputClasses}
                />
                {question.max && (
                  <p className="text-xs text-gray-400 mt-1 text-right">
                    {strVal(value).length}/{question.max} characters
                  </p>
                )}
              </>
            )}

            {question.type === "checkbox" && (
              <input
                id={fieldId}
                type="checkbox"
                checked={Boolean(value)}
                onChange={(e) => onChange(question.id, e.target.checked)}
                className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
              />
            )}

            {(question.type === "boolean" ||
              question.type === "radio") &&
              (() => {
                const options =
                  question.type === "radio" && hasOptions
                    ? optionEntries(question.options)
                    : (booleanLike.boolean as string[]).map((v) => ({
                        value: v,
                        label: v,
                      }));
                return (
                  <div className="space-y-2">
                    {options.map((opt) => {
                      const selected = strVal(value) === opt.value;
                      return (
                        <label
                          key={opt.value}
                          className="flex items-center"
                          htmlFor={`${fieldId}-${opt.value}`}
                        >
                          <input
                            id={`${fieldId}-${opt.value}`}
                            type="radio"
                            name={fieldId}
                            checked={selected}
                            onChange={() => onChange(question.id, opt.value)}
                            className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300"
                          />
                          <span className="ml-2 text-sm text-gray-700">
                            {opt.label}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                );
              })()}

            {question.type === "select" && (
              <select
                id={fieldId}
                value={strVal(value)}
                onChange={(e) => onChange(question.id, e.target.value)}
                className={selectClasses}
              >
                <option value="" disabled>
                  Select...
                </option>
                {optionEntries(question.options).map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            )}

            {question.type === "multi_select" && (
              <div className="space-y-2">
                {optionEntries(question.options).map((opt) => {
                  const selected = toList(value).includes(opt.value);
                  return (
                    <label
                      key={opt.value}
                      className="flex items-center"
                      htmlFor={`${fieldId}-${opt.value}`}
                    >
                      <input
                        id={`${fieldId}-${opt.value}`}
                        type="checkbox"
                        checked={selected}
                        onChange={(e) => {
                          const next = new Set<string>(toList(value));
                          if (e.target.checked) next.add(opt.value);
                          else next.delete(opt.value);
                          onChange(question.id, Array.from(next));
                        }}
                        className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                      />
                      <span className="ml-2 text-sm text-gray-700">
                        {opt.label}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            {question.type === "number" && (
              <input
                id={fieldId}
                type="number"
                min={question.min}
                max={question.max}
                step={question.step}
                value={value == null ? "" : String(value)}
                onChange={(e) => {
                  const num =
                    e.target.value === "" ? null : parseFloat(e.target.value);
                  onChange(question.id, num);
                }}
                placeholder={question.placeholder}
                className={inputClasses}
              />
            )}

            {(nativeInputTypes[question.type] &&
              !["number", "email", "phone"].includes(question.type) &&
              question.type !== "money" &&
              question.type !== "percentage" &&
              question.type !== "duration") && (
              <input
                id={fieldId}
                type={nativeInputTypes[question.type]}
                value={strVal(value)}
                onChange={(e) => onChange(question.id, e.target.value)}
                placeholder={question.placeholder}
                className={inputClasses}
              />
            )}

            {/* money / percentage / duration: numeric input with unit adornment */}
            {(question.type === "money" ||
              question.type === "percentage" ||
              question.type === "duration" ||
              question.type === "email" ||
              question.type === "phone") && (
              <input
                id={fieldId}
                type={
                  question.type === "email"
                    ? "email"
                    : question.type === "phone"
                    ? "tel"
                    : "number"
                }
                step={question.type === "percentage" ? 0.01 : 1}
                value={value == null ? "" : String(value)}
                onChange={(e) => {
                  if (
                    question.type === "money" ||
                    question.type === "percentage" ||
                    question.type === "duration"
                  ) {
                    const num =
                      e.target.value === ""
                        ? null
                        : parseFloat(e.target.value);
                    onChange(question.id, num);
                  } else {
                    onChange(question.id, e.target.value);
                  }
                }}
                placeholder={question.placeholder}
                className={inputClasses}
              />
            )}

            {question.hint && (
              <p className="text-xs text-gray-400 mt-1">{question.hint}</p>
            )}
          </div>
        );
      })}
    </div>
  );
};

function toList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v));
  if (value === null || value === undefined) return [];
  return [String(value)];
}