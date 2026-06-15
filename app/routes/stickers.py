import os

import cloudinary.uploader
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from app import db
from app.extensions import limiter
from app.models import Sticker, UserDownload
from app.security import sanitize_tags, sanitize_text, validate_sort

stickers_bp = Blueprint("stickers", __name__)

ALLOWED_FORMATS = {"image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/pjpeg": "jpeg"}
MAX_FILE_SIZE = 5 * 1024 * 1024
GIF_MAX_FILE_SIZE = 15 * 1024 * 1024


def current_user_id() -> int:
    return int(get_jwt_identity())


def get_owned_sticker(sticker_id: int) -> tuple[Sticker | None, tuple[dict, int] | None]:
    sticker = db.session.get(Sticker, sticker_id)
    if sticker is None:
        return None, ({"error": "Sticker not found."}, 404)
    if sticker.uploader_id != current_user_id():
        return sticker, ({"error": "You do not have permission to access this sticker."}, 403)
    return sticker, None


@stickers_bp.get("")
@limiter.limit("60 per minute")
def list_stickers():
    sort = validate_sort(request.args.get("sort", "newest"))
    query = Sticker.query

    if sort == "trending":
        query = query.order_by(Sticker.download_count.desc(), Sticker.created_at.desc())
    else:
        query = query.order_by(Sticker.created_at.desc())

    stickers = query.limit(50).all()
    return {"stickers": [sticker.to_public_dict() for sticker in stickers]}


@stickers_bp.post("")
@jwt_required()
@limiter.limit("20 per hour")
def upload_sticker():
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return {"error": "Sticker file is required."}, 400

    if uploaded_file.mimetype not in ALLOWED_FORMATS:
        return {"error": "Only PNG, WebP, JPEG, and GIF sticker files are allowed."}, 400

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)

    limit = GIF_MAX_FILE_SIZE if uploaded_file.mimetype == "image/gif" else MAX_FILE_SIZE
    if size > limit:
        return {"error": "Image files must be 5MB or smaller; GIFs must be 15MB or smaller."}, 413

    detected_format = ALLOWED_FORMATS[uploaded_file.mimetype]

    if detected_format == "png":
        upload_kwargs = dict(
            folder="stickerhub",
            upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
            resource_type="image",
            format="png",
            transformation=[{"quality": "auto"}],
        )
    elif detected_format == "gif":
        upload_kwargs = dict(
            folder="stickerhub",
            upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
            resource_type="image",
            format="gif",
        )
    else:
        upload_kwargs = dict(
            folder="stickerhub",
            upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
            resource_type="image",
            format="webp",
            transformation=[{"quality": "auto", "fetch_format": "webp"}],
        )

    result = cloudinary.uploader.upload(uploaded_file, **upload_kwargs)

    sticker = Sticker(
        cloudinary_public_id=result["public_id"],
        cloudinary_url=result["secure_url"],
        format=detected_format,
        size=size,
        title=sanitize_text(request.form.get("title"), max_length=120),
        tags=sanitize_tags(request.form.get("tags")),
        uploader_id=current_user_id(),
    )
    db.session.add(sticker)
    db.session.commit()

    return {"sticker": sticker.to_owner_dict()}, 201


@stickers_bp.get("/dashboard")
@jwt_required()
@limiter.limit("60 per minute")
def dashboard():
    user_id = current_user_id()
    uploaded = Sticker.query.filter_by(uploader_id=user_id).order_by(Sticker.created_at.desc()).all()
    downloaded = (
        db.session.query(Sticker)
        .join(UserDownload, UserDownload.sticker_id == Sticker.id)
        .filter(UserDownload.user_id == user_id)
        .order_by(UserDownload.downloaded_at.desc())
        .all()
    )

    return {
        "uploaded": [sticker.to_owner_dict() for sticker in uploaded],
        "downloaded": [sticker.to_public_dict() for sticker in downloaded],
    }


@stickers_bp.patch("/<int:sticker_id>")
@jwt_required()
@limiter.limit("60 per hour")
def update_sticker(sticker_id):
    sticker, error = get_owned_sticker(sticker_id)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    if "title" in data:
        sticker.title = sanitize_text(data.get("title"), max_length=120)
    if "tags" in data:
        sticker.tags = sanitize_tags(data.get("tags"))

    db.session.commit()
    return {"sticker": sticker.to_owner_dict()}


@stickers_bp.delete("/<int:sticker_id>")
@jwt_required()
@limiter.limit("60 per hour")
def delete_sticker(sticker_id):
    sticker, error = get_owned_sticker(sticker_id)
    if error:
        return error

    cloudinary.uploader.destroy(sticker.cloudinary_public_id, resource_type="image")
    db.session.delete(sticker)
    db.session.commit()
    return {"message": "Sticker deleted."}


@stickers_bp.post("/<int:sticker_id>/download")
@jwt_required()
@limiter.limit("120 per hour")
def record_download(sticker_id):
    sticker = db.session.get(Sticker, sticker_id)
    if sticker is None:
        return {"error": "Sticker not found."}, 404

    user_id = current_user_id()

    sticker.download_count += 1
    db.session.add(UserDownload(user_id=user_id, sticker_id=sticker.id))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        sticker.download_count += 1
        db.session.commit()

    download_url = sticker.original_format_url
    return {
        "download_url": download_url,
        "whatsapp_link": f"whatsapp://send?text={download_url}",
        "instagram_share_url": download_url,
    }
