"""Register and login through Supabase Auth."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from postgrest.exceptions import APIError
from supabase import create_client
from supabase_auth.errors import AuthApiError, AuthInvalidCredentialsError

from schemas import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from supabase_client import key, supabase, url

router = APIRouter(tags=["auth"])
bearer = HTTPBearer()

PROFILE_COLUMNS = (
    "id,email,username,full_name,bio,profile_picture_url,"
    "is_active,is_admin,created_at,updated_at"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute(query):
    try:
        return query.execute()
    except APIError as exc:
        if exc.code == "PGRST205":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase table is missing. Run schema.sql in the Supabase SQL editor.",
            )
        if exc.code == "23505":
            raise HTTPException(status_code=400, detail="A record with that value already exists")
        if exc.code == "23503":
            raise HTTPException(status_code=400, detail="Related record was not found")
        raise HTTPException(status_code=400, detail=exc.message or "Supabase request failed")


def auth_client():
    return create_client(url, key)


def raise_auth_error(exc: Exception) -> None:
    if isinstance(exc, AuthInvalidCredentialsError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if isinstance(exc, AuthApiError):
        message = exc.message or "Authentication failed"
        lowered = message.lower()
        if exc.code == "over_email_send_rate_limit" or "rate limit" in lowered:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Supabase could not send another confirmation email. "
                    "In the Supabase dashboard open Authentication, then Email, "
                    "turn off Confirm email, and register again."
                ),
            )
        if "already" in lowered and "registered" in lowered:
            raise HTTPException(status_code=400, detail="Email already registered")
        if exc.code in {"invalid_credentials", "invalid_grant"} or "invalid login" in lowered:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        code = exc.status if isinstance(exc.status, int) and 400 <= exc.status <= 599 else 400
        raise HTTPException(status_code=code, detail=message)
    raise exc


def profile_by_email(email: str):
    response = execute(
        supabase.table("users").select(PROFILE_COLUMNS).eq("email", email)
    )
    rows = response.data or []
    return rows[0] if rows else None


def profile_by_username(username: str):
    response = execute(
        supabase.table("users").select("id").eq("username", username)
    )
    return bool(response.data)


def insert_profile(email: str, username: str, full_name: str | None):
    now = utc_now()
    response = execute(
        supabase.table("users").insert({
            "email": email,
            "username": username,
            "hashed_password": "supabase-auth",
            "full_name": full_name,
            "is_active": True,
            "is_admin": False,
            "created_at": now,
            "updated_at": now,
        }).select(PROFILE_COLUMNS)
    )
    rows = response.data or []
    if not rows:
        raise HTTPException(status_code=400, detail="Profile was not created")
    return rows[0]


def ensure_profile(email: str, username: str, full_name: str | None = None):
    existing = profile_by_email(email)
    if existing:
        return existing
    if profile_by_username(username):
        username = f"{username}-{email.split('@')[0]}"[:50]
    return insert_profile(email, username, full_name)


def session_payload(session, profile: dict, confirmation_required: bool = False) -> dict:
    return {
        "access_token": session.access_token if session else None,
        "refresh_token": session.refresh_token if session else None,
        "token_type": "bearer",
        "expires_in": session.expires_in if session else None,
        "confirmation_required": confirmation_required,
        "user": profile,
    }


def user_from_access_token(token: str) -> dict:
    client = auth_client()
    try:
        result = client.auth.get_user(token)
    except (AuthApiError, AuthInvalidCredentialsError) as exc:
        raise_auth_error(exc)
    if result is None or result.user is None or not result.user.email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    profile = profile_by_email(result.user.email)
    if not profile:
        metadata = result.user.user_metadata or {}
        username = metadata.get("username") or result.user.email.split("@")[0]
        profile = ensure_profile(result.user.email, username, metadata.get("full_name"))
    return profile


@router.post("/api/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    email = str(body.email)
    username = body.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if profile_by_email(email):
        raise HTTPException(status_code=400, detail="Email already registered")
    if profile_by_username(username):
        raise HTTPException(status_code=400, detail="Username already taken")

    client = auth_client()
    try:
        response = client.auth.sign_up({
            "email": email,
            "password": body.password,
            "options": {
                "data": {
                    "username": username,
                    "full_name": body.full_name,
                }
            },
        })
    except (AuthApiError, AuthInvalidCredentialsError) as exc:
        raise_auth_error(exc)

    auth_user = response.user
    if auth_user is None:
        raise HTTPException(status_code=400, detail="Could not register")
    if not (auth_user.identities or []):
        raise HTTPException(status_code=400, detail="Email already registered")

    profile = ensure_profile(email, username, body.full_name)
    confirmation_required = response.session is None
    return session_payload(response.session, profile, confirmation_required)


@router.post("/api/auth/login", response_model=AuthResponse)
def login(body: LoginRequest):
    client = auth_client()
    try:
        response = client.auth.sign_in_with_password({
            "email": str(body.email),
            "password": body.password,
        })
    except (AuthApiError, AuthInvalidCredentialsError) as exc:
        raise_auth_error(exc)

    if response.session is None or response.user is None or not response.user.email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Confirm your email, then log in.",
        )

    metadata = response.user.user_metadata or {}
    username = metadata.get("username") or response.user.email.split("@")[0]
    profile = ensure_profile(response.user.email, username, metadata.get("full_name"))
    return session_payload(response.session, profile)


@router.get("/api/auth/me", response_model=UserResponse)
def me(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    return user_from_access_token(credentials.credentials)


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    client = auth_client()
    try:
        client.auth.admin.sign_out(credentials.credentials)
    except (AuthApiError, AuthInvalidCredentialsError) as exc:
        raise_auth_error(exc)
