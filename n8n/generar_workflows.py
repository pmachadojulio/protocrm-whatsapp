# -*- coding: utf-8 -*-
"""Genera los workflows n8n del Proto-CRM WhatsApp (Meta Cloud API).

Uso:  python n8n\\generar_workflows.py
Salida: n8n\\WA-Inbound-Meta.json, n8n\\WA-Send-Message.json, n8n\\WA-Campaign-Broadcast.json
"""
import json
import os
import uuid

OUT = os.path.dirname(os.path.abspath(__file__))


def uid():
    return str(uuid.uuid4())


def node(name, ntype, version, params, pos, webhook_id=None):
    n = {
        "parameters": params,
        "id": uid(),
        "name": name,
        "type": ntype,
        "typeVersion": version,
        "position": pos,
    }
    if webhook_id:
        n["webhookId"] = webhook_id
    return n


def link(dst, idx=0):
    return {"node": dst, "type": "main", "index": idx}


def workflow(name, nodes, connections, tags=None):
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"instanceId": uid()},
        "active": False,
        "tags": tags or ["proto-crm"],
    }


# ---------------------------------------------------------------------------
# Workflow 1: Inbound Meta Cloud API (webhook + bot + persistencia)
# ---------------------------------------------------------------------------
W1_NAME = "ProtoCRM WA Inbound (Meta Cloud API)"

w1_nodes = [
    node("Webhook Entrante", "n8n-nodes-base.webhook", 2, {
        "path": "wa-inbound",
        "httpMethod": ["GET", "POST"],
        "responseMode": "responseNode",
        "options": {"allowedOrigins": "*"},
    }, [0, 0], webhook_id="w1-inbound-" + uuid.uuid4().hex[:12]),

    node("IF Verificacion Meta", "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [
                {
                    "id": uid(),
                    "leftValue": "={{ $json.query['hub.mode'] }}",
                    "rightValue": "subscribe",
                    "operator": {"type": "string", "operation": "equals", "singleValue": True},
                },
                {
                    "id": uid(),
                    "leftValue": "={{ $json.query['hub.verify_token'] }}",
                    "rightValue": "={{ $env.WA_VERIFY_TOKEN }}",
                    "operator": {"type": "string", "operation": "equals", "singleValue": True},
                },
            ],
            "combinator": "and",
        }
    }, [220, 0]),

    node("Responder Challenge", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "text",
        "responseBody": "={{ $json.query['hub.challenge'] }}",
        "options": {},
    }, [440, -180]),

    node("ACK 200", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "text",
        "responseBody": "OK",
        "options": {},
    }, [440, 180]),

    node("Parsear Payload Meta", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const item = $input.first().json;\n"
            "const body = item.body;\n"
            "const out = [];\n"
            "if (body && Array.isArray(body.entry)) {\n"
            "  for (const entry of body.entry) {\n"
            "    for (const change of (entry.changes || [])) {\n"
            "      const value = change.value || {};\n"
            "      if (Array.isArray(value.messages)) {\n"
            "        for (const m of value.messages) {\n"
            "          const meta = value.metadata || {};\n"
            "          const profile = (value.contacts && value.contacts[0] && value.contacts[0].profile) ? value.contacts[0].profile : {};\n"
            "          let textBody = null;\n"
            "          let mediaUrl = null;\n"
            "          if (m.type === 'text' && m.text) textBody = m.text.body;\n"
            "          else if (m.type === 'button' && m.button) textBody = m.button.text;\n"
            "          else if (m.type === 'interactive' && m.interactive) {\n"
            "            if (m.interactive.list_reply) textBody = m.interactive.list_reply.title;\n"
            "            else if (m.interactive.button_reply) textBody = m.interactive.button_reply.title;\n"
            "          } else if (m.type === 'image' && m.image) mediaUrl = m.image.url;\n"
            "          else if (m.type === 'audio' && m.audio) mediaUrl = m.audio.url;\n"
            "          else if (m.type === 'video' && m.video) mediaUrl = m.video.url;\n"
            "          else if (m.type === 'document' && m.document) mediaUrl = m.document.url;\n"
            "          else if (m.type === 'sticker' && m.sticker) mediaUrl = m.sticker.url;\n"
            "          out.push({\n"
            "            message_id: m.id || null,\n"
            "            from: m.from || null,\n"
            "            profile_name: profile.name || null,\n"
            "            timestamp: m.timestamp ? new Date(parseInt(m.timestamp, 10) * 1000).toISOString() : null,\n"
            "            phone_number_id: meta.phone_number_id || null,\n"
            "            display_phone_number: meta.display_phone_number || null,\n"
            "            message_type: m.type || 'unknown',\n"
            "            text_body: textBody,\n"
            "            media_url: mediaUrl\n"
            "          });\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
            "if (out.length === 0) return [{ json: { guard: true, reason: 'sin mensajes' } }];\n"
            "return out.map(i => ({ json: i }));"
        ),
    }, [660, 180]),

    node("IF Procesar", "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [
                {
                    "id": uid(),
                    "leftValue": "={{ $json.guard }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true"},
                }
            ],
            "combinator": "and",
        }
    }, [880, 180]),

    node("Fin (sin mensajes)", "n8n-nodes-base.noOp", 1, {}, [1100, 300]),
    node("NoOp", "n8n-nodes-base.noOp", 1, {}, [3000, 300]),

    node("Upsert Contacto", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.contacts (phone, name, last_seen)\n"
            "VALUES ({{ $json.from }}, {{ JSON.stringify($json.profile_name) }}, now())\n"
            "ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name, last_seen = now()\n"
            "RETURNING id;"
        ),
        "options": {},
    }, [1100, 60]),

    node("Upsert Conversacion", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.conversations (contact_id, channel, wa_phone_number_id, status, bot_handled, last_message_at)\n"
            "VALUES ((SELECT id FROM crm.contacts WHERE phone = {{ $json.from }}), 'whatsapp', "
            "{{ JSON.stringify($json.phone_number_id) }}, 'bot', 1, now())\n"
            "ON CONFLICT (contact_id, channel) DO UPDATE SET\n"
            "  last_message_at = now()\n"
            "RETURNING id;"
        ),
        "options": {},
    }, [1300, 60]),

    node("Insertar Mensaje Entrante", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.messages (conversation_id, direction, message_type, body, media_url, wa_message_id, created_at)\n"
            "VALUES (\n"
            "  (SELECT id FROM crm.conversations WHERE contact_id = "
            "(SELECT id FROM crm.contacts WHERE phone = {{ $json.from }}) AND channel = 'whatsapp'),\n"
            "  'in', {{ JSON.stringify($json.message_type) }}, {{ JSON.stringify($json.text_body) }}, "
            "{{ JSON.stringify($json.media_url) }}, {{ JSON.stringify($json.message_id) }}, now()\n"
            ")\n"
            "ON CONFLICT (wa_message_id) DO NOTHING;"
        ),
        "options": {},
    }, [1500, 60]),

    node("Chequear Handoff", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT bot_handled, status, last_message_at,\n"
            "  (SELECT created_at FROM crm.messages WHERE conversation_id=c.id AND direction='out' ORDER BY created_at DESC LIMIT 1) as last_human_at\n"
            "FROM crm.conversations c WHERE contact_id=(SELECT id FROM crm.contacts WHERE phone={{ $json.from }}) AND channel='whatsapp';"
        ),
        "options": {},
    }, [1600, 140]),

    node("IF Handoff Activo", "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [
                {
                    "id": uid(),
                    "leftValue": "={{ $json.bot_handled }}",
                    "rightValue": 0,
                    "operator": {"type": "number", "operation": "equals", "singleValue": True},
                }
            ],
            "combinator": "and",
        }
    }, [1750, 140]),

    node("Fin Handoff - Bot Pausado", "n8n-nodes-base.noOp", 1, {}, [1900, 250]),

    node("Cargar Reglas Bot", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT id, keywords, response, priority\n"
            "FROM crm.bot_rules\n"
            "WHERE active = true\n"
            "ORDER BY priority ASC, id ASC;"
        ),
        "options": {},
    }, [1700, 60]),

    node("Decidir Respuesta Bot", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const msg = $input.first().json;\n"
            "const rules = $('Cargar Reglas Bot').all();\n"
            "const text = (msg.text_body || '').toLowerCase();\n"
            "let reply = null;\n"
            "for (const r of rules) {\n"
            "  const kws = (r.json.keywords || '').split(',').map(k => k.trim().toLowerCase()).filter(Boolean);\n"
            "  if (kws.some(k => text.includes(k))) { reply = r.json.response; break; }\n"
            "}\n"
            "const hasKey = !!(process.env.OPENROUTER_API_KEY || '');\n"
            "let need_llm = false;\n"
            "if (!reply) {\n"
            "  if (hasKey) {\n"
            "    need_llm = true;\n"
            "  } else if (/^(hola|buenas|buen dia|buenos dias|hello|hey)/.test(text)) {\n"
            "    reply = 'Hola! Gracias por escribirnos. Como podemos ayudarte?';\n"
            "  } else {\n"
            "    reply = 'Hola, gracias por escribirnos. Un asesor te respondera a la brevedad.';\n"
            "  }\n"
            "}\n"
            "return [{ json: { ...msg, reply, need_llm } }];"
        ),
    }, [1900, 60]),

    node("IF Pide IA", "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [
                {
                    "id": uid(),
                    "leftValue": "={{ $json.need_llm }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true"},
                }
            ],
            "combinator": "and",
        }
    }, [2100, 180]),

    node("Historial Reciente", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT direction, body\n"
            "FROM crm.messages\n"
            "WHERE conversation_id = (\n"
            "  SELECT id FROM crm.conversations\n"
            "  WHERE contact_id = (SELECT id FROM crm.contacts WHERE phone = {{ $json.from }})\n"
            "    AND channel = 'whatsapp')\n"
            "ORDER BY created_at DESC\n"
            "LIMIT 10;"
        ),
        "options": {},
    }, [2320, 180]),

    node("Armar Prompt IA", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const cur = $('Decidir Respuesta Bot').first().json;\n"
            "const hist = $input.all().reverse()\n"
            "  .filter(i => i.json.body)\n"
            "  .map(i => ({ role: i.json.direction === 'in' ? 'user' : 'assistant', content: String(i.json.body) }));\n"
            "const empresa = process.env.EMPRESA_CTX || 'Empresa de indumentaria. Horarios: Lunes a viernes 9-18, sábados 9-13. Turnos: sin turno previo.';\n"
            "const sys = 'Sos el asistente de ' + empresa + ' por WhatsApp. '\n"
            "  + 'Respondé en espanol rioplatense, breve (maximo 2-3 frases), amable y concreto. '\n"
            "  + 'Usa el contexto de la empresa para horarios, turnos y direccion. Si no tenes la informacion, ofrecé derivar a un agente humano. No inventes precios ni promesas.';\n"
            "const payload = {\n"
            "  model: process.env.LLM_MODEL || 'meta-llama/llama-3.3-70b-instruct:free',\n"
            "  max_tokens: 200,\n"
            "  temperature: 0.6,\n"
            "  messages: [{ role: 'system', content: sys }].concat(hist).concat([{ role: 'user', content: cur.text_body || '' }])\n"
            "};\n"
            "return [{ json: { payload, cur } }];"
        ),
    }, [2540, 180]),

    node("Llamar OpenRouter", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Authorization", "value": "=Bearer {{ $env.OPENROUTER_API_KEY }}"},
            {"name": "Content-Type", "value": "application/json"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.payload) }}",
        "options": {"timeout": 30000},
    }, [2760, 180]),

    node("Parsear Respuesta IA", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const base = $('Armar Prompt IA').first().json.cur;\n"
            "let reply = null;\n"
            "try {\n"
            "  const out = $input.first().json;\n"
            "  reply = out.choices && out.choices[0] && out.choices[0].message ? (out.choices[0].message.content || '').trim() : null;\n"
            "} catch (e) { reply = null; }\n"
            "if (!reply) {\n"
            "  const text = (base.text_body || '').toLowerCase();\n"
            "  if (/^(hola|buenas|buen dia|buenos dias|hello|hey)/.test(text)) {\n"
            "    reply = 'Hola! Gracias por escribirnos. Como podemos ayudarte?';\n"
            "  } else {\n"
            "    reply = 'Hola, gracias por escribirnos. Un asesor te respondera a la brevedad.';\n"
            "  }\n"
            "}\n"
            "return [{ json: { ...base, reply, answered_by_ai: true } }];"
        ),
    }, [2980, 180]),

    node("Enviar Respuesta", "n8n-nodes-base.whatsApp", 2, {
        "resource": "message",
        "operation": "send",
        "sendAs": "api",
        "phoneNumberId": "={{ $env.WA_PHONE_NUMBER_ID }}",
        "message": {"content": "={{ $json.reply }}"},
    }, [2100, 60]),

    node("Insertar Mensaje Saliente", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.messages (conversation_id, direction, message_type, body, wa_message_id, created_at)\n"
            "VALUES (\n"
            "  (SELECT id FROM crm.conversations WHERE contact_id = "
            "(SELECT id FROM crm.contacts WHERE phone = {{ $json.from }}) AND channel = 'whatsapp'),\n"
            "  'out', 'text', {{ JSON.stringify($json.reply) }}, {{ JSON.stringify($json.message_id) }}, now()\n"
            ");"
        ),
        "options": {},
    }, [2300, 60]),
]

w1_connections = {
    "Webhook Entrante": {"main": [[link("IF Verificacion Meta")]]},
    "IF Verificacion Meta": {"main": [[link("Responder Challenge")], [link("ACK 200")]]},
    "ACK 200": {"main": [[link("Parsear Payload Meta")]]},
    "Parsear Payload Meta": {"main": [[link("IF Procesar")]]},
    "IF Procesar": {"main": [[link("Fin (sin mensajes)")], [link("Upsert Contacto")]]},
    "Upsert Contacto": {"main": [[link("Upsert Conversacion")]]},
    "Upsert Conversacion": {"main": [[link("Insertar Mensaje Entrante")]]},
    "Insertar Mensaje Entrante": {"main": [[link("Chequear Handoff")]]},
    "Chequear Handoff": {"main": [[link("IF Handoff Activo")]]},
    "IF Handoff Activo": {"main": [[link("Fin Handoff - Bot Pausado")], [link("Cargar Reglas Bot")]]},
    "Cargar Reglas Bot": {"main": [[link("Decidir Respuesta Bot")]]},
    "Decidir Respuesta Bot": {"main": [[link("IF Pide IA")]]},
    "IF Pide IA": {"main": [[link("Historial Reciente")], [link("Enviar Respuesta")]]},
    "Historial Reciente": {"main": [[link("Armar Prompt IA")]]},
    "Armar Prompt IA": {"main": [[link("Llamar OpenRouter")]]},
    "Llamar OpenRouter": {"main": [[link("Parsear Respuesta IA")]]},
    "Parsear Respuesta IA": {"main": [[link("Enviar Respuesta")]]},
    "Enviar Respuesta": {"main": [[link("Insertar Mensaje Saliente")]]},
}

# ---------------------------------------------------------------------------
# Workflow 2: lanzador de envio (handoff humano / integraciones)
#   POST http://localhost:5678/webhook/wa-send  {"to":"54911...","text":"hola"}
# ---------------------------------------------------------------------------
W2_NAME = "ProtoCRM WA Send Message (lanzador)"

w2_nodes = [
    node("Lanzador de Envio", "n8n-nodes-base.webhook", 2, {
        "path": "wa-send",
        "httpMethod": "POST",
        "responseMode": "onReceived",
        "options": {"allowedOrigins": "*"},
    }, [0, 0], webhook_id="w2-send-" + uuid.uuid4().hex[:12]),

    node("Validar y Armar", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const item = $input.first().json;\n"
            "const body = item.body || {};\n"
            "const headers = item.headers || {};\n"
            "const sendKey = headers['x-api-key'] || headers['X-Api-Key'] || '';\n"
            "if (sendKey !== (process.env.WA_SEND_KEY || '')) {\n"
            "  return [{ json: { ok: 'no', reason: 'x-api-key invalida' } }];\n"
            "}\n"
            "const to = String(body.to || '').replace(/[^0-9]/g, '');\n"
            "const text = typeof body.text === 'string' ? body.text.trim() : '';\n"
            "if (!to || !text) return [{ json: { ok: 'no', reason: 'faltan to o text' } }];\n"
            "return [{ json: { ok: 'si', to, text } }];"
        ),
    }, [220, 0]),

    node("IF Envio Valido", "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [
                {
                    "id": uid(),
                    "leftValue": "={{ $json.ok }}",
                    "rightValue": "si",
                    "operator": {"type": "string", "operation": "equals", "singleValue": True},
                }
            ],
            "combinator": "and",
        }
    }, [440, 0]),

    node("Fin (invalido)", "n8n-nodes-base.noOp", 1, {}, [660, 160]),

    node("Enviar Mensaje", "n8n-nodes-base.whatsApp", 2, {
        "resource": "message",
        "operation": "send",
        "sendAs": "api",
        "phoneNumberId": "={{ $env.WA_PHONE_NUMBER_ID }}",
        "to": "={{ $json.to }}",
        "message": {"content": "={{ $json.text }}"},
    }, [660, 0]),

    node("Registrar Envio", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.messages (conversation_id, direction, message_type, body, created_at)\n"
            "VALUES (\n"
            "  (SELECT id FROM crm.conversations WHERE contact_id = "
            "(SELECT id FROM crm.contacts WHERE phone = {{ $json.to }}) AND channel = 'whatsapp'),\n"
            "  'out', 'text', {{ JSON.stringify($json.text) }}, now()\n"
            ");"
        ),
        "options": {},
    }, [880, 0]),

    node("Handoff a Humano", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "UPDATE crm.conversations SET bot_handled=0, status='open', last_message_at=now(), updated_at=now()\n"
            "WHERE contact_id=(SELECT id FROM crm.contacts WHERE phone={{ $json.to }}) AND channel='whatsapp';"
        ),
        "options": {},
    }, [1060, 0]),
]

w2_connections = {
    "Lanzador de Envio": {"main": [[link("Validar y Armar")]]},
    "Validar y Armar": {"main": [[link("IF Envio Valido")]]},
    "IF Envio Valido": {"main": [[link("Fin (invalido)")], [link("Enviar Mensaje")]]},
    "Enviar Mensaje": {"main": [[link("Registrar Envio")]]},
    "Registrar Envio": {"main": [[link("Handoff a Humano")]]},
}

# ---------------------------------------------------------------------------
# Workflow 3: Campana broadcast (HSM a segmento por tag)
# ---------------------------------------------------------------------------
W3_NAME = "ProtoCRM WA Campana Broadcast"

w3_nodes = [
    node("Cron Diario", "n8n-nodes-base.scheduleTrigger", 1.2, {
        "rules": [{"interval": [{"field": "hours", "hoursInterval": 1}]}],
    }, [0, 0]),

    node("Obtener Audiencia", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT id, phone, name\n"
            "FROM crm.contacts\n"
            "WHERE opt_in = true AND tags ? 'campana-demo'\n"
            "ORDER BY id;"
        ),
        "options": {},
    }, [220, 0]),

    node("Dividir en Lotes", "n8n-nodes-base.splitInBatches", 1, {
        "batchSize": 5,
        "options": {},
    }, [440, 0]),

    node("Enviar Template", "n8n-nodes-base.whatsApp", 2, {
        "resource": "template",
        "operation": "send",
        "sendAs": "api",
        "phoneNumberId": "={{ $env.WA_PHONE_NUMBER_ID }}",
        "template": {
            "name": "={{ $env.WA_TEMPLATE_BIENVENIDA }}",
            "language": "es_AR",
        },
    }, [660, -160]),

    node("Registrar Envio Campana", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "INSERT INTO crm.campaign_runs (campaign_name, contact_id, phone, status, sent_at)\n"
            "VALUES ('demo', {{ $json.id }}, {{ $json.phone }}, 'enviado', now());"
        ),
        "options": {},
    }, [880, -160]),

    node("Campana Terminada", "n8n-nodes-base.noOp", 1, {}, [660, 160]),
]

w3_connections = {
    "Cron Diario": {"main": [[link("Obtener Audiencia")]]},
    "Obtener Audiencia": {"main": [[link("Dividir en Lotes")]]},
    "Dividir en Lotes": {"main": [[link("Enviar Template")], [link("Campana Terminada")]]},
    "Enviar Template": {"main": [[link("Registrar Envio Campana")]]},
}


# ---------------------------------------------------------------------------
# Workflow 4: API de inbox para la UI (index.html)
#   GET /webhook/wa-inbox          -> lista de conversaciones
#   GET /webhook/wa-msgs?conv_id=  -> mensajes de una conversacion
# ---------------------------------------------------------------------------
W4_NAME = "ProtoCRM WA Inbox API (para UI)"

w4_nodes = [
    node("Listar Conversaciones", "n8n-nodes-base.webhook", 2, {
        "path": "wa-inbox",
        "httpMethod": "GET",
        "responseMode": "responseNode",
        "options": {"allowedOrigins": "*"},
    }, [0, 0], webhook_id="w4-inbox-" + uuid.uuid4().hex[:12]),

    node("Query Conversaciones", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT conversation_id::text, contact_id::text, phone, contact_name, "
            "status, bot_handled, last_message_at, last_message, last_direction\n"
            "FROM crm.v_inbox\n"
            "ORDER BY last_message_at DESC\n"
            "LIMIT 50;"
        ),
        "options": {},
    }, [220, 0]),

    node("Responder Conversaciones", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "",
        "options": {},
    }, [440, 0]),

    node("Mensajes de Conversacion", "n8n-nodes-base.webhook", 2, {
        "path": "wa-msgs",
        "httpMethod": "GET",
        "responseMode": "responseNode",
        "options": {"allowedOrigins": "*"},
    }, [0, 200], webhook_id="w4-msgs-" + uuid.uuid4().hex[:12]),

    node("Query Mensajes", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT id::text, conversation_id::text, direction, message_type, body, "
            "media_url, created_at\n"
            "FROM crm.messages\n"
            "WHERE conversation_id = '{{ $json.query.conv_id }}'\n"
            "ORDER BY created_at ASC\n"
            "LIMIT 200;"
        ),
        "options": {},
    }, [220, 200]),

    node("Responder Mensajes", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "",
        "options": {},
    }, [440, 200]),

    # --- sugerencias de respuesta con IA (para chips del inbox) ---
    node("Sugerencias de Respuesta", "n8n-nodes-base.webhook", 2, {
        "path": "wa-suggest",
        "httpMethod": "GET",
        "responseMode": "responseNode",
        "options": {"allowedOrigins": "*"},
    }, [0, 400], webhook_id="w4-sug-" + uuid.uuid4().hex[:12]),

    node("Historial Para Sugerencias", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "SELECT direction, body\n"
            "FROM crm.messages\n"
            "WHERE conversation_id = '{{ $json.query.conv_id }}'\n"
            "ORDER BY created_at DESC\n"
            "LIMIT 12;"
        ),
        "options": {},
    }, [220, 400]),

    node("Armar Prompt Sugerencias", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "const hist = $input.all().reverse()\n"
            "  .filter(i => i.json.body)\n"
            "  .map(i => ({ role: i.json.direction === 'in' ? 'user' : 'assistant', content: String(i.json.body) }));\n"
            "const empresa = process.env.EMPRESA_CTX || 'Empresa de indumentaria. Horarios: Lun-Vie 9-18, sab 9-13. Turnos sin turno previo.';\n"
            "const sys = 'Sos un asistente de ' + empresa + ' por WhatsApp. Analiza la conversacion '\n"
            "  + 'y devuelve SOLO un JSON array (sin markdown ni explicaciones) con exactamente 3 respuestas cortas '\n"
            "  + '(maximo 2 frases cada una) que el agente humano podria enviar como siguiente mensaje. '\n"
            "  + 'Usa el contexto de la empresa. Varia: una resolutiva, una pidiendo dato y una cierre amable.';\n"
            "const payload = {\n"
            "  model: process.env.LLM_MODEL || 'meta-llama/llama-3.3-70b-instruct:free',\n"
            "  max_tokens: 250,\n"
            "  temperature: 0.7,\n"
            "  messages: [{ role: 'system', content: sys }].concat(hist)\n"
            "};\n"
            "return [{ json: { payload } }];"
        ),
    }, [440, 400]),

    node("Llamar OpenRouter Sug", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Authorization", "value": "=Bearer {{ $env.OPENROUTER_API_KEY }}"},
            {"name": "Content-Type", "value": "application/json"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.payload) }}",
        "options": {"timeout": 30000},
    }, [660, 400]),

    node("Parsear Sugerencias", "n8n-nodes-base.code", 2, {
        "jsCode": (
            "let suggestions = [];\n"
            "try {\n"
            "  const out = $input.first().json;\n"
            "  let raw = out.choices && out.choices[0] && out.choices[0].message ? out.choices[0].message.content : '';\n"
            "  raw = String(raw || '').replace(/```json/gi, '').replace(/```/g, '').trim();\n"
            "  const m = raw.match(/\\[[\\s\\S]*\\]/);\n"
            "  const arr = JSON.parse(m ? m[0] : raw);\n"
            "  if (Array.isArray(arr)) suggestions = arr.filter(s => typeof s === 'string' && s.trim()).map(s => s.trim()).slice(0, 3);\n"
            "} catch (e) { suggestions = []; }\n"
            "return [{ json: { suggestions } }];"
        ),
    }, [880, 400]),

    node("Responder Sugerencias", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "",
        "options": {},
    }, [1100, 400]),

    node("Webhook Handoff", "n8n-nodes-base.webhook", 2, {
        "path": "wa-handoff",
        "httpMethod": ["GET","POST"],
        "responseMode": "responseNode",
        "options": {"allowedOrigins": "*"},
    }, [0, 600], webhook_id="w4-handoff-" + uuid.uuid4().hex[:12]),

    node("Ejecutar Handoff", "n8n-nodes-base.postgres", 2, {
        "operation": "executeQuery",
        "query": (
            "UPDATE crm.conversations SET bot_handled = CASE WHEN '{{ $json.query.action }}' IN ('bot','reanudar') OR '{{ $json.body.action }}' IN ('bot','reanudar') THEN 1 ELSE 0 END,\n"
            " status = CASE WHEN '{{ $json.query.action }}' IN ('bot','reanudar') OR '{{ $json.body.action }}' IN ('bot','reanudar') THEN 'bot' ELSE 'open' END,\n"
            " updated_at=now(), last_message_at=now()\n"
            "WHERE id='{{ $json.query.conv_id }}{{ $json.body.conv_id }}' RETURNING id, bot_handled, status;"
        ),
        "options": {},
    }, [220, 600]),

    node("Responder Handoff", "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "",
        "options": {},
    }, [440, 600]),
]

w4_connections = {
    "Listar Conversaciones": {"main": [[link("Query Conversaciones")]]},
    "Query Conversaciones": {"main": [[link("Responder Conversaciones")]]},
    "Mensajes de Conversacion": {"main": [[link("Query Mensajes")]]},
    "Query Mensajes": {"main": [[link("Responder Mensajes")]]},
    "Sugerencias de Respuesta": {"main": [[link("Historial Para Sugerencias")]]},
    "Historial Para Sugerencias": {"main": [[link("Armar Prompt Sugerencias")]]},
    "Armar Prompt Sugerencias": {"main": [[link("Llamar OpenRouter Sug")]]},
    "Llamar OpenRouter Sug": {"main": [[link("Parsear Sugerencias")]]},
    "Parsear Sugerencias": {"main": [[link("Responder Sugerencias")]]},
    "Webhook Handoff": {"main": [[link("Ejecutar Handoff")]]},
    "Ejecutar Handoff": {"main": [[link("Responder Handoff")]]},
}


def save(name, payload):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("OK", path)


if __name__ == "__main__":
    save("WA-Inbound-Meta.json", workflow(W1_NAME, w1_nodes, w1_connections))
    save("WA-Send-Message.json", workflow(W2_NAME, w2_nodes, w2_connections))
    save("WA-Campaign-Broadcast.json", workflow(W3_NAME, w3_nodes, w3_connections))
    save("WA-Inbox-API.json", workflow(W4_NAME, w4_nodes, w4_connections))