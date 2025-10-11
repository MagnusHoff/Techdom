from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.auth.models import User
from techdom.domain.saved_analyses import schemas as saved_schemas
from techdom.infrastructure.db import get_session
from techdom.services import auth as auth_service
from techdom.services import saved_analyses as saved_service

router = APIRouter(prefix="/analyses", tags=["analyses"])


def _normalise_query_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:255]


@router.get("", response_model=saved_schemas.SavedAnalysisCollection)
async def list_saved_analyses(
    analysis_key: str | None = Query(default=None, alias="analysisKey"),
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> saved_schemas.SavedAnalysisCollection:
    normalised_key = _normalise_query_key(analysis_key)
    items = await saved_service.list_saved_analyses(
        session,
        user_id=current_user.id,
        analysis_key=normalised_key,
    )
    return saved_schemas.SavedAnalysisCollection.from_iterable(items)


@router.post(
    "",
    response_model=saved_schemas.SavedAnalysisRead,
    status_code=status.HTTP_201_CREATED,
)
async def save_analysis(
    payload: saved_schemas.SavedAnalysisCreate,
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> saved_schemas.SavedAnalysisRead:
    saved = await saved_service.upsert_saved_analysis(
        session,
        user_id=current_user.id,
        payload=payload,
    )
    return saved_schemas.SavedAnalysisRead.model_validate(saved)


@router.get("/{analysis_id}", response_model=saved_schemas.SavedAnalysisRead)
async def retrieve_saved_analysis(
    analysis_id: str,
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> saved_schemas.SavedAnalysisRead:
    instance = await saved_service.get_saved_analysis(
        session,
        user_id=current_user.id,
        analysis_id=analysis_id.strip(),
    )
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analyse ikke funnet")
    return saved_schemas.SavedAnalysisRead.model_validate(instance)


@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_analysis(
    analysis_id: str,
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    deleted = await saved_service.delete_saved_analysis(
        session,
        user_id=current_user.id,
        analysis_id=analysis_id.strip(),
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analyse ikke funnet")
    return None
