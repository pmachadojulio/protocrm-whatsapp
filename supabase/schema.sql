# ============================================================
# Proto CRM WhatsApp — esquema portable (Postgres local o Supabase)
# - Postgres local (compose): se auto-carga en primer arranque
#   (docker-entrypoint-initdb.d). Supabase: SQL Editor → Run.
# ============================================================

create schema if not exists crm;
create extension if not exists pgcrypto;

-- ---------- CONTACTOS (ficha uCRM) ----------
create table crm.contacts (
    id            uuid primary key default gen_random_uuid(),
    phone         text not null unique,          -- numero 54911... (formato Meta)
    name          text,
    email         text,
    tags          jsonb not null default '[]'::jsonb,
    custom_fields jsonb not null default '{}'::jsonb,
    opt_in        boolean not null default true,
    source        text not null default 'whatsapp',
    last_seen     timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- ---------- AGENTES (perfiles de usuario, sin depender de auth.users) ----------
create table crm.agents (
    id           uuid primary key default gen_random_uuid(),
    username     text unique not null,
    pass_hash    text, -- hash pbkdf2 (mock) ; en produccion usar Supabase Auth
    display_name text,
    role         text not null default 'agent' check (role in ('agent', 'supervisor', 'admin')),
    active       boolean not null default true,
    created_at   timestamptz not null default now()
);
-- alias para el mock: users = agents
create or replace view crm.users as select * from crm.agents;

-- ---------- CONVERSACIONES (interaccion activa) ----------
create table crm.conversations (
    id                 uuid primary key default gen_random_uuid(),
    contact_id         uuid not null references crm.contacts(id) on delete cascade,
    channel            text not null default 'whatsapp',
    wa_phone_number_id text,                      -- numero de la linea (multi-linea futuro)
    status             text not null default 'open'
                       check (status in ('open', 'reopened', 'bot', 'closed')),
    assignee_id        uuid references crm.agents(id) on delete set null,
    bot_handled        boolean not null default false,
    last_message_at    timestamptz not null default now(),
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    unique (contact_id, channel)
);

-- ---------- MENSAJES (historial in/out completo) ----------
create table crm.messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references crm.conversations(id) on delete cascade,
    direction       text not null check (direction in ('in', 'out')),
    message_type    text not null default 'text', -- text|image|audio|video|document|sticker|...
    body            text,
    media_url       text,
    wa_message_id   text unique,                  -- dedup de reintentos de Meta
    status          text not null default 'sent',
    created_at      timestamptz not null default now(),
    created_by      uuid references crm.agents(id) on delete set null -- quien respondio (log multi-usuario)
);

-- ---------- TICKETS (casos con prioridad/SLA) ----------
create table crm.tickets (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid references crm.conversations(id) on delete cascade,
    contact_id      uuid references crm.contacts(id) on delete cascade,
    subject         text,
    priority        text not null default 'normal'
                    check (priority in ('baja', 'normal', 'alta', 'critica')),
    status          text not null default 'abierto'
                    check (status in ('abierto', 'en_progreso', 'resuelto', 'cerrado')),
    assignee_id     uuid references crm.agents(id) on delete set null,
    sla_due         timestamptz,   -- vencimiento SLA = created_at + horas según prioridad
    created_at      timestamptz not null default now(),
    closed_at       timestamptz
);

-- ---------- PIPELINE (oportunidades de venta / kanban) ----------
create table crm.opportunities (
    id         uuid primary key default gen_random_uuid(),
    contact_id uuid not null references crm.contacts(id) on delete cascade,
    title      text not null,
    amount     numeric not null default 0,
    currency   text not null default 'ARS',
    stage      text not null default 'nuevo'
               check (stage in ('nuevo', 'contactado', 'cotizado', 'ganado', 'perdido')),
    notes      text not null default '',
    created_by uuid references crm.agents(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    closed_at  timestamptz
);

-- ---------- NOTAS Y ACTIVIDADES (timeline del contacto) ----------
create table crm.notes (
    id              uuid primary key default gen_random_uuid(),
    contact_id      uuid not null references crm.contacts(id) on delete cascade,
    conversation_id uuid references crm.conversations(id) on delete set null,
    kind            text not null default 'note'
                    check (kind in ('note', 'task', 'call', 'visit')),
    body            text not null,
    due_at          timestamptz,
    done            boolean not null default false,
    created_by      uuid references crm.agents(id) on delete set null,
    created_at      timestamptz not null default now()
);

-- ---------- BOT (reglas por keyword, v0) ----------
create table crm.bot_rules (
    id       serial primary key,
    name     text not null,
    keywords text not null,          -- separadas por coma: 'horario,abierto,cierre'
    response text not null,
    priority int not null default 100,
    active   boolean not null default true
);

-- ---------- TEMPLATES HSM (sincronizados de Gupshup/Meta) ----------
create table crm.templates (
    id       serial primary key,
    name     text not null unique,   -- nombre del template en Meta/Gupshup
    category text not null default 'utility', -- utility|marketing|authentication
    language text not null default 'es_AR',
    body     text
);

-- ---------- CAMPAÑAS Y CORRIDAS (broadcast) ----------
create table crm.campaigns (
    id            serial primary key,
    name          text not null unique,
    tag           text,              -- tag de segmento (ej. 'campana-demo')
    template_name text references crm.templates(name),
    status        text not null default 'borrador'
                  check (status in ('borrador', 'activa', 'pausada', 'finalizada')),
    created_at    timestamptz not null default now()
);

create table crm.campaign_runs (
    id            serial primary key,
    campaign_name text not null,
    contact_id    uuid references crm.contacts(id),
    phone         text,
    status        text not null default 'pendiente', -- pendiente|enviado|error
    wa_message_id text,
    sent_at       timestamptz
);

-- ---------- indices ----------
create index if not exists idx_conv_status  on crm.conversations (status, last_message_at desc);
create index if not exists idx_msg_conv     on crm.messages (conversation_id, created_at);
create index if not exists idx_contact_tags on crm.contacts using gin (tags);
create index if not exists idx_tickets_st   on crm.tickets (status, priority);
create index if not exists idx_opp_stage    on crm.opportunities (stage, updated_at desc);
create index if not exists idx_opp_contact  on crm.opportunities (contact_id);
create index if not exists idx_notes_ct     on crm.notes (contact_id, created_at desc);

-- ---------- updated_at automatico ----------
create or replace function crm.set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger trg_contacts_updated
    before update on crm.contacts
    for each row execute function crm.set_updated_at();
create trigger trg_conversations_updated
    before update on crm.conversations
    for each row execute function crm.set_updated_at();
create trigger trg_opportunities_updated
    before update on crm.opportunities
    for each row execute function crm.set_updated_at();

-- ---------- vista INBOX (bandeja del agente) ----------
create or replace view crm.v_inbox as
select
    c.id                                                          as conversation_id,
    ct.id                                                         as contact_id,
    ct.phone,
    coalesce(ct.name, 'Sin nombre')                                as contact_name,
    c.status,
    c.assignee_id,
    coalesce(ag.display_name, ag.username, '')                    as assignee_name,
    c.bot_handled,
    c.last_message_at,
    (select m.body      from crm.messages m where m.conversation_id = c.id order by m.created_at desc limit 1) as last_message,
    (select m.direction from crm.messages m where m.conversation_id = c.id order by m.created_at desc limit 1) as last_direction,
    (select ag2.display_name from crm.messages m join crm.agents ag2 on ag2.id=m.created_by where m.conversation_id=c.id order by m.created_at desc limit 1) as last_by
from crm.conversations c
join crm.contacts ct on ct.id = c.contact_id
left join crm.agents ag on ag.id=c.assignee_id;

-- ---------- Seguridad ----------
-- RLS: en Supabase se activa y las policies las consume 'authenticated' (front).
-- En Postgres local este bloque no hace nada (rol inexistente) y el superusuario
-- de todos modos saltea RLS. La clave de servicio de n8n (service_role) tambien
-- saltea RLS en Supabase. Para multi-marca (v2) sumar tenant_id a cada policy.

alter table crm.contacts      enable row level security;
alter table crm.agents        enable row level security;
alter table crm.conversations enable row level security;
alter table crm.messages      enable row level security;
alter table crm.tickets       enable row level security;
alter table crm.opportunities enable row level security;
alter table crm.notes         enable row level security;
alter table crm.bot_rules     enable row level security;
alter table crm.templates     enable row level security;
alter table crm.campaigns     enable row level security;
alter table crm.campaign_runs enable row level security;

do $$
begin
    if exists (select from pg_roles where rolname = 'authenticated') then
        execute 'grant usage on schema crm to authenticated';
        execute 'grant select, insert, update, delete on all tables in schema crm to authenticated';
        execute 'grant usage, select on all sequences in schema crm to authenticated';

        execute 'create policy p_contacts_read  on crm.contacts      for select to authenticated using (true)';
        execute 'create policy p_contacts_write on crm.contacts      for all to authenticated using (true) with check (true)';
        execute 'create policy p_agents_read    on crm.agents        for select to authenticated using (true)';
        execute 'create policy p_agents_write   on crm.agents        for all to authenticated using (true) with check (true)';
        execute 'create policy p_convs_read     on crm.conversations for select to authenticated using (true)';
        execute 'create policy p_convs_write    on crm.conversations for all to authenticated using (true) with check (true)';
        execute 'create policy p_msgs_read      on crm.messages      for select to authenticated using (true)';
        execute 'create policy p_msgs_write     on crm.messages      for all to authenticated using (true) with check (true)';
        execute 'create policy p_tickets_read     on crm.tickets       for select to authenticated using (true)';
        execute 'create policy p_tickets_write    on crm.tickets       for all to authenticated using (true) with check (true)';
        execute 'create policy p_opp_read         on crm.opportunities for select to authenticated using (true)';
        execute 'create policy p_opp_write        on crm.opportunities for all to authenticated using (true) with check (true)';
        execute 'create policy p_notes_read       on crm.notes         for select to authenticated using (true)';
        execute 'create policy p_notes_write      on crm.notes         for all to authenticated using (true) with check (true)';
        execute 'create policy p_rules_read     on crm.bot_rules     for select to authenticated using (true)';
        execute 'create policy p_templates_read on crm.templates     for select to authenticated using (true)';
    end if;
end $$;

-- ---------- seed de demo ----------
insert into crm.bot_rules (name, keywords, response, priority) values
 ('horarios',    'horario,abren,abierto,cierre,dias,feriado',
  'Atendemos de lunes a viernes de 9 a 18 hs.', 10),
 ('facturacion', 'factura,facturacion,comprobante,recibo,pago',
  'Para temas de facturacion escribinos a facturacion@tuempresa.com', 20),
 ('ventas',      'precio,presupuesto,cotizacion,plan,contratar,cuanto',
  'Te dejamos con un asesor de ventas a la brevedad.', 30);

insert into crm.templates (name, category, language, body) values
 ('saludo_inicial', 'utility', 'es_AR', 'Hola {{1}}, gracias por escribirnos.');

insert into crm.campaigns (name, tag, template_name, status) values
 ('demo', 'campana-demo', 'saludo_inicial', 'borrador');