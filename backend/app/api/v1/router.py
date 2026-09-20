from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.admin import router as admin_router
from app.api.v1.organizations import router as org_router
from app.api.v1.legal_entities import router as legal_entity_router
from app.api.v1.agreements import router as agreement_router
from app.api.v1.agreement_access import router as agreement_access_router
from app.api.v1.negotiation import router as negotiation_router
from app.api.v1.negotiation import change_sets_router
from app.api.v1.external_party import router as external_party_router
from app.api.v1.external_party import agreement_router as external_agreement_router
from app.api.v1.legal_workspace import router as legal_workspace_router
from app.api.v1.approvals import definition_router as approval_def_router
from app.api.v1.approvals import record_router as approval_record_router
from app.api.v1.approvals import pending_router as approval_pending_router
from app.api.v1.authorization import router as authorization_router
from app.api.v1.audit import router as audit_router
from app.api.v1.signing import router as signing_router
from app.api.v1.lifecycle import router as lifecycle_router
from app.api.v1.amendments import router as amendments_router
from app.api.v1.terminations import router as terminations_router
from app.api.v1.renewals import router as renewals_router
from app.api.v1.documents import router as documents_router
from app.api.v1.workflow import router as workflow_router
from app.api.v1.ai_analysis import router as ai_analysis_router
from app.api.v1.obligations import router as obligations_router
from app.api.v1.obligation_ops import router as obligation_ops_router
from app.api.v1.policies import router as policies_router
from app.api.v1.policies import agreement_router as compliance_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.notifications import export_router as export_router
from app.api.v1.notification_preferences import router as preferences_router
from app.api.v1.pdf_export import router as pdf_router
from app.api.v1.delivery_status import router as delivery_router
from app.api.v1.webhooks import router as webhook_router
from app.api.v1.webhooks import cron_router as cron_router
from app.api.v1.phase3 import router as phase3_router
from app.api.v1.phase4 import router as phase4_router
from app.api.v1.i18n import router as i18n_router
from app.api.v1.translation_sync import router as translation_sync_router
from app.api.v1.translation_queue import router as translation_queue_router
from app.api.v1.translation_progress import router as translation_progress_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.canary import router as canary_router
from app.api.v1.feature_flags import router as feature_flags_router
from app.api.v1.doa import router as doa_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.signature_authority import router as signature_authority_router
from app.api.v1.rbac import router as rbac_router
from app.api.v1.company_policies import router as company_policies_router
from app.api.v1.migration_monitoring import router as migration_monitor_router
from app.api.v1.health import router as health_router
from app.api.v1.alerting import router as alerting_router
from app.api.v1.escalation import router as escalation_router
from app.api.v1.execution import router as execution_router
from app.api.v1.signing_sessions import router as signing_sessions_router
from app.api.v1.legal_knowledge import router as legal_knowledge_router
from app.api.v1.billing import router as billing_router
from app.api.v1.outbox import router as outbox_router
from app.api.v1.search import router as search_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.retention import router as retention_router
from app.api.v1.retention import repository_router
from app.api.v1.ingestion import router as ingestion_router
from app.api.v1.privacy import router as privacy_router
from app.api.v1.intelligence import router as intelligence_router
from app.api.v1.risk_graph import router as risk_graph_router
from app.api.v1.risk_graph import copilot_router
from app.api.v1.mobile import router as mobile_router
from app.api.v1.agreement_types import router as agreement_types_router
from app.api.v1.rules_engine import router as rules_engine_router
from app.api.v1.clauses import router as clauses_router
from app.api.v1.sso import router as sso_router
from app.api.v1.sso import scim_router
from app.api.v1.templates import router as templates_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.compliance_summary import router as compliance_summary_router
from app.api.v1.security_monitoring import router as security_monitoring_router
from app.api.v1.api_keys import router as api_keys_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(org_router)
api_router.include_router(legal_entity_router)
api_router.include_router(lifecycle_router)
api_router.include_router(agreement_router)
api_router.include_router(agreement_access_router)
api_router.include_router(negotiation_router)
api_router.include_router(change_sets_router)
api_router.include_router(external_agreement_router)
api_router.include_router(legal_workspace_router)
api_router.include_router(approval_def_router)
api_router.include_router(approval_record_router)
api_router.include_router(approval_pending_router)
api_router.include_router(authorization_router)
api_router.include_router(audit_router)
api_router.include_router(signing_router)
api_router.include_router(amendments_router)
api_router.include_router(terminations_router)
api_router.include_router(renewals_router)
api_router.include_router(documents_router)
api_router.include_router(workflow_router)
api_router.include_router(ai_analysis_router)
api_router.include_router(obligations_router)
api_router.include_router(obligation_ops_router)
api_router.include_router(policies_router)
api_router.include_router(compliance_router)
api_router.include_router(notifications_router)
api_router.include_router(export_router)
api_router.include_router(preferences_router)
api_router.include_router(pdf_router)
api_router.include_router(delivery_router)
api_router.include_router(webhook_router)
api_router.include_router(cron_router)
api_router.include_router(phase3_router)
api_router.include_router(phase4_router)
api_router.include_router(i18n_router)
api_router.include_router(translation_sync_router)
api_router.include_router(translation_queue_router)
api_router.include_router(translation_progress_router)
api_router.include_router(metrics_router)
api_router.include_router(canary_router)
api_router.include_router(feature_flags_router)
api_router.include_router(doa_router)
api_router.include_router(signature_authority_router)
api_router.include_router(rbac_router)
api_router.include_router(company_policies_router)
api_router.include_router(migration_monitor_router)
api_router.include_router(health_router)
api_router.include_router(alerting_router)
api_router.include_router(escalation_router)
api_router.include_router(execution_router)
api_router.include_router(signing_sessions_router)
api_router.include_router(legal_knowledge_router)
api_router.include_router(billing_router)
api_router.include_router(outbox_router)
api_router.include_router(search_router)
api_router.include_router(dashboard_router)
api_router.include_router(retention_router)
api_router.include_router(repository_router)
api_router.include_router(ingestion_router)
api_router.include_router(privacy_router)
api_router.include_router(intelligence_router)
api_router.include_router(risk_graph_router)
api_router.include_router(copilot_router)
api_router.include_router(integrations_router)
api_router.include_router(mobile_router)
api_router.include_router(agreement_types_router)
api_router.include_router(rules_engine_router)
api_router.include_router(clauses_router)
api_router.include_router(sso_router)
api_router.include_router(scim_router)
api_router.include_router(templates_router)
api_router.include_router(analytics_router)
api_router.include_router(compliance_summary_router)
api_router.include_router(security_monitoring_router)
api_router.include_router(api_keys_router)

# External party review endpoints are mounted directly in main.py
# (no /api/v1 prefix - token-based auth)
