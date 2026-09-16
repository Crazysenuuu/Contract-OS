"""Prometheus metrics service for monitoring."""
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import time
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.models.translation_queue import TranslationQueueItem, QueueStatus
from app.models.agreement import Agreement, AgreementVersion
from app.models.user import User


class MetricsService:
    """Service for collecting and exposing Prometheus metrics."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, list] = defaultdict(list)
        self._start_time = time.time()

    # ===== Counter Metrics =====

    def increment_counter(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        self._counters[key] += value

    def get_counter(self, name: str, labels: Dict[str, str] = None) -> int:
        """Get counter value."""
        key = self._make_key(name, labels)
        return self._counters.get(key, 0)

    # ===== Gauge Metrics =====

    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge metric."""
        key = self._make_key(name, labels)
        self._gauges[key] = value

    def get_gauge(self, name: str, labels: Dict[str, str] = None) -> float:
        """Get gauge value."""
        key = self._make_key(name, labels)
        return self._gauges.get(key, 0)

    # ===== Histogram Metrics =====

    def observe_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Observe a histogram value."""
        key = self._make_key(name, labels)
        self._histograms[key].append(value)

    def get_histogram(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        """Get histogram statistics."""
        key = self._make_key(name, labels)
        values = self._histograms.get(key, [])

        if not values:
            return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0, "p50": 0, "p95": 0, "p99": 0}

        sorted_values = sorted(values)
        count = len(sorted_values)
        return {
            "count": count,
            "sum": sum(sorted_values),
            "avg": sum(sorted_values) / count,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "p50": sorted_values[count // 2],
            "p95": sorted_values[int(count * 0.95)] if count > 20 else sorted_values[-1],
            "p99": sorted_values[int(count * 0.99)] if count > 100 else sorted_values[-1],
        }

    # ===== Application Metrics =====

    async def collect_application_metrics(self) -> Dict[str, Any]:
        """Collect all application metrics."""
        metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: self.get_histogram(name) for name in self._histograms.keys()
            },
        }

        # Add database metrics
        metrics["database"] = await self._collect_database_metrics()

        # Add translation metrics
        metrics["translations"] = await self._collect_translation_metrics()

        # Add API metrics
        metrics["api"] = self._collect_api_metrics()

        return metrics

    async def _collect_database_metrics(self) -> Dict[str, Any]:
        """Collect database metrics."""
        try:
            # Agreement count
            agreement_count = (await self.db.execute(
                select(func.count(Agreement.id))
            )).scalar() or 0

            # User count
            user_count = (await self.db.execute(
                select(func.count(User.id))
            )).scalar() or 0

            # Translation queue size
            queue_size = (await self.db.execute(
                select(func.count(TranslationQueueItem.id)).where(
                    TranslationQueueItem.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING])
                )
            )).scalar() or 0

            return {
                "agreements_total": agreement_count,
                "users_total": user_count,
                "translation_queue_size": queue_size,
            }
        except Exception as e:
            return {"error": str(e)}

    async def _collect_translation_metrics(self) -> Dict[str, Any]:
        """Collect translation metrics."""
        try:
            # Status counts (normalise enum keys to their string values)
            status_rows = (await self.db.execute(
                select(
                    TranslationQueueItem.status,
                    func.count(TranslationQueueItem.id)
                ).group_by(TranslationQueueItem.status)
            )).all()
            status_counts = {
                (s.value if hasattr(s, "value") else s): c
                for s, c in status_rows
            }

            # Language counts
            language_rows = (await self.db.execute(
                select(
                    TranslationQueueItem.target_language,
                    func.count(TranslationQueueItem.id)
                ).where(
                    TranslationQueueItem.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING])
                ).group_by(TranslationQueueItem.target_language)
            )).all()
            language_counts = {lang: count for lang, count in language_rows}

            # Throughput (last hour)
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            completed_last_hour = (await self.db.execute(
                select(func.count(TranslationQueueItem.id)).where(
                    TranslationQueueItem.status == QueueStatus.COMPLETED,
                    TranslationQueueItem.completed_at >= one_hour_ago
                )
            )).scalar() or 0

            return {
                "queue_pending": status_counts.get(QueueStatus.PENDING.value, 0) or 0,
                "queue_processing": status_counts.get(QueueStatus.PROCESSING.value, 0) or 0,
                "queue_completed": status_counts.get(QueueStatus.COMPLETED.value, 0) or 0,
                "queue_failed": status_counts.get(QueueStatus.FAILED.value, 0) or 0,
                "by_language": language_counts,
                "throughput_last_hour": completed_last_hour,
            }
        except Exception as e:
            return {"error": str(e)}

    def _collect_api_metrics(self) -> Dict[str, Any]:
        """Collect API metrics."""
        return {
            "requests_total": self.get_counter("api_requests_total"),
            "errors_total": self.get_counter("api_errors_total"),
            "response_time": self.get_histogram("api_response_time"),
        }

    # ===== Prometheus Format =====

    async def to_prometheus_format(self) -> str:
        """Convert metrics to Prometheus text format."""
        lines = []

        # Uptime
        lines.append(f"# HELP contractos_uptime_seconds Application uptime in seconds")
        lines.append(f"# TYPE contractos_uptime_seconds gauge")
        lines.append(f"contractos_uptime_seconds {time.time() - self._start_time}")

        # Counters
        for key, value in self._counters.items():
            name, labels = self._parse_key(key)
            label_str = self._format_labels(labels) if labels else ""
            lines.append(f"# HELP contractos_{name} Counter metric")
            lines.append(f"# TYPE contractos_{name} counter")
            lines.append(f"contractos_{name}{label_str} {value}")

        # Gauges
        for key, value in self._gauges.items():
            name, labels = self._parse_key(key)
            label_str = self._format_labels(labels) if labels else ""
            lines.append(f"# HELP contractos_{name} Gauge metric")
            lines.append(f"# TYPE contractos_{name} gauge")
            lines.append(f"contractos_{name}{label_str} {value}")

        # Histograms
        for key in self._histograms.keys():
            name, labels = self._parse_key(key)
            stats = self.get_histogram(name, labels)
            label_str = self._format_labels(labels) if labels else ""
            lines.append(f"# HELP contractos_{name} Histogram metric")
            lines.append(f"# TYPE contractos_{name} histogram")
            lines.append(f"contractos_{name}_count{label_str} {stats['count']}")
            lines.append(f"contractos_{name}_sum{label_str} {stats['sum']}")

        # Database metrics
        db_metrics = await self._collect_database_metrics()
        for key, value in db_metrics.items():
            if isinstance(value, (int, float)):
                lines.append(f"# HELP contractos_db_{key} Database metric")
                lines.append(f"# TYPE contractos_db_{key} gauge")
                lines.append(f"contractos_db_{key} {value}")

        # Translation metrics
        trans_metrics = await self._collect_translation_metrics()
        for key, value in trans_metrics.items():
            if isinstance(value, (int, float)):
                lines.append(f"# HELP contractos_translation_{key} Translation metric")
                lines.append(f"# TYPE contractos_translation_{key} gauge")
                lines.append(f"contractos_translation_{key} {value}")

        return "\n".join(lines)

    # ===== Helper Methods =====

    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Make a unique key from name and labels."""
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{name}{{{label_str}}}"
        return name

    def _parse_key(self, key: str) -> tuple:
        """Parse key into name and labels."""
        if "{" in key:
            name = key.split("{")[0]
            labels_str = key.split("{")[1].rstrip("}")
            labels = dict(item.split("=") for item in labels_str.split(","))
            return name, labels
        return key, None

    def _format_labels(self, labels: Dict[str, str]) -> str:
        """Format labels for Prometheus."""
        if not labels:
            return ""
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{{{label_str}}}"


# ===== Metrics Middleware =====

class MetricsMiddleware:
    """Middleware for collecting API metrics."""

    def __init__(self, metrics_service: MetricsService):
        self.metrics = metrics_service

    async def __call__(self, request, call_next):
        start_time = time.time()

        # Increment request counter
        self.metrics.increment_counter("api_requests_total", labels={
            "method": request.method,
            "path": request.url.path,
        })

        try:
            response = await call_next(request)

            # Record response time
            duration = time.time() - start_time
            self.metrics.observe_histogram("api_response_time", duration, labels={
                "method": request.method,
                "path": request.url.path,
            })

            # Increment error counter if needed
            if response.status_code >= 400:
                self.metrics.increment_counter("api_errors_total", labels={
                    "method": request.method,
                    "path": request.url.path,
                    "status": str(response.status_code),
                })

            return response

        except Exception as e:
            self.metrics.increment_counter("api_errors_total", labels={
                "method": request.method,
                "path": request.url.path,
                "status": "500",
            })
            raise
