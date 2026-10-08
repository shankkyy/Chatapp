# FastAPI Chat

FastAPI chat app. Users, rooms, and messages are stored in Supabase. Live messages go over a WebSocket.

## Quick Start (Windows)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8001
```

## Quick Start (macOS / Linux)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8001
```

Set `SUPABASE_URL` and `SUPABASE_KEY` in `.env`. Run `schema.sql` once in the Supabase SQL editor so the `users`, `chat_rooms`, and `chat_messages` tables exist. Then open the chat page at http://localhost:8001/chat

## Features

- Register and login with Supabase Auth
- Real-time chat rooms over WebSocket, with messages stored in Supabase
- Chat page at `/chat`
- Interactive API docs at `/docs`

## API Endpoints

### Auth
- POST /api/auth/register
- POST /api/auth/login
- GET /api/auth/me
- POST /api/auth/logout

Register and login return an access token. Send it as `Authorization: Bearer <token>` for `/api/auth/me` and `/api/auth/logout`. If email confirmation is turned on in the Supabase project, register returns `confirmation_required: true` and login works after the email is confirmed.

### Users
- POST /api/users
- GET /api/users
- GET /api/users/{id}
- PUT /api/users/{id}
- DELETE /api/users/{id}

### Chat
- POST /api/chat/rooms
- GET /api/chat/rooms
- GET /api/chat/rooms/{room_id}/messages
- WS /ws/chat/{room_id}?token={access_token}
- GET /chat

Connect with a WebSocket and send JSON `{"content": "hello"}`. The server checks the access token, saves the message, then broadcasts it to everyone in the room. On connect, that socket receives the latest 50 messages and a presence event.
