import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class SignerEvidence(Base):
    __tablename__ = "signer_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agreement_id = Column(UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False, index=True)
    signer_id = Column(UUID(as_uuid=True), ForeignKey("external_parties.id", ondelete="CASCADE"), nullable=False)
    
    action = Column(String, nullable=False)  # signed, declined, viewed
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    consent_event_id = Column(String, nullable=True)
    identity_check_result = Column(JSONB, nullable=True)
    document_hash_at_time = Column(String, nullable=True)

    agreement = relationship("Agreement", backref="signer_evidence")
    signer = relationship("ExternalParty")
