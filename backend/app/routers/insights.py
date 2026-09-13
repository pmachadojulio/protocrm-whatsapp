"""Fase D: métricas, resúmenes y base de conocimiento (RAG)."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session as SASession

from .. import metrics, rag
from ..config import EMPRESA_CTX, OPENROUTER_API_KEY, LLM_MODEL
from ..core import now_iso
from ..deps import get_current_user, get_db
from ..models import Conversation, Message, User

router = APIRouter()


@router.get("/webhook/metrics/overview")
def metrics_overview(user: User = Depends(get_current_user),
                     db: SASession = Depends(get_db)):
    return metrics.overview(db)


@router.post("/webhook/conversations/summarize")
def summarize(body: dict,
              user: User = Depends(get_current_user),
              db: SASession = Depends(get_db)):
    conv_id = body.get("conv_id") or body.get("conversation_id") or ""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        return JSONResponse({"ok": False, "error": "conversacion no encontrada"}, 404)
    msgs = db.query(Message).filter(Message.conversation_id == conv_id)\
        .order_by(Message.created_at).all()
    if not msgs:
        return {"ok": True, "mode": "vacia", "summary": "Sin mensajes."}
    lines = [f"{'Cliente' if m.direction == 'in' else 'Agente'}: {m.body or ''}"
             for m in msgs[-20:]]
    if OPENROUTER_API_KEY:
        try:
            import json
            import urllib.request
            system = (f"Sos el asistente de {EMPRESA_CTX.get('nombre','la empresa')}. "
                      f"Resumí esta conversación de WhatsApp en 3 viñetas cortas + "
                      f"sentimiento del cliente (positivo/neutro/negativo) + próxima "
                      f"acción sugerida. Español rioplatense, breve.")
            payload = json.dumps({"model": LLM_MODEL,
                                  "messages": [{"role": "system", "content": system},
                                               {"role": "user",
                                                "content": "\n".join(lines)}],
                                  "max_tokens": 300, "temperature": 0.4}).encode()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions", data=payload,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                summary = (data["choices"][0]["message"]["content"] or "").strip()
                if summary:
                    return {"ok": True, "mode": "ia", "summary": summary}
        except Exception as e:
            print(f"[summarize] LLM fallo, extractivo: {e}")
    first_in = next((m.body for m in msgs if m.direction == "in"), "")
    last = msgs[-3:]
    summary = (f"{len(msgs)} mensajes. "
               f"Arranca: {(first_in or '')[:120]}. "
               f"Cierre: {' / '.join((m.body or '')[:80] for m in last)}")
    return {"ok": True, "mode": "extractivo", "summary": summary}


@router.get("/webhook/kb/search")
def kb_search(q: str = "", k: int = 3,
              user: User = Depends(get_current_user)):
    if not q.strip():
        return {"ok": False, "error": "falta q"}
    return {"ok": True, "results": rag.search(q, k=max(1, min(k, 10)))}


@router.post("/webhook/kb/reindex")
def kb_reindex(user: User = Depends(get_current_user)):
    n = rag.reindex()
    return {"ok": True, "chunks": n, "at": now_iso()}


@router.get("/webhook/kb/status")
def kb_status(user: User = Depends(get_current_user)):
    if not rag._state["chunks"]:
        rag.reindex()
    return {"ok": True, "chunks": len(rag._state["chunks"])}
