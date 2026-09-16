"""Performance and caching service with in-memory cache, pagination optimization, and background jobs."""
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
from uuid import UUID
import time
import hashlib
import json
import threading
from collections import OrderedDict
from functools import wraps
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, desc, select

from app.models.agreement import Agreement
from app.models.user import User


class LRUCache:
    """Thread-safe LRU cache with TTL support."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if entry["expires_at"] > datetime.utcnow():
                    # Move to end (most recently used)
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return entry["value"]
                else:
                    # Expired
                    del self._cache[key]

            self._misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = {
                "value": value,
                "expires_at": datetime.utcnow() + timedelta(seconds=ttl or self._default_ttl),
                "created_at": datetime.utcnow(),
            }

            # Evict oldest if over max size
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        """Delete value from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self, pattern: Optional[str] = None):
        """Clear cache, optionally matching a pattern."""
        with self._lock:
            if pattern:
                keys_to_delete = [k for k in self._cache if pattern in k]
                for key in keys_to_delete:
                    del self._cache[key]
            else:
                self._cache.clear()

    def stats(self) -> Dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0,
        }


# Global cache instance
_cache = LRUCache(max_size=2000, default_ttl=300)


def cached(ttl: int = 300, key_prefix: str = ""):
    """Decorator for caching function results."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key_parts = [key_prefix or func.__name__]
            key_parts.extend([str(a) for a in args])
            key_parts.extend([f"{k}={v}" for k, v in sorted(kwargs.items())])
            cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()

            # Check cache
            result = _cache.get(cache_key)
            if result is not None:
                return result

            # Compute and cache
            result = func(*args, **kwargs)
            _cache.set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator


class PerformanceService:
    """Service for performance optimization, caching, and background jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cache = _cache

    # ===== CACHING =====

    def get_cached(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        return self.cache.get(key)

    def set_cached(self, key: str, value: Any, ttl: int = 300):
        """Set value in cache."""
        self.cache.set(key, value, ttl)

    def invalidate_pattern(self, pattern: str):
        """Invalidate all cached values matching pattern."""
        self.cache.clear(pattern)

    def cache_stats(self) -> Dict:
        """Get cache statistics."""
        return self.cache.stats()

    # ===== OPTIMIZED QUERIES =====

    async def get_agreements_optimized(
        self,
        organization_id: str,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        search: Optional[str] = None,
    ) -> Dict:
        """Optimized agreement listing with cursor-based pagination."""
        cache_key = f"agreements:{organization_id}:{status}:{page}:{page_size}:{sort_by}:{sort_order}:{search}"

        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Build query
        query = (
            select(Agreement).where(Agreement.organization_id == organization_id)
        )

        # Apply filters
        if status:
            query = query.where(Agreement.status == status)
        if search:
            query = query.where(Agreement.title.ilike(f"%{search}%"))

        # Count
        count_result = await self.db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # Sorting
        sort_column = getattr(Agreement, sort_by, Agreement.created_at)
        query = query.order_by(desc(sort_column) if sort_order == "desc" else sort_column)

        # Pagination
        offset = (page - 1) * page_size
        items_result = await self.db.execute(query.offset(offset).limit(page_size))
        items = items_result.scalars().all()

        # Build result
        result = {
            "items": [{
                "id": a.id,
                "title": a.title,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            } for a in items],
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "has_next": offset + page_size < total,
                "has_prev": page > 1,
            }
        }

        # Cache for 5 minutes
        self.cache.set(cache_key, result, 300)
        return result

    async def get_agreement_stats(self, organization_id: str) -> Dict:
        """Get cached agreement statistics."""
        cache_key = f"stats:agreements:{organization_id}"

        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Status distribution
        dist_result = await self.db.execute(
            select(Agreement.status, func.count(Agreement.id))
            .where(Agreement.organization_id == organization_id)
            .group_by(Agreement.status)
        )
        status_dist = {status: count for status, count in dist_result.all()}

        total = sum(status_dist.values())

        # Recent activity (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_result = await self.db.execute(
            select(func.count(Agreement.id)).where(
                Agreement.organization_id == organization_id,
                Agreement.created_at >= thirty_days_ago
            )
        )
        recent_count = recent_result.scalar_one()

        # Average time in each status
        avg_times = {}
        for status in ["draft", "sent", "executed"]:
            items_result = await self.db.execute(
                select(Agreement).where(
                    Agreement.organization_id == organization_id,
                    Agreement.status == status
                )
            )
            agreements = items_result.scalars().all()
            if agreements:
                times = [a.updated_at - a.created_at for a in agreements if a.updated_at and a.created_at]
                if times:
                    avg_times[status] = sum(times, timedelta()) / len(times)

        result = {
            "total_agreements": total,
            "status_distribution": status_dist,
            "recent_count_30d": recent_count,
            "avg_time_in_status": {k: str(v) for k, v in avg_times.items()},
        }

        self.cache.set(cache_key, result, 600)  # Cache for 10 minutes
        return result

    # ===== BACKGROUND JOBS =====

    _jobs: List[Dict] = []
    _job_lock = threading.Lock()

    def enqueue_job(
        self,
        job_type: str,
        payload: Dict,
        priority: int = 0,
        delay_seconds: int = 0
    ) -> str:
        """Enqueue a background job."""
        import uuid
        job_id = str(uuid.uuid4())

        job = {
            "id": job_id,
            "type": job_type,
            "payload": payload,
            "priority": priority,
            "status": "queued",
            "created_at": datetime.utcnow().isoformat(),
            "scheduled_at": (datetime.utcnow() + timedelta(seconds=delay_seconds)).isoformat() if delay_seconds else None,
        }

        with self._job_lock:
            self._jobs.append(job)
            # Sort by priority
            self._jobs.sort(key=lambda x: x["priority"], reverse=True)

        return job_id

    async def process_pending_jobs(self, max_jobs: int = 10) -> List[Dict]:
        """Process pending background jobs."""
        processed = []

        with self._job_lock:
            now = datetime.utcnow()
            pending = [
                j for j in self._jobs
                if j["status"] == "queued"
                and (not j.get("scheduled_at") or datetime.fromisoformat(j["scheduled_at"]) <= now)
            ]

            for job in pending[:max_jobs]:
                job["status"] = "processing"
                job["started_at"] = now.isoformat()

                try:
                    result = await self._execute_job(job)
                    job["status"] = "completed"
                    job["result"] = result
                    job["completed_at"] = datetime.utcnow().isoformat()
                except Exception as e:
                    job["status"] = "failed"
                    job["error"] = str(e)

                processed.append(job)

        return processed

    async def _execute_job(self, job: Dict) -> Any:
        """Execute a single background job."""
        job_type = job["type"]
        payload = job["payload"]

        if job_type == "compliance_check":
            from app.services.compliance_service import ComplianceService
            service = ComplianceService(self.db)
            return await service.check_compliance(
                UUID(str(payload["agreement_id"])),
                UUID(str(payload["organization_id"])),
            )
        elif job_type == "pdf_generation":
            from app.services.agreement_renderer import render_agreement
            result = await render_agreement(
                self.db,
                agreement_id=UUID(str(payload["agreement_id"])),
                generate_pdf=True,
            )
            return {"pdf_bytes": len(result.pdf) if result.pdf else 0}
        elif job_type == "notification":
            from app.services.notification_tracking import NotificationTracking
            service = NotificationTracking(self.db)
            await service.send_workflow_transition_notification(
                to_email=payload.get("to_email", ""),
                agreement_title=payload.get("agreement_title", ""),
                previous_state=payload.get("previous_state", ""),
                current_state=payload.get("current_state", "updated"),
                actor_name=payload.get("actor_name", ""),
                agreement_id=UUID(str(payload["agreement_id"])) if payload.get("agreement_id") else None,
                organization_id=UUID(str(payload["organization_id"])) if payload.get("organization_id") else None,
            )
            return {"sent": True}
        else:
            raise ValueError(f"Unknown job type: {job_type}")

    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get background job status."""
        with self._job_lock:
            for job in self._jobs:
                if job["id"] == job_id:
                    return job
        return None

    def get_queue_stats(self) -> Dict:
        """Get background job queue statistics."""
        with self._job_lock:
            statuses = {}
            for job in self._jobs:
                status = job["status"]
                statuses[status] = statuses.get(status, 0) + 1

            return {
                "total_jobs": len(self._jobs),
                "by_status": statuses,
                "oldest_pending": next(
                    (j["created_at"] for j in self._jobs if j["status"] == "queued"),
                    None
                ),
            }

    # ===== PAGINATION HELPERS =====

    async def paginate_query(self, query, page: int = 1, page_size: int = 20) -> Dict:
        """Apply cursor-based pagination to a SQLAlchemy select."""
        count_result = await self.db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()
        offset = (page - 1) * page_size
        items_result = await self.db.execute(query.offset(offset).limit(page_size))
        items = items_result.scalars().all()

        return {
            "items": items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "has_next": offset + page_size < total,
                "has_prev": page > 1,
            }
        }

    # ===== RESPONSE OPTIMIZATION =====

    def optimize_response(self, data: Any, fields: Optional[List[str]] = None) -> Any:
        """Optimize response by selecting only needed fields."""
        if not fields or not isinstance(data, dict):
            return data

        return {k: v for k, v in data.items() if k in fields or k == "id"}

    def batch_optimize(self, items: List[Dict], fields: Optional[List[str]] = None) -> List[Dict]:
        """Optimize a list of responses."""
        if not fields:
            return items

        return [{k: v for k, v in item.items() if k in fields or k == "id"} for item in items]