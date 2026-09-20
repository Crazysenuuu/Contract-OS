import React, { useState } from "react";

interface IntentWizardProps {
  onGenerate: (prompt: string) => void;
  prompting: boolean;
  promptError: string;
}

export const IntentWizard: React.FC<IntentWizardProps> = ({
  onGenerate,
  prompting,
  promptError,
}) => {
  const [intent, setIntent] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (intent.trim()) {
      onGenerate(intent.trim());
    }
  };

  const pendingTypes = [
    { label: "Partnership", prompt: "I need a Partnership Agreement between two companies sharing profits and control." },
    { label: "Independent Contractor", prompt: "I need an Independent Contractor Agreement to hire a freelancer." },
    { label: "Software License", prompt: "I need a Software License Agreement for our enterprise customers." },
    { label: "Franchise", prompt: "I need a Franchise Agreement to open a new location." },
  ];

  return (
    <div className="bg-white shadow rounded-lg p-8 border-t-4 border-blue-600">
      <div className="max-w-2xl mx-auto text-center">
        <h2 className="text-3xl font-extrabold text-gray-900 mb-4">
          What are you trying to accomplish?
        </h2>
        <p className="text-gray-500 mb-8 text-lg">
          Describe the agreement you need in plain English. We will determine the
          correct legal templates, clauses, and requirements automatically.
        </p>

        <form onSubmit={handleSubmit} className="relative">
          <div className="overflow-hidden rounded-lg border border-gray-300 shadow-sm focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500 transition-shadow bg-white">
            <textarea
              rows={4}
              name="intent"
              id="intent"
              className="block w-full resize-none border-0 py-4 px-4 text-gray-900 placeholder:text-gray-400 focus:ring-0 sm:text-lg sm:leading-relaxed"
              placeholder="e.g. I want to hire ABC Technologies to develop our mobile application for Rs. 2.5 million..."
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit(e);
                }
              }}
            />

            <div className="flex items-center justify-between p-3 bg-gray-50 border-t border-gray-200">
              <span className="text-xs text-gray-500">
                Press Enter to generate
              </span>
              <button
                type="submit"
                disabled={prompting || !intent.trim()}
                className="inline-flex items-center rounded-md bg-blue-600 px-6 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {prompting ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Generating...
                  </>
                ) : (
                  "Generate Agreement"
                )}
              </button>
            </div>
          </div>
        </form>

        <div className="mt-6 flex flex-wrap justify-center gap-2">
          {pendingTypes.map((type) => (
            <button
              key={type.label}
              type="button"
              onClick={() => setIntent(type.prompt)}
              className="inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
            >
              + {type.label}
            </button>
          ))}
        </div>

        {promptError && (
          <div className="mt-4 p-4 bg-red-50 rounded-md border border-red-200 text-left">
            <div className="flex">
              <div className="flex-shrink-0">
                <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
                </svg>
              </div>
              <div className="ml-3">
                <h3 className="text-sm font-medium text-red-800">Generation failed</h3>
                <div className="mt-2 text-sm text-red-700">
                  <p>{promptError}</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
