"""Bulk operations service for CSV import, export, and mass operations.

All methods are async and operate on an ``AsyncSession`` (FastAPI's default).
"""
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from uuid import UUID
import csv
import io
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.bulk_operations import (
    BulkJob, BulkJobItem, ImportTemplate, ExportJob, SavedFilter,
    ImportStatus, ImportType, BulkActionType
)
from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.models.company_policy import CompanyPolicy


class BulkOperationsService:
    """Service for bulk operations on agreements and other entities."""

    # CSV column mappings for imports
    IMPORT_MAPPINGS = {
        ImportType.AGREEMENTS: {
            "title": "title",
            "agreement_type": "agreement_type_id",
            "disclosing_party": "metadata.disclosing_party",
            "receiving_party": "metadata.receiving_party",
            "effective_date": "metadata.effective_date",
            "expiration_date": "metadata.expiration_date",
            "status": "status",
            "jurisdiction": "metadata.jurisdiction",
        },
        ImportType.POLICIES: {
            "name": "name",
            "description": "description",
            "category": "category",
            "clause_type": "clause_type",
            "keywords": "keywords",
            "severity": "severity_if_missing",
        },
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_bulk_job(
        self,
        organization_id: str,
        created_by: str,
        job_type: BulkActionType,
        filter_criteria: Dict = None,
        options: Dict = None
    ) -> BulkJob:
        """Create a new bulk operation job."""
        job = BulkJob(
            organization_id=organization_id,
            created_by=created_by,
            job_type=job_type,
            filter_criteria=filter_criteria or {},
            options=options or {},
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def import_from_csv(
        self,
        organization_id: str,
        created_by: str,
        import_type: ImportType,
        csv_content: str,
        template_id: Optional[str] = None
    ) -> BulkJob:
        """Import entities from CSV content."""
        # Get or use default template
        template = None
        if template_id:
            result = await self.db.execute(
                select(ImportTemplate).where(ImportTemplate.id == template_id)
            )
            template = result.scalar_one_or_none()

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)

        if not rows:
            raise ValueError("CSV file is empty")

        # Create bulk job
        job = await self.create_bulk_job(
            organization_id=organization_id,
            created_by=created_by,
            job_type=BulkActionType.STATUS_CHANGE,  # Will be updated
            options={"import_type": import_type.value, "template_id": template_id}
        )
        job.total_items = len(rows)

        # Create items for each row
        for i, row in enumerate(rows):
            # Map columns
            mapped_data = self._map_csv_row(row, import_type, template)

            # Validate row
            warnings = self._validate_row(mapped_data, import_type, template)

            item = BulkJobItem(
                bulk_job_id=job.id,
                entity_type=import_type.value,
                row_number=i + 1,
                raw_data=dict(row),
                parsed_data=mapped_data,
                status=ImportStatus.PENDING,
                warnings=warnings,
            )
            self.db.add(item)

        await self.db.commit()
        await self.db.refresh(job)

        # Process immediately
        await self._process_import_job(job)

        return job

    def _map_csv_row(
        self,
        row: Dict,
        import_type: ImportType,
        template: Optional[ImportTemplate]
    ) -> Dict:
        """Map CSV row to entity fields using template or defaults."""
        mapping = self.IMPORT_MAPPINGS.get(import_type, {})
        result = {}

        for csv_col, entity_field in mapping.items():
            if csv_col in row and row[csv_col]:
                value = row[csv_col]

                # Apply transforms from template
                if template and template.transform_rules and csv_col in template.transform_rules:
                    transform = template.transform_rules[csv_col]
                    if transform.get("type") == "date":
                        # Parse date string
                        try:
                            from datetime import datetime
                            value = datetime.strptime(value, transform.get("format", "%Y-%m-%d")).isoformat()
                        except ValueError:
                            pass
                    elif transform.get("type") == "number":
                        try:
                            value = float(value.replace(",", "").replace("$", ""))
                        except ValueError:
                            pass

                # Handle nested fields
                parts = entity_field.split(".")
                current = result
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value

        return result

    def _validate_row(
        self,
        data: Dict,
        import_type: ImportType,
        template: Optional[ImportTemplate]
    ) -> List[str]:
        """Validate a single row and return warnings."""
        warnings = []

        if import_type == ImportType.AGREEMENTS:
            if not data.get("title"):
                warnings.append("Missing title")
            if not data.get("agreement_type"):
                warnings.append("Missing agreement type")

        elif import_type == ImportType.POLICIES:
            if not data.get("name"):
                warnings.append("Missing policy name")
            if not data.get("category"):
                warnings.append("Missing category")

        # Apply template validation rules
        if template and template.validation_rules:
            for field, rules in template.validation_rules.items():
                if rules.get("required") and not self._get_nested(data, field):
                    warnings.append(f"Required field '{field}' is missing")
                if rules.get("min_length") and len(str(self._get_nested(data, field) or "")) < rules["min_length"]:
                    warnings.append(f"Field '{field}' too short (min: {rules['min_length']})")

        return warnings

    def _get_nested(self, data: Dict, path: str):
        """Get value from nested dict using dot notation."""
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    async def _process_import_job(self, job: BulkJob):
        """Process all items in an import job."""
        job.status = ImportStatus.PROCESSING
        job.started_at = datetime.utcnow()
        await self.db.commit()

        items_result = await self.db.execute(
            select(BulkJobItem).where(BulkJobItem.bulk_job_id == job.id)
        )
        items = items_result.scalars().all()

        import_type = ImportType(job.options.get("import_type", "agreements"))

        for item in items:
            try:
                if import_type == ImportType.AGREEMENTS:
                    entity = await self._create_agreement_from_import(
                        item.parsed_data, job.organization_id, job.created_by
                    )
                elif import_type == ImportType.POLICIES:
                    entity = await self._create_policy_from_import(item.parsed_data, job.organization_id)
                else:
                    raise ValueError(f"Unsupported import type: {import_type}")

                item.entity_id = entity.id if entity else None
                item.status = ImportStatus.COMPLETED
                job.successful_items += 1

            except Exception as e:
                item.status = ImportStatus.FAILED
                item.error_message = str(e)
                job.failed_items += 1

            item.processed_at = datetime.utcnow()
            job.processed_items += 1
            await self.db.commit()

        # Final status
        if job.failed_items == 0:
            job.status = ImportStatus.COMPLETED
        elif job.successful_items == 0:
            job.status = ImportStatus.FAILED
        else:
            job.status = ImportStatus.PARTIAL

        job.completed_at = datetime.utcnow()
        await self.db.commit()

    async def _create_agreement_from_import(self, data: Dict, organization_id: str, created_by: str) -> Agreement:
        """Create an agreement from imported data."""
        from app.models.agreement_type import AgreementType

        agreement_type_id = None
        type_ref = data.get("agreement_type_id")
        if isinstance(type_ref, str):
            type_result = await self.db.execute(
                select(AgreementType.id).where(AgreementType.key == type_ref)
            )
            agreement_type_id = type_result.scalar_one_or_none()
        if agreement_type_id is None:
            # Fall back to a default active type so import never hard-fails
            # purely on a missing/invalid type key.
            default_result = await self.db.execute(
                select(AgreementType.id)
                .where(AgreementType.status == "active")
                .limit(1)
            )
            agreement_type_id = default_result.scalar_one_or_none()

        now = datetime.utcnow()
        agreement = Agreement(
            title=data.get("title", "Imported Agreement"),
            agreement_type_id=agreement_type_id,
            organization_id=organization_id,
            created_by=created_by,
            status=data.get("status", "draft"),
            data=data.get("metadata") or {},
            created_at=now,
            updated_at=now,
        )
        self.db.add(agreement)
        await self.db.commit()
        await self.db.refresh(agreement)
        return agreement

    async def _create_policy_from_import(self, data: Dict, organization_id: str) -> CompanyPolicy:
        """Create a policy from imported data."""
        policy = CompanyPolicy(
            name=data.get("name", "Imported Policy"),
            description=data.get("description", ""),
            category=data.get("category", "general"),
            clause_type=data.get("clause_type", "standard"),
            keywords=data.get("keywords", []),
            severity_if_missing=data.get("severity", "medium"),
            organization_id=organization_id,
        )
        self.db.add(policy)
        await self.db.commit()
        await self.db.refresh(policy)
        return policy

    # ===== EXPORT =====

    async def export_agreements(
        self,
        organization_id: str,
        created_by: str,
        filters: Dict = None,
        columns: List[str] = None,
        format: str = "csv"
    ) -> ExportJob:
        """Export agreements to CSV/XLSX."""
        job = ExportJob(
            organization_id=organization_id,
            created_by=created_by,
            export_type="agreements",
            format=format,
            filters=filters or {},
            columns=columns or ["title", "status", "created_at"],
            status=ImportStatus.PROCESSING,
        )
        self.db.add(job)
        await self.db.commit()

        try:
            # Query agreements
            query = select(Agreement).where(Agreement.organization_id == organization_id)

            # Apply filters
            if filters:
                if filters.get("status"):
                    query = query.where(Agreement.status == filters["status"])
                if filters.get("type_id"):
                    query = query.where(Agreement.agreement_type_id == filters["type_id"])
                if filters.get("created_after"):
                    query = query.where(Agreement.created_at >= filters["created_after"])

            result = await self.db.execute(query)
            agreements = result.scalars().all()
            job.row_count = len(agreements)

            # Generate CSV content
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=job.columns)
            writer.writeheader()

            for agreement in agreements:
                row = {}
                for col in job.columns:
                    if col == "title":
                        row[col] = agreement.title
                    elif col == "status":
                        row[col] = agreement.status
                    elif col == "created_at":
                        row[col] = agreement.created_at.isoformat() if agreement.created_at else ""
                    elif col == "updated_at":
                        row[col] = agreement.updated_at.isoformat() if agreement.updated_at else ""
                    elif col.startswith("metadata."):
                        key = col.split(".", 1)[1]
                        row[col] = str((agreement.data or {}).get(key, ""))
                    else:
                        row[col] = ""
                writer.writerow(row)

            # Store content temporarily
            job.file_url = f"exports/{job.id}.{format}"
            job.status = ImportStatus.COMPLETED
            job.completed_at = datetime.utcnow()

        except Exception as e:
            job.status = ImportStatus.FAILED
            job.errors = [{"error": str(e)}]

        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def get_csv_content(self, export_job: ExportJob) -> str:
        """Generate CSV content from export job."""
        output = io.StringIO()

        # Re-run the export query
        result = await self.db.execute(
            select(Agreement).where(Agreement.organization_id == export_job.organization_id)
        )
        agreements = result.scalars().all()
        writer = csv.DictWriter(output, fieldnames=export_job.columns)
        writer.writeheader()

        for agreement in agreements:
            row = {}
            for col in export_job.columns:
                if col == "title":
                    row[col] = agreement.title
                elif col == "status":
                    row[col] = agreement.status
                elif col == "created_at":
                    row[col] = agreement.created_at.isoformat() if agreement.created_at else ""
                else:
                    row[col] = ""
            writer.writerow(row)

        return output.getvalue()

    # ===== MASS OPERATIONS =====

    async def execute_bulk_action(
        self,
        job: BulkJob
    ) -> BulkJob:
        """Execute a mass action on filtered entities."""
        job.status = ImportStatus.PROCESSING
        job.started_at = datetime.utcnow()
        await self.db.commit()

        action = job.job_type
        criteria = job.filter_criteria

        try:
            if action == BulkActionType.STATUS_CHANGE:
                count = await self._bulk_status_change(criteria, job.options.get("new_status"), job.organization_id)
            elif action == BulkActionType.ASSIGN:
                count = await self._bulk_assign(criteria, job.options.get("assignee_id"), job.organization_id)
            elif action == BulkActionType.DELETE:
                count = await self._bulk_delete(criteria, job.organization_id)
            elif action == BulkActionType.ARCHIVE:
                count = await self._bulk_archive(criteria, job.organization_id)
            elif action == BulkActionType.COMPLIANCE_CHECK:
                count = await self._bulk_compliance_check(criteria, job.organization_id)
            else:
                raise ValueError(f"Unsupported action: {action}")

            job.total_items = count
            job.processed_items = count
            job.successful_items = count
            job.status = ImportStatus.COMPLETED

        except Exception as e:
            job.status = ImportStatus.FAILED
            job.errors = [{"error": str(e)}]

        job.completed_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def _bulk_status_change(self, criteria: Dict, new_status: str, org_id: str) -> int:
        """Bulk change status of agreements."""
        query = select(Agreement).where(Agreement.organization_id == org_id)

        if criteria.get("status"):
            query = query.where(Agreement.status == criteria["status"])

        result = await self.db.execute(query)
        agreements = result.scalars().all()
        for agreement in agreements:
            agreement.status = new_status
            agreement.updated_at = datetime.utcnow()

        await self.db.commit()
        return len(agreements)

    async def _bulk_assign(self, criteria: Dict, assignee_id: str, org_id: str) -> int:
        """Bulk assign agreements to a user."""
        query = select(Agreement).where(Agreement.organization_id == org_id)

        if criteria.get("status"):
            query = query.where(Agreement.status == criteria["status"])

        result = await self.db.execute(query)
        agreements = result.scalars().all()
        for agreement in agreements:
            agreement.metadata = {**(agreement.metadata or {}), "assigned_to": assignee_id}
            agreement.updated_at = datetime.utcnow()

        await self.db.commit()
        return len(agreements)

    async def _bulk_delete(self, criteria: Dict, org_id: str) -> int:
        """Bulk soft-delete agreements."""
        query = select(Agreement).where(Agreement.organization_id == org_id)

        if criteria.get("status"):
            query = query.where(Agreement.status == criteria["status"])

        result = await self.db.execute(query)
        agreements = result.scalars().all()
        count = len(agreements)

        for agreement in agreements:
            agreement.status = "deleted"
            agreement.updated_at = datetime.utcnow()

        await self.db.commit()
        return count

    async def _bulk_archive(self, criteria: Dict, org_id: str) -> int:
        """Bulk archive agreements."""
        return await self._bulk_status_change(criteria, "archived", org_id)

    async def _bulk_compliance_check(self, criteria: Dict, org_id: str) -> int:
        """Trigger compliance checks for multiple agreements."""
        from app.services.compliance_service import ComplianceService

        query = select(Agreement).where(Agreement.organization_id == org_id)

        if criteria.get("status"):
            query = query.where(Agreement.status == criteria["status"])

        result = await self.db.execute(query)
        agreements = result.scalars().all()
        compliance_service = ComplianceService(self.db)

        count = 0
        for agreement in agreements:
            try:
                await compliance_service.check_compliance(
                    UUID(str(agreement.id)),
                    UUID(str(org_id)),
                )
                count += 1
            except Exception:
                pass  # Continue with other agreements

        return count

    # ===== SAVED FILTERS =====

    async def create_saved_filter(
        self,
        organization_id: str,
        created_by: str,
        name: str,
        entity_type: str,
        filters: Dict,
        sort_by: str = None,
        sort_order: str = "desc",
        is_shared: bool = False
    ) -> SavedFilter:
        """Create a saved filter preset."""
        saved_filter = SavedFilter(
            organization_id=organization_id,
            created_by=created_by,
            name=name,
            entity_type=entity_type,
            filters=filters,
            sort_by=sort_by,
            sort_order=sort_order,
            is_shared=is_shared,
        )
        self.db.add(saved_filter)
        await self.db.commit()
        await self.db.refresh(saved_filter)
        return saved_filter

    async def get_saved_filters(self, organization_id: str, user_id: str) -> List[SavedFilter]:
        """Get saved filters for a user (own + shared)."""
        result = await self.db.execute(
            select(SavedFilter)
            .where(
                or_(
                    SavedFilter.created_by == user_id,
                    and_(
                        SavedFilter.organization_id == organization_id,
                        SavedFilter.is_shared == True,  # noqa: E712
                    )
                )
            )
            .order_by(SavedFilter.use_count.desc())
        )
        return result.scalars().all()