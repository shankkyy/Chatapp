"""Room chat over WebSocket, with messages stored in Supabase."""
import asyncio
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field, ValidationError, field_validator

from auth import user_from_access_token
from schemas import ChatMessageResponse, ChatRoomCreate, ChatRoomResponse
from supabase_client import supabase

router = APIRouter(tags=["chat"])

MESSAGE_COLUMNS = "id,room_id,user_id,content,created_at,users(username)"
HISTORY_LIMIT = 50


class IncomingMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[int, list[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket) -> None:
        self.rooms.setdefault(room_id, []).append(websocket)

    def disconnect(self, room_id: int, websocket: WebSocket) -> None:
        connections = self.rooms.get(room_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.rooms.pop(room_id, None)

    def online_count(self, room_id: int) -> int:
        return len(self.rooms.get(room_id, []))

    async def broadcast(self, room_id: int, payload: dict) -> None:
        dead: list[WebSocket] = []
        for connection in list(self.rooms.get(room_id, [])):
            try:
                await connection.send_json(payload)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(room_id, connection)


manager = ConnectionManager()
_db_lock = asyncio.Lock()


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


async def db_call(fn, *args):
    async with _db_lock:
        return await asyncio.to_thread(fn, *args)


def username_from(row: dict) -> str | None:
    author = row.get("users")
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        return author.get("username")
    return None


def shape_message(row: dict, username: str | None = None) -> dict:
    return {
        "id": row["id"],
        "room_id": row["room_id"],
        "user_id": row["user_id"],
        "username": username if username is not None else username_from(row),
        "content": row["content"],
        "created_at": row["created_at"],
    }


def fetch_room(room_id: int):
    response = execute(supabase.table("chat_rooms").select("id,name").eq("id", room_id))
    rows = response.data or []
    return rows[0] if rows else None


def fetch_history(room_id: int):
    response = execute(
        supabase.table("chat_messages")
        .select(MESSAGE_COLUMNS)
        .eq("room_id", room_id)
        .order("created_at", desc=True)
        .limit(HISTORY_LIMIT)
    )
    rows = [shape_message(row) for row in reversed(response.data or [])]
    return rows


def insert_message(room_id: int, user_id: int, content: str):
    response = execute(
        supabase.table("chat_messages").insert({
            "room_id": room_id,
            "user_id": user_id,
            "content": content,
            "created_at": utc_now(),
        }).select(MESSAGE_COLUMNS)
    )
    return first_row(response, "Message was not saved")


def insert_room(name: str):
    response = execute(
        supabase.table("chat_rooms").insert({
            "name": name,
            "created_at": utc_now(),
        }).select("id,name,created_at")
    )
    return first_row(response, "Room was not created")


def list_room_rows():
    response = execute(
        supabase.table("chat_rooms").select("id,name,created_at").order("id")
    )
    return response.data or []


@router.get("/chat", include_in_schema=False)
def chat_page():
    return FileResponse(Path(__file__).parent / "static" / "chat.html")


@router.post("/api/chat/rooms", response_model=ChatRoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(room: ChatRoomCreate):
    name = room.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Room name is required")
    return insert_room(name)


@router.get("/api/chat/rooms", response_model=list[ChatRoomResponse])
def list_rooms():
    return list_room_rows()


@router.get("/api/chat/rooms/{room_id}/messages", response_model=list[ChatMessageResponse])
def list_messages(
    room_id: int,
    limit: int = Query(HISTORY_LIMIT, ge=1, le=100),
):
    if not fetch_room(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    response = execute(
        supabase.table("chat_messages")
        .select(MESSAGE_COLUMNS)
        .eq("room_id", room_id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    return [shape_message(row) for row in reversed(response.data or [])]


@router.websocket("/ws/chat/{room_id}")
async def chat_socket(websocket: WebSocket, room_id: int, token: str = Query(..., min_length=1)):
    await websocket.accept()
    joined = False
    user = None
    try:
        try:
            user = await db_call(user_from_access_token, token)
            room = await db_call(fetch_room, room_id)
        except HTTPException as exc:
            await websocket.send_json({"type": "error", "detail": exc.detail})
            await websocket.close(code=1008)
            return

        if not user:
            await websocket.send_json({"type": "error", "detail": "User not found"})
            await websocket.close(code=1008)
            return
        if not room:
            await websocket.send_json({"type": "error", "detail": "Room not found"})
            await websocket.close(code=1008)
            return

        try:
            history = await db_call(fetch_history, room_id)
        except HTTPException as exc:
            await websocket.send_json({"type": "error", "detail": exc.detail})
            await websocket.close(code=1011)
            return

        await manager.connect(room_id, websocket)
        joined = True
        await websocket.send_json({"type": "history", "messages": history})
        await manager.broadcast(room_id, {
            "type": "presence",
            "event": "join",
            "user_id": user["id"],
            "username": user["username"],
            "online": manager.online_count(room_id),
        })

        while True:
            try:
                payload = await websocket.receive_json()
            except JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Message must be JSON"})
                continue

            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "detail": "Message must be a JSON object"})
                continue

            try:
                incoming = IncomingMessage.model_validate(payload)
            except ValidationError:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Send JSON like {\"content\": \"hello\"}. Content must be 1-2000 characters.",
                })
                continue

            try:
                saved = await db_call(
                    insert_message, room_id, user["id"], incoming.content
                )
            except HTTPException as exc:
                await websocket.send_json({"type": "error", "detail": exc.detail})
                continue

            await manager.broadcast(room_id, {
                "type": "message",
                **shape_message(saved, username=user["username"]),
            })
    except WebSocketDisconnect:
        pass
    finally:
        if joined:
            manager.disconnect(room_id, websocket)
            await manager.broadcast(room_id, {
                "type": "presence",
                "event": "leave",
                "user_id": user["id"],
                "username": user["username"],
                "online": manager.online_count(room_id),
            })
