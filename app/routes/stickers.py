import os

import cloudinary.uploader
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Sticker, UserDownload

stickers_bp = Blueprint("stickers", __name__)

ALLOWED_FORMATS = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB to accommodate larger PNGs


@stickers_bp.get("")
def list_stickers():
    sort = request.args.get("sort", "newest")
    query = Sticker.query

    if sort == "trending":
        query = query.order_by(Sticker.download_count.desc(), Sticker.created_at.desc())
    else:
        query = query.order_by(Sticker.created_at.desc())

    stickers = query.limit(50).all()
    return {"stickers": [sticker.to_public_dict() for sticker in stickers]}


@stickers_bp.post("")
@jwt_required()
def upload_sticker():
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return {"error": "Sticker file is required."}, 400

    if uploaded_file.mimetype not in ALLOWED_FORMATS:
        return {"error": "Only PNG and WebP sticker files are allowed."}, 400

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)

    gif_max = 15 * 1024 * 1024
    limit = gif_max if uploaded_file.mimetype == "image/gif" else MAX_FILE_SIZE
    if size > limit:
        return {"error": "PNG/WebP stickers must be 5MB or smaller; GIFs must be 15MB or smaller."}, 413

    detected_format = ALLOWED_FORMATS[uploaded_file.mimetype]  # "png", "webp", or "gif"

    # For PNG: upload without format conversion to preserve transparency.
    # For GIF: upload without format conversion to preserve animation frames.
    # For WebP: convert with quality optimisation.
    if detected_format == "png":
        upload_kwargs = dict(
            folder="stickerhub",
            upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
            resource_type="image",
            format="png",
            # Keep the alpha channel – do NOT apply fetch_format=webp here
            transformation=[{"quality": "auto"}],
        )
    elif detected_format == "gif":
        upload_kwargs = dict(
            folder="stickerhub",
            upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
            resource_type="image",
            format="gif",
            # No transformation — preserves all animation frames.
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
        title=(request.form.get("title") or "").strip() or None,
        tags=(request.form.get("tags") or "").strip() or None,
        uploader_id=int(get_jwt_identity()),
    )
    db.session.add(sticker)
    db.session.commit()

    return {"sticker": sticker.to_owner_dict()}, 201


@stickers_bp.get("/dashboard")
@jwt_required()
def dashboard():
    user_id = int(get_jwt_identity())
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
def update_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.uploader_id != int(get_jwt_identity()):
        return {"error": "You can edit only stickers you uploaded."}, 403

    data = request.get_json(silent=True) or {}
    if "title" in data:
        sticker.title = (data["title"] or "").strip() or None
    if "tags" in data:
        sticker.tags = ",".join(tag.strip() for tag in (data["tags"] or "").split(",") if tag.strip()) or None

    db.session.commit()
    return {"sticker": sticker.to_owner_dict()}


@stickers_bp.delete("/<int:sticker_id>")
@jwt_required()
def delete_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.uploader_id != int(get_jwt_identity()):
        return {"error": "You can delete only stickers you uploaded."}, 403

    cloudinary.uploader.destroy(sticker.cloudinary_public_id, resource_type="image")
    db.session.delete(sticker)
    db.session.commit()
    return {"message": "Sticker deleted."}


@stickers_bp.post("/<int:sticker_id>/download")
@jwt_required()
def record_download(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    user_id = int(get_jwt_identity())

    sticker.download_count += 1
    db.session.add(UserDownload(user_id=user_id, sticker_id=sticker.id))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        sticker.download_count += 1
        db.session.commit()

    # Always serve the original format URL so PNG transparency is preserved.
    download_url = sticker.original_format_url
    return {
        "download_url": download_url,
        "whatsapp_link": f"whatsapp://send?text={download_url}",
        "instagram_share_url": download_url,
    }
