"""Modelos. Mismo dominio que el mock + schema.sql (SQLite y Postgres)."""
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text

from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    pass_hash = Column(String, nullable=False)
    display_name = Column(String, default="")
    role = Column(String, default="agent")
    active = Column(Boolean, default=True)
    created_at = Column(String)


class Session(Base):
    __tablename__ = "sessions"
    token = Column(String, primary_key=True)  # jti del JWT
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(String)
    exp = Column(Integer)


class Contact(Base):
    __tablename__ = "contacts"
    id = Column(String, primary_key=True)
    phone = Column(String, unique=True, nullable=False)
    name = Column(String, default="")
    email = Column(String, default="")
    tags = Column(Text, default="[]")
    opt_in = Column(Boolean, default=True)
    source = Column(String, default="whatsapp")
    last_seen = Column(String)
    created_at = Column(String)
    updated_at = Column(String)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String, default="whatsapp")
    status = Column(String, default="open")
    assignee_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"))
    bot_handled = Column(Integer, default=1)
    greeted = Column(Integer, default=0)       # saludo abierto ya enviado
    clarify_count = Column(Integer, default=0)  # aclaraciones pedidas (máx 2)
    last_message_at = Column(String)
    created_at = Column(String)
    updated_at = Column(String)


class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    direction = Column(String, nullable=False)
    message_type = Column(String, default="text")
    body = Column(Text)
    media_url = Column(String)
    wa_message_id = Column(String, unique=True)
    status = Column(String, default="sent")
    created_at = Column(String)
    created_by = Column(String)


class BotRule(Base):
    __tablename__ = "bot_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    keywords = Column(Text, nullable=False)
    response = Column(Text, nullable=False)
    priority = Column(Integer, default=100)
    active = Column(Boolean, default=True)


class Intent(Base):
    """Router conversacional: qué intención, si la resuelve el bot o un humano,
    y con qué etiqueta aparece en las sugerencias (chips, no menú 1-2-3)."""
    __tablename__ = "intents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    keywords = Column(Text, nullable=False)
    kind = Column(String, default="auto")  # auto | human
    label = Column(String, default="")     # etiqueta corta para sugerencias
    priority = Column(Integer, default=100)
    active = Column(Boolean, default=True)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id = Column(String, primary_key=True)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    amount = Column(Float, default=0)
    currency = Column(String, default="ARS")
    stage = Column(String, default="nuevo")
    notes = Column(Text, default="")
    created_by = Column(String)
    created_at = Column(String)
    updated_at = Column(String)
    closed_at = Column(String)


class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(String)
    kind = Column(String, default="note")
    body = Column(Text, nullable=False)
    due_at = Column(String)
    done = Column(Integer, default=0)
    created_by = Column(String)
    created_at = Column(String)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(String, primary_key=True)
    conversation_id = Column(String)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"))
    subject = Column(String, default="Sin asunto")
    priority = Column(String, default="normal")
    status = Column(String, default="abierto")
    assignee_id = Column(String)
    sla_due = Column(String)
    created_at = Column(String)
    closed_at = Column(String)
