"""Authentication and password-recovery routes."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status

from ..dependencies import CurrentUser, DbSession
from ..models import PasswordResetToken, User
from ..schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    UserCreate,
    UserOut,
)
from ..security import create_token, hash_password, hash_token, verify_password


router = APIRouter(prefix="/auth", tags=["Authentication"])
PASSWORD_RESET_TTL_MINUTES = int(os.getenv("PASSWORD_RESET_TTL_MINUTES", "30"))
EXPOSE_RESET_URL = os.getenv("EXPOSE_RESET_URL", "true").lower() == "true"


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession):
    email = str(payload.email).lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(name=payload.name.strip(), email=email, password_hash=hash_password(payload.password), api_token=create_token())
    db.add(user)
    db.commit()
    db.refresh(user)
    return AuthResponse(user=user, access_token=user.api_token)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: DbSession):
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return AuthResponse(user=user, access_token=user.api_token)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.post("/forgot-password", response_model=ForgotPasswordResponse, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: ForgotPasswordRequest, request: Request, db: DbSession):
    """Create a single-use token without revealing whether an email exists."""
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    reset_url = None
    if user:
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
        raw_token = create_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES),
            )
        )
        db.commit()
        if EXPOSE_RESET_URL:
            reset_url = f"{str(request.base_url).rstrip('/')}?reset_token={raw_token}"
    return ForgotPasswordResponse(
        message="If an account matches that email, password reset instructions are ready.",
        reset_url=reset_url,
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: DbSession):
    reset = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == hash_token(payload.token)).first()
    now = datetime.now(timezone.utc)
    if not reset or reset.used_at or as_utc(reset.expires_at) <= now:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired")
    user = db.get(User, reset.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired")
    user.password_hash = hash_password(payload.password)
    user.api_token = create_token()  # Invalidate any previously saved API key.
    reset.used_at = now
    db.commit()
    return MessageResponse(message="Password updated. Please sign in with your new password.")
