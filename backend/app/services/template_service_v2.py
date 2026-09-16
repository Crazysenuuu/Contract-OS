import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.template import Template, TemplateVariable, TemplateVersion
from app.schemas.template import (
    TemplateCreate,
    TemplateUpdate,
    TemplateVersionCreate,
)


class TemplateNotFoundError(Exception):
    pass


async def get_template(db: AsyncSession, template_id: uuid.UUID, org_id: uuid.UUID | None = None) -> Template:
    stmt = (
        select(Template)
        .options(selectinload(Template.variables))
        .where(Template.id == template_id)
    )
    if org_id is not None:
        stmt = stmt.where((Template.organization_id == org_id) | (Template.is_system == True))
    
    result = await db.execute(stmt)
    template = result.scalars().first()
    
    if not template:
        raise TemplateNotFoundError(f"Template {template_id} not found")
        
    return template


async def get_templates(
    db: AsyncSession,
    org_id: uuid.UUID | None = None,
    include_system: bool = True,
    status: str | None = None,
    agreement_type_id: uuid.UUID | None = None,
) -> list[Template]:
    stmt = select(Template).options(selectinload(Template.variables))
    
    if org_id is not None:
        if include_system:
            stmt = stmt.where((Template.organization_id == org_id) | (Template.is_system == True))
        else:
            stmt = stmt.where(Template.organization_id == org_id)
            
    if status:
        stmt = stmt.where(Template.status == status)
        
    if agreement_type_id:
        stmt = stmt.where(Template.agreement_type_id == agreement_type_id)
        
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_template(
    db: AsyncSession,
    template_in: TemplateCreate,
    org_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> Template:
    db_template = Template(
        organization_id=org_id,
        created_by=user_id,
        name=template_in.name,
        description=template_in.description,
        jurisdiction=template_in.jurisdiction,
        language=template_in.language,
        status=template_in.status,
        is_system=template_in.is_system,
        agreement_type_id=template_in.agreement_type_id,
    )
    db.add(db_template)
    
    for idx, var_in in enumerate(template_in.variables):
        db_var = TemplateVariable(
            template=db_template,
            key=var_in.key,
            label=var_in.label,
            var_type=var_in.var_type,
            required=var_in.required,
            default_value=var_in.default_value,
            options=var_in.options,
            description=var_in.description,
            sort_order=var_in.sort_order if var_in.sort_order != 0 else idx,
        )
        db.add(db_var)
        
    await db.flush()
    await db.refresh(db_template, ["variables"])
    return db_template


async def update_template(
    db: AsyncSession,
    template_id: uuid.UUID,
    template_in: TemplateUpdate,
    org_id: uuid.UUID | None,
) -> Template:
    template = await get_template(db, template_id, org_id)
    
    update_data = template_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(template, field, value)
        
    await db.flush()
    return template


async def delete_template(
    db: AsyncSession,
    template_id: uuid.UUID,
    org_id: uuid.UUID | None,
) -> None:
    template = await get_template(db, template_id, org_id)
    await db.delete(template)
    await db.flush()


async def get_template_versions(
    db: AsyncSession,
    template_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> list[TemplateVersion]:
    # Ensure access
    await get_template(db, template_id, org_id)
    
    stmt = (
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version_number.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_template_version(
    db: AsyncSession,
    template_id: uuid.UUID,
    version_in: TemplateVersionCreate,
    org_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> TemplateVersion:
    # Ensure access
    await get_template(db, template_id, org_id)
    
    # Calculate hash if missing
    content_hash = version_in.content_hash
    if not content_hash:
        import hashlib
        content_hash = hashlib.sha256(version_in.content.encode("utf-8")).hexdigest()
        
    db_version = TemplateVersion(
        template_id=template_id,
        version_number=version_in.version_number,
        content=version_in.content,
        content_hash=content_hash,
        variables_snapshot=version_in.variables_snapshot,
        clauses_snapshot=version_in.clauses_snapshot,
        rules_snapshot=version_in.rules_snapshot,
        status=version_in.status,
        change_notes=version_in.change_notes,
        created_by=user_id,
        locked_at=datetime.now(timezone.utc) if version_in.status == "locked" else None,
    )
    db.add(db_version)
    await db.flush()
    return db_version
