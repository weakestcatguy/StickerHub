from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from app import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    stickers = db.relationship("Sticker", back_populates="uploader", cascade="all, delete-orphan")
    downloads = db.relationship("UserDownload", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_private_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
        }


class Sticker(db.Model):
    __tablename__ = "stickers"

    id = db.Column(db.Integer, primary_key=True)
    cloudinary_public_id = db.Column(db.String(255), nullable=False)
    cloudinary_url = db.Column(db.String(500), nullable=False)
    format = db.Column(db.String(10), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(120), nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    download_count = db.Column(db.Integer, default=0, nullable=False, index=True)

    uploader = db.relationship("User", back_populates="stickers")
    downloads = db.relationship("UserDownload", back_populates="sticker", cascade="all, delete-orphan")

    def to_public_dict(self):
        return {
            "id": self.id,
            "cloudinary_url": self.cloudinary_url,
            "format": self.format,
            "size": self.size,
            "title": self.title,
            "tags": self.tag_list,
            "created_at": self.created_at.isoformat(),
            "download_count": self.download_count,
        }

    def to_owner_dict(self):
        data = self.to_public_dict()
        data["cloudinary_public_id"] = self.cloudinary_public_id
        return data

    @property
    def tag_list(self):
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]


class UserDownload(db.Model):
    __tablename__ = "user_downloads"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    sticker_id = db.Column(db.Integer, db.ForeignKey("stickers.id"), nullable=False, index=True)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="downloads")
    sticker = db.relationship("Sticker", back_populates="downloads")

    __table_args__ = (
        db.UniqueConstraint("user_id", "sticker_id", name="uq_user_download_sticker"),
    )
