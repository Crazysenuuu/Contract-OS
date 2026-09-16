import uuid

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class AgreementType(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "agreement_types"

    key: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    schema: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    template_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # Name of the Jinja2 template used to render this agreement type,
        # e.g. 'msa_lk_v1'. Data-driven resolution of type -> template.
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
    )
