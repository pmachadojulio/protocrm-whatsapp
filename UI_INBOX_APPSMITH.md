^^# Inbox gráfico de agentes — Appsmith (self-hosted, $0)

Appsmith Community Edition es open source y corre en el compose (puerto 8080). Conecta directo al
Postgres local y usa el webhook `wa-send` de n8n para responder. No requiere Supabase.

## 1. Primer acceso

1. Abrir http://localhost:8080
2. Crear la cuenta admin local (primera vez queda como superusuario del workspace).
3. New App (partís de una app vacía; se puede renombrar, ej. "WhatsApp Inbox").

## 2. Datasource

- **+ → New Datasource → PostgreSQL**
- Host: `postgres` (nombre del servicio en el compose — NO localhost)
- Port: `5432` — Database: `proto_crm` — User: `proto` — Password: el `POSTGRES_PASSWORD` del `.env`
- **Save**.

## 3. Queries (pestaña Queries → + New Query)

### `convos` (lista de conversaciones, POSTGRES)
```sql
select * from crm.v_inbox
order by last_message_at desc
limit 50;
```

### `msgs` (historial de la conversación seleccionada, POSTGRES)
```sql
select * from crm.messages
where conversation_id = {{ convos.selectedItem.conversation_id }}
order by created_at asc;
```

### `sendMsg` (responder → dispara el webhook de n8n, REST)
- Tipo: `New API` / REST
- URL: `http://localhost:5678/webhook/wa-send`
- Method: `POST`
- Headers:
  - `X-Api-Key`: el valor de `WA_SEND_KEY` del `.env`
  - `Content-Type: application/json`
- Body (JSON):
```json
{"to": {{ convos.selectedItem.phone }}, "text": {{ enviar_in.text }}}
```
> Si vas a operar desde OTRA máquina, reemplazar `localhost:5678` por la URL https del túnel.

## 4. Widgets del canvas (drag & drop)

| Widget | Data / config |
|---|---|
| **List** `convos_list` | Data: `{{ convos.data }}`. Textos: nombre (`currentItem.contact_name`), último mensaje (`currentItem.last_message`), estado/`bot_handled`. |
| **List** `msgs_list` | Data: `{{ msgs.data }}`. Mostrar `currentItem.direction` (in/out), `currentItem.body`, `currentItem.created_at`. |
| **Input** `enviar_in` | Placeholder: "Escribí la respuesta...". Default: `EnterText` |
| **Button** `btn_enviar` | Label "Enviar". `onClick` → run query `sendMsg` → run `msgs` → run `convos` → set `enviar_in` vacío. |
| **Text** `total_conv` | Data: `{{ convos.data.length }}` (contador). |

Los lists toman datos de una query: en las propiedades del widget poner `{{ convos.data }}` /
`{{ msgs.data }}` y en **onItemClick** de `convos_list` → run query `msgs`.

## 5. "Tiempo real" sin pagar nada

Appsmith no tiene websockets nativos genéricos; la forma simple es **polling**:

1. Crear un **JS Object** `refrescar`:
```javascript
export default {
  iniciar: () => setInterval(() => {
    convos.run();
    if (convos.selectedItem) msgs.run();
  }, 5000),
};
```
2. En **Page → Settings → onPageLoad** → `refrescar.iniciar()`.

Con 5 s de refresco es casi instantáneo para decenas de agentes, y no cuesta nada.
Para cáscara realtime (websocket) en v2: Supabase Realtime o un canal propio (p.ej. Postgres
LISTEN/NOTIFY vía una app liviana).

## 6. Notas

- El historial completo queda en `crm.messages` aunque nadie tenga el inbox abierto
  (objetivo principal del proto: **registro de todos los in/out**).
- `status` y `assignee_id` de `conversations` se pueden editar desde Appsmith (handoff:
  bot → humano) — para v1 agregar un par de queries `UPDATE` y botones en la conversación
  seleccionada.
- Si el número de agentes crece, considerar Directus (CRUD inmediato sobre el mismo Postgres)
  o un front React/Refine dedicado. Appsmith cubre el proto sin escribir código.