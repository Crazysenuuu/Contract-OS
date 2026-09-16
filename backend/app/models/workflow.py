import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowDefinition(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "workflow_definitions"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    agreement_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id", ondelete="CASCADE"),
        nullable=True,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    states = relationship(
        "WorkflowState",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )

    transitions = relationship(
        "WorkflowTransition",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )


class WorkflowState(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "workflow_states"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )

    key: Mapped[str] = mapped_column(String(100), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workflow = relationship("WorkflowDefinition", back_populates="states")

    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "key",
            name="uq_workflow_state_key",
        ),
    )


class WorkflowTransition(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "workflow_transitions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )

    from_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_states.id", ondelete="CASCADE"),
        nullable=False,
    )

    to_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_states.id", ondelete="CASCADE"),
        nullable=False,
    )

    action_key: Mapped[str] = mapped_column(String(100), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    requires_permission: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )

    workflow = relationship("WorkflowDefinition", back_populates="transitions")

    from_state = relationship("WorkflowState", foreign_keys=[from_state_id])

    to_state = relationship("WorkflowState", foreign_keys=[to_state_id])

    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "from_state_id",
            "action_key",
            name="uq_workflow_transition_action",
        ),
    )


class WorkflowInstance(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "workflow_instances"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    current_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_states.id", ondelete="RESTRICT"),
        nullable=False,
    )

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    workflow = relationship("WorkflowDefinition")

    current_state = relationship("WorkflowState")
