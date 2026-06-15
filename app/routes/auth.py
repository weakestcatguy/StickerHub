from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
import pyotp

from app import db
from app.extensions import limiter
from app.models import User
from app.security import validate_email, validate_password, validate_username

auth_bp = Blueprint("auth", __name__)


def auth_claims(user: User) -> dict:
    return {"tv": user.token_version}


def issue_auth_response(user: User, status_code: int = 200):
    access_token = create_access_token(identity=str(user.id), additional_claims=auth_claims(user))
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=auth_claims(user))
    response = jsonify({"user": user.to_private_dict(), "message": "Authenticated via secure HttpOnly cookies."})
    set_access_cookies(response, access_token)
    set_refresh_cookies(response, refresh_token)
    return response, status_code


def verify_mfa_code(user: User, code: str | None) -> bool:
    if not user.mfa_enabled:
        return True
    if not code or not user.mfa_secret:
        return False
    totp = pyotp.TOTP(user.mfa_secret)
    return totp.verify(code.strip(), valid_window=1)


@auth_bp.post("/register")
@limiter.limit("5 per hour")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    for validator, value in (
        (validate_username, username),
        (validate_email, email),
        (validate_password, password),
    ):
        error = validator(value)
        if error:
            return {"error": error}, 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return {"error": "Username or email is already in use."}, 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return issue_auth_response(user, 201)


@auth_bp.post("/login")
@limiter.limit("10 per minute")
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    password = data.get("password") or ""
    mfa_code = (data.get("mfa_code") or "").strip()

    user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()
    if not user or not user.check_password(password):
        return {"error": "Invalid credentials."}, 401

    if user.mfa_enabled and not verify_mfa_code(user, mfa_code):
        return {"error": "Multi-factor authentication code required or invalid.", "mfa_required": True}, 401

    return issue_auth_response(user)


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
@limiter.limit("30 per minute")
def refresh():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        return {"error": "User not found."}, 404

    jwt_data = get_jwt()
    if jwt_data.get("tv", 0) != user.token_version:
        return {"error": "Session expired. Please log in again."}, 401

    access_token = create_access_token(identity=str(user.id), additional_claims=auth_claims(user))
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=auth_claims(user))
    response = jsonify({"message": "Token rotated."})
    set_access_cookies(response, access_token)
    set_refresh_cookies(response, refresh_token)
    return response


@auth_bp.post("/logout")
@limiter.limit("30 per minute")
def logout():
    from flask_jwt_extended import verify_jwt_in_request

    verify_jwt_in_request(optional=True, refresh=True)
    user_id = get_jwt_identity()
    if user_id:
        user = db.session.get(User, int(user_id))
        if user:
            user.invalidate_sessions()
            db.session.commit()

    response = jsonify({"message": "Logged out."})
    unset_jwt_cookies(response)
    return response


@auth_bp.get("/me")
@jwt_required()
def me():
    user = db.session.get_or_404(User, int(get_jwt_identity()))
    return {"user": user.to_private_dict()}


@auth_bp.post("/mfa/setup")
@jwt_required()
@limiter.limit("10 per hour")
def mfa_setup():
    user = db.session.get_or_404(User, int(get_jwt_identity()))
    secret = pyotp.random_base32()
    user.mfa_secret = secret
    user.mfa_enabled = False
    db.session.commit()

    totp = pyotp.TOTP(secret)
    return {
        "secret": secret,
        "provisioning_uri": totp.provisioning_uri(name=user.email, issuer_name="StickerHub"),
        "message": "Scan the URI in an authenticator app, then confirm with a code.",
    }


@auth_bp.post("/mfa/enable")
@jwt_required()
@limiter.limit("10 per hour")
def mfa_enable():
    user = db.session.get_or_404(User, int(get_jwt_identity()))
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()

    if not user.mfa_secret:
        return {"error": "Run MFA setup first."}, 400

    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(code, valid_window=1):
        return {"error": "Invalid authenticator code."}, 400

    user.mfa_enabled = True
    user.invalidate_sessions()
    db.session.commit()
    return issue_auth_response(user)


@auth_bp.post("/mfa/disable")
@jwt_required()
@limiter.limit("10 per hour")
def mfa_disable():
    user = db.session.get_or_404(User, int(get_jwt_identity()))
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""

    if not user.check_password(password):
        return {"error": "Invalid password."}, 401
    if user.mfa_enabled and not verify_mfa_code(user, code):
        return {"error": "Invalid authenticator code."}, 401

    user.mfa_enabled = False
    user.mfa_secret = None
    user.invalidate_sessions()
    db.session.commit()
    return issue_auth_response(user)
