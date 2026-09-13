# ProtoCRM WhatsApp (estilo UContact) — Documento de retoma

> Para retomar en **otra conversación**: pasale a la IA este README (o la carpeta del proyecto)
> y pedile "seguir con el proto CRM WhatsApp". Este archivo tiene el estado, la decisión de stack
> y los próximos pasos.

## Qué es

Proto-CRM de WhatsApp para una empresa: **registro completo de mensajes entrantes y salientes**,
bot por reglas, campañas y (opcional) inbox de agentes. Réplica del caso WhatsApp de UContact
(contact center omnicanal de net2phone que usa Gupshup/Meta como canal).

## Decisión de stack (adoptada)

- **Canal**: Meta WhatsApp Cloud API directa (oficial, free tier $0). Gupshup = alternativa
  cuando se quiera revender (modelo UContact). NUNCA Waha/no oficial (riesgo de ban).
- **Orquestación**: n8n self-hosted (Docker).
- **Datos**: Postgres local (Docker) — portable a Supabase nube (mismo `schema.sql`).
- **UI**: `index.html` (tester vanilla JS, $0) y Appsmith CE (inbox de agentes, opcional).
- **Costos**: $0 recurrentes (free tier Meta 1.000 conversaciones servicio/mes + 250 marketing/mes).

## Arquitectura

```
Cliente → Meta → n8n /webhook/wa-inbound → Postgres (contacts/conversations/messages)
                                        → bot_rules (SQL) → respuesta
                                        → si no matchea y hay key: OpenRouter (LLM free) → respuesta
Agente  → Appsmith (8080) o index.html → n8n /webhook/wa-send → Meta → Cliente
UI      → wa-inbox / wa-msgs (JSON) + wa-suggest (3 respuestas sugeridas por IA)
```

Webhooks de n8n (path dentro del host 5678):

| Path | Método | Rol |
|---|---|---|
| `/webhook/wa-inbound` | POST | webhook de Meta (mensajes + verify GET) → guarda y responde (reglas SQL + fallback IA) |
| `/webhook/wa-send` | POST | enviar mensaje fuera de la ventana 24h o responder; requiere header `X-Api-Key` = `WA_SEND_KEY` |
| `/webhook/wa-inbox` | GET | lista conversaciones (JSON) para la UI |
| `/webhook/wa-msgs?conv_id=` | GET | historial de mensajes (JSON) para la UI |
| `/webhook/wa-suggest?conv_id=` | GET | 3 posibles respuestas sugeridas por LLM (JSON array; vacío sin key) |

## Estructura de archivos

```
Duplicado Ucontact\
├── README.md                    <- este documento de retoma
├── SETUP.md                     <- paso a paso para desplegar
├── index.html                   <- tester visual local (abrir con doble click)
├── MAPEO_UCONTACT.md            <- investigación: módulos de UContact y equivalencias
├── DECISIONES_Y_STACK.md        <- comparativa de canales, presupuesto $0, roadmap v0→v3
├── UI_INBOX_APPSMITH.md         <- recipe del inbox de agentes (Appsmith)
├── docker-compose.yml           <- postgres + n8n + appsmith + cloudflared
├── .env.example                 <- secretos (copiar a .env, no commitear)
├── supabase\
│   └── schema.sql               <- esquema portable (local o Supabase) + seed + RLS guardado
└── n8n\
    ├── generar_workflows.py     <- genera los 4 JSON (py_compile + correr + validar)
    ├── WA-Inbound-Meta.json
    ├── WA-Send-Message.json
    ├── WA-Campaign-Broadcast.json
    └── WA-Inbox-API.json
```

## Estado (checkpoint 20/08/2026)

**Hecho**
- Investigación UContact completa (docs oficiales): integración Gupshup/Meta, API REST, modelo de
  interacciones → `MAPEO_UCONTACT.md`.
- Decisión de stack + análisis presupuesto $0 → `DECISIONES_Y_STACK.md`.
- Compose: postgres (schema auto-cargado) + n8n + appsmith + cloudflared.
- Bot en DOS capas: reglas SQL (`bot_rules`) + **fallback LLM vía OpenRouter** (modelo `:free`,
  configurable con `LLM_MODEL`; sin key sigue funcionando con defaults).
- **Respuestas sugeridas**: endpoint `wa-suggest` (IA propone 3 opciones) + chips clickeables en
  `index.html`.
- 4 workflows n8n generados y validados (20 nodos Inbound, 12 Inbox-API) + wa-send protegido.
- `index.html` tester (conversaciones, historial, responder, simular entrada de Meta, sugerencias).
- Esquema CRM portable + seed + RLS condicional.

**No probado / pendiente**
- Ningún `docker compose up` se ejecutó (Docker Desktop no está en esta PC).
- No hay app/WABA/número de Meta creados por el usuario ni cuenta OpenRouter.

**Fase A+B (2026-09-13, ver `PROCESO_COMPLETO.md` §11)**
- Seguridad: PBKDF2 + migración de hashes, auth en todos los endpoints, sesiones
  persistentes, firma Meta (`WA_APP_SECRET`), `WA_SEND_KEY` en mock, paginación,
  sanitización, `backup.py`. Tests: `python3 test_fase_a.py` (38/38).
- CRM: pipeline/kanban (`opportunities`), ficha 360 (`timeline`), notas/tareas,
  tickets con SLA. UI: tabs Inbox/Pipeline/Ficha/Tickets + Reabrir en `index.html`;
  `demo.html` suma Pipeline (demo online en GitHub Pages).
- El esquema local no se probó contra Postgres 16 real.
- La rama IA (inbound + sugerencias) no se probó end-to-end contra OpenRouter real.
- Appsmith no configurado (hay recipe, no app armada).

## Cómo retomar en otra conversación

1. Dar contexto: pegar este README (o decir "continuar el proto CRM WhatsApp en
   `Duplicado Ucontact`") e indicar dónde quedó el checkpoint.
2. Próximo paso sugerido Nº 1: ejecutar el stack en una máquina con Docker Desktop:
   `Copy-Item .env.example .env` → completar → `docker compose up -d` → abrir `index.html` y
   simular un mensaje. Reportar errores de arranque (compose/logs de n8n/postgres).
3. Próximo paso sugerido Nº 2: flujo del lado del cliente — cuenta Meta (app + WABA + test
   number + token + webhook) siguiendo `SETUP.md` secciones 2 y 5.

## Siguientes funcionalidades (roadmap v0→v3)

1. Inbox completo de agentes (Appsmith o front propio): handoff bot→humano (status open/bot),
   asignación, tickets + SLA.
2. Media a disco (descargar media_url con token de acceso cuando se reciba imagen/audio).
3. Multi-marca (v2): `tenant_id` + RLS por tenant, ruteo por `phone_number_id`.
4. IA: YA implementada (OpenRouter free como fallback del bot + sugerencias). Mejoras posibles:
   memoria de largo plazo, RAG sobre documentos de la empresa, handoff automático por intención.
5. Producto comercial (v3): Gupshup Partner / certificación BSP → onboarding WABA de clientes +
   facturación por conversación.
6. Voz/SMS/email (omnicanal) como UContact: conectores Twilio/Infobip/n2p sobre el mismo modelo
   de interacciones. Voz IA: **Vapi** ($0.05/min + passthrough, trial ~$10 — ver
   DECISIONES_Y_STACK.md §8; NO es $0 recurrente).
7. Prospección social $0: **Agent-Reach** (CLI open source, sin APIs pagas) desde n8n
   `Execute Command` → leads a `crm.contacts` con tag `prospeccion` → campaña broadcast.