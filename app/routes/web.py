import os

import cloudinary.uploader
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app import db
from app.extensions import limiter
from app.models import Sticker, User, UserDownload
from app.routes.stickers import ALLOWED_FORMATS, GIF_MAX_FILE_SIZE, MAX_FILE_SIZE
from app.security import (
    is_safe_redirect,
    sanitize_tags,
    sanitize_text,
    validate_email,
    validate_password,
    validate_sort,
    validate_username,
)

ALLOWED_EXTENSIONS = {".png", ".webp", ".gif", ".jpeg", ".jpg"}
ALLOWED_MIMETYPES = set(ALLOWED_FORMATS.keys())

web_bp = Blueprint("web", __name__)


def validate_sticker_file(uploaded_file):
    if uploaded_file is None or not uploaded_file.filename:
        return "Sticker file is required."

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return "Only .png, .jpeg, .jpg, .webp, and .gif sticker files are allowed."

    if uploaded_file.mimetype not in ALLOWED_MIMETYPES:
        return "Only PNG, JPEG, WebP, and GIF sticker files are allowed."

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)
    limit = GIF_MAX_FILE_SIZE if uploaded_file.mimetype == "image/gif" else MAX_FILE_SIZE
    if size > limit:
        return "Images must be 5MB or smaller; GIFs must be 15MB or smaller."

    return None


def get_owned_sticker(sticker_id: int) -> Sticker | None:
    sticker = db.session.get(Sticker, sticker_id)
    if sticker is None or sticker.uploader_id != current_user.id:
        return None
    return sticker


@web_bp.get("/")
@limiter.limit("120 per minute")
def index():
    sort = validate_sort(request.args.get("sort", "newest"))
    query = Sticker.query
    if sort == "trending":
        query = query.order_by(Sticker.download_count.desc(), Sticker.created_at.desc())
    else:
        query = query.order_by(Sticker.created_at.desc())

    stickers = query.limit(50).all()
    return render_template("index.html", stickers=stickers, sort=sort)


@web_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    for validator, value in (
        (validate_username, username),
        (validate_email, email),
        (validate_password, password),
    ):
        error = validator(value)
        if error:
            flash(error, "error")
            return render_template("register.html"), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        flash("Username or email is already in use.", "error")
        return render_template("register.html"), 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user, remember=False)
    session.permanent = True
    flash("Welcome to StickerHub.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    identifier = (request.form.get("identifier") or "").strip().lower()
    password = request.form.get("password") or ""
    user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()

    if not user or not user.check_password(password):
        flash("Invalid credentials.", "error")
        return render_template("login.html"), 401

    login_user(user, remember=False)
    session.permanent = True
    flash("Logged in.", "success")
    next_url = request.args.get("next")
    if is_safe_redirect(next_url):
        return redirect(next_url)
    return redirect(url_for("web.dashboard"))



@web_bp.route("/logout", methods=["GET", "POST"])
def logout():
    if current_user.is_authenticated:
        current_user.invalidate_sessions()
        db.session.commit()
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("web.index"))


@web_bp.get("/dashboard")
@login_required
def dashboard():
    uploaded = Sticker.query.filter_by(uploader_id=current_user.id).order_by(Sticker.created_at.desc()).all()
    downloaded = (
        db.session.query(Sticker)
        .join(UserDownload, UserDownload.sticker_id == Sticker.id)
        .filter(UserDownload.user_id == current_user.id)
        .order_by(UserDownload.downloaded_at.desc())
        .all()
    )
    return render_template("dashboard.html", uploaded=uploaded, downloaded=downloaded)



@web_bp.route("/upload", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per hour", methods=["POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html")

    uploaded_file = request.files.get("file")
    error = validate_sticker_file(uploaded_file)
    if error:
        flash(error, "error")
        return render_template("upload.html"), 400

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)

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
        uploader_id=current_user.id,
    )
    db.session.add(sticker)
    db.session.commit()
    flash("Sticker published.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/stickers/<int:sticker_id>/edit")
@login_required
@limiter.limit("60 per hour")
def edit_sticker(sticker_id):
    sticker = get_owned_sticker(sticker_id)
    if sticker is None:
        flash("You can edit only stickers you uploaded.", "error")
        return redirect(url_for("web.dashboard"))

    sticker.title = sanitize_text(request.form.get("title"), max_length=120)
    sticker.tags = sanitize_tags(request.form.get("tags"))
    db.session.commit()
    flash("Sticker updated.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/stickers/<int:sticker_id>/delete")
@login_required
@limiter.limit("60 per hour")
def delete_sticker(sticker_id):
    sticker = get_owned_sticker(sticker_id)
    if sticker is None:
        flash("You can delete only stickers you uploaded.", "error")
        return redirect(url_for("web.dashboard"))

    cloudinary.uploader.destroy(sticker.cloudinary_public_id, resource_type="image")
    db.session.delete(sticker)
    db.session.commit()
    flash("Sticker deleted.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.get("/stickers/<int:sticker_id>/download")
@limiter.limit("120 per hour")
def download_sticker(sticker_id):
    sticker = db.session.get(Sticker, sticker_id)
    if sticker is None:
        flash("Sticker not found.", "error")
        return redirect(url_for("web.index"))

    sticker.download_count += 1
    if current_user.is_authenticated:
        db.session.add(UserDownload(user_id=current_user.id, sticker_id=sticker.id))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        sticker.download_count += 1
        db.session.commit()

    return redirect(sticker.original_format_url)
