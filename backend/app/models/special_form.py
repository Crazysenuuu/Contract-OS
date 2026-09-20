import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin

class SpecialFormRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Tracks physical notarization or witness signatures for immovable agreements."""
    
    __tablename__ = "special_form_records"
    
    agreement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreements.id", ondelete="CASCADE"), index=True
    )
    
    notary_or_witness_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notarization_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")  # PENDING, VERIFIED, REJECTED
    uploaded_scan_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    agreement = relationship("Agreement", backref="special_form_records")
