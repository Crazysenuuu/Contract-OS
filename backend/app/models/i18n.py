"""Internationalization models for multi-language contract support."""
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models.base import Base


class LanguageDirection(str, enum.Enum):
    LTR = "ltr"  # Left to right (English, French, etc.)
    RTL = "rtl"  # Right to left (Arabic, Hebrew, etc.)


class TranslationStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Language(Base):
    """Supported languages for contracts."""
    __tablename__ = "languages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Language info
    code = Column(String(10), unique=True, nullable=False)  # ISO 639-1: en, si, ta, zh, ar
    name = Column(String(100), nullable=False)  # English, Sinhala, Tamil
    native_name = Column(String(100))  # සිංහල, தமிழ்
    locale = Column(String(20))  # en-US, si-LK, ta-LK

    # Direction
    direction = Column(SQLEnum(LanguageDirection), default=LanguageDirection.LTR)

    # Features
    has_numbers_system = Column(Boolean, default=True)  # Western numerals vs others
    has_date_format = Column(String(20))  # DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD
    has_currency = Column(String(10))  # LKR, USD, SGD

    # Legal system support
    legal_systems = Column(JSON, default=list)  # ["common_law", "civil_law", "mixed"]
    supported_jurisdictions = Column(JSON, default=list)  # ["LK", "SG", "US"]

    # Status
    is_active = Column(Boolean, default=True)
    is_beta = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    translations = relationship("Translation", back_populates="language")


class Translation(Base):
    """Translation strings for UI and contract content."""
    __tablename__ = "translations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    language_id = Column(UUID(as_uuid=True), ForeignKey("languages.id"), nullable=False)

    # Translation key
    namespace = Column(String(50), nullable=False)  # 'ui', 'contract', 'email', 'clause'
    key = Column(String(200), nullable=False)  # 'confidentiality.title', 'button.save'
    context = Column(String(200))  # Additional context for translators

    # Content
    value = Column(Text, nullable=False)  # Translated text
    plural_form = Column(String(20))  # 'zero', 'one', 'other' (for pluralization)
    variables = Column(JSON, default=list)  # ["name", "date", "amount"] - template variables

    # Quality
    status = Column(SQLEnum(TranslationStatus), default=TranslationStatus.DRAFT)
    confidence_score = Column(Integer)  # 0-100: machine translation confidence
    is_machine_translated = Column(Boolean, default=False)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime)

    # Metadata
    version = Column(Integer, default=1)
    notes = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    language = relationship("Language", back_populates="translations")

    __table_args__ = (
        # Unique constraint: one translation per language/namespace/key/plural
        {'extend_existing': True}
    )


class LocalizedContent(Base):
    """Localized content for agreements and clauses."""
    __tablename__ = "localized_content"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    language_id = Column(UUID(as_uuid=True), ForeignKey("languages.id"), nullable=False)

    # Source reference
    source_type = Column(String(50), nullable=False)  # 'agreement', 'clause', 'template', 'policy'
    source_id = Column(UUID(as_uuid=True), nullable=False)

    # Translation info
    title = Column(String(500))
    content = Column(Text)  # Full translated content
    summary = Column(Text)  # Brief summary in target language

    # Status
    status = Column(SQLEnum(TranslationStatus), default=TranslationStatus.DRAFT)
    quality_score = Column(Integer)  # 0-100
    is_auto_translated = Column(Boolean, default=False)

    # Version tracking
    source_version = Column(Integer)  # Version of source content
    translation_version = Column(Integer, default=1)
    last_synced_at = Column(DateTime)  # When last synced with source

    # Translator info
    translated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    language = relationship("Language")


class ContractClauseTranslation(Base):
    """Translations for specific contract clauses."""
    __tablename__ = "contract_clause_translations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clause_id = Column(UUID(as_uuid=True), ForeignKey("extracted_clauses.id"), nullable=False)
    language_id = Column(UUID(as_uuid=True), ForeignKey("languages.id"), nullable=False)

    # Translated content
    title = Column(String(200))
    text = Column(Text, nullable=False)
    explanation = Column(Text)  # Plain language explanation

    # Legal terms mapping
    legal_terms = Column(JSON, default=dict)
    # {"confidentiality": "රහස්‍යභාවය", "disclosure": "හෙළිදරව් කිරීම"}

    # Quality
    status = Column(SQLEnum(TranslationStatus), default=TranslationStatus.DRAFT)
    quality_score = Column(Integer)

    # Metadata
    translator_notes = Column(Text)
    review_notes = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GlossaryTerm(Base):
    """Legal glossary for consistent terminology across translations."""
    __tablename__ = "glossary_terms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)  # null = system

    # Term
    source_language = Column(String(10), nullable=False)  # en
    source_term = Column(String(200), nullable=False)  # Confidentiality

    # Translations
    translations = Column(JSON, nullable=False)
    # {"si": "රහස්‍යභාවය", "ta": "இரகசியம்", "zh": "保密"}

    # Context
    category = Column(String(50))  # 'legal', 'business', 'technical'
    definition = Column(Text)  # Definition in source language
    usage_notes = Column(Text)  # When/how to use this term
    examples = Column(JSON, default=list)  # Example usages

    # Status
    is_approved = Column(Boolean, default=False)
    usage_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentLocale(Base):
    """Locale settings for a specific document/agreement."""
    __tablename__ = "document_locales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("agreements.id"), unique=True, nullable=False)

    # Language settings
    primary_language = Column(String(10), nullable=False)  # en
    secondary_languages = Column(JSON, default=list)  # ["si", "ta"]
    translation_mode = Column(String(20), default="side_by_side")  # 'side_by_side', 'tabbed', 'primary_only'

    # Formatting
    number_format = Column(String(20), default="western")  # 'western', 'arabic', 'devanagari'
    date_format = Column(String(20), default="DD/MM/YYYY")
    currency_format = Column(String(20), default="LKR ##,###")
    time_format = Column(String(20), default="24h")

    # Legal
    governing_language = Column(String(10))  # Which language version governs in case of conflict
    show_governing_clause = Column(Boolean, default=True)
    governing_clause_text = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
