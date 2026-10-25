# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/malinatrash/pgadmin-bridge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/malinatrash/pgadmin-bridge/releases/tag/v1.0.0
