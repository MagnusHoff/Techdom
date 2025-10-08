from __future__ import annotations

import logging

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain import history
from techdom.domain.auth import schemas
from techdom.domain.auth.models import User, UserRole
from techdom.infrastructure.db import get_session
from techdom.infrastructure.security import create_access_token
from techdom.services import auth as auth_service
from techdom.services.auth import (
    DuplicateUsernameError,
    ExpiredEmailVerificationTokenError,
    InvalidCurrentPasswordError,
    InvalidEmailVerificationTokenError,
    InvalidPasswordError,
    InvalidUsernameError,
    UserNotFoundError,
    EmailVerificationRateLimitedError,
)
from techdom.infrastructure.email import (
    send_email_verification_email,
    send_password_reset_email,
)


router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post(
    "/register",
    response_model=schemas.UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a standard user",
)
async def register_user(
    payload: schemas.UserCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> schemas.UserRead:
    try:
        user = await auth_service.create_user(
            session,
            email=payload.email,
            username=payload.username,
            password=payload.password,
        )
    except (auth_service.DuplicateEmailError, DuplicateUsernameError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="E-post eller brukernavn er allerede i bruk",
        ) from exc
    except InvalidUsernameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    verification = await auth_service.generate_email_verification_token(session, user_id=user.id)
    if verification:
        email_address, token = verification
        verification_url = auth_service.build_email_verification_url(token)
        if verification_url:
            background_tasks.add_task(send_email_verification_email, email_address, verification_url)
        else:
            logger.warning(
                "EMAIL_VERIFICATION_URL_BASE not configured; verification token for %s: %s",
                email_address,
                token,
            )
    return schemas.UserRead.model_validate(user)


@router.post("/login", response_model=schemas.AuthResponse, summary="Authenticate a user")
async def login(
    payload: schemas.UserLogin,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> schemas.AuthResponse:
    normalized_email = payload.email.strip().lower()

    if await auth_service.is_login_rate_limited(session, email=normalized_email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="For mange innloggingsforsøk. Vent 15 minutter og prøv igjen.",
        )

    user = await auth_service.authenticate_user(
        session, email=payload.email, password=payload.password
    )
    if not user:
        client_ip = request.client.host if request.client else None
        await auth_service.register_failed_login_attempt(
            session, email=normalized_email, ip_address=client_ip
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldig email eller passord",
        )

    await auth_service.clear_login_attempts(session, email=normalized_email)

    if not user.is_email_verified:
        verification = await auth_service.generate_email_verification_token(session, user_id=user.id)
        if verification:
            email_address, token = verification
            verification_url = auth_service.build_email_verification_url(token)
            if verification_url:
                background_tasks.add_task(send_email_verification_email, email_address, verification_url)
            else:
                logger.warning(
                    "EMAIL_VERIFICATION_URL_BASE not configured; refreshed verification token for %s: %s",
                    email_address,
                    token,
                )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="E-postadressen er ikke verifisert. Sjekk innboksen din for verifisering.",
        )

    raw_role = getattr(user.role, "value", user.role)
    fallback_role = UserRole.USER.value
    role_value = str(raw_role or fallback_role)
    access_token = create_access_token(data={"sub": user.email, "role": role_value})
    return schemas.AuthResponse(
        access_token=access_token, user=schemas.UserRead.model_validate(user)
    )


@router.get("/me", response_model=schemas.UserRead, summary="Get current user")
async def read_me(
    current_user: User = Depends(auth_service.get_current_active_user),
) -> schemas.UserRead:
    return schemas.UserRead.model_validate(current_user)


@router.patch(
    "/me/username",
    response_model=schemas.UserRead,
    summary="Oppdater brukernavn for innlogget bruker",
)
async def update_username(
    payload: schemas.UpdateUsername,
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> schemas.UserRead:
    try:
        user = await auth_service.update_username(
            session, user_id=current_user.id, username=payload.username
        )
    except DuplicateUsernameError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brukernavn er allerede i bruk",
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc
    except InvalidUsernameError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return schemas.UserRead.model_validate(user)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Oppdater passord for innlogget bruker",
)
async def change_password(
    payload: schemas.ChangePassword,
    current_user: User = Depends(auth_service.get_current_active_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await auth_service.change_password(
            session,
            user_id=current_user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except InvalidCurrentPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Feil nåværende passord",
        ) from exc
    except InvalidPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc


@router.get(
    "/me/status",
    response_model=schemas.UserStatus,
    summary="Hent status for analyser til innlogget bruker",
)
async def read_my_status(
    current_user: User = Depends(auth_service.get_current_active_user),
) -> schemas.UserStatus:
    summary = history.summarise(window_days=7)
    return schemas.UserStatus(
        total_user_analyses=summary.total,
        total_last_7_days=summary.last_7_days,
        last_run_at=summary.last_run_at,
    )


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


@router.get(
    "/users",
    response_model=schemas.UserCollection,
    summary="List users (admin only)",
)
async def list_users(
    search: str | None = Query(default=None, description="Filter by email"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(auth_service.get_current_active_admin),
) -> schemas.UserCollection:
    users, total = await auth_service.list_users(
        session, search=search, limit=limit, offset=offset
    )
    items = [schemas.UserRead.model_validate(user) for user in users]
    return schemas.UserCollection(total=total, items=items)


@router.patch(
    "/users/{user_id}/role",
    response_model=schemas.UserRead,
    summary="Update user role (admin only)",
)
async def update_user_role(
    user_id: int,
    payload: schemas.UpdateUserRole,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(auth_service.get_current_active_admin),
) -> schemas.UserRead:
    try:
        user = await auth_service.update_user_role(
            session, user_id=user_id, role=payload.role
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    return schemas.UserRead.model_validate(user)


@router.patch(
    "/users/{user_id}",
    response_model=schemas.UserRead,
    summary="Oppdater brukerdetaljer (kun admin)",
)
async def admin_update_user(
    user_id: int,
    payload: schemas.AdminUpdateUser,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(auth_service.get_current_active_admin),
) -> schemas.UserRead:
    try:
        user = await auth_service.update_username(
            session, user_id=user_id, username=payload.username
        )
    except DuplicateUsernameError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brukernavn er allerede i bruk",
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc
    except InvalidUsernameError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return schemas.UserRead.model_validate(user)


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Oppdater passord for bruker (kun admin)",
)
async def admin_update_user_password(
    user_id: int,
    payload: schemas.AdminChangeUserPassword,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(auth_service.get_current_active_admin),
) -> None:
    try:
        await auth_service.set_user_password(
            session, user_id=user_id, new_password=payload.new_password
        )
    except InvalidPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Slett bruker (kun admin)",
)
async def admin_delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    current_admin: User = Depends(auth_service.get_current_active_admin),
) -> None:
    if user_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Du kan ikke slette din egen administratorkonto.",
        )

    try:
        await auth_service.delete_user(session, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request password reset link",
)
async def password_reset_request(
    payload: schemas.PasswordResetRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    result = await auth_service.generate_password_reset_token(session, email=payload.email)
    if result:
        user_email, token = result
        reset_url = auth_service.build_password_reset_url(token)
        if reset_url:
            background_tasks.add_task(send_password_reset_email, user_email, reset_url)
        else:
            logger.warning(
                "PASSWORD_RESET_URL_BASE not configured; password reset token for %s: %s",
                user_email,
                token,
            )
    return {"status": "accepted"}


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Complete password reset",
)
async def password_reset_confirm(
    payload: schemas.PasswordResetConfirm,
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await auth_service.reset_password(
            session, token=payload.token, new_password=payload.password
        )
    except (
        auth_service.InvalidPasswordResetTokenError,
        auth_service.ExpiredPasswordResetTokenError,
        InvalidPasswordError,
        UserNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Ugyldig eller utløpt token"
        ) from exc


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send e-postverifisering på nytt",
)
async def resend_email_verification(
    payload: schemas.EmailVerificationResend,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    normalized_email = payload.email.strip().lower()
    user = await auth_service.get_user_by_email(session, email=normalized_email)
    if not user or user.is_email_verified:
        return {"status": "accepted"}

    try:
        verification = await auth_service.generate_email_verification_token(
            session,
            user_id=user.id,
            min_interval=timedelta(seconds=30),
        )
    except EmailVerificationRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Vent {exc.retry_after_seconds} sekunder før du prøver igjen.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    if verification:
        email_address, token = verification
        verification_url = auth_service.build_email_verification_url(token)
        if verification_url:
            background_tasks.add_task(send_email_verification_email, email_address, verification_url)
        else:
            logger.warning(
                "EMAIL_VERIFICATION_URL_BASE not configured; resend token for %s: %s",
                email_address,
                token,
            )

    return {"status": "accepted"}


@router.post(
    "/verify-email/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bekreft e-postadresse",
)
async def verify_email_confirm(
    payload: schemas.EmailVerificationConfirm,
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await auth_service.verify_email_token(session, token=payload.token)
    except (InvalidEmailVerificationTokenError, ExpiredEmailVerificationTokenError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ugyldig eller utløpt token",
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bruker ikke funnet"
        ) from exc
