"""Fase D: métricas del CRM (SQLite y Postgres compatibles)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as SASession

from .config import OPEN_STAGES, STAGES
from .core import ticket_breached
from .models import Conversation, Message, Opportunity, Ticket


def _day_series(db: SASession, days: int = 7) -> list:
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()
    rows = db.query(
        func.substr(Message.created_at, 1, 10).label("d"),
        Message.direction, func.count().label("c"),
    ).filter(Message.created_at >= start).group_by("d", Message.direction).all()
    by_day: dict = {}
    for d, direction, c in rows:
        by_day.setdefault(d, {"in": 0, "out": 0})[direction] = c
    out = []
    for i in range(days):
        day = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).date().isoformat()
        out.append({"day": day, "in": by_day.get(day, {}).get("in", 0),
                    "out": by_day.get(day, {}).get("out", 0)})
    return out


def _avg_first_response_min(db: SASession) -> float | None:
    convs = db.query(Conversation).all()
    deltas = []
    for cv in convs:
        msgs = sorted(
            (m for m in db.query(Message).filter(
                Message.conversation_id == cv.id).all() if m.created_at),
            key=lambda m: m.created_at)
        first_in = next((m for m in msgs if m.direction == "in"), None)
        first_out = next((m for m in msgs
                          if m.direction == "out" and first_in
                          and m.created_at >= first_in.created_at), None)
        if first_in and first_out:
            try:
                a = datetime.fromisoformat(first_in.created_at)
                b = datetime.fromisoformat(first_out.created_at)
                if a.tzinfo is None:
                    a = a.replace(tzinfo=timezone.utc)
                if b.tzinfo is None:
                    b = b.replace(tzinfo=timezone.utc)
                deltas.append((b - a).total_seconds() / 60)
            except Exception:
                pass
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 1)


def overview(db: SASession) -> dict:
    conv_rows = db.query(Conversation.status, func.count()).group_by(Conversation.status).all()
    opp_rows = db.query(Opportunity.stage, func.count(),
                        func.coalesce(func.sum(Opportunity.amount), 0)).group_by(Opportunity.stage).all()
    opp_by_stage = {s: {"count": 0, "amount": 0} for s in STAGES}
    for s, c, amt in opp_rows:
        if s in opp_by_stage:
            opp_by_stage[s] = {"count": c, "amount": round(float(amt or 0), 2)}
    open_amount = round(sum(opp_by_stage[s]["amount"] for s in OPEN_STAGES), 2)
    tickets = db.query(Ticket).all()
    tick_open = sum(1 for t in tickets if t.status in ("abierto", "en_progreso"))
    tick_breached = sum(1 for t in tickets if ticket_breached(t.status, t.sla_due))
    agent_rows = db.query(Message.created_by, func.count()).filter(
        Message.direction == "out", Message.created_by.isnot(None))\
        .group_by(Message.created_by).order_by(func.count().desc()).limit(10).all()
    return {
        "conversations_by_status": {s: c for s, c in conv_rows},
        "opportunities_by_stage": opp_by_stage,
        "pipeline_open_amount": open_amount,
        "tickets_open": tick_open,
        "tickets_breached": tick_breached,
        "messages_per_day": _day_series(db),
        "avg_first_response_min": _avg_first_response_min(db),
        "agent_messages": [{"user_id": u or "bot", "count": c} for u, c in agent_rows],
    }
