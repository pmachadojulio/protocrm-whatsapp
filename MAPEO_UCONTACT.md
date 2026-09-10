# Mapeo de UContact → Proto-CRM WhatsApp

Fuente: docs oficiales de uContact (docs.ucontactcloud.com, v2.0.0, uContact by net2phone) y
documentación de Gupshup/Meta. Objetivo: entender qué hace UContact para replicar lo replicable
(bajo el canal oficial de Meta).

---

## 1. Qué es UContact

SaaS de contact center **omnicanal** (uContact by net2phone). La capa que se clona es el software;
la capa que no se clona es la certificación Meta/BSP y la telefonía.

| Canal | Proveedores soportados |
|---|---|
| WhatsApp | **Gupshup (BSP)** o **WhatsApp Business (Meta Cloud API)** |
| SMS | Twilio, Infobip, net2phone |
| Voz | net2phone, Unite (SIP/Asterisk), con IVR |
| Email | Gmail, Outlook, SMTP-IMAP |
| Instagram / Messenger | Meta |

Roles: **Administración** (configura), **Supervisión** (métricas/wallboards), **Agente** (inbox de
interacciones), **Desarrollador** (flujos, bots, API, base de datos programable).

## 2. Cómo cuelga WhatsApp (arquitectura real)

Meta es dueño del canal. Gupshup es el **BSP certificado** (intermediario oficial). UContact es el
ISV/software que se conecta a Gupshup:

```
Cliente (WhatsApp)  ──▶  Meta  ──▶  Gupshup (BSP)  ──▶  UContact webhook
                                                        https://<instancia>.ucontactcloud.com/api/inbound/gupshupWpp/event
Cliente  ◀──  Meta  ◀──  Gupshup  ◀──  UContact (envía mensajes / HSM con API key de Gupshup)
```

- Conector Gupshup en UContact = `API Key` (cuenta Gupshup) + `Partner App Token` (Portal de
  Partners) + `App ID` + `Mensajes por segundo` (rate limit) + lista de números.
- Los **templates HSM se sincronizan** desde Gupshup (manual + actualizaciones automáticas; a las
  campañas llegan en estado deshabilitado hasta habilitarse).
- Conector **WhatsApp Business (Meta)**: solo `App ID` + `App secret` de una app de Meta, y la lista
  de números (MPS default 1000). Es decir: UContact también sabe hablar directo con la Cloud API.

## 3. Mapa de módulos y su equivalente en el proto

| Módulo UContact | Qué hace | Equivalente en el proto (n8n + Supabase) |
|---|---|---|
| Usuarios y Perfiles | agentes, roles, permisos, horarios | `crm.agents` + `auth.users` (Supabase Auth) |
| Conectores | proveedores (Gupshup/Meta/…) y números por conector | credenciales de n8n + `WA_PHONE_NUMBER_ID` |
| Canales | conector + campaña/cola + config del canal | `crm.conversations.channel` |
| Campañas | colas inbound/outbound, tipificaciones (niveles) | `crm.campaigns` + `crm.bot_rules` |
| Automatizaciones | flujos sin código (bot/IVR/webhook) | **workflows n8n** (idéntico concepto) |
| Diseñador de Flujos | canvas con nodos: IVR, bot, webhooks, blacklist, transferencias | n8n (canvas de nodos nativo) |
| Diseñador de Bots | chatbot con variables globales y licencias | Code nodes + `crm.bot_rules` + `crm.bot_sessions` |
| uCRM | ficha de contacto; `contactId` linkeado a cada interacción | `crm.contacts` + `custom_fields` jsonb |
| Interacciones | GUID por interacción, segmentos, timings, disposiciones, transfer | `crm.conversations` + `crm.messages` + `crm.tickets` |
| Inbox del Agente | bandeja, responder, disposición | (v1) endpoint de envío del proto + (v2) front con Supabase realtime |
| Supervisión / Wallboards | métricas por agente/campaña, pausas, horario | views SQL + dashboard (Appsmith/Supabase Studio) |
| Hubs Salientes | broadcast outbound con listas/campañas | workflow **Campaña Broadcast** (templates + segmento por tag) |
| Base de Datos uContact | tablas propias + funciones programables | Supabase Postgres + RLS + funciones SQL |
| API REST | Bearer token; interactions / automations / stats / transfers | Supabase REST + endpoints del lanzador n8n |
| Window Proxy | eventos live en UI (assigned/finished/disposition) | Supabase Realtime (futuro inbox) |
| Webhooks de eventos | AgentRingNoAnswer, FinishInteraction, AssignInteraction… | n8n + webhook table de eventos (futuro) |

## 4. Modelo de datos de interacciones (para replicar en el proto)

Del API de interacciones de UContact (GET `/api/interactions`, GET `/api/interactions/id/<guid>`):

- **Interacción** = GUID con: canal, dirección (inbound/outbound), segmentos (campaña+agente),
  client (`id`, `name`, `contactId` = link al CRM), flags (holiday/outOfTime/finished), disposition
  (id + comment + levels), connectorId.
- **Segmento** = etapa del ciclo de vida (cola → agente → transfer): timings (`startDate`,
  `endDate`, `dateAttended`, `dateFirstResponse`, `duration`, `holdtime`, `attentionTime`, `art`),
  `in`/`out` (mensajes entrantes/salientes), events (hold…), transfer (attended/blind), comments.
- Autenticación API: header `Authorization: Bearer <token>`; el token hereda permisos del usuario.

En el proto v1: `conversations` = interacción (contacto + canal + estado), `messages` = historial
(dirección in/out, tipo, body, wa_message_id), `tickets` = caso con prioridad/SLA, y las métricas
(art, first response, holdtime) son calculables en SQL desde `messages.created_at`.

## 5. Lo que NO se clona (moat real de UContact)

1. **Certificación** como BSP de Meta (o acuerdo Partner con Gupshup) para onboardear WABA de
   clientes terceros y facturarles el canal.
2. **Telefonía**: PBX/voz (net2phone, Unite, Asterisk, marcadores predictivos) — dominio aparte.
3. **Soporte y compliance regional** (facturación, GDPR/leyes locales).
4. El diseñador de flujos visual con IVR, colas y transferencias a la altura de un contact center
   de voz — el proto copia el caso WhatsApp, no el PBX.

## 6. Fuentes clave

- Docs UContact: https://docs.ucontactcloud.com/es/ (Administración / Supervisor / Agente / Desarrollador / Integraciones)
- Gupshup en UContact: https://docs.ucontactcloud.com/es/integrations/gupshup/whatsapp.html
- WhatsApp Business (Meta) en UContact: https://docs.ucontactcloud.com/es/integrations/whatsapp-business.html
- API de interacciones: https://docs.ucontactcloud.com/es/developer/api/interactions-api.html
- Guía WhatsApp Business API (Gupshup): https://www.gupshup.io/resources/guide/whatsapp-business-api-guide
- Meta Cloud API: https://developers.facebook.com/docs/whatsapp/cloud-api