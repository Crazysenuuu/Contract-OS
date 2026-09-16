import hashlib
from uuid import UUID

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement, AgreementVersion
from app.models.agreement_type import AgreementType
from app.models.user import User
from app.schemas.agreement import (
    AgreementCreate,
    AgreementResponse,
    AgreementTypeResponse,
    AgreementUpdate,
    AgreementVersionResponse,
)
from app.services.agreement_renderer import (
    get_template_questions,
    render_agreement,
    resolve_template_key,
    validate_answers,
)
from app.services.schema_validation_service import (
    validate_agreement_schema,
)
from app.services.lifecycle_service import agreement_is_immutable
from app.services.template_engine import get_available_templates

router = APIRouter(prefix="/agreements", tags=["agreements"])


def calculate_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RenderRequest(BaseModel):
    generate_pdf: bool = True
    watermark: bool = True  # Overlay viewer identity on shared copies (1.22)


class UpdateAnswersRequest(BaseModel):
    answers: dict
    check_compliance: bool = True  # Auto-check compliance after update


class ComplianceResult(BaseModel):
    compliance_score: float
    violations_found: int
    critical: int
    high: int
    medium: int
    low: int
    summary: str
    violations: list[dict]


class UpdateAnswersResponse(BaseModel):
    status: str
    data: dict
    compliance: Optional[ComplianceResult] = None
    validation: Optional[dict] = None


class ValidationIssueResponse(BaseModel):
    field: str | None
    message: str
    severity: str
    stage: str


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    issues: list[ValidationIssueResponse] = []
    warnings: list[ValidationIssueResponse] = []
    stages_run: list[str] = []


class NLCreateRequest(BaseModel):
    prompt: str


class NLCreateResponse(BaseModel):
    agreement: AgreementResponse
    intent: dict


@router.post("/create-from-prompt", response_model=NLCreateResponse)
async def create_agreement_from_prompt(
    data: NLCreateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an agreement from a natural-language description (spec ¶69)."""
    from app.services.nl_creation_service import NLCreationError, NLCreationService

    svc = NLCreationService(db)
    try:
        agreement, intent = await svc.create_from_prompt(
            prompt=data.prompt,
            org_id=org_id,
            current_user=current_user,
        )
    except NLCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return NLCreateResponse(agreement=agreement, intent=intent)


@router.post(
    "",
    response_model=AgreementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agreement(
    data: AgreementCreate,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.agreement_schema_validator import (
        build_json_schema,
        validate,
        extract_entity_references,
    )
    from app.models.legal_entity import LegalEntity

    atype = (
        await db.execute(
            select(AgreementType).where(
                AgreementType.id == data.agreement_type_id,
                AgreementType.status == "active",
            )
        )
    ).scalar_one_or_none()
    if atype is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement type not found or not published",
        )

    intake = data.data or {}
    # An empty payload is the wizard's "create draft" call: it creates the
    # agreement shell first and fills answers incrementally (validation is
    # enforced by POST /{id}/validate before rendering). Only validate intake
    # that was actually supplied at creation time.
    if intake:
        json_schema = build_json_schema(atype.schema or {})
        try:
            validate(json_schema, intake)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=["agreement.data", getattr(exc, "args", [exc])[0]],
            )

    # Spec 2.03.10: a client must not submit another organization's legal
    # entity UUID and have it accepted.
    entity_keys = extract_entity_references(atype.schema or {})
    if entity_keys:
        ids = {UUID(str(intake[k])) for k in entity_keys if intake.get(k)}
        if ids:
            owned = await db.execute(
                select(LegalEntity.id).where(
                    LegalEntity.id.in_(ids),
                    LegalEntity.organization_id == org_id,
                    LegalEntity.status == "active",
                )
            )
            owned_ids = {row[0] for row in owned.all()}
            missing = ids - owned_ids
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Legal entity is not available to this organization",
                )

    parent_id = None
    if data.parent_agreement_id is not None:
        parent = await db.execute(
            select(Agreement).where(
                Agreement.id == data.parent_agreement_id,
                Agreement.organization_id == org_id,
            )
        )
        if parent.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent agreement not found",
            )
        parent_id = data.parent_agreement_id

    agreement = Agreement(
        organization_id=org_id,
        agreement_type_id=data.agreement_type_id,
        agreement_type_version=atype.version,
        title=data.title,
        governing_law=data.governing_law,
        effective_date=data.effective_date,
        parent_agreement_id=parent_id,
        created_by=current_user.id,
        data=intake,
    )
    db.add(agreement)
    await db.flush()

    initial_version = AgreementVersion(
        agreement_id=agreement.id,
        version_number=1,
        content="",
        content_hash=calculate_hash(""),
        status="draft",
        created_by=current_user.id,
    )
    db.add(initial_version)
    await db.flush()
    await db.refresh(agreement)

    return agreement


@router.get("", response_model=list[AgreementResponse])
async def list_agreements(
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agreement)
        .where(Agreement.organization_id == org_id)
        .order_by(Agreement.created_at.desc())
    )
    return result.scalars().all()


@router.get(
    "/{agreement_id:uuid}",
    response_model=AgreementResponse,
)
async def get_agreement(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    return agreement


@router.patch(
    "/{agreement_id}",
    response_model=AgreementResponse,
)
async def update_agreement(
    agreement_id: UUID,
    data: AgreementUpdate,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    if agreement.status not in ("draft", "negotiating"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agreement cannot be modified in current status",
        )

    if agreement_is_immutable(agreement):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Executed agreements cannot be edited directly; create an amendment",
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agreement, field, value)

    await db.flush()
    await db.refresh(agreement)

    return agreement


@router.get(
    "/{agreement_id}/versions",
    response_model=list[AgreementVersionResponse],
)
async def list_versions(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    agreement_result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    if agreement_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number)
    )
    return result.scalars().all()


# --- Template & Rendering Endpoints ---


@router.get(
    "/templates/list",
    status_code=status.HTTP_200_OK,
)
async def list_templates():
    """List available agreement templates."""
    return get_available_templates()


@router.get(
    "/types",
    response_model=list[AgreementTypeResponse],
    status_code=status.HTTP_200_OK,
)
async def list_agreement_types(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all available agreement types from the database."""
    result = await db.execute(
        select(AgreementType).where(AgreementType.status == "active")
    )
    return result.scalars().all()


@router.get(
    "/types/{type_id}/questions",
    status_code=status.HTTP_200_OK,
)
async def get_agreement_type_questions(
    type_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the questionnaire schema for a specific agreement type.

    Catalog types share a generic template_key, so questions must be
    resolved per type id, not per template (the template-key endpoint can
    only return one type's questions when many types share a template).
    """
    from app.services.agreement_renderer import normalize_questions_for_ui

    atype = (
        await db.execute(
            select(AgreementType).where(
                AgreementType.id == type_id,
                AgreementType.status == "active",
            )
        )
    ).scalar_one_or_none()
    if atype is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement type not found or not published",
        )
    if atype.schema and atype.schema.get("questions"):
        return normalize_questions_for_ui(atype.schema["questions"])

    # Type without a stored schema falls back to its template questionnaire.
    questions = get_template_questions(atype.template_key or "")
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No questionnaire defined for type '{atype.key}'",
        )
    return normalize_questions_for_ui(questions)


@router.get(
    "/templates/{template_key}/questions",
    status_code=status.HTTP_200_OK,
)
async def get_template_questions_endpoint(
    template_key: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the questionnaire schema for a template.

    Resolves questions from the seeded agreement_type schema (matched by
    ``template_key``); falls back to the built-in questionnaire. Options are
    normalized to ``[{value, label}]`` for the wizard.
    """
    from app.services.agreement_renderer import normalize_questions_for_ui

    result = await db.execute(
        select(AgreementType).where(
            AgreementType.status == "active",
            AgreementType.template_key == template_key,
        )
    )
    atype = result.scalars().first()
    if atype and atype.schema and atype.schema.get("questions"):
        return normalize_questions_for_ui(atype.schema["questions"])

    questions = get_template_questions(template_key)
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_key}' not found",
        )
    return normalize_questions_for_ui(questions)


@router.post(
    "/{agreement_id}/answers",
    status_code=status.HTTP_200_OK,
)
async def update_answers(
    agreement_id: UUID,
    data: UpdateAnswersRequest,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update the answers_json for an agreement (wizard progress)."""
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    if agreement.status not in ("draft", "negotiating"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agreement cannot be modified in current status",
        )

    if agreement_is_immutable(agreement):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Executed agreements cannot be edited directly; create an amendment",
        )

    agreement.data.update(data.answers)
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(agreement, "data")
    await db.flush()
    await db.refresh(agreement)

    # Formal schema validation (spec 1.9) — warnings are returned to the
    # client but do not block saving draft progress.
    validation = await validate_agreement_schema(
        db,
        agreement_type_id=agreement.agreement_type_id,
        answers=agreement.data,
    )

    # Auto-compliance check if requested
    compliance_result = None
    if data.check_compliance:
        try:
            from app.services.compliance_service import ComplianceService
            compliance_svc = ComplianceService(db)
            compliance = await compliance_svc.check_compliance(
                agreement_id=agreement_id,
                organization_id=org_id,
                checked_by=None,
            )
            await db.flush()  # Save the compliance report
            compliance_result = ComplianceResult(
                compliance_score=compliance["compliance_score"],
                violations_found=compliance["violations_found"],
                critical=compliance["critical"],
                high=compliance["high"],
                medium=compliance["medium"],
                low=compliance["low"],
                summary=compliance["summary"],
                violations=compliance["violations"],
            )
        except Exception:
            # Compliance check is best-effort - don't fail the update
            pass

    return UpdateAnswersResponse(
        status="updated",
        data=agreement.data,
        compliance=compliance_result,
        validation=validation.to_dict() if not validation.valid or validation.warnings else None,
    )


@router.post(
    "/{agreement_id}/validate",
    response_model=ValidationResponse,
    status_code=status.HTTP_200_OK,
)
async def validate_agreement_answers(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Validate that all required answers are present for rendering."""
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    # Formal schema validation pipeline (spec 1.9)
    validation = await validate_agreement_schema(
        db,
        agreement_type_id=agreement.agreement_type_id,
        answers=agreement.data,
    )

    # Fall back to the renderer's required-field check so the response is
    # consistent even for legacy templates without stored schemas. Use the
    # same question source as the questions endpoint (stored schema first,
    # built-in fallback) so validation checks the fields the wizard actually
    # displayed.
    from app.services.agreement_renderer import normalize_questions_for_ui

    template_key = await resolve_template_key(db, agreement)
    atype_result = await db.execute(
        select(AgreementType).where(AgreementType.id == agreement.agreement_type_id)
    )
    atype = atype_result.scalar_one_or_none()
    schema_questions = (
        (atype.schema or {}).get("questions")
        if atype and atype.schema
        else None
    )
    errors = validate_answers(
        template_key,
        agreement.data,
        questions=(
            normalize_questions_for_ui(schema_questions)
            if schema_questions
            else None
        ),
    )

    return ValidationResponse(
        valid=validation.valid and len(errors) == 0,
        errors=errors + validation.error_messages,
        issues=[e.to_dict() for e in validation.errors],
        warnings=[w.to_dict() for w in validation.warnings],
        stages_run=validation.stages_run,
    )


@router.post(
    "/{agreement_id}/render",
    status_code=status.HTTP_200_OK,
)
async def render_agreement_endpoint(
    agreement_id: UUID,
    data: RenderRequest = RenderRequest(),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Render the agreement from template + answers."""
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    # Formal schema validation gate (spec 1.9) — blocking errors prevent render.
    validation = await validate_agreement_schema(
        db,
        agreement_type_id=agreement.agreement_type_id,
        answers=agreement.data,
    )

    template_key = await resolve_template_key(db, agreement)
    errors = validate_answers(template_key, agreement.data)
    if errors or not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Validation failed",
                "errors": errors + validation.error_messages,
                "issues": [e.to_dict() for e in validation.errors],
            },
        )

    try:
        render_result = await render_agreement(
            db,
            agreement_id=agreement_id,
            generate_pdf=data.generate_pdf,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if render_result.pdf:
        from app.services import document_storage

        pdf = render_result.pdf
        if data.watermark:
            # Re-render HTML with viewer watermark and convert to PDF so the
            # shared copy carries who/when provenance (spec 1.22).
            viewer = f"{current_user.name} ({current_user.email})" if getattr(current_user, "name", None) else str(current_user.id)
            watermarked_html = document_storage.watermark_html(render_result.html, viewer_name=viewer, org_name=str(org_id))
            try:
                from app.services.pdf_generator import html_to_pdf
                watermarked_pdf = html_to_pdf(watermarked_html)
                if watermarked_pdf:
                    pdf = watermarked_pdf
            except Exception:
                pass

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition":
                    f'attachment; filename="agreement_{agreement_id}.pdf"',
                "X-Content-Hash": render_result.content_hash,
            },
        )

    return {
        "rendered_text": render_result.rendered_text,
        "content_hash": render_result.content_hash,
    }


@router.get(
    "/{agreement_id}/current-terms",
    status_code=status.HTTP_200_OK,
)
async def get_current_terms_endpoint(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Consolidated current terms: base + all active amendments (2.07.26)."""
    from app.services.lifecycle_service import get_current_terms

    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )
    return await get_current_terms(db, agreement)


@router.post(
    "/{agreement_id}/draft/generate",
    status_code=status.HTTP_201_CREATED,
)
async def generate_agreement_draft(
    agreement_id: UUID,
    current_user=Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Generate a draft from the clause library (spec 1.8.16/1.8.22).

    Requires the agreement.create_draft capability - distinct from
    agreement.propose_change (spec 1.8.23). Chain: auth -> tenant ->
    participant -> permission -> DB-backed context -> applicable approved
    clauses -> variables resolved -> immutable version -> audit event.
    """
    from app.dependencies.agreement_access import verify_agreement_access
    from app.services.agreement_draft_assembler import (
        MissingRequiredClauseError,
        generate_draft,
    )
    from app.services.clause_renderer import MissingClauseVariableError
    from app.services.audit_service import record_event

    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.create_draft",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    try:
        version = await generate_draft(db, agreement, current_user.id)
    except MissingRequiredClauseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except MissingClauseVariableError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=current_user.id,
        actor_type="user",
        action="AGREEMENT_DRAFT_GENERATED",
        resource_type="agreement",
        resource_id=agreement.id,
        metadata_json={"agreement_version_id": str(version.id)},
    )
    await db.commit()
    await db.refresh(version)
    return {
        "agreement_version_id": str(version.id),
        "version_number": version.version_number,
        "content_hash": version.content_hash,
    }


@router.get(
    "/{agreement_id}/quality-check",
    status_code=status.HTTP_200_OK,
)
async def quality_check_agreement(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the deterministic contract-quality engines (spec 77-81).

    Lint-style findings over the latest rendered version: undefined terms,
    broken cross-references, percentage-split mismatches, date ordering
    violations, and party/signatory mismatches. Purely advisory — findings
    never block the workflow.
    """
    from app.services.audit_service import record_event
    from app.services.contract_quality import run_quality_checks

    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    # Latest version text (if any has been rendered/created yet).
    version_result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    latest_version = version_result.scalar_one_or_none()

    # Party + signatory names for the consistency engine.
    from app.models.agreement_access import AgreementParty
    from app.models.legal_entity import LegalEntity

    party_result = await db.execute(
        select(LegalEntity.legal_name)
        .join(AgreementParty, AgreementParty.legal_entity_id == LegalEntity.id)
        .where(AgreementParty.agreement_id == agreement_id)
    )
    parties = [row[0] for row in party_result.all()]

    from app.models.execution import SignatureRequest

    signer_result = await db.execute(
        select(SignatureRequest.name).where(
            SignatureRequest.agreement_id == agreement_id
        )
    )
    signers = [row[0] for row in signer_result.all()]

    data = agreement.data or {}
    report = run_quality_checks(
        latest_version.content if latest_version else None,
        agreement_meta={
            "effective_date": data.get("effective_date"),
            "expiry_date": data.get("expiry_date"),
            "execution_date": data.get("execution_date"),
            "renewal_notice_date": data.get("renewal_notice_date"),
            "doc_type": "agreement",
            "parties": parties,
            "signers": signers,
        },
    )

    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=current_user.id,
        actor_type="user",
        action="AGREEMENT_QUALITY_CHECKED",
        resource_type="agreement",
        resource_id=agreement.id,
        metadata_json={
            "findings": report["counts"],
            "has_blockers": report["has_blockers"],
        },
    )
    await db.commit()

    return {
        "agreement_id": str(agreement_id),
        **report,
    }
