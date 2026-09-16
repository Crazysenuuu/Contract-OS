"""User portal dashboard API (Panels.txt spec 1.1-1.8).

Provides the dashboard payload (overview counts, my tasks, notifications,
recent activity) plus CRUD for the user's tasks.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.models.user_task import UserTask
from app.services.dashboard_service import (
    get_dashboard,
    record_activity,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# --- Schemas --------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    priority: str = "normal"
    task_type: str = "manual"
    agreement_id: uuid.UUID | None = None
    due_at: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    due_at: datetime | None = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str
    priority: str
    task_type: str
    agreement_id: uuid.UUID | None
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Endpoints -------------------------------------------------------------

@router.get("")
async def dashboard(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get the full dashboard payload for the current user."""
    return await get_dashboard(
        db,
        user_id=current_user.id,
        org_id=org_id,
    )


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's tasks."""
    query = select(UserTask).where(
        UserTask.organization_id == org_id,
        UserTask.user_id == current_user.id,
    )
    if status_filter:
        query = query.where(UserTask.status == status_filter)
    query = query.order_by(
        UserTask.due_at.asc().nullslast(),
        UserTask.created_at.desc(),
    ).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a task for the current user."""
    task = UserTask(
        organization_id=org_id,
        user_id=current_user.id,
        title=data.title,
        description=data.description,
        priority=data.priority,
        task_type=data.task_type,
        agreement_id=data.agreement_id,
        due_at=data.due_at,
        status="pending",
    )
    db.add(task)
    await db.flush()
    await record_activity(
        db,
        organization_id=org_id,
        user_id=current_user.id,
        action="TASK_CREATED",
        resource_type="user_task",
        resource_id=task.id,
        summary=f"Created task: {task.title}",
    )
    await db.commit()
    await db.refresh(task)
    return task


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: uuid.UUID,
    data: TaskUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update a task (status transitions, priority, due date, etc.)."""
    result = await db.execute(
        select(UserTask).where(
            UserTask.id == task_id,
            UserTask.organization_id == org_id,
            UserTask.user_id == current_user.id,
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    if data.title is not None:
        task.title = data.title
    if data.description is not None:
        task.description = data.description
    if data.priority is not None:
        task.priority = data.priority
    if data.due_at is not None:
        task.due_at = data.due_at

    if data.status is not None:
        if data.status not in {"pending", "in_progress", "completed", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid task status: {data.status}",
            )
        task.status = data.status
        task.completed_at = (
            datetime.now(timezone.utc) if data.status == "completed" else None
        )

    await db.flush()
    await record_activity(
        db,
        organization_id=org_id,
        user_id=current_user.id,
        action="TASK_UPDATED",
        resource_type="user_task",
        resource_id=task.id,
        summary=f"Updated task: {task.title}",
    )
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def delete_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a task."""
    result = await db.execute(
        select(UserTask).where(
            UserTask.id == task_id,
            UserTask.organization_id == org_id,
            UserTask.user_id == current_user.id,
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    await db.delete(task)
    await db.flush()
    await record_activity(
        db,
        organization_id=org_id,
        user_id=current_user.id,
        action="TASK_DELETED",
        resource_type="user_task",
        resource_id=task_id,
        summary=f"Deleted task: {task.title}",
    )
    await db.commit()
    return {"deleted": True}