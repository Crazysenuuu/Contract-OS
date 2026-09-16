import React from "react";

interface Question {
  id: string;
  label: string;
  type: string;
  required: boolean;
  // Options arrive either as {value,label} objects or as plain strings
  // depending on the agreement type's schema.
  options?: Array<{ value: string; label: string } | string>;
}

interface DynamicFormBuilderProps {
  questions: Question[];
  answers: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
}

export const DynamicFormBuilder: React.FC<DynamicFormBuilderProps> = ({
  questions,
  answers,
  onChange,
}) => {
  return (
    <div className="space-y-6">
      {questions.map((question) => (
        <div key={question.id}>
          <label
            htmlFor={`question-${question.id}`}
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            {question.label}
            {question.required && (
              <span className="text-red-500 ml-1">*</span>
            )}
          </label>

          {question.type === "text" && (
            <input
              id={`question-${question.id}`}
              type="text"
              value={(answers[question.id] as string) || ""}
              onChange={(e) => onChange(question.id, e.target.value)}
              className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
            />
          )}

          {question.type === "textarea" && (
            <textarea
              id={`question-${question.id}`}
              value={(answers[question.id] as string) || ""}
              onChange={(e) => onChange(question.id, e.target.value)}
              rows={3}
              className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
            />
          )}

          {question.type === "number" && (
            <input
              id={`question-${question.id}`}
              type="number"
              value={(answers[question.id] as number) || ""}
              onChange={(e) => onChange(question.id, parseFloat(e.target.value))}
              className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
            />
          )}

          {question.type === "date" && (
            <input
              id={`question-${question.id}`}
              type="date"
              value={(answers[question.id] as string) || ""}
              onChange={(e) => onChange(question.id, e.target.value)}
              className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
            />
          )}

          {question.type === "select" && (
            <select
              id={`question-${question.id}`}
              value={(answers[question.id] as string) || ""}
              onChange={(e) => onChange(question.id, e.target.value)}
              className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 bg-white"
            >
              <option value="" disabled>
                Select...
              </option>
              {question.options?.map((opt) =>
                typeof opt === "object" && opt !== null ? (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ) : (
                  <option key={String(opt)} value={String(opt)}>
                    {String(opt)}
                  </option>
                )
              )}
            </select>
          )}

          {question.type === "boolean" && (
            <div className="flex items-center mt-2">
              <input
                id={`question-${question.id}`}
                type="checkbox"
                checked={(answers[question.id] as boolean) || false}
                onChange={(e) => onChange(question.id, e.target.checked)}
                className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
              />
              <span className="ml-2 text-sm text-gray-700">Yes</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
