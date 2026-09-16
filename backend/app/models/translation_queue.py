"""Translation queue model for automated translation tasks."""
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models.base import Base


class QueueStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY = "retry"


class TranslationPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TranslationSource(str, enum.Enum):
    AUTO = "auto"  # Automatically queued
    MANUAL = "manual"  # Manually requested
    BULK = "bulk"  # Bulk operation
    SYNC = "sync"  # Version sync triggered


class TranslationQueueItem(Base):
    """Individual translation task in the queue."""
    __tablename__ = "translation_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)

    # Source content
    source_type = Column(String(50), nullable=False)  # 'agreement', 'clause', 'template', 'policy'
    source_id = Column(UUID(as_uuid=True), nullable=False)
    source_version = Column(Integer)

    # Target language
    target_language = Column(String(10), nullable=False)  # 'si', 'ta', 'zh', etc.
    source_language = Column(String(10), nullable=False, default="en")

    # Content to translate
    source_content = Column(Text, nullable=False)
    source_title = Column(String(500))

    # Translation result
    translated_content = Column(Text)
    translated_title = Column(String(500))

    # Status & priority
    status = Column(SQLEnum(QueueStatus), default=QueueStatus.PENDING)
    priority = Column(SQLEnum(TranslationPriority), default=TranslationPriority.NORMAL)
    source = Column(SQLEnum(TranslationSource), default=TranslationSource.AUTO)

    # Processing info
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    error_message = Column(Text)
    retry_after = Column(DateTime)

    # Quality
    quality_score = Column(Float)
    confidence_score = Column(Float)
    is_machine_translated = Column(Boolean, default=True)
    needs_review = Column(Boolean, default=True)

    # Assignment
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime)

    # Timestamps
    queued_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Extra metadata
    extra_data = Column(JSON, default=dict)
    # {
    #   "trigger": "version_created",
    #   "trigger_id": "version-uuid",
    #   "auto_approve": false,
    #   "notify_on_complete": true
    # }


class TranslationTemplate(Base):
    """Reusable translation templates for common content."""
    __tablename__ = "translation_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)

    name = Column(String(200), nullable=False)
    description = Column(Text)
    category = Column(String(50))  # 'clause', 'notice', 'template'

    # Source content
    source_language = Column(String(10), nullable=False, default="en")
    source_content = Column(Text, nullable=False)
    source_variables = Column(JSON, default=list)  # ["party_name", "date", "amount"]

    # Pre-translated versions
    translations = Column(JSON, default=dict)
    # {"si": "...", "ta": "...", "zh": "..."}

    # Usage
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime)

    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TranslationWorker(Base):
    """Background worker tracking for translation processing."""
    __tablename__ = "translation_workers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Worker info
    worker_id = Column(String(100), unique=True, nullable=False)
    hostname = Column(String(200))
    process_id = Column(Integer)

    # Status
    is_active = Column(Boolean, default=True)
    current_task_id = Column(UUID(as_uuid=True), ForeignKey("translation_queue.id"), nullable=True)

    # Stats
    tasks_completed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)
    avg_processing_time = Column(Float)  # seconds

    # Heartbeat
    last_heartbeat = Column(DateTime)
    started_at = Column(DateTime, default=datetime.utcnow)
