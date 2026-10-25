# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/malinatrash/pgadmin-bridge/security/advisories/new)
with steps to reproduce and the version you tested. You will get an answer within a week.

## Threat model

pgadmin-bridge is a personal tool that runs on the user's own machine and signs in to pgAdmin with
that user's account.

- **In scope:** anything that lets a client, a web page in the user's browser, or another local user
  reach the Postgres port, the web UI or the stored credentials without the proxy password; anything
  that makes the bridge do more than the pgAdmin account allows; leaking pgAdmin credentials or
  cookies.
- **By design:** the web UI has no login and both listeners trust the loopback interface. Exposing
  them on a network is a misconfiguration; the README says so.

## Hardening checklist

- Keep both ports bound to `127.0.0.1` (the default and the docker compose setup).
- Set a strong proxy password (the Settings page can generate one).
- Enable read-only sessions and a statement timeout for production instances.
- Keep the data volume private: it holds your pgAdmin password and session cookies.
