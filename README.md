# pgproxy

A PostgreSQL wire-protocol proxy that runs your SQL through **pgAdmin's REST API**.

Some teams only get database access through a hosted pgAdmin: no direct port, no
`psql`, only the web Query Tool. pgproxy lets DataGrip, psql, DBeaver or any other
PostgreSQL client talk to those databases as if they were ordinary Postgres servers.
Every client connection becomes one pgAdmin Query Tool, so you get exactly the access
and the server-side audit trail pgAdmin already gives you: no new credentials, no new
privileges, no bypass.

- Pure Python, standard library only, one small container.
- Several pgAdmin instances at once; every server in their trees is reachable.
- Real column types, NOTICEs, error positions, transaction status, query cancel.
- Idle connections release their Postgres backend and get a fresh one on the next
  query (session `SET`s are replayed), so forgotten DataGrip tabs do not hold connections.
- Optional statement timeout and read-only mode, applied to every session.
- A small web UI to configure all of this; settings persist in a volume.

## Quick start

```bash
docker compose up -d --build
open http://127.0.0.1:8080
```

1. In the web UI add a pgAdmin instance: a name, its URL and the account you log in
   with. "Test login" lists the servers it sees in the pgAdmin tree.
2. Set a **proxy password**: this is what database clients will send.
3. Save. The proxy is listening on `127.0.0.1:5432`.

Everything lives in the `pgproxy-data` volume (`config.json` plus pgAdmin session
cookies) and comes back after a restart.

Without Docker:

```bash
python3 main.py --port 5432 --web-port 8080     # data in ./data
```

## Connecting a client

| Field    | Value                                                                |
|----------|----------------------------------------------------------------------|
| Host     | `127.0.0.1`, port `5432`                                             |
| User     | the **server name from the pgAdmin tree**, e.g. `prod-main` (or `instance/server` if two instances have a server with the same name) |
| Database | a database on that server; empty = the server's maintenance database |
| Password | the proxy password from the web UI                                   |
| SSL      | off (`sslmode=disable`)                                              |

Only the **simple query protocol** is supported. In JDBC drivers (DataGrip, DBeaver)
set `preferQueryMode=simple`; psql uses it already.

```bash
psql "host=127.0.0.1 port=5432 user=prod-main dbname=app sslmode=disable"
```

DataGrip tips: one data source per pgAdmin server, `preferQueryMode=simple` on the
Advanced tab, select only the schemas you need, and tick *Read-only* for production.

## What the web UI shows

- **pgAdmin instances**: add, edit, remove, test the login.
- **Proxy settings**: password, statement timeout, idle-backend release, read-only.
  Applied live, without a restart.
- **Discovered servers**: what clients can put in the `User` field.
- **Connections**: who is connected, whether a backend is open or released, idle time,
  open transaction, and a button to disconnect a client.
- **Log**: every statement with its timing and result tag, every HTTP call to pgAdmin.

## How it works

```
psql / DataGrip  ──wire v3──▶  pgproxy  ──HTTPS──▶  pgAdmin  ──libpq──▶  PostgreSQL
```

For each client the proxy opens a pgAdmin Query Tool (`/sqleditor/initialize`) on the
requested server and database and sends every statement through
`/sqleditor/query_tool/start` + `/sqleditor/poll`. Rows are streamed to the client as
pgAdmin hands them out. The proxy keeps one pgAdmin login per instance and serializes
its HTTP calls, because pgAdmin mixes up transactions when a session is used
concurrently.

Limitations:

- A batch with several statements returns the result of the last one (pgAdmin's behaviour).
- No `COPY`, no extended protocol, no SSL between client and proxy (it only listens on loopback).
- Query latency is two HTTP round trips to pgAdmin per statement.

## Security notes

- Both listeners are published on the host's loopback only. Do not expose them.
- The web UI has no authentication of its own; it relies on being loopback-only.
- pgAdmin credentials are stored in `config.json` inside the volume (mode 0600).
- Sessions are *read-write* unless you enable read-only. The statement timeout
  (`120` s is a sensible default for production) and the idle release protect the
  server from forgotten queries and connections.

## Development

```bash
python3 -m unittest discover tests -v
```

The tests run psql (when installed) and a raw wire client through the proxy into a
fake pgAdmin implemented with the standard library (`tests/fake_pgadmin.py`).

Tested against pgAdmin 7 and 8. MIT license.
