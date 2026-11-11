# Tool result snapshots

These 38 JSON files contain each tool's successful `data` payload, exported through real tool dispatch against an isolated disposable PostgreSQL schema. Confirmation tools use `confirmed=true` and fixed request IDs. Plan snapshots retain every original database column. The three v3.3 rotation snapshots (`get_rotation_table`, `save_rotation`, `withdraw_rotation`) were written by hand from the tool result shapes: the exporter does not seed a rotation scenario.

Regenerate from the repository root with the documented disposable server on port 55433:

```bash
env -u INVESTMENT_ASSISTANT_DATABASE_URL -u TEST_DATABASE_URL \
  INVESTMENT_ASSISTANT_TEST_DATABASE_URL=postgresql://test@127.0.0.1:55433/workbench_v321_test \
  .venv/bin/python -m scripts.dev.export_tool_fixtures
```

The exporter accepts only an explicit test database on localhost:55433, creates and drops its own schema, applies the focused test migration manifest plus migration 014 for fundamentals, and routes every transaction to that schema. It clears inherited environment variables and never loads local credentials or configuration. Acquisition uses deterministic offline `AdapterDeps`; `ensure_data`, collection, normalization, coverage and database writes execute normally. A Python socket guard rejects attempted outbound connections. Temporary SEC evidence is deleted with the export scratch directory.

These are UI examples with simulated acquisition inputs, not live market evidence. Database-generated timestamps reflect export time; market dates and tool inputs are fixed. `test_tool_result_fixtures.py` checks all tool names, JSON parsing and five live payload shapes.
