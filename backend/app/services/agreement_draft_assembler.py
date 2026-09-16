"""Draft assembly from the clause library (spec 1.8.12, 1.8.16-1.8.17).

Resolves the agreement type's clause bindings into applicable approved
clause versions, renders their variables against server-built context and
records exactly which clause versions produced the draft.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.clause import AgreementVersionClause
from app.services.agreement_context import AgreementContextBuilder
from app.services.clause_applicability import ClauseApplicabilityService
from app.services.clause_renderer import ClauseRenderer


@dataclass
class AssembledClause:
    clause_id: object
    clause_version_id: object
    title: str
    content: str
    display_order: int


class MissingRequiredClauseError(ValueError):
    """A required clause binding has no applicable approved version."""


class AgreementDraftAssembler:
    def __init__(self):
        self.applicability = ClauseApplicabilityService()
        self.renderer = ClauseRenderer()
        self.context_builder = AgreementContextBuilder()

    async def assemble(
        self,
        db: AsyncSession,
        *,
        agreement: Agreement,
        agreement_data: dict,
        agreement_type_version_id=None,
    ) -> list[AssembledClause]:
        from app.models.clause import AgreementTypeClauseBinding

        binding_query = (
            select(AgreementTypeClauseBinding)
            .order_by(AgreementTypeClauseBinding.display_order)
        )
        if agreement_type_version_id is not None:
            binding_query = binding_query.where(
                AgreementTypeClauseBinding.agreement_type_version_id
                == agreement_type_version_id
            )
        else:
            binding_query = binding_query.where(
                AgreementTypeClauseBinding.agreement_type_id == agreement.agreement_type_id
            )

        result = await db.execute(binding_query)
        bindings = result.scalars().all()

        assembled: list[AssembledClause] = []

        for binding in bindings:
            clause_id = getattr(binding, "clause_id", None)
            if clause_id is None:
                continue

            applicable = await self.applicability.resolve_clause(
                db,
                clause_id=clause_id,
                jurisdiction_id=getattr(agreement, "jurisdiction_id", None),
                agreement_data=agreement_data,
            )

            if applicable is None:
                required = getattr(binding, "required", False)
                if required:
                    raise MissingRequiredClauseError(
                        "Required clause has no applicable approved version: "
                        f"clause_id={clause_id}"
                    )
                continue

            rendered = self.renderer.render(
                applicable.clause_version.content,
                agreement_data,
            )

            assembled.append(
                AssembledClause(
                    clause_id=binding.clause_id,
                    clause_version_id=applicable.clause_version.id,
                    title=applicable.clause_version.title,
                    content=rendered,
                    display_order=getattr(binding, "display_order", 0) or 0,
                )
            )

        return assembled

    async def build_context(self, db: AsyncSession, agreement: Agreement) -> dict:
        return await self.context_builder.build(db, agreement)


def assemble_document(clauses: list[AssembledClause]) -> str:
    """Ordered document text from assembled clauses (spec 1.8.16)."""
    ordered = sorted(clauses, key=lambda item: item.display_order)
    sections: list[str] = []
    for index, clause in enumerate(ordered, start=1):
        sections.append(f"{index}. {clause.title}\n\n{clause.content}")
    return "\n\n".join(sections)


async def record_version_clauses(
    db: AsyncSession,
    *,
    version: AgreementVersion,
    clauses: list[AssembledClause],
) -> None:
    """Persist clause provenance for a generated version (spec 1.8.17)."""
    for order, clause in enumerate(sorted(clauses, key=lambda c: c.display_order), start=1):
        db.add(
            AgreementVersionClause(
                agreement_version_id=version.id,
                clause_id=clause.clause_id,
                clause_version_id=clause.clause_version_id,
                display_order=order,
            )
        )
    await db.flush()


async def generate_draft(
    db: AsyncSession,
    agreement: Agreement,
    created_by,
) -> AgreementVersion:
    """Full clause-library draft generation for an agreement (spec 1.8.16).

    Real DB data -> context -> applicable approved clauses -> variables
    resolved -> assembled -> immutable version created -> hash -> provenance.
    """
    from app.services.agreement_versioning import create_version

    context = await AgreementContextBuilder().build(db, agreement)
    agreement_data = {**context, **(agreement.data or {})}

    assembler = AgreementDraftAssembler()
    clauses = await assembler.assemble(db, agreement=agreement, agreement_data=agreement_data)

    content = assemble_document(clauses)

    version = await create_version(
        db,
        agreement,
        content,
        created_by,
        status="draft",
    )

    await record_version_clauses(db, version=version, clauses=clauses)

    return version
