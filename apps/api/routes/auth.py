from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.auth import schemas
from techdom.domain.auth.models import User
from techdom.infrastructure.db import get_session
from techdom.infrastructure.security import create_access_token
from techdom.services import auth as auth_service


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=schemas.UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a standard user",
)
async def register_user(
    payload: schemas.UserCreate, session: AsyncSession = Depends(get_session)
) -> schemas.UserRead:
    try:
        user = await auth_service.create_user(
            session, email=payload.email, password=payload.password
        )
    except auth_service.DuplicateEmailError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        )
    return schemas.UserRead.model_validate(user)


@router.post("/login", response_model=schemas.AuthResponse, summary="Authenticate a user")
async def login(
    payload: schemas.UserLogin, session: AsyncSession = Depends(get_session)
) -> schemas.AuthResponse:
    user = await auth_service.authenticate_user(
        session, email=payload.email, password=payload.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(
        data={"sub": user.email, "role": user.role.value}
    )
    return schemas.AuthResponse(
        access_token=access_token, user=schemas.UserRead.model_validate(user)
    )


@router.get("/me", response_model=schemas.UserRead, summary="Get current user")
async def read_me(
    current_user: User = Depends(auth_service.get_current_active_user),
) -> schemas.UserRead:
    return schemas.UserRead.model_validate(current_user)


@router.get(
    "/admin/ping",
    response_model=schemas.UserRead,
    summary="Verify admin access",
)
async def admin_ping(
    current_admin: User = Depends(auth_service.get_current_active_admin),
) -> schemas.UserRead:
    return schemas.UserRead.model_validate(current_admin)


@router.get(
    "/signicat/initiate",
    summary="Placeholder for Signicat BankID flow",
)
async def signicat_initiate() -> dict[str, str]:
    return {
        "status": "not_implemented",
        "details": "Signicat BankID integration pending. Configure Signicat client credentials before enabling.",
    }
