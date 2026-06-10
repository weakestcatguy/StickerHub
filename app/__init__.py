import os
import tempfile
from pathlib import Path

import cloudinary
from dotenv import load_dotenv
from flask import Flask, request, session
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()


def resolve_database_url():
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)
    if database_url:
        return database_url
    if os.getenv("VERCEL"):
        sqlite_path = Path(tempfile.gettempdir()) / "stickerhub.db"
        return f"sqlite:///{sqlite_path.as_posix()}"
    return "postgresql://postgres:postgres@localhost:5432/stickerhub"


def initialize_database(app):
    try:
        with app.app_context():
            db.create_all()
        app.config["DATABASE_READY"] = True
    except SQLAlchemyError as exc:
        app.logger.exception("Database initialization failed: %s", exc)
        app.config["DATABASE_READY"] = False


def create_app():
    project_root = Path(__file__).resolve().parents[1]
    if not os.getenv("VERCEL"):
        load_dotenv(project_root / ".env")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", app.config["SECRET_KEY"])
    app.config["SQLALCHEMY_DATABASE_URI"] = resolve_database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    CORS(app, resources={r"/api/*": {"origins": os.getenv("FRONTEND_ORIGIN", "*")}})

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )

    from app.models import User
    from app.routes.auth import auth_bp
    from app.routes.stickers import stickers_bp
    from app.routes.web import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(stickers_bp, url_prefix="/api/stickers")

    initialize_database(app)

    @app.context_processor
    def inject_current_user():
        user_id = session.get("user_id")
        user = User.query.get(user_id) if user_id else None
        return {"current_user": user}

    @app.get("/api/health")
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

    return app
