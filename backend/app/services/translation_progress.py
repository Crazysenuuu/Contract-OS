"""Translation progress tracking service for real-time dashboard."""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import json
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, desc, select

from app.models.translation_queue import (
    TranslationQueueItem, TranslationWorker,
    QueueStatus, TranslationPriority
)
from app.models.i18n import Language, LocalizedContent


class TranslationProgressService:
    """Service for tracking and broadcasting translation progress."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._subscribers: List[Dict] = []

    # ===== PROGRESS TRACKING =====

    async def get_overall_progress(self, organization_id: str = None) -> Dict:
        """Get overall translation progress."""
        stmt = select(
            TranslationQueueItem.status,
            func.count(TranslationQueueItem.id)
        )
        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)
        stmt = stmt.group_by(TranslationQueueItem.status)
        rows = (await self.db.execute(stmt)).all()

        status_counts = {}
        for status, count in rows:
            status_key = status.value if hasattr(status, "value") else status
            status_counts[status_key] = count

        # Language progress
        language_progress = await self._get_language_progress(organization_id)

        # Worker status
        workers = await self._get_worker_status()

        # Throughput (items per minute in last hour)
        throughput = await self._calculate_throughput(organization_id)

        # ETA calculation
        pending_count = status_counts.get(QueueStatus.PENDING.value, 0) or 0
        avg_time = throughput.get("avg_processing_time_seconds", 30)
        eta_seconds = pending_count * avg_time

        # Recent completions
        recent_completions = await self._get_recent_completions(organization_id, limit=10)

        total = sum(status_counts.values()) if status_counts else 0
        completed = status_counts.get(QueueStatus.COMPLETED.value, 0) or 0
        progress_pct = (completed / total * 100) if total > 0 else 100

        return {
            "overall": {
                "total": total,
                "completed": completed,
                "pending": status_counts.get(QueueStatus.PENDING.value, 0) or 0,
                "processing": status_counts.get(QueueStatus.PROCESSING.value, 0) or 0,
                "failed": status_counts.get(QueueStatus.FAILED.value, 0) or 0,
                "progress_percent": round(progress_pct, 1),
                "eta_seconds": round(eta_seconds, 0),
            },
            "by_language": language_progress,
            "workers": workers,
            "throughput": throughput,
            "recent_completions": recent_completions,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _get_language_progress(self, organization_id: str = None) -> Dict:
        """Get progress breakdown by target language."""
        stmt = select(
            TranslationQueueItem.target_language,
            TranslationQueueItem.status,
            func.count(TranslationQueueItem.id)
        )

        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)

        stmt = stmt.group_by(
            TranslationQueueItem.target_language,
            TranslationQueueItem.status
        )
        results = (await self.db.execute(stmt)).all()

        language_stats = {}
        for lang, status, count in results:
            if lang not in language_stats:
                language_stats[lang] = {
                    "total": 0,
                    "completed": 0,
                    "pending": 0,
                    "processing": 0,
                    "failed": 0,
                    "progress_percent": 0,
                }

            status_key = status.value if hasattr(status, 'value') else status
            language_stats[lang]["total"] += count
            if status_key == "completed":
                language_stats[lang]["completed"] += count
            elif status_key == "pending":
                language_stats[lang]["pending"] += count
            elif status_key == "processing":
                language_stats[lang]["processing"] += count
            elif status_key == "failed":
                language_stats[lang]["failed"] += count

        # Calculate progress percentages
        for lang in language_stats:
            total = language_stats[lang]["total"]
            completed = language_stats[lang]["completed"]
            language_stats[lang]["progress_percent"] = round(
                (completed / total * 100) if total > 0 else 100, 1
            )

        return language_stats

    async def _get_worker_status(self) -> List[Dict]:
        """Get status of all active workers."""
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        result = await self.db.execute(
            select(TranslationWorker).where(
                TranslationWorker.is_active == True,  # noqa: E712
                TranslationWorker.last_heartbeat >= cutoff
            )
        )
        workers = result.scalars().all()

        return [{
            "worker_id": w.worker_id,
            "hostname": w.hostname,
            "current_task_id": w.current_task_id,
            "tasks_completed": w.tasks_completed,
            "tasks_failed": w.tasks_failed,
            "avg_processing_time": w.avg_processing_time,
            "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
            "is_healthy": (datetime.utcnow() - (w.last_heartbeat or datetime.min)).total_seconds() < 300,
        } for w in workers]

    async def _calculate_throughput(self, organization_id: str = None) -> Dict:
        """Calculate translation throughput."""
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)

        stmt = select(TranslationQueueItem).where(
            TranslationQueueItem.status == QueueStatus.COMPLETED,
            TranslationQueueItem.completed_at >= one_hour_ago
        )

        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)

        completed_items = (await self.db.execute(stmt)).scalars().all()

        # Calculate stats
        total_completed = len(completed_items)
        items_per_minute = total_completed / 60 if total_completed > 0 else 0

        # Average processing time
        processing_times = []
        for item in completed_items:
            if item.started_at and item.completed_at:
                duration = (item.completed_at - item.started_at).total_seconds()
                processing_times.append(duration)

        avg_processing_time = sum(processing_times) / len(processing_times) if processing_times else 30

        # By language throughput
        language_throughput = {}
        for item in completed_items:
            lang = item.target_language
            if lang not in language_throughput:
                language_throughput[lang] = {"completed": 0, "avg_time": 0}
            language_throughput[lang]["completed"] += 1

        for lang in language_throughput:
            lang_items = [i for i in completed_items if i.target_language == lang]
            lang_times = [
                (i.completed_at - i.started_at).total_seconds()
                for i in lang_items
                if i.started_at and i.completed_at
            ]
            language_throughput[lang]["avg_time"] = (
                sum(lang_times) / len(lang_times) if lang_times else 0
            )

        return {
            "items_per_minute": round(items_per_minute, 2),
            "items_last_hour": total_completed,
            "avg_processing_time_seconds": round(avg_processing_time, 2),
            "by_language": language_throughput,
        }

    async def _get_recent_completions(self, organization_id: str = None, limit: int = 10) -> List[Dict]:
        """Get recent translation completions."""
        stmt = select(TranslationQueueItem).where(
            TranslationQueueItem.status == QueueStatus.COMPLETED,
        )

        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)

        stmt = stmt.order_by(
            TranslationQueueItem.completed_at.desc()
        ).limit(limit)
        items = (await self.db.execute(stmt)).scalars().all()

        return [{
            "id": item.id,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "target_language": item.target_language,
            "source_title": item.source_title,
            "quality_score": item.quality_score,
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
            "processing_time": (
                (item.completed_at - item.started_at).total_seconds()
                if item.started_at and item.completed_at else None
            ),
        } for item in items]

    # ===== LIVE METRICS =====

    async def get_live_metrics(self) -> Dict:
        """Get live metrics for real-time dashboard."""
        now = datetime.utcnow()

        # Queue depth over time (last 5 minutes)
        queue_depth = []
        for i in range(5, 0, -1):
            time_point = now - timedelta(minutes=i)
            count = (await self.db.execute(
                select(func.count(TranslationQueueItem.id)).where(
                    TranslationQueueItem.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                    TranslationQueueItem.queued_at <= time_point
                )
            )).scalar()
            queue_depth.append({
                "time": time_point.isoformat(),
                "count": count or 0,
            })

        # Active workers count
        cutoff = now - timedelta(minutes=5)
        active_workers = (await self.db.execute(
            select(func.count(TranslationWorker.id)).where(
                TranslationWorker.is_active == True,  # noqa: E712
                TranslationWorker.last_heartbeat >= cutoff
            )
        )).scalar() or 0

        # Current processing
        current_processing = (await self.db.execute(
            select(TranslationQueueItem).where(
                TranslationQueueItem.status == QueueStatus.PROCESSING
            )
        )).scalars().all()

        return {
            "queue_depth": queue_depth,
            "active_workers": active_workers,
            "current_processing": [{
                "id": item.id,
                "target_language": item.target_language,
                "source_type": item.source_type,
                "started_at": item.started_at.isoformat() if item.started_at else None,
            } for item in current_processing],
            "timestamp": now.isoformat(),
        }

    # ===== PROGRESS HISTORY =====

    async def get_progress_history(
        self,
        hours: int = 24,
        organization_id: str = None
    ) -> List[Dict]:
        """Get translation progress history."""
        start_time = datetime.utcnow() - timedelta(hours=hours)

        stmt = select(
            func.date_trunc('hour', TranslationQueueItem.completed_at).label('hour'),
            func.count(TranslationQueueItem.id).label('count')
        ).where(
            TranslationQueueItem.status == QueueStatus.COMPLETED,
            TranslationQueueItem.completed_at >= start_time
        )

        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)

        stmt = stmt.group_by('hour').order_by('hour')
        results = (await self.db.execute(stmt)).all()

        return [{
            "hour": row.hour.isoformat() if row.hour else None,
            "completed": row.count,
        } for row in results]

    async def get_language_history(
        self,
        language_code: str,
        hours: int = 24,
        organization_id: str = None
    ) -> Dict:
        """Get translation history for a specific language."""
        start_time = datetime.utcnow() - timedelta(hours=hours)

        stmt = select(TranslationQueueItem).where(
            TranslationQueueItem.target_language == language_code,
            TranslationQueueItem.completed_at >= start_time
        )

        if organization_id:
            stmt = stmt.where(TranslationQueueItem.organization_id == organization_id)

        items = (await self.db.execute(stmt)).scalars().all()

        # Calculate stats
        total = len(items)
        processing_times = [
            (i.completed_at - i.started_at).total_seconds()
            for i in items
            if i.started_at and i.completed_at
        ]

        return {
            "language": language_code,
            "total_completed": total,
            "avg_processing_time": (
                sum(processing_times) / len(processing_times)
                if processing_times else 0
            ),
            "min_processing_time": min(processing_times) if processing_times else 0,
            "max_processing_time": max(processing_times) if processing_times else 0,
            "quality_scores": [
                i.quality_score for i in items if i.quality_score is not None
            ],
        }

    # ===== SUBSCRIBER MANAGEMENT =====

    def subscribe(self, callback) -> str:
        """Subscribe to progress updates."""
        import uuid
        subscriber_id = str(uuid.uuid4())
        self._subscribers.append({
            "id": subscriber_id,
            "callback": callback,
            "created_at": datetime.utcnow(),
        })
        return subscriber_id

    def unsubscribe(self, subscriber_id: str):
        """Unsubscribe from progress updates."""
        self._subscribers = [
            s for s in self._subscribers if s["id"] != subscriber_id
        ]

    def broadcast_update(self, update: Dict):
        """Broadcast an update to all subscribers."""
        for subscriber in self._subscribers:
            try:
                subscriber["callback"](update)
            except Exception:
                pass  # Remove failed subscribers
