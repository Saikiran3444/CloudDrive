"""CloudDrive — a compact, self-hosted file-sharing API.

Run locally with ``uvicorn app.main:app --reload``.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from .database import Base, engine
from .dependencies import CurrentUser, DbSession
from .models import FileShare, StoredFile, User
from .routes.auth import router as auth_router
from .schemas import (
    CreateShareRequest,
    FileOut,
    RenameFileRequest,
    SharedFileOut,
    ShareOut,
)
from .security import create_token
from .storage import create_storage


Base.metadata.create_all(bind=engine)
app = FastAPI(
    title="CloudDrive API",
    description="Upload, share, and manage files with access control.",
    version="1.0.0",
)
app.include_router(auth_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

default_storage_dir = Path("/tmp/clouddrive-storage") if os.getenv("VERCEL") else Path(__file__).resolve().parent / "storage"
STORAGE_DIR = Path(os.getenv("CLOUD_STORAGE_DIR", default_storage_dir))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
STORAGE = create_storage(STORAGE_DIR)
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#2563eb"/>
<path d="M20 22a8 8 0 0 1 8-8h11l9 9v19a8 8 0 0 1-8 8H28a8 8 0 0 1-8-8V22Z" fill="#fff"/>
<path d="M39 14v10h10M27 34h14M27 40h10" fill="none" stroke="#2563eb" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def file_out(file: StoredFile, access: str, permission: str) -> FileOut:
    return FileOut(
        id=file.id,
        name=file.original_name,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
        owner_email=file.owner.email,
        created_at=file.created_at,
        updated_at=file.updated_at,
        access=access,
        permission=permission,
    )


def find_file(db: Session, file_id: str) -> StoredFile:
    file = db.query(StoredFile).options(joinedload(StoredFile.owner)).filter(StoredFile.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return file


def active_email_share(file: StoredFile, user: User) -> FileShare | None:
    now = datetime.now(timezone.utc)
    for share in file.shares:
        if share.recipient_email and share.recipient_email.lower() == user.email.lower():
            if share.expires_at is None or as_utc(share.expires_at) > now:
                return share
    return None


def require_access(file: StoredFile, user: User, download: bool = False) -> tuple[str, str]:
    if file.owner_id == user.id:
        return "owner", "download"
    share = active_email_share(file, user)
    if share and (not download or share.permission == "download"):
        return "shared", share.permission
    raise HTTPException(status_code=403, detail="You do not have access to this file")


def active_link_share(db: Session, token: str) -> FileShare:
    share = (
        db.query(FileShare)
        .options(joinedload(FileShare.file).joinedload(StoredFile.owner))
        .filter(FileShare.share_token == token)
        .first()
    )
    if not share or (share.expires_at and as_utc(share.expires_at) <= datetime.now(timezone.utc)):
        raise HTTPException(status_code=404, detail="Share link is invalid or expired")
    return share


def share_out(share: FileShare, request: Request) -> ShareOut:
    url = str(request.base_url).rstrip("/") + f"/shared/{share.share_token}" if share.share_token else None
    return ShareOut(
        id=share.id,
        recipient_email=share.recipient_email,
        permission=share.permission,
        expires_at=share.expires_at,
        created_at=share.created_at,
        share_url=url,
    )


def stream_storage_file(storage_name: str) -> Iterator[bytes]:
    with STORAGE.open(storage_name) as source:
        while chunk := source.read(1024 * 1024):
            yield chunk


@app.get("/", tags=["System"])
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["System"])
def health_check(db: DbSession):
    db.execute(text("SELECT 1"))
    return {
        "status": "healthy",
        "database": "connected",
        "storage": "s3" if os.getenv("S3_BUCKET") else "local",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Serve the browser icon requested automatically by local API clients."""
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.post("/files", response_model=FileOut, status_code=status.HTTP_201_CREATED, tags=["Files"])
async def upload_file(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    filename: Annotated[str | None, Query(max_length=255)] = None,
):
    """Upload raw bytes with `?filename=...` or an `X-Filename` header."""
    requested_name = filename or request.headers.get("x-filename")
    safe_name = Path(requested_name or "").name.strip()
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Provide a filename query parameter or X-Filename header")
    declared_size = request.headers.get("content-length")
    if declared_size and declared_size.isdigit() and int(declared_size) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Files may not exceed {MAX_UPLOAD_BYTES} bytes")

    storage_name = create_token()
    temporary = tempfile.NamedTemporaryFile(mode="w+b", delete=False)
    destination = Path(temporary.name)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"Files may not exceed {MAX_UPLOAD_BYTES} bytes")
            temporary.write(chunk)
        temporary.flush()
        temporary.seek(0)
        content_type = request.headers.get("content-type", "application/octet-stream").split(";")[0]
        STORAGE.put(storage_name, temporary, content_type)
    except Exception:
        STORAGE.delete(storage_name)
        raise
    finally:
        temporary.close()
        destination.unlink(missing_ok=True)
    file = StoredFile(
        original_name=safe_name,
        storage_name=storage_name,
        content_type=content_type,
        size_bytes=total,
        owner_id=user.id,
    )
    db.add(file)
    db.commit()
    db.refresh(file)
    db.refresh(user)
    return file_out(file, "owner", "download")


@app.get("/files", response_model=list[FileOut], tags=["Files"])
def list_files(user: CurrentUser, db: DbSession):
    own = db.query(StoredFile).options(joinedload(StoredFile.owner)).filter(StoredFile.owner_id == user.id).all()
    shared = (
        db.query(StoredFile)
        .options(joinedload(StoredFile.owner), joinedload(StoredFile.shares))
        .join(FileShare)
        .filter(FileShare.recipient_email == user.email.lower())
        .all()
    )
    results = [file_out(file, "owner", "download") for file in own]
    for file in shared:
        share = active_email_share(file, user)
        if share:
            results.append(file_out(file, "shared", share.permission))
    return sorted(results, key=lambda item: item.created_at, reverse=True)


@app.get("/files/{file_id}", response_model=FileOut, tags=["Files"])
def get_file(file_id: str, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    access, permission = require_access(file, user)
    return file_out(file, access, permission)


@app.get("/files/{file_id}/download", response_class=FileResponse, tags=["Files"])
def download_file(file_id: str, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    require_access(file, user, download=True)
    if not STORAGE.exists(file.storage_name):
        raise HTTPException(status_code=410, detail="File content is no longer available")
    return StreamingResponse(STORAGE.open(file.storage_name), media_type=file.content_type, headers={"Content-Disposition": f'attachment; filename="{file.original_name}"'})


@app.get("/files/{file_id}/view", response_class=FileResponse, tags=["Files"])
def view_file(file_id: str, user: CurrentUser, db: DbSession):
    """Return file content inline for an owner or a recipient with view access."""
    file = find_file(db, file_id)
    require_access(file, user)
    if not STORAGE.exists(file.storage_name):
        raise HTTPException(status_code=410, detail="File content is no longer available")
    # No filename is supplied: compatible browsers render supported MIME types inline.
    return StreamingResponse(stream_storage_file(file.storage_name), media_type=file.content_type)


@app.patch("/files/{file_id}", response_model=FileOut, tags=["Files"])
def rename_file(file_id: str, payload: RenameFileRequest, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can rename a file")
    name = Path(payload.name).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Invalid filename")
    file.original_name = name
    db.commit()
    db.refresh(file)
    return file_out(file, "owner", "download")


@app.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Files"])
def delete_file(file_id: str, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete a file")
    STORAGE.delete(file.storage_name)
    db.query(FileShare).filter(FileShare.file_id == file.id).delete(synchronize_session=False)
    db.delete(file)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/files/{file_id}/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED, tags=["Sharing"])
def create_share(file_id: str, payload: CreateShareRequest, request: Request, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can share a file")
    if payload.expires_at and as_utc(payload.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Expiry must be in the future")
    email = str(payload.recipient_email).lower() if payload.recipient_email else None
    if email:
        existing = db.query(FileShare).filter(FileShare.file_id == file.id, FileShare.recipient_email == email).first()
        if existing:
            existing.permission, existing.expires_at = payload.permission, payload.expires_at
            db.commit()
            db.refresh(existing)
            return share_out(existing, request)
    share = FileShare(
        file_id=file.id,
        recipient_email=email,
        permission=payload.permission,
        expires_at=payload.expires_at,
        share_token=None if email else create_token(),
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share_out(share, request)


@app.get("/files/{file_id}/shares", response_model=list[ShareOut], tags=["Sharing"])
def list_shares(file_id: str, request: Request, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can view shares")
    shares = db.query(FileShare).filter(FileShare.file_id == file.id).order_by(FileShare.created_at.desc()).all()
    return [share_out(share, request) for share in shares]


@app.delete("/files/{file_id}/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sharing"])
def revoke_share(file_id: str, share_id: str, user: CurrentUser, db: DbSession):
    file = find_file(db, file_id)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can revoke a share")
    share = db.query(FileShare).filter(FileShare.id == share_id, FileShare.file_id == file.id).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    db.delete(share)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/shared/{token}", response_model=SharedFileOut, tags=["Public share links"])
def public_share_metadata(token: str, db: DbSession):
    share = active_link_share(db, token)
    file = share.file
    return SharedFileOut(
        id=file.id,
        name=file.original_name,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
        permission=share.permission,
        expires_at=share.expires_at,
    )


@app.get("/shared/{token}/download", response_class=FileResponse, tags=["Public share links"])
def public_share_download(token: str, db: DbSession):
    share = active_link_share(db, token)
    if share.permission != "download":
        raise HTTPException(status_code=403, detail="This link does not permit downloads")
    if not STORAGE.exists(share.file.storage_name):
        raise HTTPException(status_code=410, detail="File content is no longer available")
    return StreamingResponse(
        stream_storage_file(share.file.storage_name),
        media_type=share.file.content_type,
        headers={"Content-Disposition": f'attachment; filename="{share.file.original_name}"'},
    )


@app.get("/shared/{token}/view", response_class=FileResponse, tags=["Public share links"])
def public_share_view(token: str, db: DbSession):
    share = active_link_share(db, token)
    if not STORAGE.exists(share.file.storage_name):
        raise HTTPException(status_code=410, detail="File content is no longer available")
    return StreamingResponse(stream_storage_file(share.file.storage_name), media_type=share.file.content_type)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
