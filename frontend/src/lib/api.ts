const API_BASE = "/api/v1";

// ---------------------------------------------------------------------------
// Session storage (spec §7/§34: 15-minute access tokens + rotating refresh
// tokens). The web app previously stored only the access token, so every
// session hard-died after 15 minutes mid-use. This mirrors the mobile
// auth_interceptor contract: on a 401, refresh once (single-flight —
// concurrent 401s share one refresh call), replay the original request with
// the new access token, and only sign the user out when the refresh itself
// fails (refresh token expired/revoked).
// ---------------------------------------------------------------------------

export interface SessionTokens {
  accessToken: string;
  refreshToken: string;
}

/** Storage abstraction so the module works in node-test environments. */
export interface SessionStore {
  getTokens(): SessionTokens | null;
  setTokens(tokens: SessionTokens): void;
  clear(): void;
}

const LOCAL_STORAGE_KEY_ACCESS = "token";
const LOCAL_STORAGE_KEY_REFRESH = "refresh_token";

function browserSessionStore(): SessionStore | null {
  const ls = (globalThis as { localStorage?: Storage }).localStorage;
  if (!ls || typeof ls.getItem !== "function") return null;
  return {
    getTokens(): SessionTokens | null {
      const accessToken = ls.getItem(LOCAL_STORAGE_KEY_ACCESS);
      if (!accessToken) return null;
      return {
        accessToken,
        refreshToken: ls.getItem(LOCAL_STORAGE_KEY_REFRESH) ?? "",
      };
    },
    setTokens(tokens: SessionTokens): void {
      ls.setItem(LOCAL_STORAGE_KEY_ACCESS, tokens.accessToken);
      if (tokens.refreshToken) {
        ls.setItem(LOCAL_STORAGE_KEY_REFRESH, tokens.refreshToken);
      }
    },
    clear(): void {
      ls.removeItem(LOCAL_STORAGE_KEY_ACCESS);
      ls.removeItem(LOCAL_STORAGE_KEY_REFRESH);
    },
  };
}

let activeStore: SessionStore | null = browserSessionStore();
let onSessionExpired: (() => void) | null = null;

/** Swap the backing store (tests, embedded contexts). Pass null to detach. */
export function setSessionStore(store: SessionStore | null): void {
  activeStore = store;
}

/** Register the callback fired when the session cannot be refreshed. */
export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

export function currentSessionTokens(): SessionTokens | null {
  return activeStore?.getTokens() ?? null;
}

export function persistSessionTokens(tokens: SessionTokens): void {
  activeStore?.setTokens(tokens);
}

export function clearSessionTokens(): void {
  activeStore?.clear();
}

interface TokenPairResponse {
  access_token: string;
  refresh_token?: string | null;
  user_id: string;
}

function storeTokenPair(response: TokenPairResponse): string {
  const tokens = currentSessionTokens();
  persistSessionTokens({
    accessToken: response.access_token,
    // The backend rotates refresh tokens on every /auth/refresh; only
    // overwrite when a new one was actually issued.
    refreshToken: response.refresh_token || tokens?.refreshToken || "",
  });
  for (const listener of Array.from(tokensRotatedListeners)) {
    try {
      listener(response.access_token);
    } catch {
      // A broken listener must not break the request that rotated tokens.
    }
  }
  return response.access_token;
}

type TokensRotatedListener = (accessToken: string) => void;

const tokensRotatedListeners = new Set<TokensRotatedListener>();

/**
 * Subscribe to access-token rotations (login and background refreshes).
 * Returns an unsubscribe function. lets React state stay in sync with the
 * storage-backed session without prop drilling.
 */
export function onTokensRotated(
  listener: TokensRotatedListener
): () => void {
  tokensRotatedListeners.add(listener);
  return () => {
    tokensRotatedListeners.delete(listener);
  };
}

let refreshInFlight: Promise<string> | null = null;

/**
 * Exchange the stored refresh token for a fresh token pair. Concurrent
 * callers share one in-flight request (single-flight) so N simultaneous 401s
 * produce exactly one refresh and never burn rotation nonces.
 */
async function refreshSession(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const tokens = currentSessionTokens();
    if (!tokens?.refreshToken) {
      throw new Error("no_refresh_token");
    }
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: tokens.refreshToken }),
      });
      if (!response.ok) {
        throw new Error(`refresh failed: ${response.status}`);
      }
      const pair = (await response.json()) as TokenPairResponse;
      return storeTokenPair(pair);
    } catch (err) {
      // Refresh failed — the session is unrecoverable. Clear credentials and
      // let the app react (redirect to login, show a toast, etc.).
      clearSessionTokens();
      try {
        onSessionExpired?.();
      } catch {
        // A broken handler must not mask the original error.
      }
      throw err;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

// Legacy exported client (settings/alerting page composes URLs against the API)
export const apiClient = { baseUrl: "" };

interface ApiOptions {
  method?: string;
  body?: unknown;
  token?: string;
}

async function apiRequestOnce<T>(
  endpoint: string,
  options: ApiOptions
): Promise<{ ok: boolean; status: number; payload: T | null; error: unknown }> {
  const { method = "GET", body, token } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    return {
      ok: false,
      status: response.status,
      payload: null,
      error: new Error(error.detail || `API error: ${response.status}`),
    };
  }

  const contentType = response.headers.get("content-type");
  if (contentType?.includes("application/json")) {
    return { ok: true, status: response.status, payload: await response.json(), error: null };
  }

  return { ok: true, status: response.status, payload: response as unknown as T, error: null };
}

async function apiRequest<T>(
  endpoint: string,
  options: ApiOptions = {}
): Promise<T> {
  let result = await apiRequestOnce<T>(endpoint, options);

  // Access tokens are short-lived (15 minutes). A 401 on an authenticated
  // call means the token expired — refresh once and replay the request
  // before surfacing an error to the caller.
  if (!result.ok && result.status === 401 && options.token) {
    try {
      const freshAccessToken = await refreshSession();
      result = await apiRequestOnce<T>(endpoint, {
        ...options,
        token: freshAccessToken,
      });
    } catch {
      // Fall through: return the original 401-shaped error below. The
      // session-expired handler has already been notified.
    }
  }

  if (!result.ok) {
    throw result.error;
  }
  return result.payload as T;
}

// Auth
export async function register(data: {
  email: string;
  name: string;
  password: string;
  /** COPPA age gate: date of birth (YYYY-MM-DD). Used only for the 13+ check; never stored. */
  date_of_birth: string;
}) {
  const result = await apiRequest<{
    access_token: string;
    refresh_token?: string | null;
    user_id: string;
  }>("/auth/register", { method: "POST", body: data });
  storeTokenPair(result);
  return result;
}

export async function login(
  data: { email: string; password: string; mfa_code?: string }
) {
  // When the account has MFA enabled and no code was supplied, the server
  // responds 401 "MFA code required" and apiRequest throws — the caller
  // catches that and shows the code field.
  const result = await apiRequest<{
    access_token: string;
    refresh_token?: string | null;
    user_id: string;
  }>("/auth/login", { method: "POST", body: data });
  storeTokenPair(result);
  return result;
}

/**
 * Dedicated hardened admin login (Panels.txt). Requires MFA unconditionally;
 * returns the same token pair as the generic login on success.
 */
export async function adminLogin(data: {
  email: string;
  password: string;
  mfa_code?: string;
}) {
  const result = await apiRequest<{
    access_token: string;
    refresh_token?: string | null;
    user_id: string;
  }>("/auth/admin/login", { method: "POST", body: data });
  storeTokenPair(result);
  return result;
}

export async function verifyEmail(token: string) {
  return apiRequest<{ message: string }>("/auth/verify-email", {
    method: "POST",
    body: { token },
  });
}

export async function getMe(token: string) {
  return apiRequest<{
    id: string;
    email: string;
    name: string;
    status: string;
    is_admin: boolean;
    mfa_enabled?: boolean;
    phone?: string | null;
  }>("/auth/me", { token });
}

// ---------------------------------------------------------------------------
// Profile & account security (Panels.txt user portal)
// ---------------------------------------------------------------------------

export async function updateProfile(
  token: string,
  data: { name?: string; phone?: string | null }
) {
  return apiRequest<{
    id: string;
    email: string;
    name: string;
    phone: string | null;
  }>("/auth/me", { method: "PATCH", body: data, token });
}

export async function changePassword(
  token: string,
  data: { current_password: string; new_password: string }
) {
  return apiRequest<{ message: string }>("/auth/me/password", {
    method: "POST",
    body: data,
    token,
  });
}

export async function disableMfa(token: string, data: { password: string; code: string }) {
  return apiRequest<{ message: string }>("/auth/mfa/disable", {
    method: "POST",
    body: data,
    token,
  });
}

export interface UserSessionInfo {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  login_at: string;
  last_seen_at: string | null;
  logout_at: string | null;
  status: string;
}

export async function listMySessions(token: string) {
  return apiRequest<UserSessionInfo[]>("/auth/me/sessions", { token });
}

export async function revokeMySession(token: string, sessionId: string) {
  return apiRequest<{ message: string }>(
    `/auth/me/sessions/${sessionId}/revoke`,
    { method: "POST", token }
  );
}

// Registered devices (push notifications, spec 2.06)
export interface UserDeviceInfo {
  id: string;
  device_id: string;
  platform: string;
  push_token: string | null;
  app_version: string | null;
  last_seen_at: string;
  revoked_at: string | null;
}

export async function listMyDevices(token: string) {
  return apiRequest<UserDeviceInfo[]>("/mobile/devices", { token });
}

export async function removeMyDevice(token: string, deviceId: string) {
  return apiRequest<void>(`/mobile/devices/${deviceId}`, {
    method: "DELETE",
    token,
  });
}

export async function logout(token: string) {
  return apiRequest<void>("/auth/logout", { method: "POST", token });
}

// SSO / OIDC (spec 1.21.8)
export async function ssoStart(email: string) {
  return apiRequest<{
    authorization_url: string;
    connection_id: string;
    organization_name: string;
  }>("/sso/oidc/start", { method: "POST", body: { email } });
}

// Organizations
export async function getMyOrganization(token: string) {
  return apiRequest<{
    id: string;
    name: string;
    slug: string;
    country: string;
  }>("/organizations/me", { token });
}

// Legal Entities
export async function listLegalEntities(token: string) {
  return apiRequest<Array<{ id: string; legal_name: string; country: string }>>(
    "/legal-entities",
    { token }
  );
}

export async function createLegalEntity(
  token: string,
  data: {
    legal_name: string;
    country: string;
    registration_number?: string;
  }
) {
  return apiRequest<{ id: string; legal_name: string }>(
    "/legal-entities",
    { method: "POST", body: data, token }
  );
}

// Agreements
export interface AgreementType {
  id: string;
  key: string;
  name: string;
  description: string | null;
  category: string;
  template_key: string | null;
  status: string;
  version: number;
}

export async function listAgreementTypes(token: string) {
  return apiRequest<AgreementType[]>("/agreements/types", { token });
}

// Questionnaire for a specific agreement type. Catalog types share a
// generic template_key, so questions must be resolved per type id — the
// template-key endpoint cannot disambiguate between types using the same
// template.
export async function getAgreementTypeQuestions(typeId: string, token?: string) {
  return apiRequest<
    Array<{
      id: string;
      label: string;
      type: string;
      required: boolean;
      default?: unknown;
      options?: Array<{ value: string; label: string }>;
      section: string;
      // Dynamic questionnaire (spec §4.2): declarative branch condition.
      condition?: { field?: string; operator?: string; value?: unknown };
      placeholder?: string;
      helper_text?: string;
      min?: number;
      max?: number;
      step?: number;
      rows?: number;
    }>
  >(`/agreements/types/${typeId}/questions`, { token });
}

export interface AgreementSummary {
  id: string;
  agreement_number: string | null;
  title: string;
  status: string;
  governing_law: string | null;
  effective_date: string | null;
  execution_date: string | null;
  expiry_date: string | null;
  created_at: string;
  updated_at: string;
}

export async function listAgreements(token: string) {
  return apiRequest<AgreementSummary[]>("/agreements", { token });
}

export async function createAgreement(
  token: string,
  data: {
    title: string;
    agreement_type_id: string;
    governing_law?: string;
  }
) {
  return apiRequest<{ id: string; title: string; status: string }>(
    "/agreements",
    { method: "POST", body: data, token }
  );
}

export async function getAgreement(token: string, id: string) {
  return apiRequest<{
    id: string;
    title: string;
    status: string;
    data: Record<string, unknown>;
  }>(`/agreements/${id}`, { token });
}

export interface ComplianceResult {
  compliance_score: number;
  violations_found: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  summary: string;
  violations: Array<{
    id: string;
    policy_id: string;
    violation_type: string;
    description: string;
    severity: string;
    found_text: string | null;
    expected_text: string | null;
    confidence: number;
  }>;
}

export async function updateAgreementAnswers(
  token: string,
  id: string,
  answers: Record<string, unknown>,
  checkCompliance = true
) {
  return apiRequest<{
    status: string;
    data: Record<string, unknown>;
    compliance: ComplianceResult | null;
  }>(`/agreements/${id}/answers`, {
    method: "POST",
    body: { answers, check_compliance: checkCompliance },
    token,
  });
}

export async function validateAgreement(token: string, id: string) {
  return apiRequest<{ valid: boolean; errors: string[] }>(
    `/agreements/${id}/validate`,
    { method: "POST", token }
  );
}

export async function renderAgreement(
  token: string,
  id: string,
  generatePdf = true
) {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
  };

  const response = await fetch(
    `${API_BASE}/agreements/${id}/render?generate_pdf=${generatePdf}`,
    {
      method: "POST",
      headers,
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Render failed");
  }

  return response;
}

// Templates
export async function listTemplates() {
  return apiRequest<Array<{ key: string; filename: string }>>(
    "/agreements/templates/list"
  );
}

export interface TemplateVariable {
  id: string;
  template_id: string;
  key: string;
  label: string;
  var_type: string;
  required: boolean;
  default_value: string | null;
  options: Array<{ value: string; label: string }> | null;
  description: string | null;
  sort_order: number;
}

export interface TemplateDetail {
  id: string;
  name: string;
  description: string | null;
  jurisdiction: string | null;
  language: string | null;
  status: string;
  is_system: boolean;
  agreement_type_id: string | null;
  variables: TemplateVariable[];
  created_at: string;
  updated_at: string;
}

export async function listTemplatesPage(token: string) {
  return apiRequest<TemplateDetail[]>("/templates?page_size=100", { token });
}

export async function getTemplateById(token: string, templateId: string) {
  return apiRequest<TemplateDetail>(`/templates/${templateId}`, { token });
}

export async function listTemplateVersions(token: string, templateId: string) {
  return apiRequest<Array<{ id: string; template_id: string; version_number: number; content: string; created_by: string | null; locked_at: string | null; created_at: string; updated_at: string }>>(`/templates/${templateId}/versions`, { token });
}

export async function getTemplateQuestions(templateKey: string) {
  return apiRequest<
    Array<{
      id: string;
      label: string;
      type: string;
      required: boolean;
      default?: unknown;
      options?: Array<{ value: string; label: string }>;
      section: string;
    }>
  >(`/agreements/templates/${templateKey}/questions`);
}

// Workflow
export async function getWorkflowState(token: string, agreementId: string) {
  return apiRequest<{
    key: string;
    name: string;
    is_initial: boolean;
    is_terminal: boolean;
  }>(`/agreements/${agreementId}/workflow/state`, { token });
}

export async function getWorkflowActions(token: string, agreementId: string) {
  return apiRequest<
    Array<{
      action_key: string;
      name: string;
      description: string;
      requires_permission: string | null;
    }>
  >(`/agreements/${agreementId}/workflow/actions`, { token });
}

export async function transitionWorkflow(
  token: string,
  agreementId: string,
  actionKey: string
) {
  return apiRequest<{
    previous_state: string;
    current_state: string;
  }>(`/agreements/${agreementId}/workflow/transition`, {
    method: "POST",
    body: { action_key: actionKey },
    token,
  });
}

// Negotiation & Changes
export async function proposeChange(
  token: string,
  agreementId: string,
  data: {
    change_type: string;
    explanation?: string;
    modifications: Array<{
      clause_identifier: string;
      change_type: string;
      new_content: string;
      reason?: string;
    }>;
  }
) {
  return apiRequest<{
    id: string;
    status: string;
  }>(`/agreements/${agreementId}/changes`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function listChanges(
  token: string,
  agreementId: string,
  status?: string
) {
  const params = status ? `?change_status=${status}` : "";
  return apiRequest<Array<{
    id: string;
    change_type: string;
    explanation: string | null;
    status: string;
    created_at: string;
  }>>(`/agreements/${agreementId}/changes${params}`, { token });
}

export async function acceptChange(
  token: string,
  agreementId: string,
  changeId: string
) {
  return apiRequest<{ id: string; status: string }>(
    `/agreements/${agreementId}/changes/${changeId}/accept`,
    { method: "POST", token }
  );
}

export async function rejectChange(
  token: string,
  agreementId: string,
  changeId: string
) {
  return apiRequest<{ id: string; status: string }>(
    `/agreements/${agreementId}/changes/${changeId}/reject`,
    { method: "POST", token }
  );
}

export async function getDiff(
  token: string,
  agreementId: string,
  baseVersion: number,
  comparedVersion: number
) {
  return apiRequest<{
    base_version: number;
    compared_version: number;
    summary: {
      total_clauses: number;
      added: number;
      removed: number;
      modified: number;
      unchanged: number;
    };
    clauses: Array<{
      clause_identifier: string;
      old_content: string;
      new_content: string;
      change_type: string;
    }>;
  }>(
    `/agreements/${agreementId}/diff?base_version=${baseVersion}&compared_version=${comparedVersion}`,
    { token }
  );
}

export async function getRedline(
  token: string,
  agreementId: string,
  baseVersion: number,
  comparedVersion: number
) {
  return apiRequest<{
    base_version: number;
    compared_version: number;
    redline_html: string;
  }>(
    `/agreements/${agreementId}/redline?base_version=${baseVersion}&compared_version=${comparedVersion}`,
    { token }
  );
}

export interface AgreementVersionSummary {
  id: string;
  version_number: number;
  status: string;
  content_hash: string;
  note: string | null;
  created_by: string;
  locked_at: string | null;
  created_at: string;
}

export interface AgreementVersionDetail extends AgreementVersionSummary {
  agreement_id: string;
  content: string;
  data: Record<string, unknown> | null;
  updated_at: string;
}

export async function listVersions(
  token: string,
  agreementId: string
) {
  return apiRequest<AgreementVersionSummary[]>(
    `/agreements/${agreementId}/versions`,
    { token }
  );
}

export async function getAgreementVersion(
  token: string,
  agreementId: string,
  versionNumber: number
) {
  return apiRequest<AgreementVersionDetail>(
    `/agreements/${agreementId}/versions/${versionNumber}`,
    { token }
  );
}

export async function createAgreementVersion(
  token: string,
  agreementId: string,
  data: { note?: string; content?: string; data?: Record<string, unknown> } = {}
) {
  return apiRequest<AgreementVersionDetail>(
    `/agreements/${agreementId}/versions`,
    { method: "POST", body: data, token }
  );
}

export async function compareAgreementVersions(
  token: string,
  agreementId: string,
  fromVersion: number,
  toVersion: number
) {
  return apiRequest<{
    from_version: number;
    to_version: number;
    from_content_hash: string;
    to_content_hash: string;
    content_diff: string;
    data_diff: {
      added: Array<{ key: string; new: unknown }>;
      removed: Array<{ key: string; old: unknown }>;
      changed: Array<{ key: string; old: unknown; new: unknown }>;
    };
  }>(
    `/agreements/${agreementId}/versions/compare?from_version=${fromVersion}&to_version=${toVersion}`,
    { token }
  );
}

export async function restoreAgreementVersion(
  token: string,
  agreementId: string,
  versionNumber: number
) {
  return apiRequest<AgreementVersionDetail>(
    `/agreements/${agreementId}/versions/${versionNumber}/restore`,
    { method: "POST", token }
  );
}

// External Party
export async function addExternalParty(
  token: string,
  agreementId: string,
  data: {
    agreement_party_id: string;
    company_name: string;
    signatory_name: string;
    signatory_email: string;
    signatory_title?: string;
    can_comment?: boolean;
    can_propose_changes?: boolean;
    can_accept?: boolean;
    can_sign?: boolean;
  }
) {
  return apiRequest<{
    id: string;
    access_token: string;
    status: string;
  }>(`/agreements/${agreementId}/external-parties`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function listExternalParties(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    company_name: string;
    signatory_name: string;
    signatory_email: string;
    status: string;
    access_token: string;
  }>>(`/agreements/${agreementId}/external-parties`, { token });
}

// External Review (no auth required - token-based)
export async function reviewAgreement(token: string) {
  return apiRequest<{
    agreement_id: string;
    title: string;
    company_name: string;
    signatory_name: string;
    version_number: number;
    content: string;
    status: string;
  }>(`/review/${token}`);
}

export async function addExternalComment(
  token: string,
  data: { content: string; clause_identifier?: string }
) {
  return apiRequest<{
    id: string;
    content: string;
    status: string;
  }>(`/review/${token}/comment`, {
    method: "POST",
    body: data,
  });
}

export async function acceptExternal(token: string) {
  return apiRequest<{ status: string; message: string }>(
    `/review/${token}/accept`,
    { method: "POST" }
  );
}

export async function rejectExternal(token: string) {
  return apiRequest<{ status: string; message: string }>(
    `/review/${token}/reject`,
    { method: "POST" }
  );
}

export async function signExternal(
  token: string,
  consentText: string
) {
  return apiRequest<{ signature_id: string; signed_at: string; message: string }>(
    `/review/${token}/sign`,
    { method: "POST", body: { consent_text: consentText } }
  );
}

// Authorization
export async function checkPermission(
  token: string,
  agreementId: string,
  permissionKey: string
) {
  return apiRequest<{
    allowed: boolean;
    reason: string;
    participant_id: string | null;
    party_id: string | null;
  }>(`/agreements/${agreementId}/check-permission`, {
    method: "POST",
    body: { permission_key: permissionKey },
    token,
  });
}

export async function getMyPermissions(
  token: string,
  agreementId: string
) {
  return apiRequest<{ permissions: string[] }>(
    `/agreements/${agreementId}/my-permissions`,
    { token }
  );
}

export async function grantPermission(
  token: string,
  agreementId: string,
  data: {
    participant_id: string;
    permission_key: string;
    granted?: boolean;
  }
) {
  return apiRequest<{ status: string; permission_key: string }>(
    `/agreements/${agreementId}/grant-permission`,
    { method: "POST", body: data, token }
  );
}

export async function getParticipantPermissions(
  token: string,
  agreementId: string,
  participantId: string
) {
  return apiRequest<{ permissions: string[] }>(
    `/agreements/${agreementId}/participants/${participantId}/permissions`,
    { token }
  );
}

// Participants
export async function listParticipants(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    agreement_id: string;
    agreement_party_id: string;
    user_id: string;
    participant_role: string;
    status: string;
    can_view: boolean;
    can_comment: boolean;
    can_propose_changes: boolean;
    can_approve: boolean;
    can_sign: boolean;
  }>>(`/agreements/${agreementId}/participants`, { token });
}

export async function addParticipant(
  token: string,
  agreementId: string,
  data: {
    user_id: string;
    agreement_party_id: string;
    participant_role: string;
    can_view?: boolean;
    can_comment?: boolean;
    can_propose_changes?: boolean;
    can_approve?: boolean;
    can_sign?: boolean;
  }
) {
  return apiRequest<{ id: string; participant_role: string }>(
    `/agreements/${agreementId}/participants`,
    { method: "POST", body: data, token }
  );
}

// Parties
export async function listParties(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    agreement_id: string;
    legal_entity_id: string;
    party_role: string;
    display_name: string | null;
  }>>(`/agreements/${agreementId}/parties`, { token });
}

// AI Analysis
export interface AnalysisCoverage {
  characters_total: number;
  chunks: number;
  chunks_analyzed: number;
  chunks_failed: number;
  truncated: boolean;
  note?: string | null;
}

export async function analyzeContract(
  token: string,
  agreementId: string
) {
  return apiRequest<{
    summary: string;
    key_terms: Record<string, unknown>;
    risks: Array<{
      category: string;
      severity: string;
      finding: string;
      explanation: string | null;
      recommendation: string | null;
      confidence: number;
    }>;
    confidence: number;
    coverage?: AnalysisCoverage | null;
  }>(`/agreements/${agreementId}/analyze`, { method: "POST", token });
}

export async function detectRisks(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    category: string;
    severity: string;
    clause_identifier: string | null;
    finding: string;
    explanation: string | null;
    recommendation: string | null;
    confidence: number;
    reviewer_status: string;
  }>>(`/agreements/${agreementId}/risks`, { method: "POST", token });
}

export async function listRisks(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    category: string;
    severity: string;
    clause_identifier: string | null;
    finding: string;
    explanation: string | null;
    recommendation: string | null;
    confidence: number;
    reviewer_status: string;
  }>>(`/agreements/${agreementId}/risks`, { token });
}

export async function updateRiskStatus(
  token: string,
  agreementId: string,
  riskId: string,
  data: { reviewer_status: string; reviewer_notes?: string }
) {
  return apiRequest<{
    id: string;
    reviewer_status: string;
  }>(`/agreements/${agreementId}/risks/${riskId}`, {
    method: "PATCH",
    body: data,
    token,
  });
}

export async function compareVersions(
  token: string,
  agreementId: string,
  baseVersion: number,
  comparedVersion: number
) {
  return apiRequest<{
    summary: string;
    changes_detected: number;
    risk_changes: Array<unknown>;
    detailed_changes: Array<unknown>;
    coverage?: AnalysisCoverage | null;
  }>(
    `/agreements/${agreementId}/compare?base_version=${baseVersion}&compared_version=${comparedVersion}`,
    { method: "POST", token }
  );
}

// Obligations
export async function listObligations(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    agreement_id: string;
    owner_party: string;
    description: string;
    obligation_type: string;
    amount: string | null;
    frequency: string | null;
    due_date: string | null;
    status: string;
    clause_identifier: string | null;
    created_at: string;
  }>>(`/agreements/${agreementId}/obligations`, { token });
}

export async function createObligation(
  token: string,
  agreementId: string,
  data: {
    owner_party: string;
    description: string;
    obligation_type: string;
    amount?: string;
    frequency?: string;
    due_date?: string;
    clause_identifier?: string;
  }
) {
  return apiRequest<{
    id: string;
    status: string;
  }>(`/agreements/${agreementId}/obligations`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function updateObligationStatus(
  token: string,
  agreementId: string,
  obligationId: string,
  status: string
) {
  return apiRequest<{
    id: string;
    status: string;
  }>(`/agreements/${agreementId}/obligations/${obligationId}/status`, {
    method: "PATCH",
    body: { status },
    token,
  });
}

export async function getObligationStats(
  token: string,
  agreementId: string
) {
  return apiRequest<{
    total: number;
    upcoming: number;
    due: number;
    completed: number;
    overdue: number;
    due_within_30_days: number;
  }>(`/agreements/${agreementId}/obligations/stats`, { token });
}

// Compliance & Policies
export async function listPolicies(
  token: string
) {
  return apiRequest<Array<{
    id: string;
    name: string;
    description: string | null;
    category: string;
    clause_type: string;
    severity_if_missing: string;
    is_active: boolean;
    priority: number;
  }>>(`/policies`, { token });
}

export async function createPolicy(
  token: string,
  data: {
    name: string;
    description?: string;
    category: string;
    clause_type: string;
    standard_text?: string;
    keywords?: string[];
    rules?: Record<string, unknown>;
    severity_if_missing?: string;
    applies_to_types?: string[];
    priority?: number;
  }
) {
  return apiRequest<{ id: string; name: string }>(`/policies`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function runComplianceCheck(
  token: string,
  agreementId: string
) {
  return apiRequest<{
    report_id: string;
    compliance_score: number;
    policies_checked: number;
    violations_found: number;
    critical: number;
    high: number;
    medium: number;
    low: number;
    summary: string;
    violations: Array<{
      id: string;
      policy_id: string;
      violation_type: string;
      description: string;
      severity: string;
      found_text: string | null;
      expected_text: string | null;
      confidence: number;
    }>;
  }>(`/agreements/${agreementId}/compliance/check`, {
    method: "POST",
    token,
  });
}

export async function listComplianceReports(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    compliance_score: number;
    violations_found: number;
    critical_count: number;
    high_count: number;
    medium_count: number;
    low_count: number;
    summary: string | null;
    created_at: string;
  }>>(`/agreements/${agreementId}/compliance/reports`, { token });
}

export async function listComplianceViolations(
  token: string,
  agreementId: string
) {
  return apiRequest<Array<{
    id: string;
    violation_type: string;
    description: string;
    severity: string;
    confidence: number;
    reviewer_status: string;
  }>>(`/agreements/${agreementId}/compliance/violations`, { token });
}

export async function updateViolationStatus(
  token: string,
  agreementId: string,
  violationId: string,
  data: { reviewer_status: string; reviewer_notes?: string }
) {
  return apiRequest<{
    id: string;
    reviewer_status: string;
  }>(
    `/agreements/${agreementId}/compliance/violations/${violationId}`,
    { method: "PATCH", body: data, token }
  );
}

// Audit Trail
export async function getAuditTrail(
  token: string,
  agreementId: string
) {
  return apiRequest<AuditEvent[]>(`/agreements/${agreementId}/audit`, { token });
}

// Signing
export async function sendAgreement(
  token: string,
  agreementId: string
) {
  return apiRequest<{ status: string }>(
    `/agreements/${agreementId}/send`,
    { method: "POST", token }
  );
}

export async function signAgreement(
  token: string,
  agreementId: string
) {
  return apiRequest<{ status: string; message: string }>(
    `/agreements/${agreementId}/sign`,
    { method: "POST", token }
  );
}

// Notifications & Delivery Status
export async function listNotifications(
  token: string,
  options?: { agreement_id?: string; notification_type?: string; limit?: number }
) {
  const params = new URLSearchParams();
  if (options?.agreement_id) params.set("agreement_id", options.agreement_id);
  if (options?.notification_type) params.set("notification_type", options.notification_type);
  if (options?.limit) params.set("limit", String(options.limit));
  const qs = params.toString();
  return apiRequest<Array<{
    id: string;
    notification_type: string;
    to_email: string;
    subject: string;
    status: string;
    message_id: string | null;
    sent_at: string | null;
    read_at: string | null;
    created_at: string;
  }>>(`/notifications${qs ? `?${qs}` : ""}`, { token });
}

export async function getNotificationStats(token: string) {
  return apiRequest<{
    total: number;
    failed: number;
    by_type: Record<string, number>;
  }>("/notifications/stats", { token });
}

export async function getDeliveryStatus(
  token: string,
  notificationId: string
) {
  return apiRequest<{
    notification_id: string;
    status: string;
    message_id: string | null;
    error_message: string | null;
    sent_at: string | null;
  }>(`/notifications/delivery/${notificationId}`, { token });
}

export async function getDeliveryStats(token: string) {
  return apiRequest<{
    total_sent: number;
    total_failed: number;
    total_pending: number;
    delivery_rate: number;
    by_type: Record<string, Record<string, number>>;
  }>("/notifications/delivery-stats", { token });
}

export async function retryNotification(
  token: string,
  notificationId: string
) {
  return apiRequest<{
    notification_id: string;
    status: string;
  }>(`/notifications/retry/${notificationId}`, { method: "POST", token });
}

// Notification Preferences
export async function getNotificationPreferences(token: string) {
  return apiRequest<{
    id: string;
    email_review_invitation: boolean;
    email_agreement_viewed: boolean;
    email_change_requested: boolean;
    email_agreement_accepted: boolean;
    email_signature_completed: boolean;
    email_approval_request: boolean;
    email_workflow_transition: boolean;
    email_compliance_violation: boolean;
    email_obligation_reminder: boolean;
    sms_enabled: boolean;
    digest_enabled: boolean;
    digest_frequency: string;
  }>("/notification-preferences", { token });
}

export async function updateNotificationPreferences(
  token: string,
  data: Record<string, unknown>
) {
  return apiRequest<{ id: string }>("/notification-preferences", {
    method: "PATCH",
    body: data,
    token,
  });
}

export async function resetNotificationPreferences(token: string) {
  return apiRequest<{ id: string }>("/notification-preferences/reset", {
    method: "POST",
    token,
  });
}

// Webhooks
export async function listWebhooks(token: string) {
  return apiRequest<Array<{
    id: string;
    url: string;
    description: string | null;
    is_active: boolean;
    events: string[] | null;
    retry_count: number;
    created_at: string;
  }>>("/webhooks", { token });
}

export async function createWebhook(
  token: string,
  data: {
    url: string;
    description?: string;
    events?: string[];
    secret?: string;
  }
) {
  return apiRequest<{ id: string; url: string }>("/webhooks", {
    method: "POST",
    body: data,
    token,
  });
}

export async function deleteWebhook(token: string, webhookId: string) {
  return apiRequest<{ deleted: boolean }>(`/webhooks/${webhookId}`, {
    method: "DELETE",
    token,
  });
}

export async function testWebhook(token: string, webhookId: string) {
  return apiRequest<{ deliveries_queued: number }>(
    `/webhooks/${webhookId}/test`,
    { method: "POST", token }
  );
}

export async function getWebhookDeliveries(
  token: string,
  webhookId: string
) {
  return apiRequest<Array<{
    id: string;
    event_type: string;
    status: string;
    response_status_code: number | null;
    error_message: string | null;
    attempt: number;
    created_at: string;
  }>>(`/webhooks/${webhookId}/deliveries`, { token });
}

export async function getWebhookEvents() {
  return apiRequest<{ events: string[] }>("/webhooks/events");
}

// Phase 3: Jurisdictions
export async function listJurisdictions(token: string) {
  return apiRequest<Array<{
    id: string;
    code: string;
    name: string;
    region: string | null;
    language: string;
    legal_system: string | null;
    currency: string;
    timezone: string;
    required_clauses: string[] | null;
    prohibited_clauses: string[] | null;
    signature_requirements: Record<string, unknown> | null;
    default_dispute_resolution: string | null;
    is_active: boolean;
  }>>("/phase3/jurisdictions", { token });
}

export async function getJurisdiction(token: string, code: string) {
  return apiRequest<{
    id: string;
    code: string;
    name: string;
    region: string | null;
    language: string;
    legal_system: string | null;
    currency: string;
    timezone: string;
    required_clauses: string[] | null;
    prohibited_clauses: string[] | null;
    signature_requirements: Record<string, unknown> | null;
    default_dispute_resolution: string | null;
    is_active: boolean;
  }>(`/phase3/jurisdictions/${code}`, { token });
}

// Phase 3: Clause Suggestions
export async function suggestClauses(
  token: string,
  jurisdictionCode: string,
  agreementType = "mutual_nda"
) {
  return apiRequest<Array<{
    clause_type: string;
    name: string;
    text: string;
    explanation: string;
    risk_level: string;
    is_mandatory: boolean;
    alternatives: Array<Record<string, unknown>>;
    confidence: number;
  }>>(`/phase3/clause-suggestions/${jurisdictionCode}?agreement_type=${agreementType}`, { token });
}

export async function assessRisks(
  token: string,
  clauseText: string,
  clauseType: string,
  jurisdictionCode?: string
) {
  const params = new URLSearchParams({ clause_text: clauseText, clause_type: clauseType });
  if (jurisdictionCode) params.set("jurisdiction_code", jurisdictionCode);
  return apiRequest<Array<{
    category: string;
    severity: string;
    score: number;
    description: string;
    mitigation: string;
  }>>(`/phase3/risk-assessment?${params.toString()}`, { method: "POST", token });
}

export async function compareClauses(
  token: string,
  clauseType: string,
  textA: string,
  textB: string,
  jurisdictionCode?: string
) {
  return apiRequest<{
    clause_type: string;
    version_a: { risk_score: number; risks: Array<Record<string, unknown>> };
    version_b: { risk_score: number; risks: Array<Record<string, unknown>> };
    recommendation: string;
    difference: number;
  }>("/phase3/compare-clauses", {
    method: "POST",
    body: { clause_type: clauseType, text_a: textA, text_b: textB, jurisdiction_code: jurisdictionCode },
    token,
  });
}

// Phase 3: E-Signature
export async function createEnvelope(
  token: string,
  data: {
    agreement_id: string;
    signers: Array<{ name: string; email: string; role: string }>
    subject: string;
    message: string;
  }
) {
  return apiRequest<{
    envelope_id: string;
    status: string;
    signing_url: string | null;
    created_at: string | null;
  }>("/phase3/esignature/create-envelope", { method: "POST", body: data, token });
}

export async function getEnvelopeStatus(token: string, envelopeId: string) {
  return apiRequest<Record<string, unknown>>(`/phase3/esignature/status/${envelopeId}`, { token });
}

export async function simulateSigning(token: string, envelopeId: string, signerEmail: string) {
  return apiRequest<{
    envelope_id: string;
    status: string;
    signed_count: number;
    total_signers: number;
  }>(`/phase3/esignature/simulate-sign/${envelopeId}?signer_email=${signerEmail}`, { method: "POST", token });
}

// Phase 4: Bulk Operations
export async function bulkImport(token: string, data: { csv_content: string; import_type?: string }) {
  return apiRequest<{ job_id: string; status: string; total_items: number; successful_items: number; failed_items: number }>("/phase4/bulk/import", { method: "POST", body: data, token });
}

export async function bulkAction(token: string, data: { action: string; filter_criteria?: Record<string, unknown>; options?: Record<string, unknown> }) {
  return apiRequest<{ job_id: string; status: string; total_items: number; successful_items: number }>("/phase4/bulk/action", { method: "POST", body: data, token });
}

export async function listBulkJobs(token: string, status?: string) {
  const params = status ? `?status=${status}` : "";
  return apiRequest<Array<{ id: string; job_type: string; status: string; total_items: number; successful_items: number; failed_items: number; created_at: string | null }>>(`/phase4/bulk/jobs${params}`, { token });
}

export async function getBulkJob(token: string, jobId: string) {
  return apiRequest<{ id: string; job_type: string; status: string; total_items: number; processed_items: number; successful_items: number; failed_items: number; errors: unknown[] }>(`/phase4/bulk/jobs/${jobId}`, { token });
}

export async function bulkExport(token: string, data: { export_type?: string; format?: string; columns?: string[] }) {
  return apiRequest<{ job_id: string; status: string; row_count: number; format: string }>("/phase4/bulk/export", { method: "POST", body: data, token });
}

// Phase 4: Document Intelligence
export async function extractClauses(token: string, data: { agreement_id: string; text: string }) {
  return apiRequest<{ clauses_found: number; clauses: Array<{ id: string; title: string; category: string; risk_level: string | null; risk_score: number | null; sentiment: string | null; tags: string[]; text_preview: string }> }>("/phase4/clauses/extract", { method: "POST", body: data, token });
}

export async function listClauses(token: string, agreementId: string, category?: string) {
  const params = category ? `?agreement_id=${agreementId}&category=${category}` : `?agreement_id=${agreementId}`;
  return apiRequest<Array<{ id: string; title: string; category: string; risk_level: string | null; risk_score: number | null; sentiment: string | null; tags: string[]; text: string }>>(`/phase4/clauses${params}`, { token });
}

export async function listClauseLibrary(token: string, category?: string) {
  const params = category ? `?category=${category}` : "";
  return apiRequest<Array<{ id: string; title: string; category: string; risk_level: string | null; risk_score: number | null; usage_count: number; tags: string[]; text_preview: string }>>(`/phase4/clauses/library${params}`, { token });
}

export async function addClauseToLibrary(token: string, data: { clause_id: string; title: string; description?: string; jurisdictions?: string[]; agreement_types?: string[] }) {
  return apiRequest<{ id: string; title: string }>("/phase4/clauses/add-to-library", { method: "POST", body: data, token });
}

export async function getClauseStats(token: string) {
  return apiRequest<{ total_clauses_extracted: number; library_entries: number; risk_distribution: Record<string, number>; category_distribution: Record<string, number> }>("/phase4/clauses/stats", { token });
}

// Phase 4: Tenant & Branding
export async function getTenant(token: string) {
  return apiRequest<{ exists: boolean; id?: string; slug?: string; plan?: string; features?: Record<string, boolean>; branding?: Record<string, unknown>; limits?: Record<string, number>; usage?: Record<string, number> }>("/phase4/tenant", { token });
}

export async function createTenant(token: string, slug: string, plan?: string) {
  return apiRequest<{ id: string; slug: string; plan: string }>(`/phase4/tenant?slug=${slug}${plan ? `&plan=${plan}` : ""}`, { method: "POST", token });
}

export async function getBranding(token: string) {
  return apiRequest<Record<string, unknown>>("/phase4/tenant/branding", { token });
}

export async function updateBranding(token: string, data: Record<string, unknown> | null) {
  return apiRequest<Record<string, unknown>>("/phase4/tenant/branding", { method: "PATCH", body: data, token });
}

export async function getTenantLimits(token: string) {
  return apiRequest<{ allowed: boolean; usage?: Record<string, string>; issues?: string[] }>("/phase4/tenant/limits", { token });
}

export async function getTenantThemes(token: string) {
  return apiRequest<Array<{ id: string; name: string; description: string | null; colors: Record<string, string>; is_default: boolean; supports_dark_mode: boolean }>>("/phase4/tenant/themes", { token });
}

// Phase 4: Performance
export async function getPerformanceStats(token: string) {
  return apiRequest<{ cache: { size: number; max_size: number; hits: number; misses: number; hit_rate: number }; jobs: { total_jobs: number; by_status: Record<string, number> } }>("/phase4/performance/stats", { token });
}

export async function getAgreementsOptimized(token: string, params: { page?: number; page_size?: number; sort_by?: string; sort_order?: string; search?: string; status?: string }) {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined) searchParams.set(k, String(v)); });
  return apiRequest<{ items: Array<{ id: string; title: string; status: string; created_at: string | null }>; pagination: { total: number; page: number; page_size: number; total_pages: number; has_next: boolean; has_prev: boolean } }>(`/phase4/performance/agreements?${searchParams.toString()}`, { token });
}

// Phase 4: Background Jobs
export async function enqueueJob(token: string, data: { job_type: string; payload: Record<string, unknown>; priority?: number; delay_seconds?: number }) {
  return apiRequest<{ job_id: string; status: string }>("/phase4/jobs/enqueue", { method: "POST", body: data, token });
}

export async function getJobStatus(token: string, jobId: string) {
  return apiRequest<{ id: string; status: string; result?: unknown; error?: string }>(`/phase4/jobs/${jobId}`, { token });
}

export async function getQueueStats(token: string) {
  return apiRequest<{ total_jobs: number; by_status: Record<string, number>; oldest_pending: string | null }>("/phase4/jobs/queue/stats", { token });
}

// Metrics / Observability
export async function getMetricsSummary(token: string) {
  return apiRequest<{
    uptime: number;
    requests: { total: number; errors: number };
    translations: { queue_size: number; completed_last_hour: number };
    database: Record<string, unknown>;
  }>("/metrics/summary", { token });
}

export async function getMetricsAlerts(token: string) {
  return apiRequest<{
    alerts: Array<{ severity: string; message: string; metric: string; value: number; threshold: number }>;
    alert_count: number;
    has_critical: boolean;
  }>("/metrics/alerts", { token });
}

export async function getMetricsDashboard(token: string) {
  return apiRequest<Record<string, unknown>>("/metrics/dashboard", { token });
}

// Translation Sync
export async function createVersionWithTranslations(token: string, agreementId: string, data: { content: string; sync_existing?: boolean }) {
  return apiRequest<{ version_id: string; version_number: number; translation_sync: Record<string, unknown> }>(`/translation-sync/agreements/${agreementId}/versions/translate`, { method: "POST", body: data, token });
}

export async function getVersionTranslations(token: string, agreementId: string, versionId: string) {
  return apiRequest<{ version_id: string; primary_language: string; secondary_languages: string[]; translations: Record<string, { status: string; quality_score: number | null; last_synced: string | null; is_outdated: boolean }>; summary: { total: number; synced: number; outdated: number; pending: number } }>(`/translation-sync/agreements/${agreementId}/versions/${versionId}/translations`, { token });
}

export async function updateVersionTranslation(token: string, agreementId: string, versionId: string, data: { language_code: string; title?: string; content?: string; summary?: string }) {
  return apiRequest<{ id: string; language: string; status: string }>(`/translation-sync/agreements/${agreementId}/versions/${versionId}/translations`, { method: "POST", body: data, token });
}

export async function syncTranslations(token: string, agreementId: string, data: { from_version_id: string; to_version_id: string }) {
  return apiRequest<{ status: string; synced_languages: string[]; outdated_languages: string[]; pending_languages: string[]; changes_detected: boolean; details: Record<string, unknown> }>(`/translation-sync/agreements/${agreementId}/sync-translations`, { method: "POST", body: data, token });
}

export async function bulkSyncTranslations(token: string, agreementId: string, data: { to_version_id: string }) {
  return apiRequest<{ status: string; message: string; synced_languages?: string[]; outdated_languages?: string[] }>(`/translation-sync/agreements/${agreementId}/bulk-sync`, { method: "POST", body: data, token });
}

export async function compareVersionTranslations(token: string, agreementId: string, versionId1: string, versionId2: string) {
  return apiRequest<{ version_1: string; version_2: string; languages_compared: number; changes: Record<string, { status: string; diff_lines?: number; diff_preview?: string }>; summary: { unchanged: number; updated: number; added: number; removed: number } }>(`/translation-sync/agreements/${agreementId}/compare-translations?version_id_1=${versionId1}&version_id_2=${versionId2}`, { token });
}

export async function getTranslationDashboard(token: string, agreementId: string) {
  return apiRequest<{ agreement_id: string; primary_language: string; secondary_languages: string[]; total_versions: number; total_translations: number; versions: Array<{ version_id: string; version_number: number; status: string; created_at: string | null; translation_sync: Record<string, unknown> | null; translated_languages: Array<{ code: string; status: string; quality_score: number | null }> }> }>(`/translation-sync/agreements/${agreementId}/translation-dashboard`, { token });
}

// Translation Queue
export async function getTranslationQueueStats(token: string) {
  return apiRequest<{ total: number; by_status: Record<string, number>; by_priority: Record<string, number>; by_language: Record<string, number>; avg_processing_time_seconds: number | null; estimated_wait_seconds: number }>("/translation-queue/stats", { token });
}

export async function listTranslationQueue(token: string, params?: { status?: string; target_language?: string; source_type?: string; limit?: number; offset?: number }) {
  const searchParams = new URLSearchParams();
  if (params) Object.entries(params).forEach(([k, v]) => { if (v !== undefined) searchParams.set(k, String(v)); });
  const qs = searchParams.toString();
  return apiRequest<Array<{ id: string; source_type: string; source_id: string; target_language: string; source_title: string | null; status: string; priority: string; source: string; attempts: number; error_message: string | null; quality_score: number | null; queued_at: string | null; completed_at: string | null }>>(`/translation-queue/items${qs ? `?${qs}` : ""}`, { token });
}

export async function enqueueTranslation(token: string, data: { source_type: string; source_id: string; target_language: string; source_content: string; source_title?: string; priority?: string }) {
  return apiRequest<{ id: string; status: string; target_language: string }>("/translation-queue/enqueue", { method: "POST", body: data, token });
}

export async function autoQueueTranslations(token: string, data: { source_type: string; source_id: string; source_content: string; source_title?: string }) {
  return apiRequest<{ queued: number; languages: string[] }>("/translation-queue/auto-queue", { method: "POST", body: data, token });
}

export async function processTranslationQueue(token: string, workerId?: string) {
  const params = workerId ? `?worker_id=${workerId}` : "";
  return apiRequest<{ id?: string; source_type?: string; target_language?: string; source_content?: string; status: string; message?: string }>(`/translation-queue/process${params}`, { method: "POST", token });
}

export async function completeTranslation(token: string, itemId: string, data: { translated_content: string; translated_title?: string; quality_score?: number; is_machine_translated?: boolean }) {
  return apiRequest<{ id: string; status: string; target_language: string }>(`/translation-queue/items/${itemId}/complete`, { method: "POST", body: data, token });
}

export async function cancelTranslation(token: string, itemId: string) {
  return apiRequest<{ id: string; status: string }>(`/translation-queue/items/${itemId}/cancel`, { method: "POST", token });
}

export async function retryFailedTranslations(token: string, maxItems?: number) {
  return apiRequest<{ retried: number; items: Array<{ id: string; target_language: string }> }>(`/translation-queue/retry-failed${maxItems ? `?max_items=${maxItems}` : ""}`, { method: "POST", token });
}

export async function processAgreementTranslations(token: string, agreementId: string) {
  return apiRequest<{ status: string; queued: number; languages?: string[] }>(`/translation-queue/agreement/${agreementId}/process`, { method: "POST", token });
}

// Translation Progress
export async function getTranslationProgress(token: string) {
  return apiRequest<{ overall: { total: number; completed: number; pending: number; processing: number; failed: number; progress_percent: number; eta_seconds: number }; by_language: Record<string, { total: number; completed: number; pending: number; processing: number; failed: number; progress_percent: number }>; workers: Array<{ worker_id: string; hostname: string | null; tasks_completed: number; is_healthy: boolean }>; throughput: { items_per_minute: number; items_last_hour: number; avg_processing_time_seconds: number }; recent_completions: Array<{ id: string; target_language: string; source_title: string | null; completed_at: string | null; processing_time: number | null }> }>("/translation-progress/overall", { token });
}

export async function getLiveMetrics(token: string) {
  return apiRequest<{ queue_depth: Array<{ time: string; count: number }>; active_workers: number; current_processing: Array<{ id: string; target_language: string; source_type: string; started_at: string | null }> }>("/translation-progress/live", { token });
}

export async function getTranslationProgressDashboard(token: string) {
  return apiRequest<{ summary: { total: number; completed: number; pending: number; progress_percent: number; eta_seconds: number }; languages: Record<string, { total: number; completed: number; progress_percent: number; avg_processing_time: number }>; workers: Array<{ worker_id: string; is_healthy: boolean }>; throughput: { items_per_minute: number; items_last_hour: number }; recent_completions: Array<{ id: string; target_language: string; completed_at: string | null }>; live: { queue_depth: Array<{ time: string; count: number }>; active_workers: number; current_processing: Array<{ id: string; target_language: string }> }; history: Array<{ hour: string; completed: number }> }>("/translation-progress/dashboard", { token });
}

export async function getTranslationStats(token: string) {
  return apiRequest<{ overall: { total: number; completed: number; pending: number; progress_percent: number }; by_language: Record<string, { total: number; completed: number; progress_percent: number }>; throughput: { items_per_minute: number; items_last_hour: number }; live: { active_workers: number; current_processing: number } }>("/translation-progress/stats", { token });
}

// Data Governance: Retention & Legal Holds
export async function listRetentionPolicies(token: string) {
  return apiRequest<Array<{ id: string; name: string; description: string | null; scope: string; agreement_type_key: string | null; retention_months: number; disposition: string; is_active: boolean; created_at: string }>>("/retention/policies", { token });
}

export async function createRetentionPolicy(token: string, data: { name: string; description?: string; scope: string; agreement_type_key?: string; retention_months: number; disposition: string; is_active?: boolean }) {
  return apiRequest<{ id: string; name: string; scope: string; retention_months: number; disposition: string }>("/retention/policies", { method: "POST", body: data, token });
}

export async function updateRetentionPolicy(token: string, policyId: string, data: { retention_months?: number; disposition?: string; is_active?: boolean }) {
  return apiRequest<{ id: string }>(`/retention/policies/${policyId}`, { method: "PATCH", body: data, token });
}

export async function listLegalHolds(token: string, agreementId?: string) {
  return apiRequest<Array<{ id: string; agreement_id: string | null; reason: string; hold_type: string; released_at: string | null; created_at: string }>>(`/retention/holds${agreementId ? `?agreement_id=${agreementId}` : ""}`, { token });
}

export async function createLegalHold(token: string, data: { agreement_id?: string; reason: string; hold_type?: string }) {
  return apiRequest<{ id: string; reason: string; hold_type: string }>("/retention/holds", { method: "POST", body: data, token });
}

export async function releaseLegalHold(token: string, holdId: string) {
  return apiRequest<{ id: string; released_at: string | null }>(`/retention/holds/${holdId}/release`, { method: "POST", token });
}

// Data Governance: Privacy (GDPR erasure)
export async function listErasureRequests(token: string) {
  return apiRequest<Array<{ id: string; data_subject: string; regulation: string; status: string; agreement_id: string | null; shredded_fields: string[] | null; completed_at: string | null }>>("/privacy/erasure-requests", { token });
}

export async function createErasureRequest(token: string, data: { data_subject: string; agreement_id?: string; regulation?: string }) {
  return apiRequest<{ id: string; status: string }>("/privacy/erasure-requests", { method: "POST", body: data, token });
}

export async function executeErasureRequest(token: string, requestId: string, data: { field_paths?: string[]; agreement_id?: string; preserved_notes?: string }) {
  return apiRequest<{ id: string; status: string; shredded_fields: string[] }>(`/privacy/erasure-requests/${requestId}/execute`, { method: "POST", body: data, token });
}

// Feature Flags
export async function listFeatureFlags(token: string) {
  return apiRequest<Array<{ name: string; description: string; flag_type: string; status: string; enabled: boolean; percentage: number; tags: string[] }>>("/feature-flags/flags", { token });
}

export async function getFeatureFlag(token: string, flagName: string) {
  return apiRequest<{ name: string; description: string; flag_type: string; status: string; enabled: boolean; percentage: number; allowed_users: string[]; allowed_groups: string[]; tags: string[] }>(`/feature-flags/flags/${flagName}`, { token });
}

export async function createFeatureFlag(token: string, data: { name: string; description: string; flag_type?: string; enabled?: boolean; percentage?: number; tags?: string[] }) {
  return apiRequest<{ name: string; flag_type: string; status: string; enabled: boolean }>("/feature-flags/flags", { method: "POST", body: data, token });
}

export async function updateFeatureFlag(token: string, flagName: string, data: { enabled?: boolean; status?: string; percentage?: number; description?: string }) {
  return apiRequest<{ name: string; status: string; enabled: boolean }>(`/feature-flags/flags/${flagName}`, { method: "PATCH", body: data, token });
}

export async function deleteFeatureFlag(token: string, flagName: string) {
  return apiRequest<{ status: string; name: string }>(`/feature-flags/flags/${flagName}`, { method: "DELETE", token });
}

export async function evaluateFeatureFlag(token: string, flagName: string, userId?: string) {
  return apiRequest<{ flag_name: string; enabled: boolean; variant: string | null; reason: string }>(`/feature-flags/flags/${flagName}/evaluate`, { method: "POST", body: { user_id: userId }, token });
}

export async function evaluateAllFeatureFlags(token: string, userId?: string) {
  return apiRequest<Record<string, boolean>>("/feature-flags/evaluate-all", { method: "POST", body: { user_id: userId }, token });
}

export async function getEnabledFeatureFlags(token: string, userId?: string) {
  const params = userId ? `?user_id=${userId}` : "";
  return apiRequest<{ enabled_flags: string[] }>(`/feature-flags/enabled${params}`, { token });
}

export async function getFeatureFlagStats(token: string) {
  return apiRequest<{ total_flags: number; active_flags: number; inactive_flags: number; by_type: Record<string, number>; enabled_count: number }>("/feature-flags/stats", { token });
}

export async function exportFeatureFlags(token: string) {
  return apiRequest<{ flags: Array<{ name: string; description: string; flag_type: string; enabled: boolean; tags: string[] }> }>("/feature-flags/export", { token });
}

// Signature Authority
export async function checkSigningAuthority(token: string, data: { user_id: string; agreement_value: number; currency?: string; legal_entity_id?: string }) {
  return apiRequest<{ allowed: boolean; reason?: string; message?: string; signatory_id?: string; authority_type?: string; required_approvals?: Array<{ role: string; required: boolean }> }>("/signature-authority/check", { method: "POST", body: data, token });
}

export async function getRequiredApprovals(token: string, agreementValue: number, currency?: string) {
  return apiRequest<{ approvals: Array<{ role: string; required: boolean }> }>(`/signature-authority/approvals-required?agreement_value=${agreementValue}&currency=${currency || 'LKR'}`, { token });
}

export async function getEntitySigningReport(token: string, entityId: string) {
  return apiRequest<{ total_signatories: number; active_signatories: number; inactive_signatories: number; authority_distribution: Record<string, { count: number; total_capacity: number }>; unlimited_authority: number }>(`/signature-authority/entity/${entityId}/report`, { token });
}

export async function listSignatories(token: string, entityId: string) {
  return apiRequest<Array<{ id: string; name: string; title: string | null; authority_type: string; authority_scope: string; maximum_value: number | null; currency: string | null; is_active: boolean }>>(`/signature-authority/entity/${entityId}/signatories`, { token });
}

export async function addSignatory(token: string, data: { legal_entity_id: string; name: string; title?: string; email?: string; authority_type: string; authority_scope?: string; maximum_value?: number; currency?: string }) {
  return apiRequest<{ id: string; name: string; authority_type: string }>("/signature-authority/signatories", { method: "POST", body: data, token });
}

// Company Policies
export async function listCompanyPolicies(token: string) {
  return apiRequest<{ rules: Array<{ id: string; name: string; description: string; action: string; approval_roles: string[]; severity: string }> }>("/company-policies/rules", { token });
}

export async function evaluateAgreementPolicy(token: string, agreementId: string, agreementData?: Record<string, unknown>) {
  return apiRequest<{ compliance_score: number; rules_evaluated: number; rules_triggered: number; warnings: Array<{ rule_id: string; rule_name: string; description: string; severity: string }>; required_approvals: Array<{ role: string; rule_id: string; rule_name: string; severity: string }> }>("/company-policies/evaluate", { method: "POST", body: { agreement_id: agreementId, agreement_data: agreementData }, token });
}

export async function getPolicySummary(token: string) {
  return apiRequest<{ total_rules: number; rules_by_severity: Record<string, number>; approval_roles_required: string[] }>("/company-policies/summary", { token });
}

export async function getPolicyChecks(token: string, agreementId: string) {
  return apiRequest<{ agreement_id: string; compliance_score: number; warnings: Array<{ rule_id: string; rule_name: string; severity: string }>; required_approvals: Array<{ role: string; required: boolean }>; summary: { passed: number; failed: number; warnings: number } }>(`/company-policies/checks?agreement_id=${agreementId}`, { token });
}

// Admin - platform owner only
export interface AdminSession {
  session_id: string;
  user_id: string;
  name: string;
  email: string;
  company: string | null;
  status: string;
  login_at: string;
  last_seen_at: string | null;
  logout_at: string | null;
  online_seconds: number;
  online_duration: string;
  ip_address: string | null;
  user_agent: string | null;
}

export async function getAdminHealth(token: string) {
  return apiRequest<{
    status: string;
    timestamp: string;
    services: Record<string, { status: string; message?: string; consecutive_failures?: number; recent_failures_24h?: number; active_sessions?: number }>;
  }>("/admin/health", { token });
}

export async function getAdminOverview(token: string) {
  return apiRequest<{
    generated_at: string;
    users: { total: number; active: number; admins: number };
    companies: number;
    agreements: { total: number; by_status: Record<string, number> };
    sessions: { active_now: number; active_users_now: number; last_24h: number };
  }>("/admin/overview", { token });
}

export async function getAdminActiveSessions(token: string) {
  return apiRequest<AdminSession[]>("/admin/sessions/active", { token });
}

export async function getAdminSessionHistory(token: string, limit = 50) {
  return apiRequest<AdminSession[]>(`/admin/sessions/history?limit=${limit}`, { token });
}

export async function getAdminCompanies(token: string) {
  return apiRequest<Array<{
    organization_id: string;
    name: string;
    slug: string;
    country: string;
    users: number;
    active_users: number;
  }>>("/admin/companies", { token });
}

export interface AdminUser {
  user_id: string;
  name: string;
  email: string;
  status: string;
  is_admin: boolean;
  mfa_enabled: boolean;
  organization: string | null;
  created_at: string | null;
}

export async function getAdminUsers(token: string) {
  return apiRequest<AdminUser[]>("/admin/users", { token });
}

// --- DMCA notice queue (17 U.S.C. § 512) -------------------------------

export interface DmcaNotice {
  id: string;
  kind: "takedown" | "counter";
  reporter_name: string;
  reporter_email: string;
  work_description: string;
  material_location: string;
  status: "received" | "action_taken" | "rejected" | "restored";
  admin_note: string | null;
  received_at: string;
  resolved_at: string | null;
}

export async function listDmcaNotices(token: string) {
  return apiRequest<DmcaNotice[]>("/legal/dmca/notices", { token });
}

export async function updateDmcaNotice(
  token: string,
  noticeId: string,
  data: { status: "action_taken" | "rejected" | "restored"; admin_note?: string }
) {
  return apiRequest<DmcaNotice>(`/legal/dmca/notices/${noticeId}`, {
    method: "PATCH",
    body: data,
    token,
  });
}

export async function adminPromoteUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/promote`, {
    method: "POST",
    token,
  });
}

export async function adminDemoteUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/demote`, {
    method: "POST",
    token,
  });
}

export async function adminActivateUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/activate`, {
    method: "POST",
    token,
  });
}

export async function adminDeactivateUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/deactivate`, {
    method: "POST",
    token,
  });
}

// Admin suspend / unsuspend (spec 2.7-2.8) — suspension also terminates
// active sessions and lands in the audit chain.
export async function adminSuspendUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/suspend`, {
    method: "POST",
    token,
  });
}

export async function adminUnsuspendUser(token: string, userId: string) {
  return apiRequest<{ message: string }>(`/admin/users/${userId}/unsuspend`, {
    method: "POST",
    token,
  });
}

// ---------------------------------------------------------------------------
// Portfolio intelligence & forecasting (spec 3.17 / 3.18)
// ---------------------------------------------------------------------------

export interface PortfolioMetricPoint {
  date: string;
  value: number;
}

export interface PortfolioDashboard {
  status: string;
  message?: string;
  snapshot_date?: string;
  metrics?: Record<string, { value: number | null; is_missing: boolean; missing_reason?: string | null }>;
  trend?: Record<string, PortfolioMetricPoint[]>;
  anomalies?: Array<{
    metric_key: string;
    snapshot_date: string;
    observed_value: number;
    baseline_mean: number;
    deviation: number;
    direction: string;
  }>;
  insights?: Array<{
    id: string;
    category: string;
    title: string;
    body: string;
    severity: string;
    generated_by: string;
    evidence: { metric_refs?: Array<{ metric_key: string; value: number }> } | null;
  }>;
}

export async function getPortfolioDashboard(token: string) {
  return apiRequest<PortfolioDashboard>("/analytics/portfolio", { token });
}

export async function triggerPortfolioAggregation(token: string) {
  return apiRequest<{ status: string; organizations_processed: number }>(
    "/analytics/portfolio/aggregate",
    { method: "POST", token }
  );
}

export interface ForecastRunSummary {
  id: string;
  metric_key: string;
  model_type: string;
  status: string;
  status_reason?: string | null;
  horizon_days: number;
  dataset_manifest?: { snapshot_count?: number } | null;
  backtest?: { evaluated?: boolean; mae?: number; mape?: number | null } | null;
  completed_at?: string | null;
  predictions?: Array<{
    target_date: string;
    predicted_value: number;
    confidence_low: number | null;
    confidence_high: number | null;
  }>;
}

export async function listForecastRuns(token: string) {
  return apiRequest<ForecastRunSummary[]>("/forecast/runs", { token });
}

export async function createForecastRun(
  token: string,
  body: { metric_key: string; horizon_days: number; model_type: string }
) {
  return apiRequest<ForecastRunSummary>("/forecast/runs", {
    method: "POST",
    token,
    body,
  });
}

// Contract quality (spec 77-81)
export interface QualityFinding {
  engine: string;
  severity: string;
  code: string;
  message: string;
  evidence: string | null;
  location: number | null;
}

export interface QualityReport {
  agreement_id: string;
  findings: QualityFinding[];
  has_blockers: boolean;
  counts: { high: number; medium: number; low: number };
}

export async function getQualityCheck(token: string, agreementId: string) {
  return apiRequest<QualityReport>(
    `/agreements/${agreementId}/quality-check`,
    { token }
  );
}

// Audit chain (spec 1.20)
export interface AuditEvent {
  id: string;
  agreement_id: string | null;
  actor_id: string | null;
  actor_type: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  metadata_json: Record<string, unknown> | null;
  ip_address: string | null;
  sequence_number: number | null;
  prev_hash: string | null;
  event_hash: string | null;
  created_at: string;
}

export interface AuditEvidence {
  id: string;
  agreement_id: string | null;
  version_id: string | null;
  evidence_type: string;
  content_hash: string;
  content_ref: string | null;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
}

export async function verifyAuditChain(token: string, agreementId: string) {
  return apiRequest<{
    valid: boolean;
    checked: number;
    message: string;
    first_broken: Record<string, unknown> | null;
  }>(`/agreements/${agreementId}/audit/verify`, { method: "POST", token });
}

export async function exportAuditHistory(token: string, agreementId: string, limit = 1000) {
  return apiRequest<Record<string, unknown>>(
    `/agreements/${agreementId}/audit/export?limit=${limit}`,
    { token }
  );
}

export async function listAuditEvidence(token: string, agreementId: string) {
  return apiRequest<AuditEvidence[]>(`/agreements/${agreementId}/audit/evidence`, { token });
}

export async function createAuditEvidence(
  token: string,
  agreementId: string,
  data: {
    evidence_type: string;
    content_hash: string;
    version_id?: string;
    content_ref?: string;
    metadata_json?: Record<string, unknown>;
  }
) {
  return apiRequest<AuditEvidence>(`/agreements/${agreementId}/audit/evidence`, {
    method: "POST",
    body: data,
    token,
  });
}

// Execution evidence (spec 1.15)
export interface SignatureRequest {
  id: string;
  agreement_id: string;
  version_id: string;
  party_id: string | null;
  name: string;
  email: string;
  role: string;
  signer_type: string;
  status: string;
  sent_at: string | null;
  signed_at: string | null;
  expires_at: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface ExecutionRequirement {
  id: string;
  agreement_id: string | null;
  agreement_type_id: string | null;
  requirement_type: string;
  description: string;
  severity: string;
  status: string;
  satisfied_at: string | null;
  satisfied_by: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface ExecutionPackage {
  id: string;
  agreement_id: string;
  version_id: string;
  final_document_hash: string;
  package_hash: string;
  status: string;
  sealed_at: string | null;
  sealed_by: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  items: Array<{
    id: string;
    evidence_type: string;
    content_hash: string;
    data_json: Record<string, unknown>;
    created_at: string;
  }>;
}

export async function createSignatureRequest(
  token: string,
  agreementId: string,
  data: {
    name: string;
    email: string;
    version_id: string;
    party_id?: string;
    role?: string;
    signer_type?: string;
    expires_at?: string;
    metadata_json?: Record<string, unknown>;
  }
) {
  return apiRequest<SignatureRequest>(`/agreements/${agreementId}/signature-requests`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function listSignatureRequests(token: string, agreementId: string) {
  return apiRequest<SignatureRequest[]>(`/agreements/${agreementId}/signature-requests`, { token });
}

export async function sendSignatureRequest(token: string, agreementId: string, requestId: string) {
  return apiRequest<SignatureRequest>(`/agreements/${agreementId}/signature-requests/${requestId}/send`, {
    method: "POST",
    token,
  });
}

export async function declineSignatureRequest(
  token: string,
  agreementId: string,
  requestId: string,
  reason?: string
) {
  return apiRequest<SignatureRequest>(`/agreements/${agreementId}/signature-requests/${requestId}/decline`, {
    method: "POST",
    body: { reason },
    token,
  });
}

export async function signSignatureRequest(
  token: string,
  agreementId: string,
  requestId: string,
  data: { name: string; email: string; consent_text: string; identity_verified?: boolean; identity_method?: string }
) {
  return apiRequest<{ signature_id: string; signature_hash: string; signed_at: string; request_status: string }>(
    `/agreements/${agreementId}/signature-requests/${requestId}/sign`,
    { method: "POST", body: data, token }
  );
}

export async function listExecutionRequirements(token: string, agreementId: string) {
  return apiRequest<ExecutionRequirement[]>(`/agreements/${agreementId}/execution/requirements`, { token });
}

export async function checkExecutionRequirements(token: string, agreementId: string) {
  return apiRequest<{
    ready: boolean;
    required_count: number;
    satisfied_required_count: number;
    pending_count: number;
    pending: Array<{ id: string; requirement_type: string; description: string; severity: string }>;
  }>(`/agreements/${agreementId}/execution/requirements/check`, { token });
}

export async function createExecutionRequirement(
  token: string,
  agreementId: string,
  data: { requirement_type: string; description: string; severity?: string; metadata_json?: Record<string, unknown> }
) {
  return apiRequest<ExecutionRequirement>(`/agreements/${agreementId}/execution/requirements`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function satisfyExecutionRequirement(
  token: string,
  agreementId: string,
  requirementId: string,
  data?: { status?: string; metadata_json?: Record<string, unknown> }
) {
  return apiRequest<ExecutionRequirement>(`/agreements/${agreementId}/execution/requirements/${requirementId}/satisfy`, {
    method: "POST",
    body: data ?? {},
    token,
  });
}

export async function sealExecutionPackage(
  token: string,
  agreementId: string,
  data: { version_id: string; final_document_hash: string; metadata_json?: Record<string, unknown> }
) {
  return apiRequest<ExecutionPackage>(`/agreements/${agreementId}/execution/package/seal`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function getExecutionPackage(token: string, agreementId: string) {
  return apiRequest<ExecutionPackage>(`/agreements/${agreementId}/execution/package`, { token });
}

export async function verifyExecutionPackage(token: string, agreementId: string) {
  return apiRequest<Record<string, unknown>>(`/agreements/${agreementId}/execution/package/verify`, {
    method: "POST",
    token,
  });
}

// Legal knowledge engine (spec 1.10)
export interface LegalSource {
  id: string;
  tenant_id: string | null;
  title: string;
  source_type: string;
  jurisdiction_code: string;
  url: string | null;
  source_version: string | null;
  content_hash: string;
  status: string;
  acquired_at: string;
  last_checked_at: string | null;
  metadata_json: Record<string, unknown>;
  versions: Array<{ id: string; version_number: number; content_hash: string; status: string; created_at: string }>;
}

export interface LegalRule {
  id: string;
  tenant_id: string | null;
  rule_key: string;
  title: string;
  jurisdiction_code: string;
  source_id: string | null;
  source_version_id: string | null;
  applies_to_agreement_types: string[];
  proposition: string;
  executable_condition: Record<string, unknown> | null;
  severity: string;
  status: string;
  effective_from: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface LegalFinding {
  rule_id: string;
  rule_key: string;
  title: string;
  severity: string;
  passed: boolean;
  proposition?: string;
  message: string;
  source: { source_id: string | null; source_version_id: string | null; jurisdiction_code: string } | null;
}

export async function createLegalSource(
  token: string,
  data: {
    title: string;
    source_type: string;
    jurisdiction_code: string;
    content_text: string;
    url?: string;
    source_version?: string;
    extracted_text?: string;
    change_notes?: string;
    metadata_json?: Record<string, unknown>;
  }
) {
  return apiRequest<LegalSource>("/legal/sources", { method: "POST", body: data, token });
}

export async function listLegalSources(token: string, jurisdictionCode?: string) {
  const params = jurisdictionCode ? `?jurisdiction_code=${jurisdictionCode}` : "";
  return apiRequest<LegalSource[]>(`/legal/sources${params}`, { token });
}

export async function approveLegalSource(token: string, sourceId: string, notes?: string) {
  return apiRequest<LegalSource>(`/legal/sources/${sourceId}/approve`, {
    method: "POST",
    body: { notes },
    token,
  });
}

export async function rejectLegalSource(token: string, sourceId: string, notes?: string) {
  return apiRequest<LegalSource>(`/legal/sources/${sourceId}/reject`, {
    method: "POST",
    body: { notes },
    token,
  });
}

export async function createLegalRule(
  token: string,
  data: {
    rule_key: string;
    title: string;
    jurisdiction_code: string;
    proposition: string;
    source_id: string;
    source_version_id: string;
    executable_condition?: Record<string, unknown>;
    applies_to_agreement_types?: string[];
    severity?: string;
    effective_from?: string;
    change_notes?: string;
    metadata_json?: Record<string, unknown>;
  }
) {
  return apiRequest<LegalRule>("/legal/rules", { method: "POST", body: data, token });
}

export async function listLegalRules(
  token: string,
  params?: { jurisdiction_code?: string; agreement_type?: string; status?: string }
) {
  const searchParams = new URLSearchParams();
  if (params) Object.entries(params).forEach(([k, v]) => { if (v !== undefined) searchParams.set(k, String(v)); });
  const qs = searchParams.toString();
  return apiRequest<LegalRule[]>(`/legal/rules${qs ? `?${qs}` : ""}`, { token });
}

export async function approveLegalRule(token: string, ruleId: string, notes?: string) {
  return apiRequest<LegalRule>(`/legal/rules/${ruleId}/approve`, {
    method: "POST",
    body: { notes },
    token,
  });
}

export async function retireLegalRule(token: string, ruleId: string, notes?: string) {
  return apiRequest<LegalRule>(`/legal/rules/${ruleId}/retire`, {
    method: "POST",
    body: { notes },
    token,
  });
}

export async function validateAgreementLegal(token: string, agreementId: string) {
  return apiRequest<{
    agreement_id: string;
    governing_law: string | null;
    blocking_count: number;
    finding_count: number;
    can_proceed: boolean;
    findings: LegalFinding[];
  }>(`/agreements/${agreementId}/legal-validation`, { token });
}

// Billing & entitlements (spec 1.24)
export interface BillingPlan {
  id: string;
  name: string;
  code: string;
  description: string | null;
  monthly_price_cents: number;
  currency: string;
  is_active: boolean;
  features: Record<string, unknown>;
}

export interface Subscription {
  id: string;
  tenant_id: string;
  plan_id: string;
  plan_code: string | null;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  seat_limit: number | null;
  external_provider: string | null;
  external_subscription_id: string | null;
  canceled_at: string | null;
}

export interface Entitlement {
  feature_key: string;
  source: string;
  enabled: boolean;
  limit: number | null;
  remaining?: number | null;
  allowed?: boolean;
  requested?: number;
}

export interface Invoice {
  id: string;
  tenant_id: string;
  subscription_id: string | null;
  number: string;
  status: string;
  amount_cents: number;
  currency: string;
  due_date: string | null;
  paid_at: string | null;
  external_id: string | null;
  created_at: string;
  lines: Array<{ id: string; description: string; quantity: number; unit_price_cents: number; amount_cents: number }>;
}

export async function listBillingPlans(token: string) {
  return apiRequest<BillingPlan[]>("/billing/plans", { token });
}

export async function subscribeToPlan(
  token: string,
  data: { plan_code: string; external_provider?: string; external_subscription_id?: string }
) {
  return apiRequest<Subscription>("/billing/subscribe", { method: "POST", body: data, token });
}

export async function changeSubscription(
  token: string,
  data: { plan_code: string; external_provider?: string; external_subscription_id?: string }
) {
  return apiRequest<Subscription>("/billing/subscription", { method: "PATCH", body: data, token });
}

export async function getSubscription(token: string) {
  return apiRequest<Subscription>("/billing/subscription", { token });
}

export async function cancelSubscription(token: string) {
  return apiRequest<Subscription>("/billing/subscription/cancel", { method: "POST", token });
}

export async function getEntitlements(token: string) {
  return apiRequest<{ subscription: Subscription | null; entitlements: Entitlement[] }>("/billing/entitlements", { token });
}

export async function recordUsage(
  token: string,
  data: { feature_key: string; quantity?: number; source?: string; source_ref?: string; metadata_json?: Record<string, unknown> }
) {
  return apiRequest<{
    usage_record_id: string;
    feature_key: string;
    quantity: number;
    entitlement: Entitlement;
  }>("/billing/usage", { method: "POST", body: data, token });
}

export async function checkUsage(token: string, featureKey: string) {
  return apiRequest<Entitlement>(`/billing/usage/${featureKey}`, { token });
}

export async function listInvoices(token: string) {
  return apiRequest<Invoice[]>("/billing/invoices", { token });
}

export async function issueInvoice(
  token: string,
  data: { lines: Array<{ description: string; quantity?: number; unit_price_cents?: number }>; currency?: string; metadata_json?: Record<string, unknown> }
) {
  return apiRequest<Invoice>("/billing/invoices/issue", { method: "POST", body: data, token });
}

export async function payInvoice(token: string, invoiceId: string) {
  return apiRequest<Invoice>(`/billing/invoices/${invoiceId}/pay`, { method: "POST", token });
}

// Event outbox & real-time (spec 1.14)
export interface OutboxEvent {
  id: string;
  tenant_id: string | null;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string | null;
  payload: Record<string, unknown>;
  status: string;
  attempts: number;
  next_attempt_at: string | null;
  published_at: string | null;
  last_error: string | null;
  created_at: string;
}

export async function enqueueOutboxEvent(
  token: string,
  data: { event_type: string; aggregate_type: string; aggregate_id?: string; payload?: Record<string, unknown> }
) {
  return apiRequest<OutboxEvent>("/outbox", { method: "POST", body: data, token });
}

export async function listOutboxEvents(token: string, status?: string, limit = 100) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  return apiRequest<OutboxEvent[]>(`/outbox/events?${params.toString()}`, { token });
}

export async function getOutboxStats(token: string) {
  return apiRequest<{ pending: number; published: number; failed: number; total: number }>("/outbox/stats", { token });
}

export async function processOutboxEvents(token: string, limit = 50) {
  return apiRequest<{ delivered: number; failed: number; processed: number }>(`/outbox/process?limit=${limit}`, {
    method: "POST",
    token,
  });
}

// Contract Repository Search (spec 2.13)
export interface SearchResultItem {
  id: string;
  title: string;
  status: string;
  agreement_type_id: string;
  agreement_type_name: string | null;
  party_names: string[];
  governing_law: string | null;
  effective_date: string | null;
  execution_date: string | null;
  expiry_date: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface SearchFilters {
  status?: string;
  agreement_type_id?: string;
  party?: string;
  effective_from?: string;
  effective_to?: string;
  expiry_from?: string;
  expiry_to?: string;
}

export async function searchAgreements(
  token: string,
  q: string,
  filters: SearchFilters = {},
  limit = 20,
  offset = 0
) {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  Object.entries(filters).forEach(([k, v]) => {
    if (v) params.set(k, String(v));
  });
  params.set("limit", String(limit));
  if (offset > 0) params.set("offset", String(offset));
  return apiRequest<{ items: SearchResultItem[]; total: number; took_ms: number | null }>(
    `/search/agreements?${params.toString()}`,
    { token }
  );
}

export interface SavedSearch {
  id: string;
  organization_id: string;
  created_by: string;
  name: string;
  query: string | null;
  filters: SearchFilters;
  is_favorite: boolean;
  created_at: string;
  updated_at: string;
}

export async function listSavedSearches(token: string) {
  return apiRequest<SavedSearch[]>("/search/saved", { token });
}

export async function createSavedSearch(
  token: string,
  data: { name: string; query?: string; filters?: SearchFilters; is_favorite?: boolean }
) {
  return apiRequest<SavedSearch>("/search/saved", { method: "POST", body: data, token });
}

export async function updateSavedSearch(
  token: string,
  savedId: string,
  data: { name?: string; query?: string; filters?: SearchFilters; is_favorite?: boolean }
) {
  return apiRequest<SavedSearch>(`/search/saved/${savedId}`, { method: "PATCH", body: data, token });
}

export async function deleteSavedSearch(token: string, savedId: string) {
  return apiRequest<{ deleted: boolean; id: string }>(`/search/saved/${savedId}`, {
    method: "DELETE",
    token,
  });
}

// Notification unread badge
export async function getUnreadNotificationCount(token: string) {
  return apiRequest<{ count: number }>("/notifications/unread-count", { token });
}

export async function markAllNotificationsRead(token: string) {
  return apiRequest<{ marked: number }>("/notifications/mark-all-read", {
    method: "POST",
    token,
  });
}


// Dashboard (Panels.txt spec 1.1-1.8)
export interface DashboardTask {
  id: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  task_type: string;
  agreement_id: string | null;
  due_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface DashboardNotification {
  id: string;
  notification_type: string;
  subject: string;
  status: string;
  agreement_id: string | null;
  read: boolean;
  created_at: string | null;
}

export interface DashboardActivity {
  id: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  summary: string | null;
  created_at: string | null;
}

export interface DashboardPayload {
  user_id: string;
  organization_id: string;
  counts: {
    pending_tasks: number;
    unread_notifications: number;
    agreements: number;
    executed_agreements: number;
    open_obligations: number;
  };
  tasks: DashboardTask[];
  notifications: DashboardNotification[];
  recent_activity: DashboardActivity[];
}

export async function getDashboard(token: string) {
  return apiRequest<DashboardPayload>("/dashboard", { token });
}

export async function listMyTasks(token: string, status?: string) {
  const qs = status ? `?status=${status}` : "";
  return apiRequest<DashboardTask[]>(`/dashboard/tasks${qs}`, { token });
}

export async function createMyTask(
  token: string,
  data: {
    title: string;
    description?: string;
    priority?: string;
    task_type?: string;
    agreement_id?: string;
    due_at?: string;
  }
) {
  return apiRequest<DashboardTask>("/dashboard/tasks", {
    method: "POST",
    body: data,
    token,
  });
}

export async function updateMyTask(
  token: string,
  taskId: string,
  data: {
    title?: string;
    description?: string;
    status?: string;
    priority?: string;
    due_at?: string;
  }
) {
  return apiRequest<DashboardTask>(`/dashboard/tasks/${taskId}`, {
    method: "PATCH",
    body: data,
    token,
  });
}

export async function deleteMyTask(token: string, taskId: string) {
  return apiRequest<{ deleted: boolean }>(`/dashboard/tasks/${taskId}`, {
    method: "DELETE",
    token,
  });
}

// ---------------------------------------------------------------------------
// Natural-language agreement creation (spec ¶69)
// ---------------------------------------------------------------------------

export interface NLCreateResult {
  agreement: {
    id: string;
    title: string;
    status: string;
  };
  intent: {
    template_key: string | null;
    title: string;
    governing_law: string | null;
    answers: Record<string, unknown>;
    parties: Array<{ name: string; role: string }>;
  };
}

export async function createAgreementFromPrompt(token: string, prompt: string) {
  return apiRequest<NLCreateResult>("/agreements/create-from-prompt", {
    method: "POST",
    body: { prompt },
    token,
  });
}

// ---------------------------------------------------------------------------
// Contract Intelligence + AI Copilot (spec 2.10 / 35 / 36)
// ---------------------------------------------------------------------------

export interface CopilotAnswer {
  intent: string;
  status: string;
  answer: string;
  citations: Array<{
    source_number: number;
    agreement_id: string;
    version_id: string | null;
    clause_id: string | null;
    quote: string;
  }>;
  uncertainty: string | null;
  suggested_actions: string[];
  requires_human_review: boolean;
  evidence_count: number;
}

export async function askCopilot(
  token: string,
  question: string,
  agreementId?: string
) {
  return apiRequest<CopilotAnswer>("/copilot/ask", {
    method: "POST",
    body: { question, agreement_id: agreementId },
    token,
  });
}

export interface RiskGraphStats {
  nodes: number;
  edges: number;
  by_type: Record<string, number>;
}

export async function getRiskGraphStats(token: string) {
  return apiRequest<RiskGraphStats>("/risk-graph/stats", { token });
}

export interface OpenObligationRow {
  agreement_id: string;
  agreement_title: string;
  counterparty: string | null;
  obligation: string;
  status: string;
  due_date: string | null;
}

export async function getOpenObligations(token: string) {
  return apiRequest<OpenObligationRow[]>("/risk-graph/open-obligations", {
    token,
  });
}

export async function getHighRiskAgreements(token: string) {
  return apiRequest<
    Array<{
      agreement_id: string;
      title: string;
      edge_count: number;
      risk_score: number;
    }>
  >("/risk-graph/high-risk", { token });
}

export async function getExpiringAgreements(token: string, days = 90) {
  return apiRequest<
    Array<{
      agreement_id: string;
      title: string;
      status: string;
      expiry_date: string | null;
      days_remaining: number | null;
    }>
  >(`/risk-graph/expiring?days=${days}`, { token });
}

export async function buildRiskGraph(token: string, agreementId: string) {
  return apiRequest<{ edges_built: boolean }>(
    `/risk-graph/agreements/${agreementId}/build`,
    { method: "POST", token }
  );
}

// ---------------------------------------------------------------------------
// Integration connectors (spec 24.6)
// ---------------------------------------------------------------------------

export interface IntegrationConnectorRow {
  id: string;
  provider: string;
  provider_label: string;
  name: string;
  enabled: boolean;
  events: string[];
  settings: Record<string, unknown>;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_error: string | null;
}

export async function listIntegrationProviders(token: string) {
  return apiRequest<{
    providers: Array<{ key: string; label: string }>;
    supported_events: string[];
  }>("/integrations/providers", { token });
}

export async function listIntegrationConnectors(token: string) {
  return apiRequest<IntegrationConnectorRow[]>("/integrations", { token });
}

export async function createIntegrationConnector(
  token: string,
  data: {
    provider: string;
    name?: string;
    settings?: Record<string, unknown>;
    events?: string[];
  }
) {
  return apiRequest<{ id: string; provider: string; name: string; enabled: boolean }>(
    "/integrations",
    { method: "POST", body: data, token }
  );
}

export async function testIntegrationConnector(token: string, id: string) {
  return apiRequest<{ ok: boolean; provider: string; sample_payload: unknown }>(
    `/integrations/${id}/test`,
    { method: "POST", token }
  );
}

// ---------------------------------------------------------------------------
// OCR / legacy ingestion workspace (spec 24.1)
// ---------------------------------------------------------------------------

export async function createIngestionJob(
  token: string,
  source = "upload",
  targetAgreementTypeKey?: string
) {
  const params = new URLSearchParams();
  params.set("source", source);
  if (targetAgreementTypeKey) params.set("target_agreement_type_key", targetAgreementTypeKey);
  return apiRequest<{ id: string; status: string }>(
    `/ingestion/jobs?${params.toString()}`,
    { method: "POST", token }
  );
}

export async function listIngestionJobs(token: string) {
  return apiRequest<
    Array<{ id: string; source: string; status: string; total_files: number; completed_files: number; failed_files: number; created_at: string | null }>
  >("/ingestion/jobs", { token });
}

export async function getIngestionReviewQueue(token: string) {
  return apiRequest<
    Array<{
      id: string;
      ocr_document_id: string;
      filename: string | null;
      confidence: number;
      status: string;
      extracted_text_preview: string | null;
      created_at: string | null;
    }>
  >("/ingestion/review-queue", { token });
}

export async function decideIngestionReview(
  token: string,
  taskId: string,
  approved: boolean,
  correctedText?: string,
  notes?: string
) {
  return apiRequest<Record<string, unknown>>(
    `/ingestion/review-queue/${taskId}/decision`,
    { method: "POST", body: { approved, corrected_text: correctedText, notes }, token }
  );
}

// ---------------------------------------------------------------------------
// Approval workspace (spec 2.05): context + decisions + pending (spec 24.2)
// ---------------------------------------------------------------------------

export interface ApprovalContext {
  agreement: { id: string; title: string; status: string };
  version: {
    id: string;
    version_number: number;
    content_hash: string;
  } | null;
  viewer: {
    member_id: string;
    party_id: string | null;
    role: string;
    can_approve: boolean;
  };
  legal_review: {
    status: string;
    confirmed_by?: string | null;
    confirmed_at?: string | null;
  };
  workflow: { current_step: string };
  changes: { added: number; modified: number; removed: number };
}

export interface ApprovalRecordInfo {
  id: string;
  agreement_id: string;
  definition_id: string;
  current_stage_id: string | null;
  status: string;
  agreement_version_id: string | null;
  approval_type: string;
  created_at: string;
}

export async function getApprovalContext(
  token: string,
  agreementId: string
) {
  return apiRequest<ApprovalContext>(
    `/agreements/${agreementId}/approval-context`,
    { token }
  );
}

export async function getApprovalForAgreement(
  token: string,
  agreementId: string
) {
  return apiRequest<ApprovalRecordInfo>(
    `/agreements/${agreementId}/approvals`,
    { token }
  );
}

export async function startApproval(
  token: string,
  agreementId: string,
  body: { definition_id?: string | null; approval_type?: string }
) {
  return apiRequest<ApprovalRecordInfo & { routing?: Record<string, unknown> | null }>(
    `/agreements/${agreementId}/approvals/start`,
    { token, method: "POST", body: { agreement_id: agreementId, ...body } }
  );
}

export async function makeApprovalDecision(
  token: string,
  agreementId: string,
  recordId: string,
  body: { decision: string; comment?: string | null; mfa_code?: string | null }
) {
  return apiRequest<{
    id: string;
    record_id: string;
    stage_id: string;
    user_id: string;
    decision: string;
    comment: string | null;
    decided_at: string;
  }>(`/agreements/${agreementId}/approvals/${recordId}/decide`, {
    token,
    method: "POST",
    body,
  });
}

export async function getPendingApprovals(token: string) {
  return apiRequest<Array<Record<string, unknown>>>(`/approvals/pending`, {
    token,
  });
}

export async function listApprovalDefinitions(token: string) {
  return apiRequest<
    Array<{
      id: string;
      organization_id: string;
      name: string;
      description: string | null;
      is_active: boolean;
      min_value: number | null;
      max_value: number | null;
      created_at: string;
    }>
  >(`/approval-definitions`, { token });
}

// ---------------------------------------------------------------------------
// DOA approval matrices (spec 24.2)
// ---------------------------------------------------------------------------

export async function resolveDoaMatrix(
  token: string,
  agreementValue: number,
  currency = "USD"
) {
  return apiRequest<{
    source: string;
    definition_id: string | null;
    name: string;
    required_approvals: Array<{
      role: string;
      execution_mode: string;
      required: boolean;
    }>;
    total: number;
  }>(`/doa/resolve?agreement_value=${agreementValue}&currency=${currency}`, {
    token,
  });
}

// ---------------------------------------------------------------------------
// Intelligence conversations + feedback (spec 2.10.36-2.10.38)
// ---------------------------------------------------------------------------

export interface ConversationSummary {
  id: string;
  agreement_id: string | null;
  title: string | null;
  created_at: string;
  message_count: number;
  last_message_at: string | null;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  content: string;
  citations: Array<{
    source_number: number;
    agreement_id: string;
    version_id: string | null;
    clause_id: string | null;
    quote: string;
  }>;
  answer_status: string | null;
  latency_ms: number | null;
  created_at: string;
  feedback_rating?: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  organization_id: string;
  created_by: string;
  messages: ConversationMessage[];
}

export interface AskConversationResult {
  conversation_id: string;
  message_id: string;
  status: string;
  answer: string;
  citations: Array<{
    source_number: number;
    agreement_id: string;
    version_id: string | null;
    clause_id: string | null;
    quote: string;
  }>;
  uncertainty: string | null;
  requires_human_review: boolean;
  evidence_count: number;
  configuration: string | null;
  prompt_version: string | null;
  latency_ms: number;
}

export async function listConversations(token: string, limit = 50) {
  return apiRequest<ConversationSummary[]>(
    `/intelligence/conversations?limit=${limit}`,
    { token }
  );
}

export async function createConversation(
  token: string,
  opts: { title?: string; agreementId?: string } = {}
) {
  return apiRequest<ConversationSummary>("/intelligence/conversations", {
    method: "POST",
    body: {
      title: opts.title ?? null,
      agreement_id: opts.agreementId ?? null,
    },
    token,
  });
}

export async function getConversation(token: string, conversationId: string) {
  return apiRequest<ConversationDetail>(
    `/intelligence/conversations/${conversationId}`,
    { token }
  );
}

export async function askConversation(
  token: string,
  conversationId: string,
  question: string,
  limit = 8
) {
  return apiRequest<AskConversationResult>(
    `/intelligence/conversations/${conversationId}/messages`,
    { method: "POST", body: { question, limit }, token }
  );
}

export type FeedbackRating =
  | "helpful"
  | "not_helpful"
  | "incorrect"
  | "missing_source"
  | "wrong_source"
  | "incomplete";

export async function rateMessage(
  token: string,
  messageId: string,
  rating: FeedbackRating,
  reason?: string
) {
  return apiRequest<{ id: string; rating: string }>(
    `/intelligence/messages/${messageId}/feedback`,
    { method: "POST", body: { rating, reason: reason ?? null }, token }
  );
}

// ---------------------------------------------------------------------------
// Intelligence governance: feedback review queue (spec 2.10.39)
// ---------------------------------------------------------------------------

export interface FeedbackReviewRow {
  id: string;
  rating: string;
  reason: string | null;
  created_at: string;
  question: string;
  answer: string;
  answer_status: string | null;
  message_id: string;
  conversation_id: string;
  conversation_title: string | null;
  promoted: boolean;
  example_id: string | null;
}

export async function listGovernanceFeedback(
  token: string,
  opts: { limit?: number; pendingOnly?: boolean } = {}
) {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.pendingOnly) params.set("pending_only", "true");
  const qs = params.toString();
  return apiRequest<FeedbackReviewRow[]>(
    `/intelligence/governance/feedback${qs ? `?${qs}` : ""}`,
    { token }
  );
}

export async function promoteFeedback(
  token: string,
  feedbackId: string,
  category = "user_feedback"
) {
  return apiRequest<{ example_id: string; category: string }>(
    "/intelligence/governance/feedback/promote",
    {
      method: "POST",
      body: { feedback_id: feedbackId, category },
      token,
    }
  );
}

// ---------------------------------------------------------------------------
// Company policy rules CRUD (spec 24.3 – visual rule builder)
// ---------------------------------------------------------------------------

export interface PolicyRule {
  id: string;
  name: string;
  description: string;
  action: string;
  approval_roles: string[];
  severity: string;
  condition_key?: string;
}

export async function listPolicyRules(token: string) {
  return apiRequest<{ rules: PolicyRule[] }>("/company-policies/rules", { token });
}

export async function createPolicyRule(
  token: string,
  data: {
    rule_id: string;
    name: string;
    description: string;
    condition_key: string;
    action?: string;
    approval_roles?: string[];
    severity?: string;
  }
) {
  return apiRequest<{ status: string; rule_id: string }>("/company-policies/rules", {
    method: "POST",
    body: data,
    token,
  });
}

export async function deletePolicyRule(token: string, ruleId: string) {
  return apiRequest<{ status: string; rule_id: string }>(`/company-policies/rules/${ruleId}`, {
    method: "DELETE",
    token,
  });
}

// ---------------------------------------------------------------------------
// Ingestion file upload (multipart) – spec 24.1
// ---------------------------------------------------------------------------

export async function uploadIngestionDocument(
  token: string,
  jobId: string,
  file: File,
  provider?: string
) {
  const form = new FormData();
  form.append("file", file);

  const url = `/api/v1/ingestion/jobs/${jobId}/documents${provider ? `?provider=${provider}` : ""}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Upload error: ${res.status}`);
  }
  return res.json() as Promise<{
    id: string;
    status: string;
    confidence: number;
    provider: string;
    needs_review: boolean;
    extracted_metadata: Record<string, unknown>;
  }>;
}

export async function getIngestionSummary(token: string) {
  return apiRequest<{
    total_jobs: number;
    pending_review: number;
    completed: number;
    failed: number;
  }>("/ingestion/summary", { token });
}


// ---------------------------------------------------------------------------
// Dynamic rules engine (spec 24.2): DOA approval routing rules
// ---------------------------------------------------------------------------

export interface RuleCondition {
  name: string;
  operator: string;
  value: unknown;
}

export interface RuleDefinitionPayload {
  conditions: { all: RuleCondition[] } | { any: RuleCondition[] };
  actions?: Array<Record<string, unknown>>;
}

export interface RuleDefinition {
  id: string;
  name: string;
  description: string | null;
  rules: RuleDefinitionPayload | null;
}

export async function listRuleDefinitions(token: string) {
  return apiRequest<RuleDefinition[]>("/rules-engine", { token });
}

export async function createRuleDefinition(
  token: string,
  data: {
    name: string;
    description?: string;
    rules: RuleDefinitionPayload;
    stages: Array<{
      name: string;
      order: number;
      required_role: string;
      execution_mode: string;
      require_all_approvers: boolean;
    }>;
  }
) {
  return apiRequest<RuleDefinition>("/rules-engine", {
    method: "POST",
    body: data,
    token,
  });
}

export async function deleteRuleDefinition(token: string, id: string) {
  return apiRequest<{ id: string }>(`/rules-engine/${id}`, {
    method: "DELETE",
    token,
  });
}

export async function evaluateRules(
  token: string,
  context: {
    agreement_value: number;
    agreement_type?: string;
    currency?: string;
    risk_score?: number;
    counterparty_country?: string;
  }
) {
  return apiRequest<{
    source: string;
    definition_id: string | null;
    name: string;
    required_approvals: Array<{ role: string; execution_mode: string; required: boolean }>;
    total: number;
    matched_rule: RuleDefinitionPayload | null;
  }>("/rules-engine/evaluate", { method: "POST", body: context, token });
}

// ─── Signature progress (spec §67) ───────────────────────────────────────────

export interface SignatureProgress {
  agreement_id: string;
  status: string;
  required_external: number;
  signed_external: number;
  internal_signatures: number;
  missing_external: string[];
  all_signed: boolean;
}

export async function getSignatureProgress(token: string, agreementId: string) {
  return apiRequest<SignatureProgress>(`/agreements/${agreementId}/signature-progress`, { token });
}

// ─── Amendments (spec §36) ───────────────────────────────────────────────────

export interface AmendmentChange {
  section_key: string;
  change_type: string;
  old_text?: string | null;
  new_text: string;
}

export interface Amendment {
  id: string;
  agreement_id: string;
  amendment_number: number;
  title: string;
  description: string | null;
  reason: string | null;
  status: string;
  version_number: number;
  effective_date: string | null;
  created_at: string;
  changes?: AmendmentChange[] | null;
}

export async function listAmendments(token: string, agreementId: string) {
  return apiRequest<Amendment[]>(`/agreements/${agreementId}/amendments`, { token });
}

export async function createAmendment(
  token: string,
  agreementId: string,
  data: { title: string; description?: string; reason?: string; changes: AmendmentChange[] }
) {
  return apiRequest<Amendment>(`/agreements/${agreementId}/amendments`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function activateAmendment(
  token: string,
  agreementId: string,
  amendmentId: string,
  effectiveDate?: string
) {
  return apiRequest<Amendment>(`/agreements/${agreementId}/amendments/${amendmentId}/activate`, {
    method: "POST",
    body: { effective_date: effectiveDate || null },
    token,
  });
}

// ─── Terminations (spec §66 ACTIVE → TERMINATED) ─────────────────────────────

export interface RenewalConfig {
  id: string;
  agreement_id: string;
  is_renewable: boolean;
  auto_renew: boolean;
  renewal_term_months: number;
  max_renewals: number | null;
  current_renewal_count: number;
  original_expiry_date: string | null;
  current_expiry_date: string | null;
  next_renewal_date: string | null;
  last_renewal_date: string | null;
  notice_period_days: number | null;
  notice_given: boolean;
  notice_given_date: string | null;
  price_increase_percentage: number | null;
  price_fixed_amount: number | null;
  status: string;
}

export interface Termination {
  id: string;
  agreement_id: string;
  initiated_at: string;
  reason_code: string;
  reason_detail: string | null;
  notice_date: string | null;
  notice_period_days: number | null;
  notice_served: boolean;
  cure_required: boolean;
  cure_period_days: number | null;
  cure_deadline: string | null;
  cured: boolean | null;
  status: string;
  effective_date?: string | null;
}

export async function listTerminations(token: string, agreementId: string) {
  return apiRequest<Termination[]>(`/agreements/${agreementId}/terminations`, { token });
}

export async function initiateTermination(
  token: string,
  agreementId: string,
  data: {
    reason_code: string;
    reason_detail?: string;
    notice_period_days?: number;
    cure_required?: boolean;
    cure_period_days?: number;
  }
) {
  return apiRequest<Termination>(`/agreements/${agreementId}/terminations`, {
    method: "POST",
    body: data,
    token,
  });
}

export async function issueTerminationNotice(token: string, agreementId: string, terminationId: string) {
  return apiRequest<Termination>(`/agreements/${agreementId}/terminations/${terminationId}/notice`, {
    method: "POST",
    body: {},
    token,
  });
}

export async function completeTermination(
  token: string,
  agreementId: string,
  terminationId: string,
  effectiveDate?: string
) {
  return apiRequest<Termination>(`/agreements/${agreementId}/terminations/${terminationId}/complete`, {
    method: "POST",
    body: { effective_date: effectiveDate || null },
    token,
  });
}

export async function cancelTermination(token: string, agreementId: string, terminationId: string) {
  return apiRequest<Termination>(`/agreements/${agreementId}/terminations/${terminationId}/cancel`, {
    method: "POST",
    body: {},
    token,
  });
}

// ─── RBAC (spec §51) ─────────────────────────────────────────────────────────

export interface RbacPermission {
  key: string;
  description: string | null;
}

export interface RbacRole {
  id: string;
  name: string;
  description: string | null;
  organization_id: string;
  permissions: RbacPermission[];
}

export async function listRbacPermissions(token: string) {
  return apiRequest<RbacPermission[]>("/rbac/permissions", { token });
}

export async function listRbacRoles(token: string) {
  return apiRequest<RbacRole[]>("/rbac/roles", { token });
}

export async function createRbacRole(
  token: string,
  data: { name: string; description?: string; permission_keys: string[] }
) {
  return apiRequest<RbacRole>("/rbac/roles", { method: "POST", body: data, token });
}

export async function updateRbacRolePermissions(token: string, roleId: string, permissionKeys: string[]) {
  return apiRequest<{ updated: boolean }>(`/rbac/roles/${roleId}/permissions`, {
    method: "PUT",
    body: { permission_keys: permissionKeys },
    token,
  });
}

export async function deleteRbacRole(token: string, roleId: string) {
  return apiRequest<{ deleted: boolean }>(`/rbac/roles/${roleId}`, { method: "DELETE", token });
}

export async function addRbacMember(token: string, data: { user_id: string; role_id: string }) {
  return apiRequest<{ id: string; user_id: string; role_id: string; status: string }>("/rbac/members", {
    method: "POST",
    body: data,
    token,
  });
}

// ─── SSO / SCIM (spec §54) ───────────────────────────────────────────────────

export interface SsoConnection {
  id: string;
  protocol: "saml" | "oidc";
  name: string;
  issuer: string | null;
  client_id: string | null;
  domains: string[];
  default_role: string | null;
  enforce_sso: boolean;
  enabled: boolean;
}

export async function listSsoConnections(token: string) {
  return apiRequest<SsoConnection[]>("/sso/connections", { token });
}

export async function createSsoConnection(
  token: string,
  data: {
    protocol: "saml" | "oidc";
    name: string;
    issuer?: string;
    client_id?: string;
    client_secret_ref?: string;
    idp_metadata_url?: string;
    domains: string[];
    default_role?: string;
    enforce_sso?: boolean;
  }
) {
  return apiRequest<SsoConnection>("/sso/connections", { method: "POST", body: data, token });
}

export async function testSsoConnection(token: string, connectionId: string) {
  return apiRequest<{ ok?: boolean; status?: string; detail?: string; [k: string]: unknown }>(
    `/sso/connections/${connectionId}/test`,
    { method: "POST", body: {}, token }
  );
}

export async function createScimToken(token: string, connectionId?: string) {
  return apiRequest<{ token: string; id?: string; [k: string]: unknown }>("/sso/scim-tokens", {
    method: "POST",
    body: connectionId ? { connection_id: connectionId } : {},
    token,
  });
}

// ─── Documents repository (spec §24 / 2.07.24) ───────────────────────────────

export interface RepositoryDocument {
  id: string;
  title: string;
  filename: string | null;
  media_type: string | null;
  sha256: string | null;
  size_bytes: number | null;
  classification: string | null;
  status: string;
  immutable: boolean;
  document_type: string | null;
  created_at: string | null;
}

export async function listAgreementDocuments(token: string, agreementId: string) {
  return apiRequest<{ documents: RepositoryDocument[] }>(`/agreements/${agreementId}/documents`, { token });
}

// ─── Analytics (spec §84-85) ──────────────────────────────────────────────

export interface ExecutiveAnalytics {
  volume: {
    total: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
    monthly_trend: { month: string; count: number }[];
  };
  cycle_time: { average_days: number; median_days: number; sample_size: number };
  approval_time: { average_days: number; sample_size: number };
  rates: {
    renewal_rate: number;
    termination_rate: number;
    obligation_compliance_rate: number;
    execution_rate: number;
  };
  obligations: { total: number; completed: number; overdue: number };
  risk: { low: number; medium: number; high: number; critical: number };
}

export interface FinancialAnalytics {
  total_contract_value: number;
  committed_spend: number;
  outstanding_obligations: number;
  overdue_obligations: number;
  average_value_by_type: Record<string, { average_value: number; count: number }>;
  value_by_status: Record<string, { total_value: number; count: number }>;
}

export async function getExecutiveAnalytics(token: string): Promise<ExecutiveAnalytics> {
  return apiRequest<ExecutiveAnalytics>('/analytics/executive', { token });
}

export async function getFinancialAnalytics(token: string): Promise<FinancialAnalytics> {
  return apiRequest<FinancialAnalytics>('/analytics/financial', { token });
}

// ─── Compliance summary (spec §89) ────────────────────────────────────────

export interface ComplianceSummary {
  contracts_requiring_review: number;
  missing_dpa: number;
  unsigned_amendments: number;
  upcoming_renewals: number;
  pending_approvals: number;
  overdue_obligations: number;
}

export async function getComplianceSummary(token: string): Promise<ComplianceSummary> {
  return apiRequest<ComplianceSummary>('/compliance/summary', { token });
}

// ─── Supplier intelligence (spec §86) ─────────────────────────────────────

export interface SupplierIntelligence {
  suppliers: {
    company_name: string;
    contract_count: number;
    total_value: number;
    active_contracts: number;
    pending_contracts: number;
    obligations: { total: number; completed: number; overdue: number };
    renewals: number;
  }[];
  total_suppliers: number;
}

export async function getSupplierIntelligence(token: string): Promise<SupplierIntelligence> {
  return apiRequest<SupplierIntelligence>('/analytics/supplier-performance', { token });
}

// ─── Security monitoring (spec §95) ──────────────────────────────────────

export interface SecurityScanResult {
  findings: {
    category: string;
    severity: string;
    description: string;
    actor_id: string | null;
    evidence: Record<string, unknown>;
    detected_at: string;
  }[];
  total: number;
  by_severity: { critical: number; high: number; medium: number; low: number };
}

export async function getSecurityScan(token: string): Promise<SecurityScanResult> {
  return apiRequest<SecurityScanResult>('/security/scan', { token });
}

// ─── API keys (spec §7) ──────────────────────────────────────────────

export interface APIKeyRecord {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string | null;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export async function listApiKeys(token: string): Promise<APIKeyRecord[]> {
  return apiRequest<APIKeyRecord[]>('/api-keys', { token });
}

export async function createApiKey(
  token: string,
  data: { name: string; scopes?: string; expires_days?: number }
): Promise<APIKeyRecord & { raw_key: string }> {
  return apiRequest('/api-keys', { method: 'POST', body: data, token });
}

export async function revokeApiKey(token: string, keyId: string): Promise<void> {
  return apiRequest(`/api-keys/${keyId}`, { method: 'DELETE', token });
}

// ─── Renewals (spec §67 ACTIVE → RENEWED) ─────────────────────────────

export async function getRenewalConfig(token: string, id: string): Promise<RenewalConfig> {
  const res = await fetch(`${API_BASE}/agreements/${id}/renewal`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to get renewal config");
  return res.json();
}

export async function updateRenewalConfig(token: string, id: string, data: Partial<RenewalConfig>): Promise<RenewalConfig> {
  const res = await fetch(`${API_BASE}/agreements/${id}/renewal`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update renewal config");
  return res.json();
}

export async function processRenewal(token: string, id: string, force = false): Promise<any> {
  const res = await fetch(`${API_BASE}/agreements/${id}/renewal/process`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to process renewal");
  }
  return res.json();
}

export async function giveRenewalNotice(token: string, id: string): Promise<RenewalConfig> {
  const res = await fetch(`${API_BASE}/agreements/${id}/renewal/notice`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to give non-renewal notice");
  return res.json();
}

// ---------------------------------------------------------------------------
// Admin — audit batch sealing + external timestamp anchoring (spec 1.20.15-16)
// ---------------------------------------------------------------------------

export interface AuditBatchInfo {
  id: string;
  root_hash: string;
  leaf_count: number;
  first_sequence: number;
  last_sequence: number;
  anchor_status: string; // 'externally_anchored' | 'internal_only' | 'anchor_failed'
  anchored_at: string | null;
}

export interface AuditBatchVerifyResult {
  status: string;
  batch_id: string;
  merkle: {
    valid: boolean;
    root_hash?: string;
    anchor_status?: string;
    anchored_at?: string | null;
    leaf_count?: number;
    reason?: string;
  };
  anchor: {
    valid: boolean;
    gen_time: string | null;
    reason: string | null;
  } | null;
}

export async function adminSealAuditBatch(
  token: string,
  data: { from_sequence: number; to_sequence: number; tenant_id?: string }
): Promise<{ status: string; batch: AuditBatchInfo }> {
  return apiRequest("/admin/audit/batches/seal", {
    method: "POST",
    body: data,
    token,
  });
}

export async function adminVerifyAuditBatch(
  token: string,
  batchId: string
): Promise<AuditBatchVerifyResult> {
  return apiRequest(`/admin/audit/batches/${batchId}/verify`, {
    method: "POST",
    token,
  });
}

// ─── Obligation monitoring (spec 3.15) ───────────────────────────────────

export interface MonitoringIntegrationHealth {
  last_success_at?: string | null;
  last_failure_at?: string | null;
  consecutive_failures: number;
  last_latency_ms?: number | null;
  last_error_code?: string | null;
  last_error?: string | null;
}

export interface MonitoringIntegrationRow {
  id: string;
  name: string;
  integration_type: string;
  provider_key: string;
  status: string;
  configuration: Record<string, unknown>;
  created_at: string;
  health?: MonitoringIntegrationHealth | null;
}

export interface MonitoringRuleRow {
  id: string;
  obligation_id: string;
  integration_id: string;
  source_version_id: string;
  status: string;
  pause_reason?: string | null;
  query_definition: Record<string, unknown>;
  evaluation_definition: Record<string, unknown>;
  schedule_definition: Record<string, unknown>;
  automation?: Record<string, unknown> | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_result?: string | null;
  created_at: string;
}

export interface MonitoringDashboard {
  rules_total: number;
  rules_by_status: Record<string, number>;
  monitoring_active: number;
  exceptions_open: boolean;
  last_evaluation_at?: string | null;
  last_evaluation_result?: string | null;
}

export async function getMonitoringDashboard(token: string): Promise<MonitoringDashboard> {
  return apiRequest<MonitoringDashboard>("/monitoring/dashboard", { token });
}

export async function listMonitoringRules(token: string): Promise<MonitoringRuleRow[]> {
  return apiRequest<MonitoringRuleRow[]>("/monitoring/rules", { token });
}

export async function listMonitoringIntegrations(
  token: string
): Promise<MonitoringIntegrationRow[]> {
  return apiRequest<MonitoringIntegrationRow[]>("/monitoring/integrations", { token });
}

export async function listAgreementMonitoring(
  token: string,
  agreementId: string
): Promise<MonitoringRuleRow[]> {
  return apiRequest<MonitoringRuleRow[]>(
    `/agreements/${agreementId}/monitoring`,
    { token }
  );
}

export interface MonitoringRuleCreate {
  obligation_id: string;
  integration_id: string;
  source_version_id: string;
  query_definition?: Record<string, unknown>;
  evaluation_definition?: Record<string, unknown>;
  schedule_definition?: Record<string, unknown>;
  automation?: Record<string, unknown> | null;
  status?: string;
}

export async function createMonitoringRule(
  token: string,
  data: MonitoringRuleCreate
): Promise<MonitoringRuleRow> {
  return apiRequest<MonitoringRuleRow>("/monitoring/rules", {
    method: "POST",
    token,
    body: JSON.stringify({ ...data, status: data.status ?? "DRAFT" }),
  });
}

export async function setMonitoringRuleActive(
  token: string,
  ruleId: string
): Promise<MonitoringRuleRow> {
  return apiRequest<MonitoringRuleRow>(`/monitoring/rules/${ruleId}/activate`, {
    method: "POST",
    token,
  });
}

export async function setMonitoringRulePaused(
  token: string,
  ruleId: string
): Promise<MonitoringRuleRow> {
  return apiRequest<MonitoringRuleRow>(`/monitoring/rules/${ruleId}/pause`, {
    method: "POST",
    token,
  });
}

export async function testMonitoringIntegration(
  token: string,
  integrationId: string
): Promise<{ ok: boolean; integration_id: string; message?: string }> {
  return apiRequest<{ ok: boolean; integration_id: string; message?: string }>(
    `/monitoring/integrations/${integrationId}/test`,
    { method: "POST", token }
  );
}

export async function disconnectMonitoringIntegration(
  token: string,
  integrationId: string
): Promise<{ ok: boolean }> {
  return apiRequest<{ ok: boolean }>(
    `/monitoring/integrations/${integrationId}/disconnect`,
    { method: "POST", token }
  );
}

export async function rotateMonitoringCredentials(
  token: string,
  integrationId: string
): Promise<{ ok: boolean; expires_at?: string | null }> {
  return apiRequest<{ ok: boolean; expires_at?: string | null }>(
    `/monitoring/integrations/${integrationId}/rotate-credentials`,
    { method: "POST", token }
  );
}
