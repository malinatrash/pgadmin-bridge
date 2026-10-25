# Contributing

Thanks for helping. Bug reports with a pgAdmin version and the bridge's log lines are the most useful
thing you can send; see the issue templates.

## Ground rules

- **Standard library only.** The bridge runs in locked-down corporate networks; it must install with
  nothing but Python 3.11+. The web UI is plain ES modules and CSS: no framework, no build step, no CDN.
- **No new access.** Features must not let a client do anything the user could not do in the pgAdmin
  browser UI with the same account.
- Small modules with one job each. If a file grows past ~250 lines, it probably wants to be split.

## Running it

```bash
python3 -m pgadmin_bridge --port 5433 --web-port 8081    # data in ./data
python3 -m unittest discover tests -b -v                 # tests; installs nothing
pipx run ruff check pgadmin_bridge tests                  # lint, as in CI
```

The tests start a fake pgAdmin ([`tests/fake_pgadmin.py`](tests/fake_pgadmin.py)) that implements the
login, the server tree and the Query Tool endpoints, then talk to the bridge with a raw wire client and,
when it is installed, real `psql`. When you change how the bridge calls pgAdmin, update the fake too.

## Project layout

```
pgadmin_bridge/
├── __main__.py           CLI: argument parsing, starts both listeners
├── config.py             config.json in the data dir: load, validate, save
├── log.py                timestamped log with a ring buffer for the web UI
├── pg/                   PostgreSQL wire protocol v3, server side
│   ├── wire.py           message framing: startup, auth, rows, errors
│   ├── errors.py         pgAdmin error text -> ErrorResponse fields
│   ├── params.py         Bind parsing, $n -> SQL literal substitution
│   └── types.py          type OIDs, binary encode/decode
├── pgadmin/              pgAdmin 4 over HTTP
│   ├── client.py         login (CSRF), cookies, server tree, serialized calls
│   └── query_tool.py     one Query Tool = one backend: open, run, fetch, cancel
├── proxy/                the Postgres listener
│   ├── server.py         TCP server, live config, idle reaper, status
│   ├── registry.py       instances -> discovered servers, User resolution
│   ├── session.py        one client: auth, simple protocol, SET replay
│   └── extended.py       Parse/Bind/Describe/Execute/Close
└── web/                  the web UI
    ├── server.py         HTTP plumbing and static files
    ├── api.py            JSON API handlers
    └── static/
        ├── index.html
        ├── css/          tokens, base shell, components/, views
        └── js/           main (router), store, api, dom, icons,
                          ui/ (drawer, dialog, toast, controls), views/ (one per page)
tests/
├── fake_pgadmin.py       stand-in pgAdmin 7 for the tests
├── helpers.py            wire client and the proxy + web UI fixture
└── test_*.py
```

## Commits and releases

- One logical change per commit, with a message that says why.
- Add a line to [CHANGELOG.md](CHANGELOG.md) under *Unreleased* for anything users notice.
- Releases are tags `vX.Y.Z` on `main`. The release workflow builds the multi-arch image for
  `ghcr.io` and creates the GitHub release from the changelog section of that version. Bump
  `__version__` in [`pgadmin_bridge/__init__.py`](pgadmin_bridge/__init__.py) first.
