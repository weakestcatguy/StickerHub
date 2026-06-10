import os

import cloudinary.uploader
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Sticker, User, UserDownload
from app.routes.stickers import MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {".png", ".webp"}
ALLOWED_MIMETYPES = {"image/png", "image/webp"}

web_bp = Blueprint("web", __name__)


def validate_sticker_file(uploaded_file):
    if uploaded_file is None or not uploaded_file.filename:
        return "Sticker file is required."

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return "Only .png and .webp sticker files are allowed."

    if uploaded_file.mimetype not in ALLOWED_MIMETYPES:
        return "Only PNG and WebP sticker files are allowed."

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)
    if size > MAX_FILE_SIZE:
        return "Sticker files must be 2MB or smaller."

    return None


@web_bp.get("/")
def index():
    sort = request.args.get("sort", "newest")
    query = Sticker.query
    if sort == "trending":
        query = query.order_by(Sticker.download_count.desc(), Sticker.created_at.desc())
    else:
        query = query.order_by(Sticker.created_at.desc())

    stickers = query.limit(50).all()
    return render_template("index.html", stickers=stickers, sort=sort)


@web_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not username or not email or len(password) < 8:
        flash("Username, email, and an 8 character password are required.", "error")
        return render_template("register.html"), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        flash("Username or email is already in use.", "error")
        return render_template("register.html"), 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    flash("Welcome to StickerHub.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    identifier = (request.form.get("identifier") or "").strip().lower()
    password = request.form.get("password") or ""
    user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()

    if not user or not user.check_password(password):
        flash("Invalid credentials.", "error")
        return render_template("login.html"), 401

    login_user(user)
    flash("Logged in.", "success")
    return redirect(request.args.get("next") or url_for("web.dashboard"))


@web_bp.route("/logout", methods=["GET", "POST"])
def logout():
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

    result = cloudinary.uploader.upload(
        uploaded_file,
        folder="stickerhub",
        upload_preset=os.getenv("CLOUDINARY_UPLOAD_PRESET"),
        resource_type="image",
        format="webp",
        transformation=[{"quality": "auto", "fetch_format": "webp"}],
    )

    sticker = Sticker(
        cloudinary_public_id=result["public_id"],
        cloudinary_url=result["secure_url"],
        format="webp",
        size=size,
        title=(request.form.get("title") or "").strip() or None,
        tags=(request.form.get("tags") or "").strip() or None,
        uploader_id=current_user.id,
    )
    db.session.add(sticker)
    db.session.commit()
    flash("Sticker published.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/stickers/<int:sticker_id>/edit")
@login_required
def edit_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.uploader_id != current_user.id:
        flash("You can edit only stickers you uploaded.", "error")
        return redirect(url_for("web.dashboard"))

    sticker.title = (request.form.get("title") or "").strip() or None
    sticker.tags = ",".join(
        tag.strip() for tag in (request.form.get("tags") or "").split(",") if tag.strip()
    ) or None
    db.session.commit()
    flash("Sticker updated.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/stickers/<int:sticker_id>/delete")
@login_required
def delete_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.uploader_id != current_user.id:
        flash("You can delete only stickers you uploaded.", "error")
        return redirect(url_for("web.dashboard"))

    cloudinary.uploader.destroy(sticker.cloudinary_public_id, resource_type="image")
    db.session.delete(sticker)
    db.session.commit()
    flash("Sticker deleted.", "success")
    return redirect(url_for("web.dashboard"))


@web_bp.get("/stickers/<int:sticker_id>/download")
def download_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)

    sticker.download_count += 1
    if current_user.is_authenticated:
        db.session.add(UserDownload(user_id=current_user.id, sticker_id=sticker.id))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        sticker.download_count += 1
        db.session.commit()

    return redirect(sticker.cloudinary_url)
