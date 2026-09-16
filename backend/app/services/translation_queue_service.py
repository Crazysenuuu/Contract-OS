"""Translation queue service for automated translation tasks.

Async implementation — the app injects ``AsyncSession`` everywhere, so all
data access uses the SQLAlchemy 2.0 ``select()`` API. (A previous revision
used the legacy sync ``db.query()`` API against the async session, which
crashed every endpoint with ``AttributeError: 'AsyncSession' object has no
attribute 'query'``.)
"""
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
import hashlib
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.translation_queue import (
    TranslationQueueItem, TranslationTemplate, TranslationWorker,
    QueueStatus, TranslationPriority, TranslationSource
)
from app.models.i18n import Language, LocalizedContent, TranslationStatus


class TranslationQueueService:
    """Service for managing automated translation queue."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ===== QUEUE MANAGEMENT =====

    async def enqueue_translation(
        self,
        source_type: str,
        source_id: str,
        target_language: str,
        source_content: str,
        source_title: str = None,
        source_language: str = "en",
        priority: TranslationPriority = TranslationPriority.NORMAL,
        source: TranslationSource = TranslationSource.AUTO,
        organization_id: str = None,
        metadata: Dict = None
    ) -> TranslationQueueItem:
        """Add a translation task to the queue."""
        # Check if already queued
        existing = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.source_type == source_type,
                    TranslationQueueItem.source_id == source_id,
                    TranslationQueueItem.target_language == target_language,
                    TranslationQueueItem.status.in_([
                        QueueStatus.PENDING,
                        QueueStatus.PROCESSING,
                    ])
                )
            )
        ).scalars().first()

        if existing:
            # Update existing item
            existing.source_content = source_content
            existing.source_title = source_title
            existing.priority = priority
            existing.updated_at = datetime.utcnow()
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        # Create new queue item
        item = TranslationQueueItem(
            organization_id=organization_id,
            source_type=source_type,
            source_id=source_id,
            target_language=target_language,
            source_language=source_language,
            source_content=source_content,
            source_title=source_title,
            priority=priority,
            source=source,
            extra_data=metadata or {},
        )
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def enqueue_bulk_translations(
        self,
        source_type: str,
        source_id: str,
        target_languages: List[str],
        source_content: str,
        source_title: str = None,
        priority: TranslationPriority = TranslationPriority.NORMAL,
        organization_id: str = None
    ) -> List[TranslationQueueItem]:
        """Add multiple translation tasks to the queue."""
        items = []
        for lang in target_languages:
            item = await self.enqueue_translation(
                source_type=source_type,
                source_id=source_id,
                target_language=lang,
                source_content=source_content,
                source_title=source_title,
                priority=priority,
                source=TranslationSource.BULK,
                organization_id=organization_id,
            )
            items.append(item)
        return items

    async def auto_queue_missing_translations(
        self,
        source_type: str,
        source_id: str,
        source_content: str,
        source_title: str = None,
        organization_id: str = None
    ) -> List[TranslationQueueItem]:
        """Automatically queue translations for missing languages."""
        # Get all active languages
        languages = (
            await self.db.execute(
                select(Language).where(
                    Language.is_active == True,  # noqa: E712
                    Language.code != "en",  # Skip source language
                )
            )
        ).scalars().all()

        # Check which languages already have translations
        existing_translations = (
            await self.db.execute(
                select(LocalizedContent).where(
                    LocalizedContent.source_type == source_type,
                    LocalizedContent.source_id == source_id,
                )
            )
        ).scalars().all()

        existing_lang_ids = {lt.language_id for lt in existing_translations}

        # Queue missing translations
        items = []
        for lang in languages:
            if lang.id not in existing_lang_ids:
                # Check if already queued
                queued = (
                    await self.db.execute(
                        select(TranslationQueueItem).where(
                            TranslationQueueItem.source_type == source_type,
                            TranslationQueueItem.source_id == source_id,
                            TranslationQueueItem.target_language == lang.code,
                            TranslationQueueItem.status.in_([
                                QueueStatus.PENDING,
                                QueueStatus.PROCESSING,
                            ])
                        )
                    )
                ).scalars().first()

                if not queued:
                    item = await self.enqueue_translation(
                        source_type=source_type,
                        source_id=source_id,
                        target_language=lang.code,
                        source_content=source_content,
                        source_title=source_title,
                        priority=TranslationPriority.NORMAL,
                        source=TranslationSource.AUTO,
                        organization_id=organization_id,
                        metadata={"trigger": "auto_queue_missing"},
                    )
                    items.append(item)

        return items

    async def process_next_item(self, worker_id: str = None) -> Optional[TranslationQueueItem]:
        """Get and lock the next item for processing."""
        # Get highest priority pending item
        item = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.status == QueueStatus.PENDING,
                    (
                        (TranslationQueueItem.retry_after.is_(None))
                        | (TranslationQueueItem.retry_after <= datetime.utcnow())
                    )
                ).order_by(
                    TranslationQueueItem.priority.desc(),
                    TranslationQueueItem.queued_at.asc()
                ).limit(1)
            )
        ).scalars().first()

        if not item:
            return None

        # Lock for processing
        item.status = QueueStatus.PROCESSING
        item.started_at = datetime.utcnow()
        item.attempts += 1

        if worker_id:
            item.assigned_to = worker_id

        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def complete_translation(
        self,
        item_id: str,
        translated_content: str,
        translated_title: str = None,
        quality_score: float = None,
        confidence_score: float = None,
        is_machine_translated: bool = True
    ) -> TranslationQueueItem:
        """Mark a translation as completed."""
        item = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.id == item_id
                )
            )
        ).scalars().first()

        if not item:
            raise ValueError("Queue item not found")

        item.status = QueueStatus.COMPLETED
        item.translated_content = translated_content
        item.translated_title = translated_title
        item.quality_score = quality_score
        item.confidence_score = confidence_score
        item.is_machine_translated = is_machine_translated
        item.completed_at = datetime.utcnow()

        # Create localized content
        await self._create_localized_content(item)

        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def fail_translation(
        self,
        item_id: str,
        error_message: str,
        retry: bool = True
    ) -> TranslationQueueItem:
        """Mark a translation as failed."""
        item = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.id == item_id
                )
            )
        ).scalars().first()

        if not item:
            raise ValueError("Queue item not found")

        item.error_message = error_message

        if retry and item.attempts < item.max_attempts:
            item.status = QueueStatus.RETRY
            # Exponential backoff: 1min, 5min, 15min
            backoff_minutes = [1, 5, 15]
            delay = backoff_minutes[min(item.attempts - 1, 2)]
            item.retry_after = datetime.utcnow() + timedelta(minutes=delay)
        else:
            item.status = QueueStatus.FAILED

        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def cancel_translation(self, item_id: str) -> TranslationQueueItem:
        """Cancel a translation task."""
        item = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.id == item_id
                )
            )
        ).scalars().first()

        if not item:
            raise ValueError("Queue item not found")

        if item.status in [QueueStatus.COMPLETED, QueueStatus.CANCELLED]:
            raise ValueError("Cannot cancel completed or already cancelled item")

        item.status = QueueStatus.CANCELLED
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def retry_failed_items(self, max_items: int = 10) -> List[TranslationQueueItem]:
        """Retry all failed items that are ready for retry."""
        items = (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.status == QueueStatus.RETRY,
                    TranslationQueueItem.retry_after <= datetime.utcnow(),
                ).order_by(
                    TranslationQueueItem.priority.desc()
                ).limit(max_items)
            )
        ).scalars().all()

        for item in items:
            item.status = QueueStatus.PENDING
            item.retry_after = None

        await self.db.commit()
        return list(items)

    # ===== QUEUE QUERIES =====

    async def get_queue_stats(self) -> Dict:
        """Get queue statistics."""
        stats_rows = (
            await self.db.execute(
                select(
                    TranslationQueueItem.status,
                    func.count(TranslationQueueItem.id)
                ).group_by(TranslationQueueItem.status)
            )
        ).all()
        stats = dict(stats_rows)

        total = sum(stats.values())

        # By priority
        priority_stats = dict(
            (
                await self.db.execute(
                    select(
                        TranslationQueueItem.priority,
                        func.count(TranslationQueueItem.id)
                    ).where(
                        TranslationQueueItem.status == QueueStatus.PENDING
                    ).group_by(TranslationQueueItem.priority)
                )
            ).all()
        )

        # By language
        language_stats = dict(
            (
                await self.db.execute(
                    select(
                        TranslationQueueItem.target_language,
                        func.count(TranslationQueueItem.id)
                    ).where(
                        TranslationQueueItem.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING])
                    ).group_by(TranslationQueueItem.target_language)
                )
            ).all()
        )

        # Average processing time
        avg_time = (
            await self.db.execute(
                select(
                    func.avg(
                        func.extract('epoch', TranslationQueueItem.completed_at) -
                        func.extract('epoch', TranslationQueueItem.started_at)
                    )
                ).where(
                    TranslationQueueItem.status == QueueStatus.COMPLETED
                )
            )
        ).scalar()

        # Estimated wait time
        pending_count = stats.get(QueueStatus.PENDING.value, 0) or stats.get(QueueStatus.PENDING, 0)
        avg_processing_time = avg_time or 30  # Default 30 seconds
        estimated_wait = pending_count * avg_processing_time

        return {
            "total": total,
            "by_status": {k.value if hasattr(k, 'value') else k: v for k, v in stats.items()},
            "by_priority": {k.value if hasattr(k, 'value') else k: v for k, v in priority_stats.items()},
            "by_language": language_stats,
            "avg_processing_time_seconds": round(avg_time, 2) if avg_time else None,
            "estimated_wait_seconds": round(estimated_wait, 0),
        }

    async def get_queue_items(
        self,
        status: QueueStatus = None,
        target_language: str = None,
        source_type: str = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[TranslationQueueItem]:
        """Get queue items with filters."""
        query = select(TranslationQueueItem)

        if status:
            query = query.where(TranslationQueueItem.status == status)
        if target_language:
            query = query.where(TranslationQueueItem.target_language == target_language)
        if source_type:
            query = query.where(TranslationQueueItem.source_type == source_type)

        result = await self.db.execute(
            query.order_by(
                TranslationQueueItem.priority.desc(),
                TranslationQueueItem.queued_at.asc()
            ).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def get_item_status(self, item_id: str) -> Optional[TranslationQueueItem]:
        """Get queue item status."""
        return (
            await self.db.execute(
                select(TranslationQueueItem).where(
                    TranslationQueueItem.id == item_id
                )
            )
        ).scalars().first()

    # ===== TEMPLATES =====

    async def get_templates(
        self,
        category: str = None,
        organization_id: str = None
    ) -> List[TranslationTemplate]:
        """Get translation templates."""
        from sqlalchemy import or_

        query = select(TranslationTemplate)

        if category:
            query = query.where(TranslationTemplate.category == category)
        if organization_id:
            query = query.where(
                or_(
                    TranslationTemplate.organization_id == organization_id,
                    TranslationTemplate.organization_id.is_(None)
                )
            )

        result = await self.db.execute(
            query.order_by(TranslationTemplate.usage_count.desc())
        )
        return list(result.scalars().all())

    async def get_template(self, template_id: str) -> Optional[TranslationTemplate]:
        """Get a translation template."""
        return (
            await self.db.execute(
                select(TranslationTemplate).where(
                    TranslationTemplate.id == template_id
                )
            )
        ).scalars().first()

    async def use_template(
        self,
        template_id: str,
        variables: Dict[str, str],
        target_languages: List[str]
    ) -> List[TranslationQueueItem]:
        """Use a template to create translations."""
        template = await self.get_template(template_id)
        if not template:
            raise ValueError("Template not found")

        # Replace variables in source content
        content = template.source_content
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", value)

        # Increment usage
        template.usage_count += 1
        template.last_used_at = datetime.utcnow()

        # Queue translations
        items = await self.enqueue_bulk_translations(
            source_type="template",
            source_id=template_id,
            target_languages=target_languages,
            source_content=content,
            source_title=template.name,
            organization_id=template.organization_id,
        )

        await self.db.commit()
        return items

    # ===== WORKERS =====

    async def register_worker(
        self,
        worker_id: str,
        hostname: str = None,
        process_id: int = None
    ) -> TranslationWorker:
        """Register a translation worker."""
        existing = (
            await self.db.execute(
                select(TranslationWorker).where(
                    TranslationWorker.worker_id == worker_id
                )
            )
        ).scalars().first()

        if existing:
            existing.is_active = True
            existing.last_heartbeat = datetime.utcnow()
            await self.db.commit()
            return existing

        worker = TranslationWorker(
            worker_id=worker_id,
            hostname=hostname,
            process_id=process_id,
            last_heartbeat=datetime.utcnow(),
        )
        self.db.add(worker)
        await self.db.commit()
        await self.db.refresh(worker)
        return worker

    async def heartbeat(self, worker_id: str) -> bool:
        """Update worker heartbeat."""
        worker = (
            await self.db.execute(
                select(TranslationWorker).where(
                    TranslationWorker.worker_id == worker_id
                )
            )
        ).scalars().first()

        if not worker:
            return False

        worker.last_heartbeat = datetime.utcnow()
        await self.db.commit()
        return True

    async def get_active_workers(self) -> List[TranslationWorker]:
        """Get all active workers."""
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        result = await self.db.execute(
            select(TranslationWorker).where(
                TranslationWorker.is_active == True,  # noqa: E712
                TranslationWorker.last_heartbeat >= cutoff
            )
        )
        return list(result.scalars().all())

    # ===== HELPER METHODS =====

    async def _create_localized_content(self, item: TranslationQueueItem):
        """Create localized content from completed translation."""
        language = (
            await self.db.execute(
                select(Language).where(Language.code == item.target_language)
            )
        ).scalars().first()

        if not language:
            return

        # Check if exists
        existing = (
            await self.db.execute(
                select(LocalizedContent).where(
                    LocalizedContent.source_type == item.source_type,
                    LocalizedContent.source_id == item.source_id,
                    LocalizedContent.language_id == language.id,
                )
            )
        ).scalars().first()

        if existing:
            existing.title = item.translated_title or existing.title
            existing.content = item.translated_content
            existing.status = TranslationStatus.PENDING_REVIEW if item.needs_review else TranslationStatus.APPROVED
            existing.quality_score = item.quality_score
            existing.is_auto_translated = item.is_machine_translated
            existing.updated_at = datetime.utcnow()
        else:
            localized = LocalizedContent(
                language_id=language.id,
                source_type=item.source_type,
                source_id=item.source_id,
                title=item.translated_title,
                content=item.translated_content,
                status=TranslationStatus.PENDING_REVIEW if item.needs_review else TranslationStatus.APPROVED,
                quality_score=item.quality_score,
                is_auto_translated=item.is_machine_translated,
            )
            self.db.add(localized)

    async def process_pending_for_agreement(
        self,
        agreement_id: str
    ) -> Dict:
        """Process all pending translations for an agreement."""
        from app.models.agreement import AgreementVersion

        # Get latest version
        version = (
            await self.db.execute(
                select(AgreementVersion).where(
                    AgreementVersion.agreement_id == agreement_id
                ).order_by(desc(AgreementVersion.version_number)).limit(1)
            )
        ).scalars().first()

        if not version:
            return {"status": "no_version", "queued": 0}

        # Auto queue missing translations
        items = await self.auto_queue_missing_translations(
            source_type="agreement_version",
            source_id=version.id,
            source_content=version.content,
            source_title=f"Agreement v{version.version_number}",
        )

        return {
            "status": "queued",
            "queued": len(items),
            "languages": [item.target_language for item in items],
        }
