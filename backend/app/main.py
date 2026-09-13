"""App FastAPI ProtoCRM. Misma API que el mock/n8n (/webhook/*)."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import rag
from .config import WA_APP_SECRET, WA_SEND_KEY, WA_VERIFY_TOKEN
from .db import SessionLocal, init_db, migrate
from .routers import auth, channel, contacts, crm, inbox, insights
from .seed import run_seed

app = FastAPI(title="ProtoCRM WhatsApp", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(channel.router)
app.include_router(inbox.router)
app.include_router(contacts.router)
app.include_router(crm.router)
app.include_router(insights.router)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def startup():
    init_db()
    migrate()
    db = SessionLocal()
    try:
        run_seed(db)
    finally:
        db.close()
    n = rag.reindex()
    print(f"[kb] {n} chunks indexados")
    if not WA_VERIFY_TOKEN:
        print("[aviso] WA_VERIFY_TOKEN sin configurar")
    if not WA_SEND_KEY:
        print("[aviso] WA_SEND_KEY sin configurar (solo desarrollo)")
    if not WA_APP_SECRET:
        print("[aviso] WA_APP_SECRET sin configurar (solo desarrollo)")
    print("ProtoCRM backend en http://localhost:8000 (Base para index.html)")
