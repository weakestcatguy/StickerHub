# StickerHub Flask App

Flask/Jinja web app plus REST API for anonymous public sticker discovery, authenticated uploads, private dashboards, and download tracking.

## Local setup

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app main run
```

The app reads environment variables from `.env`, and creates missing database tables on startup. Use PostgreSQL through `DATABASE_URL`:

```text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/stickerhub
```

On Vercel, add the same values in Project Settings -> Environment Variables. At minimum set `DATABASE_URL`, `FLASK_SECRET_KEY`, `JWT_SECRET_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, and `CLOUDINARY_UPLOAD_PRESET`. Also set `FRONTEND_ORIGIN` to your production URL (for example `https://stickerhub.xyz`).

The Vercel entrypoint is `main.py` (see `pyproject.toml`). Push to GitHub and Vercel will redeploy automatically.

Public feed pages and API responses intentionally omit uploader identity.

## Cloudinary

Create an unsigned or signed upload preset named `stickerhub_webp_2mb` in Cloudinary with:

- Folder: `stickerhub`
- Incoming transformation: convert/fetch format to WebP
- Maximum file size: `2097152` bytes
- Allowed formats: `png`, `webp`

The backend also validates file type and size before uploading.
