"""Bulk operations models for CSV import, mass operations, and batch processing."""
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid
import enum

from app.models.base import Base


class ImportStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ImportType(str, enum.Enum):
    AGREEMENTS = "agreements"
    POLICIES = "policies"
    CONTACTS = "contacts"
    TEMPLATES = "templates"
    OBLIGATIONS = "obligations"


class BulkActionType(str, enum.Enum):
    STATUS_CHANGE = "status_change"
    ASSIGN = "assign"
    DELETE = "delete"
    EXPORT = "export"
    COMPLIANCE_CHECK = "compliance_check"
    NOTIFY = "notify"
    ARCHIVE = "archive"


class BulkJob(Base):
    """Bulk operation job tracking."""
    __tablename__ = "bulk_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    job_type = Column(SQLEnum(BulkActionType), nullable=False)
    status = Column(SQLEnum(ImportStatus), default=ImportStatus.PENDING)

    # Target filters
    filter_criteria = Column(JSON, default=dict)  # e.g., {"status": "draft", "type": "nda"}

    # Execution tracking
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    successful_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)
    skipped_items = Column(Integer, default=0)

    # Results
    results = Column(JSON, default=dict)  # Detailed results per item
    errors = Column(JSON, default=list)  # List of errors
    error_log_url = Column(String(500))  # URL to full error log

    # Metadata
    options = Column(JSON, default=dict)  # Action-specific options
    priority = Column(Integer, default=0)  # Higher = processed first
    scheduled_at = Column(DateTime)  # For deferred execution
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

    # Relationships
    # Job detail views read items after awaits; keep eager (AsyncSession
    # cannot lazy-load without MissingGreenlet).
    items = relationship(
        "BulkJobItem",
        back_populates="bulk_job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class BulkJobItem(Base):
    """Individual item in a bulk job."""
    __tablename__ = "bulk_job_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bulk_job_id = Column(UUID(as_uuid=True), ForeignKey("bulk_jobs.id"), nullable=False)
    entity_type = Column(String(50), nullable=False)  # 'agreement', 'policy', etc.
    entity_id = Column(UUID(as_uuid=True), nullable=True)  # May be null for imports

    # For imports
    row_number = Column(Integer)
    raw_data = Column(JSON, default=dict)  # Original CSV row data
    parsed_data = Column(JSON, default=dict)  # Parsed/validated data

    # Status
    status = Column(SQLEnum(ImportStatus), default=ImportStatus.PENDING)
    error_message = Column(Text)
    warnings = Column(JSON, default=list)

    # Tracking
    processed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    # Relationships
    bulk_job = relationship("BulkJob", back_populates="items")


class ImportTemplate(Base):
    """CSV import templates for different entity types."""
    __tablename__ = "import_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)

    name = Column(String(100), nullable=False)
    description = Column(Text)
    import_type = Column(SQLEnum(ImportType), nullable=False)

    # Column definitions
    columns = Column(JSON, nullable=False)  # [{"name": "title", "type": "string", "required": true, "mapping": "agreement.title"}]
    sample_data = Column(JSON, default=list)  # Sample rows for template preview
    validation_rules = Column(JSON, default=dict)  # Custom validation rules
    transform_rules = Column(JSON, default=dict)  # Data transformation rules

    # Metadata
    is_system = Column(Boolean, default=False)  # System templates can't be deleted
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


class ExportJob(Base):
    """Export job tracking."""
    __tablename__ = "export_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    export_type = Column(String(50), nullable=False)  # 'agreements', 'compliance', 'audit'
    format = Column(String(10), nullable=False)  # 'csv', 'xlsx', 'pdf', 'json'

    # Filters
    filters = Column(JSON, default=dict)
    columns = Column(JSON, default=list)  # Columns to include

    # Status
    status = Column(SQLEnum(ImportStatus), default=ImportStatus.PENDING)
    file_url = Column(String(500))
    file_size = Column(Integer)  # bytes
    row_count = Column(Integer)

    # Scheduling
    is_scheduled = Column(Boolean, default=False)
    schedule_cron = Column(String(100))  # Cron expression
    next_run_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    completed_at = Column(DateTime)


class SavedFilter(Base):
    """Saved filter presets for quick access."""
    __tablename__ = "saved_filters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    name = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)  # 'agreement', 'obligation', etc.
    filters = Column(JSON, nullable=False)
    sort_by = Column(String(50))
    sort_order = Column(String(10), default="desc")

    is_shared = Column(Boolean, default=False)  # Visible to all org members
    use_count = Column(Integer, default=0)
    last_used_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
