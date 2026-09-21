import copy
import hashlib
from uuid import UUID

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
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
    AgreementVersionDetailResponse,
    AgreementVersionResponse,
    CreateVersionRequest,
    VersionCompareResponse,
)
from app.services.agreement_renderer import (
    get_template_questions,
    get_template_questions_for_agreement,
    render_agreement,
    resolve_template_key,
    validate_answers,
)
from app.services.agreement_versioning import (
    compare_versions as compare_agreement_versions,
    create_version,
    get_version_by_number,
    restore_version,
)
from app.services.schema_validation_service import (
    validate_agreement_schema,
)
from app.domain.agreement_states import EDITABLE_STATES
from app.domain.answer_provenance import (
    AnswerSource,
    classify_user_update,
    tag_answers,
)
from app.services.lifecycle_service import agreement_is_immutable
from app.services.template_engine import get_available_templates

router = APIRouter(prefix="/agreements", tags=["agreements"])


def calculate_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _questions_for(db: AsyncSession, agreement: Agreement) -> list[dict]:
    """Questionnaire for an agreement: type schema first, template fallback."""
    questions = await get_template_questions_for_agreement(db, agreement)
    if questions:
        return questions
    template_key = await resolve_template_key(db, agreement)
    return get_template_questions(template_key)


class RenderRequest(BaseModel):
    generate_pdf: bool = True
    watermark: bool = True  # Overlay viewer identity on shared copies (1.22)


class UpdateAnswersRequest(BaseModel):
    answers: dict
    check_compliance: bool = True  # Auto-check compliance after update
    # Spec §70: the client may declare where the answers came from; the
    # default is USER_PROVIDED (typed in the wizard). Only EXTRACTED /
    # INFERRED / USER_PROVIDED are accepted from clients — SYSTEM_DEFAULT is
    # derived server-side and LEGAL_REQUIREMENT only from the jurisdiction
    # engine.
    source: Optional[str] = None


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
    provenance: dict = {}
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

    if data.is_test_data and get_settings_lazy().environment == "production":
        # Spec §72: no synthetic contract data in production.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Synthetic / test agreements cannot be created in production",
        )

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
        answer_provenance=tag_answers({}, intake.keys(), AnswerSource.USER_PROVIDED),
        is_test_data=bool(data.is_test_data),
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
        data=copy.deepcopy(intake) if intake else None,
        note="Initial version",
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
    query = select(Agreement).where(Agreement.organization_id == org_id)
    if get_settings_lazy().environment == "production":
        # Spec §72: synthetic agreements are invisible to production users.
        query = query.where(Agreement.is_test_data.is_(False))
    result = await db.execute(query.order_by(Agreement.created_at.desc()))
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

    if agreement.status not in EDITABLE_STATES:
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


async def _load_owned_agreement(
    db: AsyncSession,
    agreement_id: UUID,
    org_id: UUID,
) -> Agreement:
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
    return agreement


@router.get(
    "/{agreement_id}/versions/compare",
    response_model=VersionCompareResponse,
)
async def compare_agreement_version_endpoints(
    agreement_id: UUID,
    from_version: int,
    to_version: int,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Diff two versions of an agreement (content + answers)."""
    await _load_owned_agreement(db, agreement_id, org_id)
    try:
        return await compare_agreement_versions(
            db, agreement_id, from_version, to_version
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/{agreement_id}/versions/{version_number}",
    response_model=AgreementVersionDetailResponse,
)
async def get_agreement_version(
    agreement_id: UUID,
    version_number: int,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single version including its answers snapshot."""
    await _load_owned_agreement(db, agreement_id, org_id)
    version = await get_version_by_number(db, agreement_id, version_number)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version_number} not found",
        )
    return version


@router.post(
    "/{agreement_id}/versions",
    response_model=AgreementVersionDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agreement_version(
    agreement_id: UUID,
    data: CreateVersionRequest = CreateVersionRequest(),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Snapshot the agreement's current state as a new immutable version.

    History is append-only: this never mutates existing versions.
    """
    agreement = await _load_owned_agreement(db, agreement_id, org_id)

    latest_content = ""
    latest = (
        await db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None and latest.content:
        latest_content = latest.content

    snapshot = data.data if data.data is not None else agreement.data
    version = await create_version(
        db=db,
        agreement=agreement,
        content=data.content if data.content is not None else latest_content,
        created_by=current_user.id,
        status="draft",
        data=snapshot,
        note=data.note or "Manual snapshot",
    )
    return version


@router.post(
    "/{agreement_id}/versions/{version_number}/restore",
    response_model=AgreementVersionDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def restore_agreement_version(
    agreement_id: UUID,
    version_number: int,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Restore a previous version by appending a new version with its content.

    The working agreement is reset to the restored answers; no existing
    version is modified.
    """
    agreement = await _load_owned_agreement(db, agreement_id, org_id)
    if agreement.status not in EDITABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agreement cannot be modified in current status",
        )
    if agreement_is_immutable(agreement):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Executed agreements cannot be edited directly; create an amendment",
        )

    version = await get_version_by_number(db, agreement_id, version_number)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version_number} not found",
        )

    restored = await restore_version(db, agreement, version, current_user.id)
    await db.flush()
    await db.refresh(restored)
    return restored


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
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get the questionnaire schema for a specific agreement type.

    Catalog types share a generic template_key, so questions must be
    resolved per type id, not per template (the template-key endpoint can
    only return one type's questions when many types share a template).

    Jurisdiction-dependent defaults (governing law, seat, institution) come
    from the organisation's jurisdiction record, never from code (spec §71).
    """
    from app.models.jurisdiction import Jurisdiction
    from app.models.organization import Organization
    from app.services.agreement_renderer import (
        apply_jurisdiction_defaults,
        normalize_questions_for_ui,
    )

    jurisdiction = (
        await db.execute(
            select(Jurisdiction)
            .join(Organization, Organization.country == Jurisdiction.code)
            .where(Organization.id == org_id, Jurisdiction.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()

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
        return normalize_questions_for_ui(
            apply_jurisdiction_defaults(atype.schema["questions"], jurisdiction)
        )

    # Type without a stored schema falls back to its template questionnaire.
    questions = get_template_questions(atype.template_key or "")
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No questionnaire defined for type '{atype.key}'",
        )
    return normalize_questions_for_ui(apply_jurisdiction_defaults(questions, jurisdiction))


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
    current_user: User = Depends(get_current_user),
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

    if agreement.status not in EDITABLE_STATES:
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

    # Spec §70: record where each answer came from. Values equal to the
    # template default stay SYSTEM_DEFAULT until the user confirms them.
    questions = await _questions_for(db, agreement)
    if data.source in (AnswerSource.EXTRACTED.value, AnswerSource.INFERRED.value):
        agreement.answer_provenance = tag_answers(
            agreement.answer_provenance, data.answers.keys(), AnswerSource(data.source)
        )
    else:
        agreement.answer_provenance = classify_user_update(
            agreement.answer_provenance, data.answers, questions
        )

    flag_modified(agreement, "data")
    flag_modified(agreement, "answer_provenance")
    await db.flush()
    await db.refresh(agreement)

    # Spec §26/§3: editing a draft appends version N+1 and never mutates
    # version N. Skip the append when the answers are byte-for-byte identical
    # to the latest snapshot so repeated saves / compliance checks don't
    # inflate history.
    from app.services.agreement_versioning import (
        calculate_data_hash,
        get_latest_version,
    )

    new_data_hash = calculate_data_hash(agreement.data)
    latest_version = await get_latest_version(db, agreement_id)
    latest_hash = (
        calculate_data_hash(latest_version.data) if latest_version is not None else None
    )
    if latest_version is None or new_data_hash != latest_hash:
        await create_version(
            db=db,
            agreement=agreement,
            content="",
            created_by=current_user.id,
            status="draft",
            data=agreement.data,
            note="Answers updated",
        )

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
        provenance=agreement.answer_provenance or {},
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
    questions = await _questions_for(db, agreement)
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
            # Spec §70: missing/assumed answers surface as findings instead of
            # being silently defaulted.
            "answers": data,
            "questions": questions,
            "provenance": agreement.answer_provenance or {},
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
