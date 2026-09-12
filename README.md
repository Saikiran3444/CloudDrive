# CloudDrive API

A self-hosted REST API for uploading, managing, and securely sharing files. It starts with SQLite locally; set `DATABASE_URL` to use PostgreSQL in deployment.

## Run

```powershell
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/docs` for the interactive API reference.

## React web client

```powershell
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` if the API is not running at `http://127.0.0.1:8000`.

## Usage flow

1. `POST /auth/register` with `name`, `email`, and a password of eight or more characters. Save `access_token`.
2. Send authenticated requests using `Authorization: Bearer <access_token>` (or `X-API-Key`).
3. Upload raw bytes using `POST /files?filename=report.pdf` and set the file MIME type as `Content-Type`.
4. Create email or public-link access with `POST /files/{file_id}/shares`.

```powershell
$token = "<access-token>"
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/files?filename=notes.txt" `
  -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "text/plain" } `
  -InFile .\notes.txt
```

## Access rules

- Owners can view, download, rename, delete, and create or revoke shares.
- Email recipients authenticate using the email address the owner shared to.
- `view` shares expose metadata; `download` shares expose metadata and file bytes.
- Link shares can expire and are immediately invalidated when revoked.

## Password recovery

`POST /auth/forgot-password` creates a one-time token that expires in 30 minutes; `POST /auth/reset-password` consumes it and invalidates the old API key. The local dashboard shows the reset URL so the full flow is testable. For production, set `EXPOSE_RESET_URL=false` and deliver that URL through a trusted email provider rather than returning it in the API response.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | SQLite at `app/clouddrive.db` | SQLAlchemy database URL |
| `CLOUD_STORAGE_DIR` | `app/storage` | Directory for stored file bytes |
| `MAX_UPLOAD_BYTES` | `26214400` | Maximum raw upload size (25 MiB) |
| `PASSWORD_RESET_TTL_MINUTES` | `30` | Password-reset link lifetime |
| `EXPOSE_RESET_URL` | `true` | Return reset URLs locally; set false in production |
| `S3_BUCKET` | unset | Use AWS S3 instead of local disk when set |
| `S3_PREFIX` | empty | Optional key prefix for S3 objects |
| `S3_ENDPOINT_URL` | unset | Optional S3-compatible endpoint (useful for MinIO) |
| `AWS_REGION` | unset | AWS region for the S3 client |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins allowed to call the API |

When deploying with PostgreSQL, set `DATABASE_URL` to a `postgresql+psycopg://...` URL.

### PostgreSQL and AWS S3 connection

The API uses SQLAlchemy for PostgreSQL and boto3 for S3. Set these variables before
starting Uvicorn:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://cloudrive:change-me@db.example.com:5432/clouddrive"
$env:S3_BUCKET = "my-cloudrive-production"
$env:S3_PREFIX = "uploads"
$env:AWS_REGION = "ap-south-1"
$env:CORS_ORIGINS = "https://cloudrive.example.com"

.\venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

AWS credentials are read using the standard boto3 credential chain (environment
variables, AWS profile, ECS task role, or EC2 instance role). The IAM identity
needs `s3:PutObject`, `s3:GetObject`, `s3:HeadObject`, and `s3:DeleteObject` for
the configured bucket/prefix.

Verify both connections after startup:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

The response includes `database: connected` and `storage: s3` when the
production configuration is active. The first upload additionally verifies that
the S3 credentials and bucket permissions can write objects.
