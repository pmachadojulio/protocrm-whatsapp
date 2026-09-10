# Decisiones de stack y ruta escalable

Tu experiencia previa (n8n + Waha + Supabase) es la base correcta; el único cambio estructural es
**reemplazar Waha (no oficial) por el canal oficial de Meta**. Eso desbloquea: templates HSM,
ventana 24h, máx. tasa, líneas múltiples y cero riesgo de banneo.

## 1. Decisión en una línea

- **Proto (1 marca, probar hoy):** Meta WhatsApp **Cloud API** directa (gratis para testing) +
  n8n + Supabase. Es lo que el flujo de este paquete implementa.
- **Clon real de UContact (revender a clientes):** el software sigue siendo el mismo (n8n +
  Supabase multitenant), pero el acceso a WhatsApp debe ser **Gupshup Partner App** (ISV) o
  certificación BSP de Meta para onboardear WABA de terceros y cobrar por conversación.

## 2. Comparativa de accesos a WhatsApp

| Opción | Oficial Meta | Costo entrada | Multi-marca | Riesgo ban | Uso recomendado |
|---|---|---|---|---|---|
| **Cloud API (Meta)** | sí | free tier + pago por conversación | por WABA/número (manual) | bajo | proto, 1 marca |
| **Gupshup (BSP)** | sí | ~usuariomínimo/APP + tarifa Meta | sí (Partner portal: apps, templates, callbacks) | bajo | clon UContact / ISV |
| Twilio / 360dialog / Infobip | sí | similar | depende | bajo | alternativas BSP |
| **Waha / wa-automate (lo que usaste)** | no | infra propia | no | alto | solo demos, nunca facturar |

## 3. Cloud API vs Gupshup (qué cambiaría en el flujo)

- **Cloud API**: creás app en `developers.facebook.com` → WABA → número de prueba/real → token
  permanente → webhook con verify. Todo con la UI de Meta; un solo cliente.
- **Gupshup**: igual exige WABA por cliente (mandato de Meta), pero agrega: portal de Partners
  (crear apps, sync de templates, billing unificado), webhooks de estado/mensaje, endpoints
  passthrough estilo Meta (`/partner/app/{appId}/v3/...`) y soporte. En el flujo n8n solo cambiaría
  el nodo de envío (URL `https://api.gupshup.io/sm/api/v1/msg` + `apikey` en vez de Graph API).
- El resto del proto (webhook inbound → Supabase → bot → reply) es idéntico para ambos.

## 4. Stack (lo que entrega este paquete)

| Pieza | Rol | Alternativas |
|---|---|---|
| **n8n** (Docker, compose incluido) | orquestador: webhook inbound, bot, envío, campañas | n8n Cloud |
| **Supabase** | Postgres + Auth + Storage + Realtime + RLS | Postgres + backend propio |
| **Cloudflared** (compose) | https público temporal para el webhook de Meta | ngrok / VPS con dominio |
| **bot_rules** (tabla SQL) | respuestas por keywords sin código (v0) | OpenAI/LLM en un Code node después |
| **campañas** (workflow) | broadcast con templates HSM a segmento por tag | — |

## 5. Hoja de ruta escalable

1. **v0 (este paquete)**: mono-marca; incontable; bot por reglas; historial completo; campaña
   broadcast; endpoint de envío (handoff manual vía curl mientras no hay UI).
2. **v1**: inbox de agentes (Appsmith/Refine sobre Supabase Realtime), handoff bot→humano
   (`status` open/bot en `conversations`), tickets + SLA, descarga de media a Storage.
3. **v2 multi-marca**: tabla `tenants`, columna `tenant_id` + RLS; un solo webhook que rutea por
   `phone_number_id`; templates y campañas por marca; wallboards en SQL.
4. **v3 producto**: Gupshup Partner App (o certificación BSP) → onboarding WABA de clientes,
   billing por conversación con margen, dashboards por cliente. = UContact en miniatura.

## 6. Costos a tener presentes (2026)

- Meta cobra **por conversación abierta** en 4 categorías (marketing/utility/auth/service). La
  ventana de **servicio es gratis** mientras dure (usualmente 24h desde el último mensaje del
  usuario): el bot de atención dentro de la ventana cuesta $0.
- Marketing/utility requieren **templates aprobados** y se cobran por conversación.
- Gupshup/BSPs agregan su tarifa (por mensaje/conversación o plan mensual) al costo de Meta.
- Nº de prueba de Meta permite testear gratis sin pagar conversaciones de marketing.

## 7. Cómo llega a presupuesto CERO (sin dejar de ser oficial)

| Pieza | Costo | Nota |
|---|---|---|
| Meta WhatsApp Cloud API | **$0** | free tier: nº de test, 1.000 conversaciones de servicio/mes, 250 de marketing/mes, 1.000 contactos únicos por WABA. Suficiente para una empresa chica sin spam. |
| n8n self-hosted | **$0** | Community Edition (Docker). |
| Postgres local (Docker) | **$0** | reemplaza a Supabase nube: mismo `schema.sql` portable. |
| Appsmith CE (inbox) | **$0** | open source, self-hosted en el compose (puerto 8080). |
| Cloudflare Tunnel | **$0** | https temporal para el webhook (cambia la URL en cada reinicio). |
| Bot | **$0** | `crm.bot_rules` en SQL. IA opcional: Ollama local (gratis) o Groq free tier, sin pagar OpenAI. |

Total recurrente: **$0** (luz e internet). Únicos gastos posibles y opcionales: un VPS (~USD 5/mes)
si querés que el servicio corra 24/7 sin depender de la PC; el pase a pagar en Meta recién cuando
superes el free tier (típicamente >1.000 clientes únicos en el mes).

Manejo de credenciales para que siga siendo gratis: secrets en `.env` (leído por el compose),
nunca en los JSON exportados; el endpoint de envío manual exige header `X-Api-Key` (`WA_SEND_KEY`).

## 8. Canal VOZ (Vapi) y prospección social (Agent-Reach)

### Vapi (voz IA) — cuándo conviene
- Plataforma de agentes de VOZ por teléfono. NO reemplaza el canal WhatsApp texto (que ya corre
  a $0): agrega un canal nuevo con costo por minuto.
- Costo real 2026: fee plataforma $0.05/min + passthrough de STT/LLM/TTS/telefonia =
  **$0.07–0.25/min all-in** (sube con voces premium/modelos grandes). Trial unico ~$10/60 min,
  **sin free tier recurrente**. Enterprise aparte (SOC2/HIPAA).
- Integración trivial con n8n (REST API: crear assistant, llamar `POST /call`, webhooks de
  resultados → se pueden registrar las llamadas en `crm.messages` como canal 'voz').
- Uso recomendado: **v3 omnicanal** (recordatorios, confirmaciones, encuestas por llamada,
  outbound calificado). Piloto con los créditos de prueba antes de comprometer volumen.
- Alternativa $0 estricta para voz (mucho mas trabajo): autohostear Whisper (STT) + Piper/TTS
  libre + LLM propio (Ollama) + SIP barato.

### Agent-Reach (github.com/Panniantong/Agent-Reach) — prospección gratis
- CLI open source (MIT, Python 3.10+, ~75k estrellas) que le da acceso a internet al agente SIN
  APIs pagas: Twitter/X, Reddit, Instagram, YouTube, GitHub, LinkedIn, RSS y busqueda web, con
  routing automatico entre backends y `agent-reach doctor` de diagnostico. Cookies/tokens quedan
  locales.
- Encaje con el proto: **social listening / generacion de leads** — workflow n8n con nodo
  `Execute Command` que corre `agent-reach` (buscar menciones/preguntas del rubro), parsear
  resultados e insertarlos en `crm.contacts` con tag `prospeccion` → alimentar la campana
  broadcast existente.
- Riesgos: scraping basado en cookies = posible violacion de ToS y bloqueos por plataforma;
  usar con volumen bajo y solo fuentes publicas. Requiere Python 3.10+ en el host (o contenedor).

## 9. Riesgos y buenas prácticas

- **Nunca** mandar marketing fuera de la ventana 24h sin template aprobado (bloqueo).
- Responder el webhook de Meta rápidamente (ACK inmediato y procesar en paralelo).
- Dedup por `wa_message_id` (los reintentos de Meta repiten payloads): cubierto en schema+flujo.
- Guardar tokens en credenciales/env, nunca en el workflow exportado.
- El túnel cloudflared cambia de URL en cada reinicio: para uso continuo usar un dominio/VPS.