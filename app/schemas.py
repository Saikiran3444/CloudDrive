from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Permission = Literal["view", "download"]


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    created_at: datetime
    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordResponse(BaseModel):
    message: str
    # Available only for local development. Production should email this URL.
    reset_url: str | None = None


class MessageResponse(BaseModel):
    message: str


class FileOut(BaseModel):
    id: str
    name: str
    content_type: str
    size_bytes: int
    owner_email: EmailStr
    created_at: datetime
    updated_at: datetime
    access: str
    permission: Permission


class RenameFileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CreateShareRequest(BaseModel):
    recipient_email: EmailStr | None = None
    permission: Permission = "download"
    expires_at: datetime | None = None


class ShareOut(BaseModel):
    id: str
    recipient_email: EmailStr | None
    permission: Permission
    expires_at: datetime | None
    created_at: datetime
    share_url: str | None = None


class SharedFileOut(BaseModel):
    id: str
    name: str
    content_type: str
    size_bytes: int
    permission: Permission
    expires_at: datetime | None
    
