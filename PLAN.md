# Contract-OS · Build Plan (from Panels.txt spec)

Mapping of the full `Panels.txt` specification (Sections 1–2 + Milestones 3.0–3.26) against
the current repo, with a phased, ticket-sized build plan. Every spec section is accounted for.

Source of truth for status: repo `backend/app/{models,services,api/v1,tasks}`,
`frontend/src/app`, `mobile/lib`, `backend/migrations/versions`, `backend/tests`, `e2e/tests`.

---

## Part 1 — Spec → current status (complete coverage)

Legend: ✅ Done · 🟡 Partial · ❌ Gap · 🚧 In flight

| Spec § | Feature | Status | Evidence |
|---|---|---|---|
| 1 | User portal + DB-backed dashboard | ✅ | `dashboard_service`, `api/v1/dashboard.py`, mig `b3c4d5e6f7a8`, `test_dashboard_portal` |
| 1.9–1.12 | Portal-separated auth (USER token rejects admins) | ✅ | `dependencies/rbac`, `test_rbac_enforcement`, `test_auth_integration` |
| 2 | System admin portal (`require_system_admin`) | ✅ | `api/v1/admin.py`, `frontend/src/app/admin/*`, `test_admin_integration` |
| 2.6 | Admin user search returns raw ORM rows | 🟡 | `api/v1/admin.py list_users` — must return schema, never leak `password_hash` |
| 2.15–2.18 | Admin audit events, sessions, MFA, protected role changes | ✅ | `audit.py`, `user.py` (UserSession/RefreshToken), `otp_challenge`, `test_approval_step_up` |
| 3.0 | Onboarding, workspaces, members, RBAC, RLS, session binding | ✅ | `organization.py`, `tenant.py`, `rbac.py`, `test_tenant_isolation` |
| 3.1 | Agreement core, immutable versions, hashing, optimistic concurrency, states | ✅ | `agreement.py`, `agreement_versioning`, `diff_engine`, `test_agreement_versioning` |
| 3.2 | Structured document builder (block model, JSONB, autosave, no raw HTML) | ✅ | `agreement_draft_assembler`, `schema_validation_service`, `agreement_renderer`, `test_authoring_studio` |
| 3.3 | Templates, immutable versions, controlled variable schema/compiler | ✅ | `template.py`, `template_engine`, `template_service_v2`, `/templates` UI |
| 3.4 | Parties/contacts (org vs individual, duplicates, merges, identifiers) | ✅ | `legal_entity.py`, `/companies`, `/contacts` |
| 3.5 | Sharing, invitations, participants, comments, revocation | ✅ | `agreement_access.py`, `authorization.py` |
| 3.6 | Review/approval, stage policies, version-tied approvals, sign-off | ✅ | `approval.py`, `approval_engine`, 7× `test_approval_*` |
| 3.7 | E-signature/execution, executed-doc immutability, evidence, RFC3161 TSA, OTP | ✅ | `execution.py`, `signing_session.py`, `esignature`, `rfc3161_tsa`, `test_otp_signing` |
| 3.8 | Repository, object storage, malware scan, retention, legal hold | ✅ | `document.py`, `document_repository`, `stored_object`, `retention.py`, `/documents` |
| 3.9 | Global search, filters, saved search, permission-aware | ✅ | `search.py`, `saved_search.py`, `/search`, `test_search` |
| 3.10 | Notifications, inbox, action center, outbox, WebSocket, push/SMS/email | ✅ | `notification.py`, `notification_service`, `event_outbox`, `ws_fanout` |
| 3.11 | Hash-chained audit trail, activity timeline, export, tamper detection | ✅ | `audit.py`, `audit_batching`, `test_audit_chain*` |
| 3.12 | Lifecycle, deadlines, renewals, expiration | ✅ | `lifecycle.py`, `renewal.py`, `business_calendar`, `test_lifecycle_e2e` |
| 3.13 | Obligations, tasks, performance tracking, reminders, recurrence | ✅ | `obligation.py`, `obligation_extractor`, `user_task` |
| 3.14 | Risk & clause intelligence, obligation-risk detection | 🟡 | `ai_analysis` (`RiskFinding`), `risk_scoring`, `contract_health`; spec's full deterministic pipeline only partially wired |
| 3.15 | Obligation **monitoring** + external data verification | ❌ | `IntegrationConnector` exists; NO `MonitoringRule → Observation → Evaluator`, no evidence/exception events |
| 3.16 | Intelligence graph, cross-agreement deps, entity risk | ✅ | `risk_graph.py`, `risk_graph_service` |
| 3.17 | Portfolio analytics, executive dashboard | ✅ | `analytics_service`, `metrics_service`, `contract_health`, `/analytics` |
| 3.18 | Forecasting, scenario modeling, what-if simulation | ❌ | No forecast/scenario/what-if module anywhere |
| 3.19 | Workflow automation & orchestration | ✅ | `orchestration.py`, `orchestration_engine`, `workflow.py`, `test_workflow_*` |
| 3.20 | External counterparty portal + guest identity/KYC | 🚧 | `external_party.py`; UNCOMMITTED: `kyc_provider.py`, `guest_kyc_verification` mig, `test_guest_kyc_flow`, `test_kyc_provider` |
| 3.21 | Ingestion, document understanding, source-grounded extraction | ✅ | `ingestion.py`, `document_intelligence.py`, `ocr_service`, `test_ingestion_ocr` |
| 3.22 | Change management, amendments, redlines, version intelligence | ✅ | `amendment.py`, `agreement_changes.py`, `diff_engine`, `test_amendment_*` |
| 3.23 | Negotiation workspace | 🟡 | `negotiation.py` (rounds, accept/reject/counter, compile); MISSING clause **playbooks**, concessions, AI-suggestion storage, playbook matching, negotiation analytics (spec 3.23 §10–11, 26–27) |
| 3.24 | Clause library, standardization, org legal policy | ✅ | `clause.py`, `document_intelligence` (ClauseLibrary), `clause_governance`, `/clause-library` |
| 3.25 | Compliance & regulatory/policy monitoring | 🟡 | `company_policy.py`, `compliance_service`, `compliance_summary`; spec's registry → requirement extract/version → applicability → controls → assessments → gaps → remediation → change-detection only partially covered |
| 3.26 | Governance, exceptions, continuous certification | ❌ | `intelligence_governance`, `clause_governance` exist; NO `governance_exceptions`, `compensating_controls`, `attestations`, `certifications`, `governance_policies`, invalidation/expiry |

---

## Part 2 — Phased plan

### Phase 0 · Finish in flight (current working tree) — integrity

| # | Task | Files | Done when |
|---|---|---|---|
| 0.1 | Land guest KYC end-to-end: KYC attempt state machine, evidence records, rate-limit/brute-force guard, external-user isolation tests | `backend/app/models/external_party.py`, `backend/app/services/kyc_provider.py`, `backend/app/services/external_party_service.py`, `backend/app/api/v1/external_party.py`, `backend/app/core/config.py`, `backend/migrations/versions/a1b2c3d4e5f6_*.py`, `backend/tests/test_guest_kyc_flow.py`, `backend/tests/test_kyc_provider.py` | Added + committed; 401/403/429 paths tested |
| 0.2 | Green gate | `make test` (backend → frontend → e2e) | All suites pass |
| 0.3 | Admin `list_users` returns `UserSummary` schema (never `password_hash`); add lower-email unique index | `backend/app/api/v1/admin.py`, migration | `test_admin_integration` asserts no sensitive fields; spec 2.6/2.15 met |

### Phase 1 · Sellable gaps (P0 first)

**T1 — Monitoring engine (spec 3.15)**
- `MonitoringRule` (obligation_id, connector_id, rule_type, params, schedule) — `backend/app/models/monitoring.py`
- `IntegrationConnection→Connector` binding — extend `app/models/integration.py`, `integration_service.py`
- `Observation` + `MonitoringEvidence` — evidence must be real, never fabricated (spec rule)
- Evaluators — `backend/app/services/monitoring/` `threshold/freshness/count/existence/delivery`
- Named exception events → outbox + notifications (bridge 3.13 → 3.10)
- Scheduler task in `backend/app/tasks/scheduler.py` (Celery beat)
- Tests: `backend/tests/test_monitoring.py` (evaluators, provider-agnostic, no-fabrication, isolation)

**T2 — Obligation-risk pipeline (spec 3.14 depth)**
- 19-step deterministic analysis: exact version → content hash → rules → semantic → cross-clause → obligation/lifecycle risk → scores → supersede stale findings → activity/audit/outbox
- Wire into `app/services/risk_scoring.py`, `app/services/obligation_extractor.py`, `app/api/v1/obligations.py`
- RLS/index work per spec 3.14 columns

**T3 — Forecasting & what-if (spec 3.18)**
- `ForecastScenario` (parameter set), `ForecastSnapshot`, baseline-diff recalc (respects 3.17 "snapshot not fact" constraints)
- Service `app/services/forecast_service.py`, API `app/api/v1/forecast.py`, page `frontend/src/app/forecast/page.tsx`
- Tests: `backend/tests/test_forecast.py`

**T4 — Compliance depth (spec 3.25)**
- Requirement registry + versioning, applicability determination, assessment engine, gap records + remediation workflow, change-detection impact
- Files: `backend/app/models/compliance.py` (or extend `company_policy.py`), `backend/app/services/compliance_service.py`, `backend/app/api/v1/compliance_summary.py`, frontend compliance page
- Guardrail: never auto-declare legal compliance (spec §2)

**T5 — E2E coverage** — Playwright for: monitoring loop (3.12→3.13→3.14→3.15), negotiation round-trip, compliance gap→remediation; `e2e/tests/*.spec.ts`

### Phase 2 · Differentiation layer

**T6 — Governance & certification (spec 3.26)**
- `governance_exceptions` + `references` + `compensating_controls`
- `governance_attestations`, `governance_certifications` (bound to exact `agreement_version_id`, expires on supersession)
- `governance_policies` + certification policies
- Exception must NOT rewrite assessment (spec §44 test); certification version-binding + expiry (§45–46); org-isolation RLS (spec §41–42); idempotent invalidation (§40)
- Files: `backend/app/models/governance.py`, `backend/app/services/governance_service.py`, `backend/app/api/v1/governance.py`, `backend/tests/governance/` (spec §43 file list), `frontend/src/app/governance/`

**T7 — Clause playbooks & negotiation intelligence (spec 3.23)**
- Playbook models + matching, concession tracking, AI-suggestion storage, negotiation analytics
- Files: `backend/app/models/negotiation.py` (extend), `backend/app/services/negotiation_service.py`, `backend/app/api/v1/negotiation.py`, `negotiate` page
- Critical rule: no direct approval duplication — canonical route stays 3.6 approval + 3.22 apply (spec §40)

**T8 — Clause-drift & deviation records (spec 3.24 tail)**
- Clause drift detection, deviation records, RLS; feed 3.16 graph / 3.17 metrics / 3.9 search indexes (spec cross-refs)

**T9 — AI provenance hardening (spec 3.21)**
- Automation must not trigger contractual actions from a raw/non-finalized extraction candidate; guard gate in `orchestration_engine.py` + `ingestion` route; extend `test_guest_link_and_ai_fallback`

**T10 — Real-data verification** — confirm `backend/scripts/seed_e2e.py` refuses `ENVIRONMENT=production` in deploy path; production bootstrap via `make admin`, no demo login; verify all dashboards keep "all numbers from PostgreSQL" rule.

### Phase 3 · Hardening & scale (continuous)

- Step-up MFA on admin: role change, disable user, delete data, security policy change, credential rotation (extend `test_approval_step_up` to admin ops)
- Two-person approval for admin create/remove (spec 2.9)
- Session revocation UI + admin device/session management (models exist; surface missing)
- Index review vs spec 2.15 + new 3.15/3.18/3.26 indexes; query-plan checks on dashboard/search hot paths (`test_load_hot_endpoints.py`)
- Observability: OpenTelemetry spans for orchestration instances, monitoring scheduler, outbox (`test_tracing.py` exists)

---

## Sequencing note
- **Buy-cost risk:** 3.18 + 3.26 have zero existing scaffolding — treat as full milestones, not gap-fills.
- **Highest sales leverage:** 3.15 monitoring → 3.14 obligation risk → 3.18 forecasting → 3.25 compliance depth.
- **Must land before sales:** Phase 0 (KYC in flight) + `make test` green + admin schema hardening.