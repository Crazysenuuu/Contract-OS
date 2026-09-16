"""
PDF Export API Endpoints.

Generate PDF reports for analytics, compliance, and obligations.
"""

import io
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.company_policy import ComplianceReport, PolicyViolation
from app.models.notification import Notification
from app.models.obligation import Obligation
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["PDF Export"])


def _generate_html_report(
    title: str,
    sections: list[dict],
) -> str:
    """Generate HTML report content."""
    sections_html = ""

    for section in sections:
        sections_html += f"""
        <div style="margin-bottom:32px;">
            <h2 style="font-size:18px;color:#111827;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-bottom:16px;">
                {section['title']}
            </h2>
            {section['content']}
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{ margin: 2cm; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #111827; line-height: 1.5; }}
            table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
            th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
            th {{ background-color: #f9fafb; font-weight: 600; }}
            .metric {{ display: inline-block; text-align: center; padding: 16px 24px; margin: 8px; background: #f3f4f6; border-radius: 8px; }}
            .metric-value {{ font-size: 28px; font-weight: bold; color: #111827; }}
            .metric-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; }}
            .severity-critical {{ color: #dc2626; }}
            .severity-high {{ color: #ea580c; }}
            .severity-medium {{ color: #ca8a04; }}
            .severity-low {{ color: #2563eb; }}
            .header {{ text-align: center; margin-bottom: 32px; padding-bottom: 16px; border-bottom: 3px solid #3b82f6; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1 style="font-size:24px;color:#111827;margin:0;">📋 ContractOS</h1>
            <h2 style="font-size:20px;color:#374151;margin:8px 0 0 0;">{title}</h2>
            <p style="font-size:12px;color:#6b7280;margin:4px 0 0 0;">
                Generated: {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}
            </p>
        </div>
        {sections_html}
        <div style="text-align:center;font-size:11px;color:#9ca3af;margin-top:32px;padding-top:16px;border-top:1px solid #e5e7eb;">
            ContractOS — Contract Lifecycle Management
        </div>
    </body>
    </html>
    """


@router.get("/export/analytics-pdf")
async def export_analytics_pdf(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export comprehensive analytics report as PDF."""
    # Gather data
    total_result = await db.execute(
        select(func.count(Agreement.id)).where(
            Agreement.organization_id == org_id
        )
    )
    total = total_result.scalar() or 0

    executed_result = await db.execute(
        select(func.count(Agreement.id)).where(
            Agreement.organization_id == org_id,
            Agreement.status == "executed",
        )
    )
    executed = executed_result.scalar() or 0

    compliance_result = await db.execute(
        select(
            func.count(ComplianceReport.id),
            func.avg(ComplianceReport.compliance_score),
        )
    )
    compliance_row = compliance_result.one()

    violations_result = await db.execute(
        select(PolicyViolation.severity, func.count(PolicyViolation.id))
        .group_by(PolicyViolation.severity)
    )
    violation_stats = {row[0]: row[1] for row in violations_result.all()}

    obligations_result = await db.execute(
        select(Obligation.status, func.count(Obligation.id))
        .group_by(Obligation.status)
    )
    obligation_stats = {row[0]: row[1] for row in obligations_result.all()}

    # Build HTML
    execution_rate = round(executed / max(total, 1) * 100, 1)

    sections = [
        {
            "title": "Agreement Overview",
            "content": f"""
                <div>
                    <div class="metric">
                        <div class="metric-value">{total}</div>
                        <div class="metric-label">Total Agreements</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{executed}</div>
                        <div class="metric-label">Executed</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{total - executed}</div>
                        <div class="metric-label">In Progress</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{execution_rate}%</div>
                        <div class="metric-label">Execution Rate</div>
                    </div>
                </div>
            """,
        },
        {
            "title": "Compliance Summary",
            "content": f"""
                <div>
                    <div class="metric">
                        <div class="metric-value">{compliance_row[0] or 0}</div>
                        <div class="metric-label">Reports Generated</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{round(float(compliance_row[1] or 0), 1)}%</div>
                        <div class="metric-label">Avg. Score</div>
                    </div>
                </div>
                <h3 style="font-size:14px;margin:16px 0 8px 0;">Violations by Severity</h3>
                <table>
                    <tr><th>Severity</th><th>Count</th></tr>
                    {''.join(f'<tr><td class="severity-{s}">{s.upper()}</td><td>{c}</td></tr>' for s, c in violation_stats.items()) if violation_stats else '<tr><td colspan="2">No violations recorded</td></tr>'}
                </table>
            """,
        },
        {
            "title": "Obligations Summary",
            "content": f"""
                <table>
                    <tr><th>Status</th><th>Count</th></tr>
                    {''.join(f'<tr><td>{s}</td><td>{c}</td></tr>' for s, c in obligation_stats.items()) if obligation_stats else '<tr><td colspan="2">No obligations recorded</td></tr>'}
                </table>
            """,
        },
    ]

    html = _generate_html_report("Analytics Report", sections)

    # Generate PDF using weasyprint or return HTML
    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=html).write_pdf()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=analytics_report.pdf"
            },
        )
    except (ImportError, OSError):
        # OSError covers weasyprint present but system Pango/cairo libs
        # missing (dev machines) — degrade to HTML instead of crashing.
        # Fallback: return HTML
        return StreamingResponse(
            io.BytesIO(html.encode("utf-8")),
            media_type="text/html",
            headers={
                "Content-Disposition": "attachment; filename=analytics_report.html"
            },
        )


@router.get("/export/compliance-pdf")
async def export_compliance_pdf(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export compliance report as PDF."""
    result = await db.execute(
        select(ComplianceReport).order_by(ComplianceReport.created_at.desc())
    )
    reports = result.scalars().all()

    violations_result = await db.execute(
        select(PolicyViolation)
        .order_by(PolicyViolation.created_at.desc())
    )
    violations = violations_result.scalars().all()

    # Build report rows
    report_rows = ""
    for r in reports:
        score_color = (
            "#10b981" if r.compliance_score >= 80
            else "#f59e0b" if r.compliance_score >= 60
            else "#ef4444"
        )
        report_rows += f"""
            <tr>
                <td>{r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '—'}</td>
                <td>{r.total_policies_checked}</td>
                <td>{r.violations_found}</td>
                <td style="color:#dc2626;">{r.critical_count}</td>
                <td style="color:#ea580c;">{r.high_count}</td>
                <td style="color:#ca8a04;">{r.medium_count}</td>
                <td style="color:#2563eb;">{r.low_count}</td>
                <td style="color:{score_color};font-weight:bold;">{r.compliance_score:.1f}%</td>
            </tr>
        """

    violation_rows = ""
    for v in violations[:50]:
        violation_rows += f"""
            <tr>
                <td>{v.violation_type}</td>
                <td style="color:{'#dc2626' if v.severity == 'critical' else '#ea580c' if v.severity == 'high' else '#374151'};">
                    {v.severity.upper()}
                </td>
                <td>{v.description[:100]}</td>
                <td>{v.reviewer_status}</td>
            </tr>
        """

    sections = [
        {
            "title": "Compliance Reports",
            "content": f"""
                <table>
                    <tr><th>Date</th><th>Checked</th><th>Violations</th><th>Critical</th><th>High</th><th>Medium</th><th>Low</th><th>Score</th></tr>
                    {report_rows if report_rows else '<tr><td colspan="8">No compliance reports yet</td></tr>'}
                </table>
            """,
        },
        {
            "title": "Recent Violations",
            "content": f"""
                <table>
                    <tr><th>Type</th><th>Severity</th><th>Description</th><th>Status</th></tr>
                    {violation_rows if violation_rows else '<tr><td colspan="4">No violations recorded</td></tr>'}
                </table>
            """,
        },
    ]

    html = _generate_html_report("Compliance Report", sections)

    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=html).write_pdf()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=compliance_report.pdf"
            },
        )
    except (ImportError, OSError):
        # OSError covers weasyprint present but system Pango/cairo libs
        # missing (dev machines) — degrade to HTML instead of crashing.
        return StreamingResponse(
            io.BytesIO(html.encode("utf-8")),
            media_type="text/html",
            headers={
                "Content-Disposition": "attachment; filename=compliance_report.html"
            },
        )
