# Backend ProtoCRM (Fase C/D)

FastAPI con **la misma API** que el mock/n8n (`/webhook/*`): `index.html`
funciona sin cambios, solo apuntando la Base a `http://localhost:8000`.

## Correr

```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --port 8000
```

Lee el `.env` de la raíz (mismos `WA_*`, `OPENROUTER_*` + `DATABASE_URL`, `JWT_SECRET`).

## Traer tus datos del mock

```bash
python3 backend/import_mock.py [ruta_mock.db]   # default: ./mock.db → backend/crm.db
```

Copia usuarios (hashes intactos, legacy incluido), contactos, conversaciones,
mensajes, reglas y CRM. No pisa lo existente. Después logueate de nuevo
(las sesiones no se migran a propósito).

## Tests

```bash
python3 tests/test_backend.py   # 26 checks: auth, canal, CRM, métricas, RAG
```

## Qué cambia vs el mock/n8n

- Auth **JWT + sesiones revocables** (tabla `sessions`), mismos headers
  (`X-Auth-Token` o `Authorization: Bearer`).
- SQLite por defecto (`backend/crm.db`); Postgres vía `DATABASE_URL`.
- n8n queda para automatizaciones/campañas, no como núcleo.
- Fase D: `kb/*.md` (RAG sin dependencias), handoff automático por intención,
  `metrics/overview`, `conversations/summarize`.

## Estructura

```
backend/app/
  main.py routers/{auth,channel,inbox,contacts,crm,insights}.py
  core.py (bot en capas + handoff)  rag.py (TF-IDF stdlib)  metrics.py
  models.py db.py seed.py security.py deps.py config.py
```
