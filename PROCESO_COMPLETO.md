# Proceso Completo — ProtoCRM WhatsApp estilo UContact

> **Documento memoria**: todo lo investigado, decidido, construido, probado y lo que falta. Para retomar en otra conversación alcanza con pasar este archivo + `README.md`.
> Última actualización: 2026-09-04. Estado: **empaquetado y probado en mock local, sin desplegar en Docker/Meta real**.

---

## 1. Objetivo original

Mapear **UContact by net2phone** (contact center omnicanal SaaS que usa **Gupshup como BSP** y **Meta WhatsApp Cloud API** como canal) y construir un **proto-CRM WhatsApp escalable** que reemplace tu flujo previo `n8n + Waha (no oficial) + Supabase` por canal **oficial de Meta**, con presupuesto cercano a **$0**, registro completo de mensajes in/out y posibilidad de clonarlo/multi-tenant.

Carpeta de trabajo: `C:\Users\jmachado\Desktop\Automatizacion procesos\Duplicado Ucontact` (vacía al 20/08/2026, dentro del vault Obsidian `Automatizacion procesos` → todo `.md` se sincroniza solo).

---

## 2. Investigación — qué es UContact y cómo cuelga de Meta

**Fuente:** docs oficiales `docs.ucontactcloud.com` v2.0.0 + documentación Gupshup + Meta Cloud API (ver `MAPEO_UCONTACT.md`).

**UContact** = software ISV, no dueño del canal. Capas:

| Canal | Proveedores soportados |
|---|---|
| WhatsApp | **Gupshup (BSP)** o **WhatsApp Business Cloud API** directa |
| SMS | Twilio, Infobip, net2phone |
| Voz | net2phone, Unite, Asterisk |
| Email | Gmail, Outlook, SMTP-IMAP |
| Instagram/Messenger | Meta |

Roles: **Administración** (usuarios/perfiles, conectores, campañas, automatizaciones), **Supervisión** (wallboards, métricas, hubs), **Agente** (inbox), **Desarrollador** (Diseñador de Flujos/Bots, API REST `Authorization: Bearer <token>`, Base de Datos programable, Functions).

**Arquitectura WhatsApp real:**

```
Cliente (WA) → Meta → Gupshup (BSP) → UContact webhook /api/inbound/gupshupWpp/event
Cliente ← Meta ← Gupshup ← UContact (API Key + Partner App Token + App ID, MPS rate limit)
```

* Conector Gupshup en UContact = `API Key` + `Partner App Token` (Portal Partners) + `App ID` + `Mensajes por segundo` + lista de números. Templates HSM sincronizados (manual + updates automáticos).
* Conector Cloud API = `App ID` + `App secret` de app Meta.
* El **moat real** no es el código JS sino la **certificación BSP/Partner** para onboardear WABA de terceros y el soporte/telefonía. Ver `DECISIONES_Y_STACK.md §8`.

Mapa módulo→proto en `MAPEO_UCONTACT.md`:

| Módulo UContact | Equivalente proto |
|---|---|
| Usuarios y Perfiles | `crm.agents` / `crm.users` + `users` en mock |
| Conectores | credenciales n8n + `WA_PHONE_NUMBER_ID` |
| Campañas / Tipificaciones | `crm.campaigns` + `bot_rules` |
| Diseñador de Flujos/Bots | workflows n8n |
| uCRM (`contactId`) | `crm.contacts` + `custom_fields` |
| Interacciones (GUID, segmentos, timings, disposition) | `conversations` + `messages` + `tickets` |
| Hubs Salientes | `WA-Campaign-Broadcast.json` |

---

## 3. Decisiones de stack

**Ver `DECISIONES_Y_STACK.md` completo.**

* **Proto 1 marca (hoy):** Meta Cloud API directa + **n8n (Docker)** + **Postgres local** (mismo `supabase/schema.sql` portable) + **Appsmith CE** (opcional) + **tester `index.html`** + `cloudflared` para webhook. Todo open source.
* **Clon comercial (revender):** mismo software + **Gupshup Partner App / certificación BSP** para onboardear WABA de clientes (multi-WABA, billing por conversación).
* **Descartado:** Waha (`wa-automate`, no oficial) — riesgo de ban, sin templates/HSM ni ventana 24h.

**Comparativa accesos WhatsApp:**

| Opción | Oficial | Multi-marca | Riesgo | Costo |
|---|---|---|---|---|
| Cloud API | sí | manual por WABA | bajo | free tier $0 |
| Gupshup BSP | sí | sí (Partner portal) | bajo | tarifa BSP + Meta |
| Twilio/360dialog/Infobip | sí | depende | bajo | similar |
| Waha | no | no | alto | infra propia |

**Presupuesto $0 (§7):**

| Pieza | Costo | Nota |
|---|---|---|
| Meta Cloud API | $0 | nº test + 1000 conv. servicio/mes + 250 marketing/mes + 1000 contactos únicos / WABA |
| n8n CE | $0 | Docker |
| Postgres local | $0 | reemplaza Supabase nube |
| Appsmith CE | $0 | puerto 8080 |
| Cloudflare Tunnel | $0 | https temporal (cambia URL al reiniciar) |
| Bot `bot_rules` | $0 | SQL; IA opcional Ollama/Groq gratis |

Voz y prospección evaluados en `DECISIONES_Y_STACK.md §8`:

* **Vapi** (voz IA): **NO** es $0 recurrente — `$0.05/min` plataforma + passthrough STT/LLM/TTS/telefonía = **$0.07–0.25/min all-in**, solo trial único ~$10/60min. Útil como canal voz en v3 omnicanal (recordatorios/encuestas), integración trivial vía REST.
* **Agent-Reach** (`github.com/Panniantong/Agent-Reach`, 75k★, MIT, Python 3.10+): CLI sin APIs pagas para Twitter/X, Reddit, Instagram, YouTube, LinkedIn, RSS, búsqueda web. Encaje: `n8n Execute Command → agent-reach` para social listening → leads a `crm.contacts` con tag `prospeccion` → campaña broadcast. Riesgo ToS por cookies.

---

## 4. Arquitectura del proto

```
Cliente → Meta → n8n /webhook/wa-inbound → Postgres (contacts/conversations/messages)
                                        → bot_rules (SQL) → respuesta
                                        → si no matchea y hay key: OpenRouter (LLM free + EMPRESA_CTX) → respuesta
Agente  → login → n8n /webhook/wa-send (X-Auth-Token + X-Api-Key) → Meta → Cliente (pausa bot 30 min, asigna @usuario)
UI      → /webhook/wa-inbox + /wa-msgs?conv_id= + /wa-suggest?conv_id= (IA sugiere 3) + /webhook/contacts + /webhook/wa-handoff/claim/close
```

**Webhooks n8n (host `5678`):**

| Path | Método | Rol | Auth |
|---|---|---|---|
| `/webhook/wa-inbound` | GET (verify) + POST | webhook Meta → guarda + bot | verify `WA_VERIFY_TOKEN` |
| `/webhook/wa-send` | POST | enviar humano (handoff) | `X-Auth-Token` (login) + `X-Api-Key=WA_SEND_KEY` |
| `/webhook/wa-inbox` | GET | lista conversaciones (con `assignee_name`, `last_by`) | — |
| `/webhook/wa-msgs?conv_id=` | GET | historial mensajes (con `created_by_name`) | — |
| `/webhook/wa-suggest?conv_id=` | GET | 3 sugerencias IA | — |
| `/webhook/wa-handoff` | GET/POST | pausar/reanudar bot | — |
| `/webhook/conversations/claim` | POST | tomar conversación (exclusiva) | `X-Auth-Token` |
| `/webhook/conversations/close` | POST | finalizar → `status=closed` | `X-Auth-Token` |
| `/webhook/contacts` | GET | lista contactos con `msg_count` | — |
| `/webhook/contacts/import` | POST | bulk `{contacts:[{phone,name,tags}]}` | `X-Auth-Token` |
| `/webhook/auth-login` | POST | `{username,password}` → `{token,user}` | — |
| `/webhook/auth-me` | GET | valida sesión | `X-Auth-Token` |

Handoff: `conversations.bot_handled` (1=bot, 0=humano) + `HANDOFF_TIMEOUT_MIN=30`. `wa-send` pone `bot_handled=0, assignee_id=usuario, status=open`. `wa-inbound` chequea `Chequear Handoff` → `IF Handoff Activo (bot_handled==0)` → `Fin Handoff - Bot Pausado` (no responde).

---

## 5. Archivos entregados

```
Duplicado Ucontact\
├── README.md                    # documento de retoma (este es PROCESO_COMPLETO, README es resumido)
├── SETUP.md                     # paso a paso deploy (Meta → .env → compose → importar workflows → webhook)
├── PROCESO_COMPLETO.md          # este archivo (memoria super detallada)
├── index.html                   # tester visual local (vanilla JS, habla con webhooks)
├── MAPEO_UCONTACT.md            # investigación UContact
├── DECISIONES_Y_STACK.md        # comparativa, presupuesto $0, roadmap v0→v3, Vapi/Agent-Reach
├── UI_INBOX_APPSMITH.md         # recipe inbox Appsmith (datasource postgres, queries, polling 5s)
├── docker-compose.yml           # postgres 16 + n8n + appsmith + cloudflared ($0, sin Supabase nube)
├── .env.example                 # WA_VERIFY_TOKEN, WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_SEND_KEY, OPENROUTER_API_KEY, LLM_MODEL, EMPRESA_CTX, POSTGRES_PASSWORD
├── empresa_contexto.json        # contexto inyectado en prompt IA: nombre, rubro, horarios Lun-Vie 9-18 sab 9-13, turnos sin turno, dirección
├── contacts_ejemplo.csv         # phone,name,tags ejemplo para importador
├── supabase/schema.sql          # portable: crm.contacts, agents(+pass_hash)/users view, conversations(bot_handled, assignee_id), messages(created_by), tickets, bot_rules, templates, campaigns, v_inbox con assignee_name/last_by, RLS guardado, seed (3 reglas + template + campaign)
├── tester_mock.py               # mock sin Docker: HTTPServer 5678 + SQLite mock.db, imita los 12 webhooks, bot 2 capas + OpenRouter opcional, handoff 30m, login multi-usuario, contactos persistentes
├── n8n/generar_workflows.py     # genera los 4 JSON (py_compile + json.load validación)
├── n8n/WA-Inbound-Meta.json     # 23 nodos (verify, parse, upsert, handoff check, reglas, IF Pide IA → historial → prompt con EMPRESA_CTX → OpenRouter → enviar)
├── n8n/WA-Send-Message.json     # 8 nodos (validar, IF, enviar WA, registrar, Handoff a Humano)
├── n8n/WA-Campaign-Broadcast.json # 6 nodos (cron 1h, obtener audiencia tags ? 'campana-demo', split 5, enviar template)
└── n8n/WA-Inbox-API.json        # 15 nodos (wa-inbox, wa-msgs, wa-suggest con IA, wa-handoff)

Validaciones: `python -m py_compile tester_mock.py` OK, `node check_js.js` → `JS SINTAXIS OK`, `python generar_workflows.py` → 4 JSON OK (20/6/6/15 nodos, conexiones íntegras).
```

---

## 6. Evolución construida (sin Docker)

1. **v0 base** — mapeo + decisiones + compose (n8n+postgres+appsmith+cloudflared) + 3 workflows + schema + `.env.example`.
2. **Tester `index.html`** — conversaciones, historial, responder, **simular mensaje Meta** sin tener número real. Fixes: CORS `allowedOrigins:*` en webhooks (file:// → :5678), `Failed to fetch` diagnosticado.
3. **Presupuesto $0** — Postgres local vía `docker-entrypoint-initdb.d` (schema portable, `pgcrypto`, RLS condicional a rol `authenticated`), sin depender de Supabase nube.
4. **IA opcional** — bot en **dos capas**: reglas SQL (`horario,turno` etc.) + fallback **OpenRouter** (`:free`, env `OPENROUTER_API_KEY`/`LLM_MODEL`, default `llama-3.3-70b:free`). Prompt sistema ahora incluye `EMPRESA_CTX`. Valida sin key (usa defaults). Handoff al llamar OpenRouter: historial 10 msgs.
5. **Login pantalla completa** — overlay que tapa todo hasta `auth-login` (seed `matias/matias123` admin, `asesor1/1234`, `asesor2/1234`). Token `X-Auth-Token` 24h en `SESSIONS`, `localStorage` + `auth-me` para revalidar. Si no logueado, `wa-send` responde `401 no autenticado`.
6. **Bug handoff reportado (river/cholo):** flujo real:
   ```
   cliente 18:12 soy de river... → bot ventas OK
   cliente 18:13 esta matias? → bot fallback OK
   agente 18:14 wacho q tal (wa-send) → bot pausado (no debía, pero siguiente cliente aún disparaba bot)
   cliente 18:14 falopin → bot respondía igual (BUG)
   ```
   **Fix:** `upsert_conversation` preserva `bot_handled`, `wa-send` hace `set_handoff(bot→human)` + `assignee_id`, `wa-inbound` hace `IF Handoff Activo` (bot_handled==0 + timeout 30m) → `Fin Handoff - Bot Pausado`. Validado en mock: `falopin` dio `{"reply":null,"handoff":true}`, `bot reanudado` volvió a responder.
7. **Persistencia contactos** — `mock.db` / Postgres guarda `contacts` por teléfono único (upsert). Importador: `POST /webhook/contacts/import` con CSV (`phone,name,tags`) + UI en drawer. Día 1 `Juan Perez / 54911... hola quería turno` → responde con contexto horarios; día posterior mismo teléfono → mismo `conversation_id`, historial 4 msgs, reconoce nombre. Validado con `contacts/import` + `wa-inbox`/`wa-msgs`.
8. **Menu independiente tipo WhatsApp** — drawer fijo 20vw (1/5 pantalla, min 300px, 85vw en móvil) desde la izquierda, backdrop, transición 0.25s. **Menú ☰** en `topBar` (no dentro de Conversaciones). Dentro 3 secciones **desplegables** (▼): **Contactos** (búsqueda, Importar/Exportar, lista `contactItem` con `msg_count`), **Conexión** (Base n8n + X-Api-Key + Probar), **Log** (consola). Conversaciones queda limpia: solo lista en orden de llegada.
9. **Asignación exclusiva** — click en **Cola** (sin `assignee_id`, `status!=closed`) → `POST /webhook/conversations/claim` → si ya la tiene otro responde `409 ya atendida por @Matias` (solo lectura, reply deshabilitado). **Mías** (assignee==yo) y **Finalizadas** (`status=closed`, botón **Finalizar** en chat) como tabs en la card de Conversaciones. Al desloguearse, tus Mías quedan asignadas y pasan a Finalizadas al cerrar, no vuelven a Cola.

Últimos fixes técnicos:

* `do_OPTIONS` faltaba `X-Auth-Token` en `Allow-Headers` → login OK pero inbox con token fallaba preflight → parcheado.
* `tester_mock.py:298` `el if` duplicado + `mock.db` viejo sin `users`/`created_by` → `SyntaxError` y `Failed to fetch` en wa-inbox → parcheado con `try/except` que devuelve `500 {"hint":"borra mock.db"}` + migración `ALTER TABLE messages ADD COLUMN created_by`.
* `index.html:324` `loadCfg()` después de `btnTest.click()` → invertido.

---

## 7. Pruebas realizadas (mock local, sin Docker)

* `python -m py_compile tester_mock.py` / `generar_workflows.py` OK, `node check_js.js` OK, `python generar_workflows.py` → 4 JSON con conexiones íntegras.
* `tester_mock.py` endpoints: `wa-inbox` (50), `wa-msgs?conv_id=`, `wa-suggest` (precio/horario genérico), `wa-inbound` (campera cholo → ventas), `wa-send` (401 sin token, 200 con token, log `[Matias -> phone]`), `wa-handoff`, `conversations/claim` (409 si tomada), `conversations/close` → `status closed` en Finalizadas, `contacts`, `contacts/import`, `auth-login` (matias OK, asesor1 OK).
* Handoff: `falopin` tras `wacho` dio `reply null`, `assignee=Matias`, `bot=0`.
* Persistencia: Juan Pérez importado, día 1 y día posterior mismo `conversation_id` con historial acumulado.
* UI: login overlay bloqueante, drawer 1/5, tabs Cola/Mías/Finalizadas, claim con 409, export CSV.

Estado del mock al entregar: `mock.db` borrado para demo fresca (seed: 1 contacto demo + 3 reglas + template + campaign + 3 usuarios). El `mock.db` que quede tras probar contiene todo el log multi-usuario.

---

## 8. Lo que falta / no probado

**Sin probar (requiere Docker/Meta real):**

* `docker compose up -d` nunca corrido (Docker Desktop no está en la PC de Nextlab), Postgres 16 con `schema.sql` auto-cargado, `n8n` credenciales (WA + Postgres), `appsmith` recipe, `cloudflared` URL y webhook `wa-inbound` en Meta (verify `WA_VERIFY_TOKEN`).
* Workflows n8n con `WA_PHONE_NUMBER_ID`, `OPENROUTER_API_KEY`, `EMPRESA_CTX` reales y templates HSM aprobados (prueba con número test).
* Rama IA real contra OpenRouter (solo probada fallback sin key y stubs de sugerencias).
* Appsmith inbox real (solo recipe en `UI_INBOX_APPSMITH.md`).

**Funcional pendiente (roadmap v0→v3):**

* v0 pulido: cerrar conversación mueve a Finalizadas (hecho), pero falta **reabrir** desde Finalizadas y **filtros por fecha/búsqueda avanzada**.
* v1 inbox pro: handoff con timeout configurable por conversación, SLA/tickets, descarga `media_url` a disco/Storage, cierre automático por inactividad.
* v2 multi-marca: `tenant_id` + RLS por tenant, ruteo por `phone_number_id`, wallboards SQL, `contacts.tags` segmentación real para campañas.
* v3 producto: Gupshup Partner App / certificación BSP para onboardear WABA de clientes y facturar por conversación; voz Vapi (ya documentado) y prospección Agent-Reach vía `Execute Command`.
* Hardening: rate limiting en `wa-send`/`wa-inbound`, validación `WA_SEND_KEY` también en mock (hoy solo en n8n), sanitización tags, paginación real en inbox (`LIMIT 50` es fijo).

---

## 9. Cómo seguir (próximos pasos sugeridos)

1. **Levantar stack** en máquina con Docker: `Copy-Item .env.example .env` → completar `WA_*`, `OPENROUTER_API_KEY` opcional, `EMPRESA_CTX`, `POSTGRES_PASSWORD` → `docker compose up -d` → `docker compose logs -f tunnel` → anotar `https://xxxx.trycloudflare.com`.
2. Importar 4 workflows en `http://localhost:5678` (registrar usuario n8n), asignar credenciales WA + Postgres (`host=postgres`), activar **Inbound** y **Inbox-API**, crear webhook en Meta con `Callback URL = https://xxx/webhook/wa-inbound` + `Verify Token`.
3. Probar con número test de Meta y con `index.html` (login `matias/matias123`) + importar `contacts_ejemplo.csv`.
4. Ajustar `empresa_contexto.json` con datos reales y (opcional) crear key OpenRouter para IA real.
5. Decidir si Appsmith o `index.html` será inbox definitivo; luego tickets/SLA y media.
6. Para otra conversación: pasar `README.md` + este archivo; el punto de partida es `SETUP.md`.

---

## 10. Referencias

* `MAPEO_UCONTACT.md` (módulos, webhooks Gupshup/Meta, API `interactions` con `guid`, `segments`, `disposition`, `client.contactId`)
* `DECISIONES_Y_STACK.md` (§8 Vapi/Agent-Reach, §7 presupuesto $0, roadmap v0→v3)
* `README.md` (documento de retoma resumido) + `SETUP.md` (paso a paso) + `UI_INBOX_APPSMITH.md`
* `empresa_contexto.json`, `contacts_ejemplo.csv`, `tester_mock.py:13` (PORT 5678, HANDOFF 30m), `supabase/schema.sql:7` (pgcrypto, `crm.v_inbox` con `assignee_name/last_by`)
* `n8n/generar_workflows.py:54` (W1 Inbound con `Chequear Handoff`/`IF Handoff Activo`), `W2 Handoff a Humano`, `W4 wa-suggest` + `wa-handoff`/`claim`/`close`, `WA-Send-Message.json:24` (X-Api-Key), `index.html:50` (login overlay) y `drawers` con tabs `Cola/Mías/Finalizadas`.

> Nota Obsidian: esta carpeta está dentro del vault `Automatizacion procesos` (`.obsidian` en ese nivel), cualquier `.md` editado acá aparece solo en Obsidian (Ctrl+R si no refresca).

---

## 11. Fase A+B — de proto a CRM (2026-09-13)

**Fase A (endurecer, mismo stack) — hecho y testeado (`test_fase_a.py`: 38/38):**
- Passwords `sha256` → **PBKDF2-HMAC-SHA256** (200k iter, salt aleatoria) con **migración transparente** en el próximo login (verificado sobre `mock.db` real).
- **Auth en todos los endpoints** (`wa-inbox`, `wa-msgs`, `wa-suggest`, `wa-handoff`, `contacts`, `claim/close/reopen`, `import`, CRM nuevos). Públicos solo: `auth-login`, `wa-inbound`, `/health`.
- **Sesiones persistentes** en tabla `sessions` (sobreviven reinicios, expiración `SESSION_DAYS`, una activa por usuario, logout invalida).
- **Firma Meta**: `wa-inbound` exige `X-Hub-Signature-256` si hay `WA_APP_SECRET`; verificación `GET hub.mode=subscribe` con `WA_VERIFY_TOKEN`.
- **`WA_SEND_KEY` también en mock** (antes solo n8n) + avisos de arranque si falta secreto.
- **Paginación** (`?limit=&offset=`, `?paged=1` → envelope `{data,total,limit,offset}`) y **sanitización** (teléfonos, largos, tags, tope 2000/lote en import).
- **`backup.py`** (rotación últimas 10 + hint `pg_dump`) y `backups/` ignorado en git.
- Nuevas vars en `.env.example`: `WA_APP_SECRET`, `SESSION_DAYS`.

**Fase B (corazón CRM) — hecho:**
- Tablas `opportunities` (title, amount, stage: nuevo/contactado/cotizado/ganado/perdido), `notes` (note/task/call/visit + due/done), `tickets` (SQLite la suma; SLA por prioridad: critica 2h, alta 8h, normal 24h, baja 72h + flag `breached`). `schema.sql` Postgres sincronizado (RLS + policies + índices).
- Endpoints: `opportunities` + `opportunities/move`, `timeline` (ficha 360: contacto + mensajes + notas + opps + tickets), `notes` + `notes/done`, `tickets` + `tickets/status`.
- `index.html`: tabs **Inbox / Pipeline (kanban con montos) / Ficha cliente (timeline unificado + notas) / Tickets (SLA)** + botón **Reabrir** en Finalizadas.
- `demo.html` (GitHub Pages): suma tab **Pipeline** con kanban simulado.

**Repo:** `github.com/pmachadojulio/protocrm-whatsapp` (público) + demo viva en `.../demo.html`.
