"""Document intelligence models for clause extraction, smart tagging, and clause library."""
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models.base import Base


class ClauseCategory(str, enum.Enum):
    CONFIDENTIALITY = "confidentiality"
    INDEMNIFICATION = "indemnification"
    LIABILITY = "liability"
    TERMINATION = "termination"
    GOVERNING_LAW = "governing_law"
    DISPUTE_RESOLUTION = "dispute_resolution"
    IP_OWNERSHIP = "ip_ownership"
    IP_LICENSE = "ip_license"
    NON_COMPETE = "non_compete"
    NON_SOLICITATION = "non_solicitation"
    REPRESENTATIONS = "representations"
    WARRANTIES = "warranties"
    FORCE_MAJEURE = "force_majeure"
    SEVERABILITY = "severability"
    ENTIRE_AGREEMENT = "entire_agreement"
    AMENDMENT = "amendment"
    ASSIGNMENT = "assignment"
    NOTICES = "notices"
    SURVIVAL = "survival"
    DEFINITIONS = "definitions"
    SCOPE = "scope"
    PAYMENT = "payment"
    TERM = "term"
    INSURANCE = "insurance"
    DATA_PROTECTION = "data_protection"
    AUDIT_RIGHTS = "audit_rights"
    OTHER = "other"


class ClauseRiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClauseSentiment(str, enum.Enum):
    FAVORABLE = "favorable"  # Favorable to your party
    NEUTRAL = "neutral"
    UNFAVORABLE = "unfavorable"
    MIXED = "mixed"


class ExtractedClause(Base):
    """Extracted clause from a contract (AI analysis artifact).

    Renamed from 'clauses' to 'extracted_clauses': the canonical 'clauses'
    table now belongs to the approved clause library (spec 1.8).
    """
    __tablename__ = "extracted_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("agreements.id"), nullable=False)
    version_id = Column(UUID(as_uuid=True), ForeignKey("agreement_versions.id"), nullable=True)

    # Clause content
    text = Column(Text, nullable=False)
    title = Column(String(200))
    section_number = Column(String(50))  # e.g., "3.2", "Article IV"

    # Classification
    category = Column(SQLEnum(ClauseCategory), nullable=False)
    subcategory = Column(String(100))
    tags = Column(JSON, default=list)  # ["mutual", "unlimited", "broad-scope"]

    # AI analysis
    risk_level = Column(SQLEnum(ClauseRiskLevel))
    risk_score = Column(Float)  # 0.0 to 1.0
    risk_explanation = Column(Text)
    sentiment = Column(SQLEnum(ClauseSentiment))
    sentiment_score = Column(Float)  # -1.0 to 1.0

    # Key entities extracted
    key_entities = Column(JSON, default=dict)
    # {
    #   "monetary_values": ["$1,000,000", "$50,000"],
    #   "dates": ["December 31, 2025"],
    #   "durations": ["2 years", "90 days"],
    #   "parties": ["ABC Corp", "XYZ Inc"],
    #   "obligations": ["shall maintain insurance", "must provide notice"]
    # }

    # Comparison with standard
    deviation_score = Column(Float)  # How much it differs from standard
    standard_clause_id = Column(UUID(as_uuid=True), ForeignKey("extracted_clauses.id"), nullable=True)  # Matched standard clause

    # Position in document
    start_char = Column(Integer)
    end_char = Column(Integer)
    page_number = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ClauseLibrary(Base):
    """Reusable clause templates in the clause library."""
    __tablename__ = "clause_library"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)  # null = system clause

    # Content
    title = Column(String(200), nullable=False)
    text = Column(Text, nullable=False)
    description = Column(Text)

    # Classification
    category = Column(SQLEnum(ClauseCategory), nullable=False)
    subcategory = Column(String(100))
    tags = Column(JSON, default=list)
    jurisdictions = Column(JSON, default=list)  # ["LK", "SG", "US"]
    agreement_types = Column(JSON, default=list)  # ["nda", "msa", "sow"]

    # Risk profile
    risk_level = Column(SQLEnum(ClauseRiskLevel))
    risk_score = Column(Float)
    favorable_for = Column(String(50))  # "disclosing", "receiving", "mutual", "neutral"
    usage_count = Column(Integer, default=0)

    # Metadata
    version = Column(Integer, default=1)
    is_system = Column(Boolean, default=False)  # System clauses can't be deleted
    is_approved = Column(Boolean, default=False)  # Approved by legal team
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime)

    # Governance lifecycle (spec 24.8): 'active', 'deprecated', 'archived'.
    # Only users with clause.publish permission may change this.
    lifecycle_status = Column(String(30), nullable=False, default="active")
    published_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    published_at = Column(DateTime)
    deprecation_reason = Column(Text)

    # Alternatives
    alternative_ids = Column(JSON, default=list)  # IDs of alternative clauses
    parent_id = Column(UUID(as_uuid=True), nullable=True)  # For clause versions

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SmartTag(Base):
    """Smart tags for auto-classifying contract content."""
    __tablename__ = "smart_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)

    name = Column(String(100), nullable=False)
    description = Column(Text)
    color = Column(String(7))  # Hex color
    icon = Column(String(50))

    # Matching rules
    keywords = Column(JSON, default=list)  # Keywords that trigger this tag
    patterns = Column(JSON, default=list)  # Regex patterns
    categories = Column(JSON, default=list)  # Clause categories to match
    min_risk_score = Column(Float)  # Minimum risk score to trigger

    # Stats
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime)

    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ClauseSimilarity(Base):
    """Tracks similarity between clauses for deduplication and comparison."""
    __tablename__ = "clause_similarities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clause_id_1 = Column(UUID(as_uuid=True), ForeignKey("extracted_clauses.id"), nullable=False)
    clause_id_2 = Column(UUID(as_uuid=True), ForeignKey("extracted_clauses.id"), nullable=False)

    similarity_score = Column(Float, nullable=False)  # 0.0 to 1.0
    semantic_similarity = Column(Float)  # Meaning-based similarity
    text_similarity = Column(Float)  # Word-level similarity
    key_differences = Column(JSON, default=list)  # Highlighted differences

    created_at = Column(DateTime, default=datetime.utcnow)
