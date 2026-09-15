create table if not exists user_roles (
  clerk_user_id text primary key,
  role text not null default 'CUSTOMER' check (role in ('CUSTOMER', 'ADMIN')),
  created_at timestamptz default now()
);

alter table user_roles enable row level security;

-- No public policies: this table is read server-side with the service key only.
-- Make a user an admin:
--   insert into user_roles (clerk_user_id, role) values ('user_xxx', 'ADMIN')
--   on conflict (clerk_user_id) do update set role = 'ADMIN';
