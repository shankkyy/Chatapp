"""FastAPI chat application backed by Supabase."""
from datetime import datetime, timezone
import os

from fastapi import FastAPI, HTTPException, Query, status
from passlib.context import CryptContext
from postgrest.exceptions import APIError

from auth import router as auth_router
from chat import router as chat_router
from schemas import UserCreate, UserResponse, UserUpdate
from supabase_client import supabase

app = FastAPI(
    title="FastAPI Chat",
    description="Chat application using Supabase",
    version="1.0.0",
)
app.include_router(auth_router)
app.include_router(chat_router)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

USER_COLUMNS = (
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


def first_row(response, detail: str):
    rows = response.data or []
    if not rows:
        raise HTTPException(status_code=404, detail=detail)
    return rows[0]


@app.get("/")
def root():
    return {"message": "Welcome to FastAPI Chat", "docs": "/docs", "chat": "/chat"}


@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": utc_now()}


# ==================== USER ENDPOINTS ====================

@app.post("/api/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate):
    existing_email = execute(
        supabase.table("users").select("id").eq("email", user.email)
    )
    if existing_email.data:
        raise HTTPException(status_code=400, detail="Email already registered")

    existing_username = execute(
        supabase.table("users").select("id").eq("username", user.username)
    )
    if existing_username.data:
        raise HTTPException(status_code=400, detail="Username already taken")

    now = utc_now()
    response = execute(
        supabase.table("users").insert({
            "email": user.email,
            "username": user.username,
            "hashed_password": pwd_context.hash(user.password),
            "full_name": user.full_name,
            "bio": user.bio,
            "profile_picture_url": user.profile_picture_url,
            "is_active": True,
            "is_admin": False,
            "created_at": now,
            "updated_at": now,
        }).select(USER_COLUMNS)
    )
    return first_row(response, "User was not created")


@app.get("/api/users", response_model=list[UserResponse])
def list_users(skip: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100)):
    response = execute(
        supabase.table("users")
        .select(USER_COLUMNS)
        .order("id")
        .range(skip, skip + limit - 1)
    )
    return response.data


@app.get("/api/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int):
    response = execute(
        supabase.table("users").select(USER_COLUMNS).eq("id", user_id)
    )
    return first_row(response, "User not found")


@app.put("/api/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, user_update: UserUpdate):
    payload = user_update.model_dump(exclude_unset=True)
    payload["updated_at"] = utc_now()
    response = execute(
        supabase.table("users")
        .update(payload)
        .eq("id", user_id)
        .select(USER_COLUMNS)
    )
    return first_row(response, "User not found")


@app.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int):
    response = execute(supabase.table("users").delete().eq("id", user_id))
    first_row(response, "User not found")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
