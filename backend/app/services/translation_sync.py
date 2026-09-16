"""Translation sync service for contract versioning."""
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import difflib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, desc, select

from app.models.agreement import Agreement, AgreementVersion
from app.models.i18n import (
    Language, LocalizedContent, ContractClauseTranslation,
    DocumentLocale, TranslationStatus
)
from app.models.document_intelligence import ExtractedClause


class TranslationSyncService:
    """Service for syncing translations across contract versions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ===== VERSION MANAGEMENT =====

    async def create_version_with_translations(
        self,
        agreement_id: str,
        content: str,
        created_by: str,
        sync_existing: bool = True
    ) -> AgreementVersion:
        """Create a new version and optionally sync translations from previous version."""
        # Get the agreement
        result = await self.db.execute(
            select(Agreement).where(Agreement.id == agreement_id)
        )
        agreement = result.scalar_one_or_none()
        if not agreement:
            raise ValueError("Agreement not found")

        # Get the latest version
        result = await self.db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(desc(AgreementVersion.version_number))
        )
        latest_version = result.scalars().first()

        new_version_number = (latest_version.version_number + 1) if latest_version else 1

        # Create the new version
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        new_version = AgreementVersion(
            agreement_id=agreement_id,
            version_number=new_version_number,
            content=content,
            content_hash=content_hash,
            status="draft",
            created_by=created_by,
        )
        self.db.add(new_version)
        await self.db.flush()

        # Sync translations if requested
        if sync_existing and latest_version:
            sync_result = await self.sync_translations_to_version(
                agreement_id=agreement_id,
                from_version_id=latest_version.id,
                to_version_id=new_version.id,
                source_content=content,
            )
            new_version.translation_sync = sync_result
        else:
            # Mark translations as needing update
            new_version.translation_sync = {
                "status": "pending",
                "message": "Translations not yet synced",
                "languages_needed": await self._get_agreement_languages(agreement_id),
            }

        await self.db.commit()
        await self.db.refresh(new_version)
        return new_version

    async def sync_translations_to_version(
        self,
        agreement_id: str,
        from_version_id: str,
        to_version_id: str,
        source_content: str
    ) -> Dict:
        """Sync translations from one version to another."""
        result = {
            "status": "completed",
            "synced_languages": [],
            "pending_languages": [],
            "outdated_languages": [],
            "changes_detected": False,
            "details": {},
        }

        # Get all localized content for the source version
        contents_result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == "agreement_version",
                LocalizedContent.source_id == from_version_id,
            )
        )
        localized_contents = contents_result.scalars().all()

        if not localized_contents:
            result["status"] = "no_translations"
            result["message"] = "No translations found in source version"
            return result

        # Get document locale for translation languages
        locale = await self._get_locale(agreement_id)

        target_languages = []
        if locale:
            target_languages = [locale.primary_language] + (locale.secondary_languages or [])
        else:
            target_languages = [lc.language_id for lc in localized_contents]

        for localized in localized_contents:
            language_code = await self._get_language_code(localized.language_id)
            if not language_code:
                continue

            # Check if translation needs update
            if self._content_has_changed(localized.content, source_content):
                # Content has changed - mark as outdated
                result["outdated_languages"].append(language_code)
                result["details"][language_code] = {
                    "status": "outdated",
                    "last_synced": localized.updated_at.isoformat() if localized.updated_at else None,
                    "message": "Source content has changed, translation needs review",
                }

                # Create outdated translation for new version
                new_localized = LocalizedContent(
                    language_id=localized.language_id,
                    source_type="agreement_version",
                    source_id=to_version_id,
                    title=localized.title,
                    content=localized.content,  # Keep old content temporarily
                    summary=localized.summary,
                    status=TranslationStatus.IN_REVIEW,
                    is_auto_translated=False,
                    source_version=localized.source_version,
                    translation_version=localized.translation_version + 1,
                    last_synced_at=datetime.utcnow(),
                )
                self.db.add(new_localized)
            else:
                # Content unchanged - copy translation directly
                result["synced_languages"].append(language_code)
                result["details"][language_code] = {
                    "status": "synced",
                    "message": "Translation copied to new version",
                }

                new_localized = LocalizedContent(
                    language_id=localized.language_id,
                    source_type="agreement_version",
                    source_id=to_version_id,
                    title=localized.title,
                    content=localized.content,
                    summary=localized.summary,
                    status=localized.status,
                    is_auto_translated=localized.is_auto_translated,
                    source_version=localized.source_version,
                    translation_version=localized.translation_version,
                    last_synced_at=datetime.utcnow(),
                )
                self.db.add(new_localized)

        # Check for languages that need translation
        synced_lang_codes = set(result["synced_languages"] + result["outdated_languages"])
        for lang_code in target_languages:
            if lang_code not in synced_lang_codes:
                result["pending_languages"].append(lang_code)
                result["details"][lang_code] = {
                    "status": "pending",
                    "message": "Translation not yet created",
                }

        result["changes_detected"] = len(result["outdated_languages"]) > 0

        if result["outdated_languages"]:
            result["status"] = "partial"
            result["message"] = f"{len(result['outdated_languages'])} language(s) need review"
        elif result["pending_languages"]:
            result["status"] = "partial"
            result["message"] = f"{len(result['pending_languages'])} language(s) pending translation"
        else:
            result["message"] = f"All {len(result['synced_languages'])} translations synced"

        await self.db.commit()
        return result

    async def get_version_translation_status(
        self,
        agreement_id: str,
        version_id: str
    ) -> Dict:
        """Get translation status for a specific version."""
        locale = await self._get_locale(agreement_id)

        primary_lang = locale.primary_language if locale else "en"
        secondary_langs = locale.secondary_languages if locale else []

        # Get all translations for this version
        contents_result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == "agreement_version",
                LocalizedContent.source_id == version_id,
            )
        )
        localized_contents = contents_result.scalars().all()

        translations = {}
        for lc in localized_contents:
            lang_code = await self._get_language_code(lc.language_id)
            if lang_code:
                translations[lang_code] = {
                    "status": lc.status.value,
                    "quality_score": lc.quality_score,
                    "last_synced": lc.last_synced_at.isoformat() if lc.last_synced_at else None,
                    "is_outdated": lc.status == TranslationStatus.IN_REVIEW,
                }

        # Build response
        result = {
            "version_id": version_id,
            "primary_language": primary_lang,
            "secondary_languages": secondary_langs,
            "translations": translations,
            "summary": {
                "total": len(secondary_langs) + 1,
                "synced": 0,
                "outdated": 0,
                "pending": 0,
            }
        }

        # Count statuses
        if primary_lang in translations:
            result["summary"]["synced"] += 1

        for lang in secondary_langs:
            if lang in translations:
                if translations[lang]["is_outdated"]:
                    result["summary"]["outdated"] += 1
                else:
                    result["summary"]["synced"] += 1
            else:
                result["summary"]["pending"] += 1

        return result

    async def update_translation_for_version(
        self,
        version_id: str,
        language_code: str,
        title: str = None,
        content: str = None,
        summary: str = None,
        reviewed_by: str = None
    ) -> LocalizedContent:
        """Update a translation for a specific version."""
        # Find the language
        result = await self.db.execute(
            select(Language).where(Language.code == language_code)
        )
        language = result.scalar_one_or_none()
        if not language:
            raise ValueError(f"Language '{language_code}' not found")

        # Find or create localized content
        result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == "agreement_version",
                LocalizedContent.source_id == version_id,
                LocalizedContent.language_id == language.id,
            )
        )
        localized = result.scalar_one_or_none()

        if localized:
            if title:
                localized.title = title
            if content:
                localized.content = content
            if summary:
                localized.summary = summary
            localized.status = TranslationStatus.APPROVED
            localized.reviewed_by = reviewed_by
            localized.reviewed_at = datetime.utcnow()
            localized.updated_at = datetime.utcnow()
        else:
            localized = LocalizedContent(
                language_id=language.id,
                source_type="agreement_version",
                source_id=version_id,
                title=title,
                content=content,
                summary=summary,
                status=TranslationStatus.APPROVED,
                reviewed_by=reviewed_by,
            )
            self.db.add(localized)

        await self.db.commit()
        await self.db.refresh(localized)
        return localized

    async def compare_version_translations(
        self,
        agreement_id: str,
        version_id_1: str,
        version_id_2: str
    ) -> Dict:
        """Compare translations between two versions."""
        # Get translations for both versions
        translations_1 = await self._get_version_translations(version_id_1)
        translations_2 = await self._get_version_translations(version_id_2)

        all_languages = set(list(translations_1.keys()) + list(translations_2.keys()))

        comparison = {
            "version_1": version_id_1,
            "version_2": version_id_2,
            "languages_compared": len(all_languages),
            "changes": {},
            "summary": {
                "unchanged": 0,
                "updated": 0,
                "added": 0,
                "removed": 0,
            }
        }

        for lang in all_languages:
            t1 = translations_1.get(lang)
            t2 = translations_2.get(lang)

            if t1 and t2:
                if t1.get("content") == t2.get("content"):
                    comparison["changes"][lang] = {
                        "status": "unchanged",
                        "content_hash": t1.get("content_hash"),
                    }
                    comparison["summary"]["unchanged"] += 1
                else:
                    # Calculate diff
                    old_content = t1.get("content", "")
                    new_content = t2.get("content", "")
                    diff = list(difflib.unified_diff(
                        old_content.splitlines(),
                        new_content.splitlines(),
                        lineterm=""
                    ))

                    comparison["changes"][lang] = {
                        "status": "updated",
                        "old_hash": t1.get("content_hash"),
                        "new_hash": t2.get("content_hash"),
                        "diff_lines": len(diff),
                        "diff_preview": "\n".join(diff[:20]),
                    }
                    comparison["summary"]["updated"] += 1
            elif t1 and not t2:
                comparison["changes"][lang] = {
                    "status": "removed",
                    "content": t1.get("content", "")[:200],
                }
                comparison["summary"]["removed"] += 1
            elif not t1 and t2:
                comparison["changes"][lang] = {
                    "status": "added",
                    "content": t2.get("content", "")[:200],
                }
                comparison["summary"]["added"] += 1

        return comparison

    async def bulk_sync_translations(
        self,
        agreement_id: str,
        to_version_id: str
    ) -> Dict:
        """Bulk sync all translations for an agreement to a specific version."""
        # Get all versions
        result = await self.db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(desc(AgreementVersion.version_number))
        )
        versions = result.scalars().all()

        if not versions:
            raise ValueError("No versions found")

        # Find the latest version with translations
        source_version = None
        for v in versions:
            if v.id != to_version_id:
                localized_result = await self.db.execute(
                    select(LocalizedContent).where(
                        LocalizedContent.source_type == "agreement_version",
                        LocalizedContent.source_id == v.id,
                    )
                )
                localized = localized_result.scalars().first()
                if localized:
                    source_version = v
                    break

        if not source_version:
            return {
                "status": "no_source",
                "message": "No existing translations found to sync from",
            }

        # Sync translations
        result = await self.sync_translations_to_version(
            agreement_id=agreement_id,
            from_version_id=source_version.id,
            to_version_id=to_version_id,
            source_content=versions[0].content if versions else "",
        )

        return result

    # ===== CLAUSE TRANSLATION SYNC =====

    async def sync_clause_translations(
        self,
        clause_id: str,
        source_content: str,
        target_languages: List[str] = None
    ) -> Dict:
        """Sync clause translations when clause content changes."""
        if not target_languages:
            target_languages = ["en", "si", "ta", "zh"]

        result = {
            "clause_id": clause_id,
            "synced": [],
            "outdated": [],
            "pending": [],
        }

        for lang_code in target_languages:
            lang_result = await self.db.execute(
                select(Language).where(Language.code == lang_code)
            )
            language = lang_result.scalar_one_or_none()
            if not language:
                continue

            clause_result = await self.db.execute(
                select(ContractClauseTranslation).where(
                    ContractClauseTranslation.clause_id == clause_id,
                    ContractClauseTranslation.language_id == language.id,
                )
            )
            clause_trans = clause_result.scalar_one_or_none()

            if clause_trans:
                # Check if content has changed significantly
                similarity = self._calculate_similarity(clause_trans.text, source_content)
                if similarity < 0.8:  # Less than 80% similar
                    clause_trans.status = TranslationStatus.IN_REVIEW
                    result["outdated"].append(lang_code)
                else:
                    result["synced"].append(lang_code)
            else:
                result["pending"].append(lang_code)

        return result

    # ===== HELPER METHODS =====

    async def _get_language_code(self, language_id: str) -> Optional[str]:
        """Get language code from language ID."""
        result = await self.db.execute(
            select(Language).where(Language.id == language_id)
        )
        language = result.scalar_one_or_none()
        return language.code if language else None

    async def _get_language_id(self, language_code: str) -> Optional[str]:
        """Get language ID from language code."""
        result = await self.db.execute(
            select(Language).where(Language.code == language_code)
        )
        language = result.scalar_one_or_none()
        return language.id if language else None

    async def _get_locale(self, agreement_id: str) -> Optional[DocumentLocale]:
        """Get the document locale row for an agreement."""
        result = await self.db.execute(
            select(DocumentLocale).where(
                DocumentLocale.agreement_id == agreement_id
            )
        )
        return result.scalar_one_or_none()

    async def _get_agreement_languages(self, agreement_id: str) -> List[str]:
        """Get languages configured for an agreement."""
        locale = await self._get_locale(agreement_id)

        if locale:
            return [locale.primary_language] + (locale.secondary_languages or [])
        return ["en"]

    def _content_has_changed(self, old_content: str, new_content: str) -> bool:
        """Check if content has changed significantly."""
        if not old_content or not new_content:
            return old_content != new_content

        # Calculate similarity
        similarity = self._calculate_similarity(old_content, new_content)
        return similarity < 0.95  # Less than 95% similar means changed

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity ratio."""
        if not text1 or not text2:
            return 0.0

        # Use difflib for similarity
        matcher = difflib.SequenceMatcher(None, text1.lower(), text2.lower())
        return matcher.ratio()

    async def _get_version_translations(self, version_id: str) -> Dict:
        """Get all translations for a version."""
        contents_result = await self.db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == "agreement_version",
                LocalizedContent.source_id == version_id,
            )
        )
        localized_contents = contents_result.scalars().all()

        result = {}
        for lc in localized_contents:
            lang_code = await self._get_language_code(lc.language_id)
            if lang_code:
                import hashlib
                content_hash = hashlib.sha256((lc.content or "").encode()).hexdigest()
                result[lang_code] = {
                    "content": lc.content,
                    "status": lc.status.value,
                    "content_hash": content_hash,
                }

        return result
