"""Authorization service.

Central authorization engine that makes explicit, auditable decisions
for every agreement action.

Chain:
    Authenticated User
    → Organization Membership
    → Agreement Participant
    → Represented Party
    → Participant Role
    → Permission Key
    → Workflow State
    → Requested Action
    → ALLOW / DENY
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.agreement_access import (
    AgreementAccessGrant,
    AgreementParticipant,
)


@dataclass(frozen=True)
class AuthorizationContext:
    """Context for an authorization decision."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    agreement_id: uuid.UUID
    agreement_party_id: uuid.UUID | None = None
    participant_id: uuid.UUID | None = None
    permission_key: str = ""


@dataclass(frozen=True)
class AuthorizationDecision:
    """Result of an authorization check."""

    allowed: bool
    reason: str
    participant_id: uuid.UUID | None = None
    party_id: uuid.UUID | None = None


class AuthorizationService:
    """Central authorization engine for agreement actions."""

    # Permission hierarchy - higher roles include lower permissions
    PERMISSION_HIERARCHY = {
        "agreement.sign": ["agreement.approve", "agreement.view"],
        "agreement.approve": ["agreement.propose_change", "agreement.view"],
        "agreement.propose_change": ["agreement.comment", "agreement.view"],
        "agreement.comment": ["agreement.view"],
    }

    async def authorize_agreement_action(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        agreement_id: uuid.UUID,
        permission_key: str,
    ) -> AuthorizationDecision:
        """Check if a user can perform an action on an agreement.

        Args:
            db: Database session.
            user_id: The user requesting access.
            organization_id: The organization (from JWT).
            agreement_id: The agreement to check.
            permission_key: The permission being checked.

        Returns:
            AuthorizationDecision with allowed/denied and reason.
        """
        # 1. Verify agreement exists and belongs to this org
        agreement = await self._get_agreement(db, agreement_id, organization_id)
        if agreement is None:
            return AuthorizationDecision(
                allowed=False,
                reason="AGREEMENT_NOT_FOUND",
            )

        # 2. Agreement creator always has full access
        if agreement.created_by == user_id:
            participant = await self._get_participant(db, agreement_id, user_id)
            return AuthorizationDecision(
                allowed=True,
                reason="AGREEMENT_CREATOR",
                participant_id=participant.id if participant else None,
                party_id=participant.agreement_party_id if participant else None,
            )

        # 3. Check participant status
        participant = await self._get_participant(db, agreement_id, user_id)
        if participant is None:
            return AuthorizationDecision(
                allowed=False,
                reason="USER_NOT_AGREEMENT_PARTICIPANT",
            )

        if participant.status != "active":
            return AuthorizationDecision(
                allowed=False,
                reason="AGREEMENT_PARTICIPATION_INACTIVE",
            )

        # 4. Check if participant has the required permission
        has_permission = await self._check_permission(
            db, participant.id, permission_key
        )

        if has_permission:
            return AuthorizationDecision(
                allowed=True,
                reason="PARTICIPANT_PERMISSION_GRANTED",
                participant_id=participant.id,
                party_id=participant.agreement_party_id,
            )

        # 5. Check access grants
        has_grant = await self._check_access_grant(
            db, agreement_id, user_id, permission_key
        )

        if has_grant:
            return AuthorizationDecision(
                allowed=True,
                reason="ACCESS_GRANT_GRANTED",
                participant_id=participant.id,
                party_id=participant.agreement_party_id,
            )

        # 6. Check permission hierarchy (e.g., sign includes approve)
        parent_permissions = self._get_parent_permissions(permission_key)
        for parent_key in parent_permissions:
            has_parent = await self._check_permission(
                db, participant.id, parent_key
            )
            if has_parent:
                return AuthorizationDecision(
                    allowed=True,
                    reason=f"INHERITED_FROM_{parent_key.upper().replace('.', '_')}",
                    participant_id=participant.id,
                    party_id=participant.agreement_party_id,
                )

        return AuthorizationDecision(
            allowed=False,
            reason="PERMISSION_NOT_GRANTED",
        )

    async def get_user_permissions(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        agreement_id: uuid.UUID,
    ) -> list[str]:
        """Get all permission keys for a user on an agreement."""
        participant = await self._get_participant(db, agreement_id, user_id)
        if participant is None:
            return []

        # Get direct permissions
        result = await db.execute(
            select(AgreementParticipantPermission.permission_key).where(
                AgreementParticipantPermission.agreement_participant_id
                == participant.id,
                AgreementParticipantPermission.granted == True,
            )
        )
        permissions = [row[0] for row in result.all()]

        # Add inherited permissions
        all_permissions = set(permissions)
        for perm in permissions:
            children = self._get_child_permissions(perm)
            all_permissions.update(children)

        return sorted(all_permissions)

    async def _get_agreement(
        self,
        db: AsyncSession,
        agreement_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> Agreement | None:
        result = await db.execute(
            select(Agreement).where(
                Agreement.id == agreement_id,
                Agreement.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_participant(
        self,
        db: AsyncSession,
        agreement_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AgreementParticipant | None:
        result = await db.execute(
            select(AgreementParticipant).where(
                AgreementParticipant.agreement_id == agreement_id,
                AgreementParticipant.user_id == user_id,
                AgreementParticipant.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def _check_permission(
        self,
        db: AsyncSession,
        participant_id: uuid.UUID,
        permission_key: str,
    ) -> bool:
        """Check if a participant has a specific permission."""
        from app.models.authorization import AgreementParticipantPermission

        result = await db.execute(
            select(AgreementParticipantPermission).where(
                AgreementParticipantPermission.agreement_participant_id
                == participant_id,
                AgreementParticipantPermission.permission_key == permission_key,
                AgreementParticipantPermission.granted == True,
            )
        )
        return result.scalar_one_or_none() is not None

    async def _check_access_grant(
        self,
        db: AsyncSession,
        agreement_id: uuid.UUID,
        user_id: uuid.UUID,
        permission_key: str,
    ) -> bool:
        """Check if user has an access grant for this permission."""
        from datetime import datetime, timezone

        result = await db.execute(
            select(AgreementAccessGrant).where(
                AgreementAccessGrant.agreement_id == agreement_id,
                AgreementAccessGrant.user_id == user_id,
                AgreementAccessGrant.permission_key == permission_key,
                AgreementAccessGrant.status == "active",
            )
        )
        grant = result.scalar_one_or_none()

        if grant is None:
            return False

        # Check expiration
        if grant.expires_at is not None:
            if grant.expires_at < datetime.now(timezone.utc):
                return False

        return True

    def _get_parent_permissions(self, permission_key: str) -> list[str]:
        """Get parent permissions that include this permission."""
        parents = []
        for parent, children in self.PERMISSION_HIERARCHY.items():
            if permission_key in children:
                parents.append(parent)
        return parents

    def _get_child_permissions(self, permission_key: str) -> list[str]:
        """Get child permissions included by this permission."""
        return self.PERMISSION_HIERARCHY.get(permission_key, [])
