import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import Response

from app.dependencies.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.evidence_service import build_evidence_package, export_evidence_pdf

router = APIRouter(
    prefix="/agreements",
    tags=["Evidence"],
)

@router.get("/{id}/evidence")
async def get_evidence_package(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns the full evidence package for the agreement."""
    try:
        package = await build_evidence_package(db, id)
        return package.to_dict()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

@router.get("/{id}/evidence/download")
async def download_evidence_pdf(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns the evidence PDF. 
    
    (Note: According to spec, this might return a presigned URL. 
    For simplicity here, we return the raw bytes/text directly.)
    """
    try:
        pdf_bytes = await export_evidence_pdf(db, id)
        return Response(content=pdf_bytes, media_type="application/pdf")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
