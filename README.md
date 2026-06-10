# StickerHub Flask App

Flask/Jinja web app plus REST API for anonymous public sticker discovery, authenticated uploads, private dashboards, and download tracking.

## Local setup

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app app.py run
```

The app reads environment variables from `.env`, and creates missing database tables on startup. Use PostgreSQL through `DATABASE_URL`:

```text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/stickerhub
```

Public feed pages and API responses intentionally omit uploader identity.

## Cloudinary

Create an unsigned or signed upload preset named `stickerhub_webp_2mb` in Cloudinary with:

- Folder: `stickerhub`
- Incoming transformation: convert/fetch format to WebP
- Maximum file size: `2097152` bytes
- Allowed formats: `png`, `webp`

The backend also validates file type and size before uploading.
