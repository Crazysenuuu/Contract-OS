"use client";

import { useEffect, useState, useCallback, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  createAgreement,
  createAgreementFromPrompt,
  getAgreementTypeQuestions,
  updateAgreementAnswers,
  validateAgreement,
  listAgreementTypes,
  listJurisdictions,
  suggestClauses,
  listLegalEntities,
  AgreementType,
  ComplianceResult,
} from "@/lib/api";

import { DynamicFormBuilder } from "./DynamicFormBuilder";
import { IntentWizard } from "./IntentWizard";
import { filterVisibleQuestions } from "@/lib/conditions";

interface Question {
  id: string;
  label: string;
  type: string;
  required: boolean;
  default?: unknown;
  options?: Array<{ value: string; label: string }>;
  section: string;
  // Declarative branch rule (spec §4.2): only render the question when the
  // condition holds against the current answers.
  condition?: { field?: string; operator?: string; value?: unknown };
  placeholder?: string;
  helper_text?: string;
  min?: number;
  max?: number;
  step?: number;
  rows?: number;
}



const severityColors: Record<string, string> = {
  critical: "bg-red-50 border-red-300 text-red-800",
  high: "bg-orange-50 border-orange-300 text-orange-800",
  medium: "bg-yellow-50 border-yellow-300 text-yellow-800",
  low: "bg-blue-50 border-blue-300 text-blue-800",
  info: "bg-gray-50 border-gray-300 text-gray-800",
};

function NewAgreementForm() {
  const { token } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

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

  // 2.03 authoring wizard steps: jurisdiction, party assignment, clauses.
  const [jurisdictions, setJurisdictions] = useState<
    Array<{
      code: string;
      name: string;
      region: string | null;
      legal_system: string | null;
      default_dispute_resolution: string | null;
      required_clauses: string[] | null;
    }>
  >([]);
  const [jurisdictionCode, setJurisdictionCode] = useState("");
  const [governingLaw, setGoverningLaw] = useState("");
  const [venue, setVenue] = useState("");
  const [legalEntities, setLegalEntities] = useState<
    Array<{ id: string; legal_name: string }>
  >([]);
  const [partyAId, setPartyAId] = useState("");
  const [partyBId, setPartyBId] = useState("");
  const [counselA, setCounselA] = useState("");
  const [counselB, setCounselB] = useState("");
  const [clauseSuggestions, setClauseSuggestions] = useState<
    Array<{
      clause_type: string;
      name: string;
      explanation: string;
      risk_level: string;
      is_mandatory: boolean;
    }>
  >([]);
  const [clauseSelections, setClauseSelections] = useState<
    Record<string, boolean>
  >({});

  useEffect(() => {
    if (token) {
      listAgreementTypes(token)
        .then((types) => {
          setAgreementTypes(types);
          const initialTemplateKey = searchParams.get("template_key");
          if (initialTemplateKey) {
            const match = types.find(t => t.template_key === initialTemplateKey);
            if (match) {
              setSelectedType(match);
              setTitle(match.name);
            }
          }
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, searchParams]);

  useEffect(() => {
    if (!token || !selectedType) return;
    // Questions are per agreement type: catalog types share a generic
    // template_key, so the type's own schema is the authoritative source.
    // All updates happen in async promise callbacks — no synchronous setState
    // in the effect body.
    getAgreementTypeQuestions(selectedType.id, token)
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

  useEffect(() => {
    if (!token) return;
    listJurisdictions(token)
      .then(setJurisdictions)
      .catch(console.error);
    listLegalEntities(token)
      .then(setLegalEntities)
      .catch(console.error);
  }, [token]);

  useEffect(() => {
    if (!token || !selectedType || !jurisdictionCode) return;
    suggestClauses(
      token,
      jurisdictionCode,
      selectedType.template_key || "mutual_nda"
    )
      .then((suggestions) => {
        setClauseSuggestions(
          suggestions.map((s) => ({
            clause_type: s.clause_type,
            name: s.name,
            explanation: s.explanation,
            risk_level: s.risk_level,
            is_mandatory: s.is_mandatory,
          }))
        );
        const selections: Record<string, boolean> = {};
        suggestions.forEach((s) => {
          selections[s.clause_type] = s.is_mandatory;
        });
        setClauseSelections(selections);
      })
      .catch(console.error);
  }, [token, selectedType, jurisdictionCode]);

  // 2.03 stepper: step 0 = setup (title/jurisdiction/parties), middle steps
  // = schema sections, final step = clause selection before generation.
  const sections = Array.from(new Set(questions.map((q) => q.section)));
  const wizardSteps = [
    "Set up",
    ...sections,
    "Clauses",
  ];
  const setupStep = 0;
  const clausesStep = wizardSteps.length - 1;
  const currentStepName = wizardSteps[currentStep];
  const currentSection =
    currentStep > setupStep && currentStep < clausesStep
      ? sections[currentStep - 1]
      : sections[0];
  // Dynamic questionnaire (spec §4.2): conditions are evaluated against the
  // live answers so branching questions appear/disappear in real time as the
  // user answers the branch trigger.
  const sectionQuestions = filterVisibleQuestions(
    questions.filter((q) => q.section === currentSection),
    answers
  );

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

  const composeAnswers = (): Record<string, unknown> => {
    const selected = clauseSuggestions
      .filter((s) => clauseSelections[s.clause_type])
      .map((s) => s.clause_type);
    const partyA = legalEntities.find((e) => e.id === partyAId)?.legal_name || "";
    const partyB = legalEntities.find((e) => e.id === partyBId)?.legal_name || "";
    return {
      ...answers,
      jurisdiction: jurisdictionCode,
      governing_law: governingLaw || jurisdictionCode,
      legal_system: jurisdictions.find((j) => j.code === jurisdictionCode)?.legal_system || null,
      venue: venue || undefined,
      party_a_entity: partyA || undefined,
      party_b_entity: partyB || undefined,
      party_a_counsel_email: counselA || undefined,
      party_b_counsel_email: counselB || undefined,
      selected_clauses: selected.length > 0 ? selected : undefined,
    };
  };

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
      await updateAgreementAnswers(token, result.id, composeAnswers(), false);
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
        composeAnswers(),
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

    if (currentStep < wizardSteps.length - 1) {
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
                Step {currentStep + 1} of {wizardSteps.length}
              </span>
              <span>{currentStepName}</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{
                  width: `${((currentStep + 1) / wizardSteps.length) * 100}%`,
                }}
              />
            </div>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
              {error}
            </div>
          )}

          {/* Wizard steps: setup, schema sections, clauses */}
          {currentStep === setupStep ? (
            <div className="bg-white shadow rounded-lg p-6 mb-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">
                Jurisdiction & Parties
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Jurisdiction
                  </label>
                  <select
                    value={jurisdictionCode}
                    onChange={(e) => setJurisdictionCode(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  >
                    <option value="">Select jurisdiction...</option>
                    {jurisdictions.map((j) => (
                      <option key={j.code} value={j.code}>
                        {j.name} ({j.code})
                        {j.legal_system ? ` — ${j.legal_system}` : ""}
                      </option>
                    ))}
                  </select>
                  {jurisdictionCode && (
                    <p className="mt-1 text-xs text-gray-500">
                      {jurisdictions.find((j) => j.code === jurisdictionCode)
                        ?.default_dispute_resolution || "No default dispute resolution listed."}
                    </p>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Governing Law (optional)
                  </label>
                  <input
                    type="text"
                    value={governingLaw}
                    onChange={(e) => setGoverningLaw(e.target.value)}
                    placeholder="Defaults to jurisdiction code"
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Venue (optional)
                  </label>
                  <input
                    type="text"
                    value={venue}
                    onChange={(e) => setVenue(e.target.value)}
                    placeholder="e.g. Singapore"
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
              </div>

              <h3 className="text-md font-medium text-gray-900 mt-6 mb-3">
                Parties & Counsel
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Party A (you)
                  </label>
                  <select
                    value={partyAId}
                    onChange={(e) => setPartyAId(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  >
                    <option value="">Select legal entity...</option>
                    {legalEntities.map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.legal_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Party B (counterparty)
                  </label>
                  <select
                    value={partyBId}
                    onChange={(e) => setPartyBId(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  >
                    <option value="">Select legal entity...</option>
                    {legalEntities.map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.legal_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Party A counsel email (optional)
                  </label>
                  <input
                    type="email"
                    value={counselA}
                    onChange={(e) => setCounselA(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Party B counsel email (optional)
                  </label>
                  <input
                    type="email"
                    value={counselB}
                    onChange={(e) => setCounselB(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
              </div>
            </div>
          ) : currentStep === clausesStep ? (
            <div className="bg-white shadow rounded-lg p-6 mb-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-medium text-gray-900">
                  Clause Selection
                </h2>
                {jurisdictionCode && (
                  <span className="text-xs text-gray-500">
                    Based on {jurisdictions.find((j) => j.code === jurisdictionCode)?.name || jurisdictionCode}
                  </span>
                )}
              </div>
              {!jurisdictionCode ? (
                <p className="text-sm text-gray-500">
                  Select a jurisdiction in step 1 to see recommended clauses.
                </p>
              ) : clauseSuggestions.length === 0 ? (
                <p className="text-sm text-gray-500">
                  No clause recommendations returned. Continue without selecting
                  clauses.
                </p>
              ) : (
                <div className="space-y-3">
                  {clauseSuggestions.map((s) => {
                    const included = !!clauseSelections[s.clause_type];
                    return (
                      <div
                        key={s.clause_type}
                        className={`border rounded-lg p-4 flex items-start justify-between ${
                          s.is_mandatory ? "border-blue-300 bg-blue-50" : "border-gray-200"
                        }`}
                      >
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="font-medium text-gray-900">{s.name}</h3>
                            {s.is_mandatory && (
                              <span className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded">
                                Required
                              </span>
                            )}
                            <span
                              className={`text-xs px-2 py-0.5 rounded ${
                                s.risk_level === "high"
                                  ? "bg-red-100 text-red-700"
                                  : s.risk_level === "medium"
                                  ? "bg-amber-100 text-amber-700"
                                  : "bg-green-100 text-green-700"
                              }`}
                            >
                              {s.risk_level} risk
                            </span>
                          </div>
                          <p className="text-sm text-gray-600 mt-1">
                            {s.explanation}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={included}
                          disabled={s.is_mandatory}
                          onChange={() =>
                            setClauseSelections((prev) => ({
                              ...prev,
                              [s.clause_type]: !included,
                            }))
                          }
                          className="mt-1 h-5 w-5 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
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
          )}

          {/* Navigation */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <button
              onClick={handleBack}
              disabled={currentStep === 0}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
            >
              Back
            </button>

            {currentStep < wizardSteps.length - 1 ? (
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

export default function NewAgreementPage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <NewAgreementForm />
    </Suspense>
  );
}
