import os
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import cloudinary
from dotenv import load_dotenv
from flask import Flask, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import csrf, limiter
from app.security import apply_security_headers, is_production

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id):
    from app.models import User

    return db.session.get(User, int(user_id))


def normalize_database_url(database_url):
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    parsed = urlparse(database_url)
    if not parsed.query:
        return database_url

    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("channel_binding", None)
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def resolve_database_url():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return normalize_database_url(database_url)
    if os.getenv("VERCEL"):
        sqlite_path = Path(tempfile.gettempdir()) / "stickerhub.db"
        return f"sqlite:///{sqlite_path.as_posix()}"
    return "postgresql://postgres:postgres@localhost:5432/stickerhub"


def ensure_user_security_columns():
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    statements = []
    if "mfa_secret" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN mfa_secret VARCHAR(64)")
    if "mfa_enabled" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    if "token_version" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")

    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def initialize_database(app):
    try:
        with app.app_context():
            db.create_all()
            ensure_user_security_columns()
        app.config["DATABASE_READY"] = True
    except SQLAlchemyError as exc:
        app.logger.exception("Database initialization failed: %s", exc)
        app.config["DATABASE_READY"] = False


def configure_jwt(app):
    cookie_secure = is_production()
    app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
    app.config["JWT_COOKIE_SECURE"] = cookie_secure
    app.config["JWT_COOKIE_HTTPONLY"] = True
    app.config["JWT_COOKIE_SAMESITE"] = "Lax"
    app.config["JWT_COOKIE_CSRF_PROTECT"] = True
    app.config["JWT_CSRF_IN_COOKIES"] = True
    app.config["JWT_ACCESS_COOKIE_PATH"] = "/"
    app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth"
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=7)
    app.config["JWT_SESSION_COOKIE"] = False


def configure_session(app):
    cookie_secure = is_production()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = cookie_secure
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = cookie_secure
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=7)


def create_app():
    project_root = Path(__file__).resolve().parents[1]
    package_root = Path(__file__).resolve().parent
    if not os.getenv("VERCEL"):
        load_dotenv(project_root / ".env")

    app = Flask(
        __name__,
        template_folder=str(package_root / "templates"),
        static_folder=str(package_root / "static"),
    )
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", app.config["SECRET_KEY"])
    app.config["SQLALCHEMY_DATABASE_URI"] = resolve_database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    app.config["RATELIMIT_STORAGE_URI"] = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    configure_session(app)
    configure_jwt(app)

    cors_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5000")
    CORS(
        app,
        resources={r"/api/*": {"origins": cors_origin}},
        supports_credentials=True,
    )

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "web.login"
    login_manager.login_message_category = "warning"

    limiter.init_app(app)
    csrf.init_app(app)

    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )

    from app.models import User

    @jwt.token_verification_loader
    def verify_token(_jwt_header, jwt_data):
        user = db.session.get(User, int(jwt_data["sub"]))
        if not user:
            return False
        return jwt_data.get("tv", 0) == user.token_version

    from app.routes.auth import auth_bp
    from app.routes.stickers import stickers_bp
    from app.routes.web import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(stickers_bp, url_prefix="/api/stickers")

    csrf.exempt(auth_bp)
    csrf.exempt(stickers_bp)

    initialize_database(app)

    @app.after_request
    def add_security_headers(response):
        return apply_security_headers(response)

    @app.get("/favicon.ico")
    @app.get("/favicon.png")
    def favicon():
        return "", 204

    @app.get("/api/health")
    @limiter.limit("30 per minute")
    def healthcheck():
        return {
            "status": "ok",
            "service": "StickerHub",
            "database_ready": app.config["DATABASE_READY"],
        }

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(error):
        db.session.rollback()
        app.logger.exception("Database request failed: %s", error)
        if request.path.startswith("/api/"):
            return {"error": "Database is unavailable. Check DATABASE_URL in the deployment environment."}, 503
        return "Database is unavailable. Check DATABASE_URL in the deployment environment.", 503

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        print("Database tables created.")

    @app.cli.command("security-check")
    def security_check_command():
        import subprocess
        import sys

        script = project_root / "scripts" / "security_check.py"
        raise SystemExit(subprocess.call([sys.executable, str(script)]))

    return app
