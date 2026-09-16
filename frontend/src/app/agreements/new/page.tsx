"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  createAgreement,
  createAgreementFromPrompt,
  getAgreementTypeQuestions,
  updateAgreementAnswers,
  validateAgreement,
  listAgreementTypes,
  AgreementType,
  ComplianceResult,
} from "@/lib/api";

import { DynamicFormBuilder } from "./DynamicFormBuilder";
import { IntentWizard } from "./IntentWizard";

interface Question {
  id: string;
  label: string;
  type: string;
  required: boolean;
  default?: unknown;
  options?: Array<{ value: string; label: string }>;
  section: string;
}



const severityColors: Record<string, string> = {
  critical: "bg-red-50 border-red-300 text-red-800",
  high: "bg-orange-50 border-orange-300 text-orange-800",
  medium: "bg-yellow-50 border-yellow-300 text-yellow-800",
  low: "bg-blue-50 border-blue-300 text-blue-800",
  info: "bg-gray-50 border-gray-300 text-gray-800",
};

export default function NewAgreementPage() {
  const { token } = useAuth();
  const router = useRouter();

  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [currentStep, setCurrentStep] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [agreementId, setAgreementId] = useState<string | null>(null);

  // Type selection state
  const [agreementTypes, setAgreementTypes] = useState<AgreementType[]>([]);
  const [selectedType, setSelectedType] = useState<AgreementType | null>(null);
  const [title, setTitle] = useState("");

  // Intent-driven (natural-language) creation state
  const [promptError, setPromptError] = useState("");
  const [prompting, setPrompting] = useState(false);

  // Compliance state
  const [compliance, setCompliance] = useState<ComplianceResult | null>(null);
  const [checkingCompliance, setCheckingCompliance] = useState(false);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (token) {
      listAgreementTypes(token)
        .then((types) => {
          setAgreementTypes(types);
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token]);

  useEffect(() => {
    if (!token || !selectedType) return;
    // Questions are per agreement type: catalog types share a generic
    // template_key, so the type's own schema is the authoritative source.
    // All updates happen in async promise callbacks — no synchronous setState
    // in the effect body.
    getAgreementTypeQuestions(selectedType.id)
      .then((qs) => {
        setQuestions(qs);
        const defaults: Record<string, unknown> = {};
        qs.forEach((q) => {
          if (q.default !== undefined) {
            defaults[q.id] = q.default;
          }
        });
        setAnswers(defaults);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, selectedType]);

  const sections = Array.from(new Set(questions.map((q) => q.section)));
  const currentSection = sections[currentStep];
  const sectionQuestions = questions.filter((q) => q.section === currentSection);

  // Debounced compliance check
  const checkCompliance = useCallback(
    (currentAnswers: Record<string, unknown>) => {
      if (!token || !agreementId) return;

      if (debounceTimer.current) {
        clearTimeout(debounceTimer.current);
      }

      debounceTimer.current = setTimeout(async () => {
        setCheckingCompliance(true);
        try {
          const result = await updateAgreementAnswers(
            token,
            agreementId,
            currentAnswers,
            true
          );
          if (result.compliance) {
            setCompliance(result.compliance);
          }
        } catch {
          // Compliance check is best-effort
        } finally {
          setCheckingCompliance(false);
        }
      }, 1500);
    },
    [token, agreementId]
  );

  const handlePromptCreate = async (intent: string) => {
    if (!token || !intent.trim()) {
      setPromptError("Describe the agreement you want to create.");
      return;
    }

    try {
      setPrompting(true);
      setPromptError("");
      const result = await createAgreementFromPrompt(token, intent.trim());
      router.push(`/agreements/${result.agreement.id}`);
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "Failed to create from prompt");
    } finally {
      setPrompting(false);
    }
  };

  const handleCreateDraft = async (): Promise<string | null> => {
    if (!token || !selectedType) return null;

    try {
      setSaving(true);
      const result = await createAgreement(token, {
        title: title || selectedType.name,
        agreement_type_id: selectedType.id,
      });
      setAgreementId(result.id);
      await updateAgreementAnswers(token, result.id, answers, false);
      return result.id;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAnswers = async () => {
    if (!token || !agreementId) return;

    try {
      setSaving(true);
      const result = await updateAgreementAnswers(
        token,
        agreementId,
        answers,
        false
      );
      if (result.compliance) {
        setCompliance(result.compliance);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleValidate = async (id?: string) => {
    const targetId = id ?? agreementId;
    if (!token || !targetId) return false;

    try {
      const result = await validateAgreement(token, targetId);
      if (!result.valid) {
        setError(`Missing fields: ${result.errors.join(", ")}`);
        return false;
      }
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
      return false;
    }
  };

  const handleNext = async () => {
    setError("");

    if (!agreementId) {
      await handleCreateDraft();
    } else {
      await handleSaveAnswers();
    }

    if (currentStep < sections.length - 1) {
      setCurrentStep(currentStep + 1);
    }
  };

  const handleBack = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleFinish = async () => {
    setError("");

    // Single-section templates never pass through "Next", so the draft may
    // not exist yet — create it here before validating, otherwise the wizard
    // dead-ends with nothing to validate or navigate to.
    let targetId = agreementId;
    if (!targetId) {
      targetId = await handleCreateDraft();
    } else {
      await handleSaveAnswers();
    }

    const isValid = await handleValidate(targetId ?? undefined);
    if (isValid && targetId) {
      router.push(`/agreements/${targetId}`);
    }
  };

  const updateAnswer = (questionId: string, value: unknown) => {
    const newAnswers = { ...answers, [questionId]: value };
    setAnswers(newAnswers);

    // Trigger debounced compliance check when agreement exists
    if (agreementId) {
      checkCompliance(newAnswers);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-12 text-gray-500">Loading template...</div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      {!selectedType ? (
        <div className="bg-white shadow rounded-lg p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">
            Select Agreement Type
          </h1>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {agreementTypes.map((type) => (
              <button
                key={type.id}
                onClick={() => {
                  setSelectedType(type);
                  setTitle(type.name);
                }}
                className="text-left p-4 border rounded-lg hover:bg-blue-50 hover:border-blue-300 transition-colors"
              >
                <div className="font-medium text-gray-900">{type.name}</div>
                {type.description && (
                  <div className="text-sm text-gray-500 mt-1">
                    {type.description}
                  </div>
                )}
                <div className="mt-2 text-xs text-blue-600 bg-blue-50 inline-block px-2 py-1 rounded">
                  {type.category}
                </div>
              </button>
            ))}
          </div>

          {/* Intent-driven creation (spec ¶69) */}
          <div className="mt-8 border-t pt-6">
            <IntentWizard
              onGenerate={handlePromptCreate}
              prompting={prompting}
              promptError={promptError}
            />
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main Wizard */}
          <div className="lg:col-span-2">
            <div className="flex justify-between items-center mb-6">
              <h1 className="text-2xl font-bold text-gray-900">
                Create {selectedType.name}
              </h1>
              <button
                onClick={() => setSelectedType(null)}
                className="text-sm text-blue-600 hover:text-blue-800"
              >
                Change Type
              </button>
            </div>
            
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Agreement Title
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                placeholder={`${selectedType.name} - [Partner Name]`}
              />
            </div>

          {/* Progress */}
          <div className="mb-8">
            <div className="flex justify-between text-sm text-gray-500 mb-2">
              <span>
                Step {currentStep + 1} of {sections.length}
              </span>
              <span>{currentSection}</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{
                  width: `${((currentStep + 1) / sections.length) * 100}%`,
                }}
              />
            </div>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
              {error}
            </div>
          )}

          {/* Questions */}
          <div className="bg-white shadow rounded-lg p-6 mb-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              {currentSection}
            </h2>

            <DynamicFormBuilder
              questions={sectionQuestions}
              answers={answers}
              onChange={updateAnswer}
            />
          </div>

          {/* Navigation */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <button
              onClick={handleBack}
              disabled={currentStep === 0}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
            >
              Back
            </button>

            {currentStep < sections.length - 1 ? (
              <button
                onClick={handleNext}
                disabled={saving}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Next"}
              </button>
            ) : (
              <button
                onClick={handleFinish}
                disabled={saving}
                className="px-4 py-2 text-sm font-medium text-white bg-green-600 border border-transparent rounded-md hover:bg-green-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Create Agreement"}
              </button>
            )}
          </div>
        </div>

        {/* Compliance Sidebar */}
        <div className="lg:col-span-1">
          <div className="sticky top-6">
            <div className="bg-white shadow rounded-lg p-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-gray-900">
                  📋 Compliance
                </h3>
                {checkingCompliance && (
                  <span className="text-xs text-gray-500 animate-pulse">
                    Checking...
                  </span>
                )}
              </div>

              {!agreementId ? (
                <p className="text-sm text-gray-500">
                  Save the agreement first to see real-time compliance warnings.
                </p>
              ) : !compliance ? (
                <p className="text-sm text-gray-500">
                  Compliance will be checked automatically as you fill in the
                  agreement.
                </p>
              ) : (
                <div>
                  {/* Score */}
                  <div className="mb-4">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-gray-700">
                        Score
                      </span>
                      <span
                        className={`text-lg font-bold ${
                          compliance.compliance_score >= 80
                            ? "text-green-600"
                            : compliance.compliance_score >= 60
                            ? "text-yellow-600"
                            : "text-red-600"
                        }`}
                      >
                        {compliance.compliance_score.toFixed(0)}%
                      </span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full transition-all ${
                          compliance.compliance_score >= 80
                            ? "bg-green-500"
                            : compliance.compliance_score >= 60
                            ? "bg-yellow-500"
                            : "bg-red-500"
                        }`}
                        style={{
                          width: `${compliance.compliance_score}%`,
                        }}
                      />
                    </div>
                  </div>

                  {/* Summary Stats */}
                  <div className="grid grid-cols-2 gap-2 mb-4">
                    {compliance.critical > 0 && (
                      <div className="bg-red-50 border border-red-200 rounded p-2 text-center">
                        <div className="text-lg font-bold text-red-600">
                          {compliance.critical}
                        </div>
                        <div className="text-xs text-red-600">Critical</div>
                      </div>
                    )}
                    {compliance.high > 0 && (
                      <div className="bg-orange-50 border border-orange-200 rounded p-2 text-center">
                        <div className="text-lg font-bold text-orange-600">
                          {compliance.high}
                        </div>
                        <div className="text-xs text-orange-600">High</div>
                      </div>
                    )}
                    {compliance.medium > 0 && (
                      <div className="bg-yellow-50 border border-yellow-200 rounded p-2 text-center">
                        <div className="text-lg font-bold text-yellow-600">
                          {compliance.medium}
                        </div>
                        <div className="text-xs text-yellow-600">Medium</div>
                      </div>
                    )}
                    {compliance.low > 0 && (
                      <div className="bg-blue-50 border border-blue-200 rounded p-2 text-center">
                        <div className="text-lg font-bold text-blue-600">
                          {compliance.low}
                        </div>
                        <div className="text-xs text-blue-600">Low</div>
                      </div>
                    )}
                    {compliance.violations_found === 0 && (
                      <div className="col-span-2 bg-green-50 border border-green-200 rounded p-3 text-center">
                        <div className="text-lg font-bold text-green-600">
                          ✅
                        </div>
                        <div className="text-sm text-green-600">
                          All policies passing
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Violations List */}
                  {compliance.violations.length > 0 && (
                    <div className="space-y-2 max-h-64 overflow-y-auto">
                      {compliance.violations.map((v) => (
                        <div
                          key={v.id}
                          className={`border rounded p-2 text-xs ${
                            severityColors[v.severity] || severityColors.info
                          }`}
                        >
                          <div className="font-medium">{v.description}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        </div>
      )}
    </div>
  );
}
