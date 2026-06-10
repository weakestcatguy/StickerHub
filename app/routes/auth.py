from flask import Blueprint, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from app import db
from app.models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not email or len(password) < 8:
        return {"error": "Username, email, and a password of at least 8 characters are required."}, 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return {"error": "Username or email is already in use."}, 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return {"access_token": token, "user": user.to_private_dict()}, 201


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()
    if not user or not user.check_password(password):
        return {"error": "Invalid credentials."}, 401

    token = create_access_token(identity=str(user.id))
    return {"access_token": token, "user": user.to_private_dict()}


@auth_bp.get("/me")
@jwt_required()
def me():
    user = User.query.get_or_404(int(get_jwt_identity()))
    return {"user": user.to_private_dict()}
