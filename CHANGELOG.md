# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-10-31

### Changed

- Far fewer and faster round trips to pgAdmin: one keep-alive HTTP(S) connection per login instead of a
  new TCP + TLS handshake for every request, gzip-compressed answers, and the rest of a large result
  fetched in one call (`/sqleditor/fetch/<id>/1`) where pgAdmin supports it, a page at a time otherwise.
- A pool of pgAdmin logins per instance (**Parallel pgAdmin sessions**, default 4): pgAdmin serializes
  the requests of one login, so statements from different clients now run in parallel instead of
  queueing behind each other.
- Rows are streamed to the client as pgAdmin hands them out, with the next chunk requested while the
  current one is being sent; the simple protocol no longer buffers the whole result first.
- Polling for a running statement adapts to how long the tool's statements usually take and never
  sleeps longer than 500 ms, down from 2 s, so long queries return sooner.
- `auto_commit` / `auto_rollback` are no longer set on every new backend; they are only switched on
  when a backend turns out to run without autocommit.
- Server trees of several pgAdmin instances are scanned in parallel.

### Added

- Local answers for what IDEs send constantly: keepalive `SELECT 1`, `select version()`, `SHOW` of the
  settings reported at connect, and `SET application_name` / `client_encoding` / `extra_float_digits`.
  They never reach pgAdmin and no longer wake a released backend, so an idle tab costs nothing.
- **Catalog cache** (off by default): results of read-only `pg_catalog` / `information_schema` queries
  are shared by all clients for the configured number of seconds, and identical in-flight queries are
  coalesced into one. Tames the introspection storm when DataGrip or DBeaver connects.
- A backend that pgAdmin lost while the client was idle is re-attached transparently when the next
  statement had not started yet, instead of ending the client connection.

## [1.0.1] - 2026-10-25

### Fixed

- Clients could not connect when the bridge started less than a minute after the machine booted
  (fresh VMs and containers): the first lookup skipped scanning the pgAdmin server trees.

## [1.0.0] - 2026-10-25

First public release as **pgadmin-bridge** (previously developed as `pgproxy`).

### Added

- PostgreSQL wire-protocol server that runs every query through pgAdmin 4's Query Tool API: simple and
  extended query protocol, real column types, binary result formats, NOTICEs, error positions,
  transaction status and query cancel.
- Several pgAdmin instances at once; User = server name from the pgAdmin tree, or `instance/server`.
- Idle backend release with replay of session `SET`s, optional read-only sessions and statement timeout.
- Web UI: overview with a connection-string builder (psql, JDBC, URI, libpq env), pgAdmin instances
  with live login test, live connections with disconnect, settings with a floating save bar, and a
  searchable log with SQL highlighting. Light and dark themes.
- `--advertise` / `PGADMIN_BRIDGE_ADVERTISE` so the UI shows the address clients really use.
- `pip install` support with a `pgadmin-bridge` command; Docker image running as non-root with a
  healthcheck; multi-arch images on `ghcr.io`.

### Changed

- The data directory variable is now `PGADMIN_BRIDGE_DATA`; `PGPROXY_DATA` is still read.
- Renaming an instance in the UI keeps its saved password.

[Unreleased]: https://github.com/malinatrash/pgadmin-bridge/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/malinatrash/pgadmin-bridge/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/malinatrash/pgadmin-bridge/releases/tag/v1.0.0
