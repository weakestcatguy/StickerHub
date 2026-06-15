import io
import os
import re
from urllib.parse import urljoin, urlparse

import bleach
from flask import request
from PIL import Image
from werkzeug.datastructures import FileStorage

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_SORTS = {"newest", "trending"}

ALLOWED_TAGS: list[str] = []
ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}


def is_production() -> bool:
    return bool(os.getenv("VERCEL")) or os.getenv("FLASK_ENV") == "production"


def sanitize_text(value: str | None, *, max_length: int = 120) -> str | None:
    if value is None:
        return None
    cleaned = bleach.clean(value.strip(), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
    cleaned = cleaned[:max_length].strip()
    return cleaned or None


def sanitize_tags(raw_tags: str | None) -> str | None:
    if not raw_tags:
        return None
    tags = []
    for tag in raw_tags.split(","):
        tag = sanitize_text(tag, max_length=32)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 10:
            break
    return ",".join(tags) if tags else None


def validate_username(username: str) -> str | None:
    if not USERNAME_PATTERN.match(username):
        return "Username must be 3–30 characters and use only letters, numbers, or underscores."
    return None


def validate_email(email: str) -> str | None:
    if not email or len(email) > 255 or not EMAIL_PATTERN.match(email):
        return "A valid email address is required."
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must include at least one letter and one number."
    return None


def validate_sort(sort: str | None) -> str:
    if sort in ALLOWED_SORTS:
        return sort
    return "newest"


def is_safe_redirect(target: str | None) -> bool:
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in {"http", "https"} and ref.netloc == test.netloc and test.path.startswith("/")


def verify_image_upload(uploaded_file: FileStorage, expected_mimetype: str) -> str | None:
    uploaded_file.seek(0)
    header = uploaded_file.read(32)
    uploaded_file.seek(0)

    if expected_mimetype == "image/png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "File content does not match PNG format."
    if expected_mimetype == "image/gif" and not header.startswith(b"GIF87a") and not header.startswith(b"GIF89a"):
        return "File content does not match GIF format."
    if expected_mimetype == "image/webp":
        if not (header[:4] == b"RIFF" and header[8:12] == b"WEBP"):
            return "File content does not match WebP format."
    if expected_mimetype in ("image/jpeg", "image/jpg", "image/pjpeg"):
        if not header.startswith(b"\xff\xd8"):
            return "File content does not match JPEG format."

    uploaded_file.seek(0)
    try:
        with Image.open(io.BytesIO(uploaded_file.read())) as image:
            image.verify()
    except Exception:
        return "Uploaded file is not a valid image."
    finally:
        uploaded_file.seek(0)

    return None


def apply_security_headers(response, *, csp_nonce: str | None = None):
    script_src = "'self' https://cdn.tailwindcss.com"
    if csp_nonce:
        script_src += f" 'nonce-{csp_nonce}'"

    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "img-src 'self' https://res.cloudinary.com data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.is_secure or is_production():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
