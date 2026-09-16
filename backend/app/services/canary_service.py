"""Canary deployment service for gradual rollouts."""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import random
from sqlalchemy.orm import Session
from sqlalchemy import func


class CanaryDeployment:
    """Represents a canary deployment configuration."""
    
    def __init__(
        self,
        deployment_id: str,
        service_name: str,
        current_version: str,
        canary_version: str,
        traffic_percentage: float = 10.0,
        status: str = "pending"
    ):
        self.deployment_id = deployment_id
        self.service_name = service_name
        self.current_version = current_version
        self.canary_version = canary_version
        self.traffic_percentage = traffic_percentage
        self.status = status
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.metrics = CanaryMetrics()
        self.stages = []
        self.current_stage = 0


class CanaryMetrics:
    """Metrics for canary deployment evaluation."""
    
    def __init__(self):
        self.request_count = 0
        self.error_count = 0
        self.latency_p50 = 0.0
        self.latency_p95 = 0.0
        self.latency_p99 = 0.0
        self.success_rate = 100.0
        self.error_rate = 0.0


class CanaryService:
    """Service for managing canary deployments."""

    def __init__(self, db: Session = None):
        self.db = db
        self._deployments: Dict[str, CanaryDeployment] = {}
        self._traffic_rules: List[Dict] = []
        
        # Default canary stages
        self.default_stages = [
            {"percentage": 5, "duration_minutes": 5, "auto_promote": True},
            {"percentage": 10, "duration_minutes": 10, "auto_promote": True},
            {"percentage": 25, "duration_minutes": 15, "auto_promote": True},
            {"percentage": 50, "duration_minutes": 20, "auto_promote": True},
            {"percentage": 75, "duration_minutes": 15, "auto_promote": True},
            {"percentage": 100, "duration_minutes": 0, "auto_promote": False},
        ]

    # ===== Deployment Management =====

    def create_deployment(
        self,
        service_name: str,
        current_version: str,
        canary_version: str,
        traffic_percentage: float = 5.0,
        stages: List[Dict] = None
    ) -> CanaryDeployment:
        """Create a new canary deployment."""
        import uuid
        deployment_id = str(uuid.uuid4())[:8]
        
        deployment = CanaryDeployment(
            deployment_id=deployment_id,
            service_name=service_name,
            current_version=current_version,
            canary_version=canary_version,
            traffic_percentage=traffic_percentage,
            status="active"
        )
        deployment.stages = stages or self.default_stages
        
        self._deployments[deployment_id] = deployment
        
        # Add traffic routing rule
        self._add_traffic_rule(deployment)
        
        return deployment

    def get_deployment(self, deployment_id: str) -> Optional[CanaryDeployment]:
        """Get deployment by ID."""
        return self._deployments.get(deployment_id)

    def list_deployments(self, status: str = None) -> List[CanaryDeployment]:
        """List all deployments."""
        deployments = list(self._deployments.values())
        if status:
            deployments = [d for d in deployments if d.status == status]
        return deployments

    def update_traffic_percentage(
        self,
        deployment_id: str,
        percentage: float
    ) -> CanaryDeployment:
        """Update traffic percentage for canary."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")
        
        deployment.traffic_percentage = min(100.0, max(0.0, percentage))
        deployment.updated_at = datetime.utcnow()
        
        # Update traffic rule
        self._update_traffic_rule(deployment)
        
        return deployment

    def promote_canary(self, deployment_id: str) -> CanaryDeployment:
        """Promote canary to stable (100% traffic)."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")
        
        deployment.traffic_percentage = 100.0
        deployment.status = "promoted"
        deployment.updated_at = datetime.utcnow()
        
        # Update traffic rule
        self._update_traffic_rule(deployment)
        
        return deployment

    def rollback_canary(self, deployment_id: str) -> CanaryDeployment:
        """Rollback canary (0% traffic)."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")
        
        deployment.traffic_percentage = 0.0
        deployment.status = "rolled_back"
        deployment.updated_at = datetime.utcnow()
        
        # Remove traffic rule
        self._remove_traffic_rule(deployment_id)
        
        return deployment

    def pause_deployment(self, deployment_id: str) -> CanaryDeployment:
        """Pause canary deployment."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")
        
        deployment.status = "paused"
        deployment.updated_at = datetime.utcnow()
        
        return deployment

    def resume_deployment(self, deployment_id: str) -> CanaryDeployment:
        """Resume canary deployment."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")
        
        deployment.status = "active"
        deployment.updated_at = datetime.utcnow()
        
        return deployment

    # ===== Traffic Routing =====

    def _add_traffic_rule(self, deployment: CanaryDeployment):
        """Add traffic routing rule."""
        rule = {
            "deployment_id": deployment.deployment_id,
            "service": deployment.service_name,
            "canary_version": deployment.canary_version,
            "stable_version": deployment.current_version,
            "weight": deployment.traffic_percentage,
            "active": True,
        }
        self._traffic_rules.append(rule)

    def _update_traffic_rule(self, deployment: CanaryDeployment):
        """Update traffic routing rule."""
        for rule in self._traffic_rules:
            if rule["deployment_id"] == deployment.deployment_id:
                rule["weight"] = deployment.traffic_percentage
                rule["active"] = deployment.traffic_percentage > 0
                break

    def _remove_traffic_rule(self, deployment_id: str):
        """Remove traffic routing rule."""
        self._traffic_rules = [
            r for r in self._traffic_rules if r["deployment_id"] != deployment_id
        ]

    def get_traffic_rules(self) -> List[Dict]:
        """Get all traffic routing rules."""
        return self._traffic_rules

    def route_request(self, service_name: str, user_id: str = None) -> str:
        """Route a request to canary or stable version."""
        # Find active deployment for this service
        for rule in self._traffic_rules:
            if rule["service"] == service_name and rule["active"]:
                # Deterministic routing based on user_id
                if user_id:
                    hash_value = hash(user_id) % 100
                else:
                    hash_value = random.randint(0, 99)
                
                if hash_value < rule["weight"]:
                    return rule["canary_version"]
                else:
                    return rule["stable_version"]
        
        # No canary deployment, return stable version
        return None

    # ===== Auto-promotion =====

    def check_auto_promotion(self, deployment_id: str) -> bool:
        """Check if canary should be auto-promoted."""
        deployment = self.get_deployment(deployment_id)
        if not deployment or deployment.status != "active":
            return False
        
        # Check if current stage duration has passed
        if deployment.current_stage < len(deployment.stages):
            stage = deployment.stages[deployment.current_stage]
            
            # Check metrics
            if self._check_metrics(deployment):
                # Auto-promote to next stage
                if stage.get("auto_promote", False):
                    self._promote_to_next_stage(deployment)
                    return True
        
        return False

    def _promote_to_next_stage(self, deployment: CanaryDeployment):
        """Promote canary to next stage."""
        deployment.current_stage += 1
        
        if deployment.current_stage < len(deployment.stages):
            next_stage = deployment.stages[deployment.current_stage]
            deployment.traffic_percentage = next_stage["percentage"]
            deployment.updated_at = datetime.utcnow()
            self._update_traffic_rule(deployment)
        else:
            # All stages complete, promote to stable
            self.promote_canary(deployment.deployment_id)

    def _check_metrics(self, deployment: CanaryDeployment) -> bool:
        """Check if metrics are within acceptable thresholds."""
        metrics = deployment.metrics
        
        # Error rate threshold: < 1%
        if metrics.error_rate > 1.0:
            return False
        
        # Latency threshold: p95 < 500ms
        if metrics.latency_p95 > 500:
            return False
        
        # Success rate threshold: > 99%
        if metrics.success_rate < 99.0:
            return False
        
        return True

    # ===== Metrics Collection =====

    def record_request(
        self,
        deployment_id: str,
        is_canary: bool,
        latency_ms: float,
        success: bool
    ):
        """Record a request for metrics."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            return
        
        metrics = deployment.metrics
        metrics.request_count += 1
        
        if not success:
            metrics.error_count += 1
        
        # Update rates
        if metrics.request_count > 0:
            metrics.error_rate = (metrics.error_count / metrics.request_count) * 100
            metrics.success_rate = 100.0 - metrics.error_rate
        
        # Update latency (simplified)
        metrics.latency_p50 = latency_ms
        metrics.latency_p95 = latency_ms * 1.5
        metrics.latency_p99 = latency_ms * 2.0

    # ===== Health Checks =====

    def check_canary_health(self, deployment_id: str) -> Dict[str, Any]:
        """Check health of canary deployment."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            return {"status": "not_found"}
        
        metrics = deployment.metrics
        
        health_status = "healthy"
        issues = []
        
        # Check error rate
        if metrics.error_rate > 5.0:
            health_status = "unhealthy"
            issues.append(f"High error rate: {metrics.error_rate:.1f}%")
        elif metrics.error_rate > 1.0:
            health_status = "degraded"
            issues.append(f"Elevated error rate: {metrics.error_rate:.1f}%")
        
        # Check latency
        if metrics.latency_p95 > 1000:
            health_status = "unhealthy"
            issues.append(f"High latency p95: {metrics.latency_p95:.0f}ms")
        elif metrics.latency_p95 > 500:
            health_status = "degraded"
            issues.append(f"Elevated latency p95: {metrics.latency_p95:.0f}ms")
        
        return {
            "deployment_id": deployment_id,
            "status": health_status,
            "traffic_percentage": deployment.traffic_percentage,
            "metrics": {
                "request_count": metrics.request_count,
                "error_rate": round(metrics.error_rate, 2),
                "success_rate": round(metrics.success_rate, 2),
                "latency_p50": round(metrics.latency_p50, 2),
                "latency_p95": round(metrics.latency_p95, 2),
                "latency_p99": round(metrics.latency_p99, 2),
            },
            "issues": issues,
        }

    def get_overall_health(self) -> Dict[str, Any]:
        """Get overall health of all canary deployments."""
        deployments = self.list_deployments(status="active")
        
        healthy = 0
        degraded = 0
        unhealthy = 0
        
        for deployment in deployments:
            health = self.check_canary_health(deployment.deployment_id)
            if health["status"] == "healthy":
                healthy += 1
            elif health["status"] == "degraded":
                degraded += 1
            else:
                unhealthy += 1
        
        return {
            "total_deployments": len(deployments),
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "overall_status": "healthy" if unhealthy == 0 else "unhealthy",
        }

    # ===== Comparison =====

    def compare_versions(
        self,
        deployment_id: str,
        time_window_minutes: int = 60
    ) -> Dict[str, Any]:
        """Compare canary vs stable version metrics."""
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            return {"error": "Deployment not found"}
        
        # Simulated comparison
        return {
            "deployment_id": deployment_id,
            "canary_version": deployment.canary_version,
            "stable_version": deployment.current_version,
            "traffic_split": f"{deployment.traffic_percentage}% canary / {100 - deployment.traffic_percentage}% stable",
            "canary_metrics": {
                "error_rate": deployment.metrics.error_rate,
                "latency_p95": deployment.metrics.latency_p95,
                "success_rate": deployment.metrics.success_rate,
            },
            "recommendation": "continue" if self._check_metrics(deployment) else "rollback",
        }
