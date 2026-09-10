# Setup paso a paso — Proto-CRM WhatsApp (estilo UContact)

Stack: **Meta WhatsApp Cloud API → n8n → Postgres local** + inbox **Appsmith** + tester
`index.html`. Todo open source / free tier (presupuesto $0 recurrente).

Docker Desktop en la máquina donde corra el stack (en la PC de Nextlab no está instalado).

## 1. .env

```powershell
cd "C:\Users\jmachado\Desktop\Automatizacion procesos\Duplicado Ucontact"
Copy-Item .env.example .env
```

## 2. Meta (una vez, gratis)

1. developers.facebook.com → My Apps → Create App → **Business**.
2. Producto **WhatsApp** → API Setup → *test number* (te da Phone number ID y un token temporal).
3. Token permanente: *System User → Generate token* (permiso `whatsapp_business_messaging`).
4. Volcar en `.env`: `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN` (aleatorio),
   `WA_SEND_KEY` (aleatorio). `WA_TEMPLATE_BIENVENIDA` solo si vas a usar campañas.
5. Free tier Meta: 1.000 conversaciones de servicio/mes + 250 marketing/mes + 1.000 contactos.

## 2b. IA gratis (opcional pero recomendado)

El bot tiene DOS capas: primero `bot_rules` (SQL, $0, instantaneo); si ninguna regla matchea y hay
key configurada, responde un **LLM via OpenRouter** (modelo gratuito). Sin key, usa respuestas
default. Tambien alimenta las **respuestas sugeridas** del inbox (`wa-suggest`).

1. openrouter.ai → Sign up (gratis, sin tarjeta para modelos `:free`) → **Keys → Create Key**.
2. Pegar la key en `.env`: `OPENROUTER_API_KEY=sk-or-...`
3. (Opcional) cambiar modelo con `LLM_MODEL=` — ver los gratuitos vigentes en
   https://openrouter.ai/models?max_price=0 (rotan seguido; el default es llama-3.3-70b:free).
4. Reiniciar: `docker compose up -d` (recarga el env).

> Los modelos free tienen rate limit (varias req/minuto). Para volumen real, un modelo pago de
> OpenRouter cuesta centavos; o quedarse solo en reglas SQL.

> Con Gupshup (modelo UContact): mismo flow, cambia el nodo de envío (API key Gupshup) y el
> webhook inbound. Ver DECISIONES_Y_STACK.md.

## 3. Levantar el stack

```powershell
docker compose up -d
docker compose logs -f tunnel   # anotar https://xxxx.trycloudflare.com
```

| Servicio | URL | Rol |
|---|---|---|
| n8n | http://localhost:5678 | orquestador (registrar usuario la 1ª vez) |
| Appsmith | http://localhost:8080 | inbox de agentes (recipe en UI_INBOX_APPSMITH.md) |
| Tester | `index.html` (doble click) | panel rápido de prueba (ver paso 6) |
| Postgres | interno 5432 | el esquema `supabase/schema.sql` se auto-carga al primer arranque |

## 4. Importar workflows (4)

n8n → Workflows → **Import from File**:

1. `WA-Inbound-Meta.json` — webhook de Meta + bot + persistencia
2. `WA-Send-Message.json` — responder/enviar (validado con header X-Api-Key)
3. `WA-Campaign-Broadcast.json` — campaña HSM (cada 1h)
4. `WA-Inbox-API.json` — GET /wa-inbox y /wa-msgs (para la UI)

**Credentials**:
- *WhatsApp API* (Cloud API): Access Token = `WA_ACCESS_TOKEN`. Asignar a los nodos de envío.
- *Postgres*: Host `postgres`, Puerto `5432`, User `proto`, Password `POSTGRES_PASSWORD`,
  DB `proto_crm`. Asignar a los nodos de consulta/inserción.

Activar el workflow **Inbound** (y WA-Inbox-API si querés la UI).

## 5. Webhook en Meta

Meta → WhatsApp → API Setup → **Webhook → Edit**:

| Campo | Valor |
|---|---|
| Callback URL | `https://<tunel>.trycloudflare.com/webhook/wa-inbound` |
| Verify Token | `WA_VERIFY_TOKEN` |
| Fields | `messages` |

Debe decir *Verified* (el Inbound activo es requisito).

## 6. Probar sin WhatsApp (recomendado al empezar)

Abrir `index.html` (doble click) y:

1. **Conexion**: base n8n + X-Api-Key = `WA_SEND_KEY` → *Probar conexion*.
2. **Simular entrante**: teléfono + nombre + texto ("Hola, necesito un presupuesto") →
   *Inyectar al webhook*. El bot responde (reglas de `bot_rules`).
3. **Conversaciones / Historial**: ver la conversación simulada con in/out.
4. **Responder**: escribir y *Responder* (usa wa-send con X-Api-Key).

Luego con el WhatsApp real: escribile al test number y repetí los pasos 3-4.

(Si el navegador bloquea por CORS: en cada Webhook node → Options → Allowed Origins (CORS) = `*`.)

## 7. Campaña (opcional)

Tag `"campana-demo"` a contactos `opt_in=true` y activar el workflow Broadcast.

## 8. Inbox de agentes con Appsmith (opcional)

Seguir `UI_INBOX_APPSMITH.md` (datasource postgres + queries convos/msgs/sendMsg + polling 5s).

## 9. Troubleshooting

| Síntoma | Causa / Fix |
|---|---|
| `Failed to fetch` en index.html | **n8n apagado** (`docker compose ps` debe mostrar `up`) o **CORS**: en los Webhook nodes (wa-inbound, wa-send, wa-inbox, wa-msgs) → Options → Allowed Origins (CORS) = `*` (los JSON ya lo traen) y **reimportar/reactivar** el workflow |
| Se ve "The requested webhook is not registered" | el workflow está **inactivo**: activar el toggle de Inbound (e Inbox-API) en n8n |
| Meta no verifica webhook | Inbound inactivo o token distinto; reiniciar compose tras editar `.env` |
| No responde el bot | credencial WhatsApp sin asignar → `docker compose logs n8n` |
| Duplicados | normal: `ON CONFLICT (wa_message_id)` los absorbe |
| Tablas vacías | volumen viejo → `docker compose down -v` y `up` de nuevo |
| Fuera de la ventana 24h | Meta solo permite templates HSM ahí |
| UI/remoto | túnel expone solo n8n; agregar segundo túnel a `http://appsmith:80` |
| Bot no usa IA | falta `OPENROUTER_API_KEY` en `.env` o el modelo `:free` quedó obsoleto → elegir otro en openrouter.ai/models |
| Sugerencias vacías en el tester | mismo motivo que arriba; sin IA el chip avisa "sin sugerencias" |
| IA lenta o corta respuestas | rate limit del free tier; bajar temperatura o usar otro modelo |