# Contributing

```bash
make install
make test
```

- New event types: add to `specs/event-schema/v1/event_types.json` AND
  `sdk/python/controldb/events.py` AND
  `services/collector/controldb_collector/ingest.py:EVENT_TYPES`.
- Never break existing events. Bump `schema_version` (date string) instead.
- Every PR must include unit + integration tests.
- Run `make test` before pushing.
