"""Translation service for multi-language contract support."""
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, or_, select

from app.models.i18n import (
    Language, Translation, LocalizedContent, ContractClauseTranslation,
    GlossaryTerm, DocumentLocale, TranslationStatus, LanguageDirection
)


# Default languages to seed
DEFAULT_LANGUAGES = [
    {
        "code": "en",
        "name": "English",
        "native_name": "English",
        "locale": "en-US",
        "direction": LanguageDirection.LTR,
        "date_format": "MM/DD/YYYY",
        "currency": "USD",
        "legal_systems": ["common_law"],
        "supported_jurisdictions": ["US", "SG", "LK", "GB"],
    },
    {
        "code": "si",
        "name": "Sinhala",
        "native_name": "සිංහල",
        "locale": "si-LK",
        "direction": LanguageDirection.LTR,
        "date_format": "DD/MM/YYYY",
        "currency": "LKR",
        "legal_systems": ["mixed"],
        "supported_jurisdictions": ["LK"],
    },
    {
        "code": "ta",
        "name": "Tamil",
        "native_name": "தமிழ்",
        "locale": "ta-LK",
        "direction": LanguageDirection.LTR,
        "date_format": "DD/MM/YYYY",
        "currency": "LKR",
        "legal_systems": ["mixed"],
        "supported_jurisdictions": ["LK", "IN"],
    },
    {
        "code": "zh",
        "name": "Chinese",
        "native_name": "中文",
        "locale": "zh-CN",
        "direction": LanguageDirection.LTR,
        "date_format": "YYYY-MM-DD",
        "currency": "CNY",
        "legal_systems": ["civil_law"],
        "supported_jurisdictions": ["CN", "SG"],
    },
    {
        "code": "ar",
        "name": "Arabic",
        "native_name": "العربية",
        "locale": "ar-SA",
        "direction": LanguageDirection.RTL,
        "date_format": "DD/MM/YYYY",
        "currency": "SAR",
        "legal_systems": ["civil_law", "sharia"],
        "supported_jurisdictions": ["SA", "AE", "QA"],
    },
    {
        "code": "hi",
        "name": "Hindi",
        "native_name": "हिन्दी",
        "locale": "hi-IN",
        "direction": LanguageDirection.LTR,
        "date_format": "DD/MM/YYYY",
        "currency": "INR",
        "legal_systems": ["common_law"],
        "supported_jurisdictions": ["IN"],
    },
    {
        "code": "ja",
        "name": "Japanese",
        "native_name": "日本語",
        "locale": "ja-JP",
        "direction": LanguageDirection.LTR,
        "date_format": "YYYY/MM/DD",
        "currency": "JPY",
        "legal_systems": ["civil_law"],
        "supported_jurisdictions": ["JP"],
    },
    {
        "code": "de",
        "name": "German",
        "native_name": "Deutsch",
        "locale": "de-DE",
        "direction": LanguageDirection.LTR,
        "date_format": "DD.MM.YYYY",
        "currency": "EUR",
        "legal_systems": ["civil_law"],
        "supported_jurisdictions": ["DE", "AT", "CH"],
    },
]

# Default UI translations
DEFAULT_UI_TRANSLATIONS = {
    "en": {
        "ui.app_name": "ContractOS",
        "ui.dashboard": "Dashboard",
        "ui.agreements": "Agreements",
        "ui.new_agreement": "New Agreement",
        "ui.analytics": "Analytics",
        "ui.settings": "Settings",
        "ui.login": "Login",
        "ui.register": "Register",
        "ui.logout": "Sign out",
        "ui.save": "Save",
        "ui.cancel": "Cancel",
        "ui.delete": "Delete",
        "ui.edit": "Edit",
        "ui.submit": "Submit",
        "ui.approve": "Approve",
        "ui.reject": "Reject",
        "ui.send": "Send",
        "ui.sign": "Sign",
        "ui.export": "Export",
        "ui.import": "Import",
        "ui.search": "Search",
        "ui.filter": "Filter",
        "ui.status": "Status",
        "ui.created": "Created",
        "ui.updated": "Updated",
        "ui.draft": "Draft",
        "ui.sent": "Sent",
        "ui.signed": "Signed",
        "ui.executed": "Executed",
        "ui.expired": "Expired",
        "ui.confidentiality": "Confidentiality",
        "ui.liability": "Liability",
        "ui.termination": "Termination",
        "ui.governing_law": "Governing Law",
        "ui.dispute_resolution": "Dispute Resolution",
        "contract.title": "Agreement Title",
        "contract.parties": "Parties",
        "contract.effective_date": "Effective Date",
        "contract.expiration_date": "Expiration Date",
        "contract.governing_law": "Governing Law",
        "contract.jurisdiction": "Jurisdiction",
    },
    "si": {
        "ui.app_name": "ContractOS",
        "ui.dashboard": "උපකරණ පුවරුව",
        "ui.agreements": "ගිවිසුම්",
        "ui.new_agreement": "නව ගිවිසුම",
        "ui.analytics": "විශ්ලේෂණ",
        "ui.settings": "සැකසුම්",
        "ui.login": "පිවිසෙන්න",
        "ui.register": "ලියාපදිංචි වන්න",
        "ui.logout": "පිටවන්න",
        "ui.save": "සුරකින්න",
        "ui.cancel": "අවලංගු කරන්න",
        "ui.delete": "මකන්න",
        "ui.edit": "සංස්කරණය",
        "ui.submit": "ඉදිරිපත් කරන්න",
        "ui.approve": "අනුමත කරන්න",
        "ui.reject": "ප්‍රතික්ෂේප කරන්න",
        "ui.send": "යවන්න",
        "ui.sign": "අත්සන් කරන්න",
        "ui.export": "අපනයනය",
        "ui.import": "ආයාතය",
        "ui.search": "සොයන්න",
        "ui.filter": "පෙරහන",
        "ui.status": "තත්ත්වය",
        "ui.created": "සාදන ලදි",
        "ui.updated": "යාවත්කාලීන කළා",
        "ui.draft": "කෙටුම්පත",
        "ui.sent": "යැවුවා",
        "ui.signed": "අත්සන් කළා",
        "ui.executed": "ක්‍රියාත්මක කළා",
        "ui.expired": "කල් ඉකුත් වුවා",
        "contract.title": "ගිවිසුමේ මාතෘකාව",
        "contract.parties": "පාර්ශ්වයන්",
        "contract.effective_date": "බලපැවැත්මේ දිනය",
        "contract.expiration_date": "කල් ඉකුත්වීමේ දිනය",
    },
    "ta": {
        "ui.app_name": "ContractOS",
        "ui.dashboard": "கட்டுப்பாட்டு பலகை",
        "ui.agreements": "ஒப்பந்தங்கள்",
        "ui.new_agreement": "புதிய ஒப்பந்தம்",
        "ui.analytics": "பகுப்பாய்வு",
        "ui.settings": "அமைப்புகள்",
        "ui.login": "உள்நுழை",
        "ui.register": "பதிவு செய்",
        "ui.logout": "வெளியேறு",
        "ui.save": "சேமி",
        "ui.cancel": "ரத்து செய்",
        "ui.delete": "நீக்கு",
        "ui.edit": "திருத்து",
        "ui.submit": "சமர்ப்பி",
        "ui.approve": "ஏற்றுக்கொள்",
        "ui.reject": "நிராகரி",
        "ui.send": "அனுப்பு",
        "ui.sign": "கையெழுத்திடு",
        "contract.title": "ஒப்பந்தத்தின் தலைப்பு",
        "contract.parties": "தரப்பினர்",
        "contract.effective_date": "நடைமுறைக்கு வரும் தேதி",
        "contract.expiration_date": "காலாவதியாகும் தேதி",
    },
}

# Default legal glossary
DEFAULT_GLOSSARY = [
    {
        "source_term": "Confidentiality",
        "category": "legal",
        "definition": "The state of keeping information secret or private",
        "translations": {
            "si": "රහස්‍යභාවය",
            "ta": "இரகசியம்",
            "zh": "保密",
            "ar": "سرية",
            "hi": "गोपनीयता",
            "ja": "機密性",
            "de": "Vertraulichkeit",
        },
    },
    {
        "source_term": "Indemnification",
        "category": "legal",
        "definition": "The action of compensating for harm or loss",
        "translations": {
            "si": "නිදොස් කිරීම",
            "ta": "இழப்பீடு",
            "zh": "赔偿",
            "ar": " تعويض",
            "hi": "क्षतिपूर्ति",
            "ja": "補償",
            "de": "Freistellung",
        },
    },
    {
        "source_term": "Liability",
        "category": "legal",
        "definition": "The state of being legally responsible",
        "translations": {
            "si": "වගකීම",
            "ta": "பொறுப்பு",
            "zh": "责任",
            "ar": "مسؤولية",
            "hi": "दायित्व",
            "ja": "責任",
            "de": "Haftung",
        },
    },
    {
        "source_term": "Termination",
        "category": "legal",
        "definition": "The action of ending an agreement or contract",
        "translations": {
            "si": "අවසන් කිරීම",
            "ta": "முடிவு",
            "zh": "终止",
            "ar": "إنهاء",
            "hi": "समाप्ति",
            "ja": "終了",
            "de": "Kündigung",
        },
    },
    {
        "source_term": "Governing Law",
        "category": "legal",
        "definition": "The jurisdiction whose laws will be used to interpret the contract",
        "translations": {
            "si": "පාලන නීතිය",
            "ta": "ஆளும் சட்டம்",
            "zh": "适用法律",
            "ar": "القانون الحاكم",
            "hi": "शासी कानून",
            "ja": "準拠法",
            "de": "Anwendbares Recht",
        },
    },
    {
        "source_term": "Dispute Resolution",
        "category": "legal",
        "definition": "The process of resolving disagreements between parties",
        "translations": {
            "si": "ආරවුල් විසඳීම",
            "ta": "முரண்பாடு தீர்வு",
            "zh": "争议解决",
            "ar": "حل النزاعات",
            "hi": "विवाद समाधान",
            "ja": "紛争解決",
            "de": "Streitbeilegung",
        },
    },
    {
        "source_term": "Force Majeure",
        "category": "legal",
        "definition": "Unforeseeable circumstances that prevent fulfillment of a contract",
        "translations": {
            "si": "බලකා ඇති කරන සිදුවීම්",
            "ta": "ஆற்றல் மிகு நிகழ்வு",
            "zh": "不可抗力",
            "ar": "القوة القاهرة",
            "hi": "अप्रत्याशित परिस्थिति",
            "ja": "不可抗力",
            "de": "Höhere Gewalt",
        },
    },
    {
        "source_term": "Intellectual Property",
        "category": "legal",
        "definition": "Creations of the mind protected by law",
        "translations": {
            "si": "බුද්ධිමය දේපළ",
            "ta": "அறிவுசார் சொத்து",
            "zh": "知识产权",
            "ar": "المالية الفكرية",
            "hi": "बौद्धिक संपदा",
            "ja": "知的財産",
            "de": "Geistiges Eigentum",
        },
    },
]


class TranslationService:
    """Service for managing translations and multi-language support."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ===== LANGUAGE MANAGEMENT =====

    async def seed_languages(self) -> int:
        """Seed default languages if not present."""
        count = 0
        for lang_data in DEFAULT_LANGUAGES:
            existing = await self.get_language(lang_data["code"])

            if not existing:
                lang = Language(
                    code=lang_data["code"],
                    name=lang_data["name"],
                    native_name=lang_data.get("native_name"),
                    locale=lang_data.get("locale"),
                    direction=lang_data.get("direction", LanguageDirection.LTR),
                    has_date_format=lang_data.get("date_format"),
                    has_currency=lang_data.get("currency"),
                    legal_systems=lang_data.get("legal_systems", []),
                    supported_jurisdictions=lang_data.get("supported_jurisdictions", []),
                )
                self.db.add(lang)
                count += 1

        self.db.commit()
        return count

    async def get_languages(self, active_only: bool = True) -> List[Language]:
        """Get all supported languages."""
        stmt = select(Language)
        if active_only:
            stmt = stmt.where(Language.is_active == True)  # noqa: E712
        stmt = stmt.order_by(Language.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_language(self, code: str) -> Optional[Language]:
        """Get language by code."""
        result = await self.db.execute(
            select(Language).where(Language.code == code)
        )
        return result.scalar_one_or_none()

    def detect_language(self, text: str) -> Optional[str]:
        """Simple language detection based on character ranges."""
        if not text:
            return None

        # Check character ranges
        for char in text[:1000]:  # Sample first 1000 chars
            code = ord(char)

            # Sinhala: 0D80-0DFF
            if 0x0D80 <= code <= 0x0DFF:
                return "si"

            # Tamil: 0B80-0BFF
            if 0x0B80 <= code <= 0x0BFF:
                return "ta"

            # Arabic: 0600-06FF
            if 0x0600 <= code <= 0x06FF:
                return "ar"

            # Chinese: 4E00-9FFF
            if 0x4E00 <= code <= 0x9FFF:
                return "zh"

            # Japanese Hiragana: 3040-309F
            if 0x3040 <= code <= 0x309F:
                return "ja"

            # Japanese Katakana: 30A0-30FF
            if 0x30A0 <= code <= 0x30FF:
                return "ja"

            # Devanagari (Hindi): 0900-097F
            if 0x0900 <= code <= 0x097F:
                return "hi"

        # Default to English for Latin characters
        return "en"

    # ===== TRANSLATION MANAGEMENT =====

    async def seed_ui_translations(self) -> int:
        """Seed default UI translations."""
        count = 0

        for lang_code, translations in DEFAULT_UI_TRANSLATIONS.items():
            language = await self.get_language(lang_code)
            if not language:
                continue

            for key, value in translations.items():
                parts = key.split(".", 1)
                namespace = parts[0] if len(parts) > 1 else "ui"
                trans_key = parts[1] if len(parts) > 1 else key

                existing = await self.db.execute(
                    select(Translation).where(
                        Translation.language_id == language.id,
                        Translation.namespace == namespace,
                        Translation.key == trans_key,
                    )
                )
                existing = existing.scalar_one_or_none()

                if not existing:
                    translation = Translation(
                        language_id=language.id,
                        namespace=namespace,
                        key=trans_key,
                        value=value,
                        status=TranslationStatus.PUBLISHED,
                    )
                    self.db.add(translation)
                    count += 1

        self.db.commit()
        return count

    async def seed_glossary(self) -> int:
        """Seed default legal glossary."""
        count = 0

        for term_data in DEFAULT_GLOSSARY:
            existing = await self.db.execute(
                select(GlossaryTerm).where(
                    GlossaryTerm.source_term == term_data["source_term"]
                )
            )
            existing = existing.scalar_one_or_none()

            if not existing:
                term = GlossaryTerm(
                    source_language="en",
                    source_term=term_data["source_term"],
                    category=term_data.get("category"),
                    definition=term_data.get("definition"),
                    translations=term_data.get("translations", {}),
                )
                self.db.add(term)
                count += 1

        self.db.commit()
        return count

    async def get_translation(self, namespace: str, key: str, language_code: str) -> Optional[str]:
        """Get a single translation."""
        language = await self.get_language(language_code)
        if not language:
            return None

        result = await self.db.execute(
            select(Translation).where(
                Translation.language_id == language.id,
                Translation.namespace == namespace,
                Translation.key == key,
                Translation.status == TranslationStatus.PUBLISHED,
            )
        )
        translation = result.scalar_one_or_none()

        if translation:
            return translation.value

        # Fallback to English
        if language_code != "en":
            return await self.get_translation(namespace, key, "en")

        return None

    async def get_translations(self, namespace: str, language_code: str) -> Dict[str, str]:
        """Get all translations for a namespace and language."""
        language = await self.get_language(language_code)
        if not language:
            # Fallback to English
            if language_code != "en":
                return await self.get_translations(namespace, "en")
            return {}

        result = await self.db.execute(
            select(Translation).where(
                Translation.language_id == language.id,
                Translation.namespace == namespace,
                Translation.status == TranslationStatus.PUBLISHED,
            )
        )
        translations = result.scalars().all()

        result = {}
        for t in translations:
            result[t.key] = t.value

        # Fill missing from English
        if language_code != "en":
            en_translations = await self.get_translations(namespace, "en")
            for key, value in en_translations.items():
                if key not in result:
                    result[key] = value

        return result

    async def set_translation(
        self,
        namespace: str,
        key: str,
        language_code: str,
        value: str,
        context: str = None
    ) -> Translation:
        """Set a translation."""
        language = await self.get_language(language_code)
        if not language:
            raise ValueError(f"Language '{language_code}' not found")

        result = await self.db.execute(
            select(Translation).where(
                Translation.language_id == language.id,
                Translation.namespace == namespace,
                Translation.key == key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
            existing.context = context or existing.context
            existing.status = TranslationStatus.DRAFT
            existing.version += 1
            existing.updated_at = datetime.utcnow()
            return existing

        translation = Translation(
            language_id=language.id,
            namespace=namespace,
            key=key,
            value=value,
            context=context,
            status=TranslationStatus.DRAFT,
        )
        self.db.add(translation)
        await self.db.commit()
        await self.db.refresh(translation)
        return translation

    # ===== CONTENT LOCALIZATION =====

    async def get_localized_content(
        self,
        source_type: str,
        source_id: str,
        language_code: str
    ) -> Optional[LocalizedContent]:
        """Get localized content for an entity."""
        language = await self.get_language(language_code)
        if not language:
            return None

        result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == source_type,
                LocalizedContent.source_id == source_id,
                LocalizedContent.language_id == language.id,
            )
        )
        return result.scalar_one_or_none()

    async def set_localized_content(
        self,
        source_type: str,
        source_id: str,
        language_code: str,
        title: str = None,
        content: str = None,
        summary: str = None,
        auto_translate: bool = False
    ) -> LocalizedContent:
        """Set localized content for an entity."""
        language = await self.get_language(language_code)
        if not language:
            raise ValueError(f"Language '{language_code}' not found")

        result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == source_type,
                LocalizedContent.source_id == source_id,
                LocalizedContent.language_id == language.id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if title:
                existing.title = title
            if content:
                existing.content = content
            if summary:
                existing.summary = summary
            existing.updated_at = datetime.utcnow()
            existing.translation_version += 1
            return existing

        localized = LocalizedContent(
            language_id=language.id,
            source_type=source_type,
            source_id=source_id,
            title=title,
            content=content,
            summary=summary,
            is_auto_translated=auto_translate,
        )
        self.db.add(localized)
        await self.db.commit()
        await self.db.refresh(localized)
        return localized

    # ===== CLAUSE TRANSLATIONS =====

    async def get_clause_translation(
        self,
        clause_id: str,
        language_code: str
    ) -> Optional[ContractClauseTranslation]:
        """Get translated clause."""
        language = await self.get_language(language_code)
        if not language:
            return None

        result = await self.db.execute(
            select(ContractClauseTranslation).where(
                ContractClauseTranslation.clause_id == clause_id,
                ContractClauseTranslation.language_id == language.id,
            )
        )
        return result.scalar_one_or_none()

    async def get_clause_translations(
        self,
        clause_id: str,
        language_codes: List[str]
    ) -> Dict[str, Optional[ContractClauseTranslation]]:
        """Get clause translations in multiple languages."""
        result = {}
        for code in language_codes:
            result[code] = await self.get_clause_translation(clause_id, code)
        return result

    # ===== GLOSSARY =====

    async def get_glossary_term(self, term: str) -> Optional[GlossaryTerm]:
        """Get glossary term."""
        result = await self.db.execute(
            select(GlossaryTerm).where(
                GlossaryTerm.source_term.ilike(term)
            )
        )
        return result.scalar_one_or_none()

    async def get_glossary_for_language(self, language_code: str) -> List[Dict]:
        """Get all glossary terms with translations for a language."""
        result = await self.db.execute(select(GlossaryTerm))
        terms = result.scalars().all()
        result = []
        for term in terms:
            translation = term.translations.get(language_code) if term.translations else None
            result.append({
                "source_term": term.source_term,
                "translation": translation,
                "category": term.category,
                "definition": term.definition,
            })
        return result

    async def add_glossary_term(
        self,
        source_term: str,
        translations: Dict[str, str],
        category: str = None,
        definition: str = None
    ) -> GlossaryTerm:
        """Add a term to the glossary."""
        existing = await self.get_glossary_term(source_term)
        if existing:
            existing.translations = {**(existing.translations or {}), **translations}
            if category:
                existing.category = category
            if definition:
                existing.definition = definition
            existing.updated_at = datetime.utcnow()
            return existing

        term = GlossaryTerm(
            source_language="en",
            source_term=source_term,
            translations=translations,
            category=category,
            definition=definition,
        )
        self.db.add(term)
        await self.db.commit()
        await self.db.refresh(term)
        return term

    # ===== DOCUMENT LOCALE =====

    async def get_document_locale(self, agreement_id: str) -> Optional[DocumentLocale]:
        """Get locale settings for a document."""
        result = await self.db.execute(
            select(DocumentLocale).where(
                DocumentLocale.agreement_id == agreement_id
            )
        )
        return result.scalar_one_or_none()

    async def set_document_locale(
        self,
        agreement_id: str,
        primary_language: str = "en",
        secondary_languages: List[str] = None,
        governing_language: str = None,
        **kwargs
    ) -> DocumentLocale:
        """Set locale settings for a document."""
        # Verify languages exist
        primary = await self.get_language(primary_language)
        if not primary:
            raise ValueError(f"Language '{primary_language}' not found")

        existing = await self.get_document_locale(agreement_id)
        if existing:
            existing.primary_language = primary_language
            existing.secondary_languages = secondary_languages or []
            if governing_language:
                existing.governing_language = governing_language
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            return existing

        locale = DocumentLocale(
            agreement_id=agreement_id,
            primary_language=primary_language,
            secondary_languages=secondary_languages or [],
            governing_language=governing_language or primary_language,
            **kwargs
        )
        self.db.add(locale)
        await self.db.commit()
        await self.db.refresh(locale)
        return locale

    # ===== HELPER: FORMAT UTILITIES =====

    async def format_date(self, date: datetime, language_code: str, format: str = None) -> str:
        """Format date according to locale."""
        if not format:
            language = await self.get_language(language_code)
            format = language.has_date_format if language else "DD/MM/YYYY"

        # Simple formatting
        if format == "MM/DD/YYYY":
            return f"{date.month:02d}/{date.day:02d}/{date.year}"
        elif format == "YYYY-MM-DD":
            return f"{date.year}-{date.month:02d}-{date.day:02d}"
        elif format == "YYYY/MM/DD":
            return f"{date.year}/{date.month:02d}/{date.day:02d}"
        elif format == "DD.MM.YYYY":
            return f"{date.day:02d}.{date.month:02d}.{date.year}"
        else:  # DD/MM/YYYY default
            return f"{date.day:02d}/{date.month:02d}/{date.year}"

    async def format_currency(self, amount: float, language_code: str, currency: str = None) -> str:
        """Format currency according to locale."""
        if not currency:
            language = await self.get_language(language_code)
            currency = language.has_currency if language else "USD"

        # Simple formatting
        if currency == "LKR":
            return f"Rs. {amount:,.2f}"
        elif currency == "JPY":
            return f"¥{amount:,.0f}"
        elif currency == "EUR":
            return f"€{amount:,.2f}"
        elif currency == "GBP":
            return f"£{amount:,.2f}"
        else:  # USD and others
            return f"${amount:,.2f}"

    def get_text_direction(self, language_code: str) -> str:
        """Get text direction for a language (sync helper).

        Kept synchronous because it only formats a well-known code; the
        DB-backed direction lookup is :meth:`get_language`.
        """
        _Rtl = {"ar"}
        return "rtl" if language_code in _Rtl else "ltr"

    async def get_supported_jurisdictions(self, language_code: str) -> List[str]:
        """Get jurisdictions that support a language."""
        language = await self.get_language(language_code)
        return language.supported_jurisdictions if language else []

    async def get_languages_for_jurisdiction(self, jurisdiction_code: str) -> List[Language]:
        """Get languages supported in a jurisdiction."""
        result = await self.db.execute(
            select(Language).where(
                Language.is_active == True,  # noqa: E712
                Language.supported_jurisdictions.contains([jurisdiction_code])
            )
        )
        return list(result.scalars().all())
