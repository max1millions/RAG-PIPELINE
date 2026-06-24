# Golden fixtures

Deterministic checks run via `bin/orion-golden-test`. Used after `orion-fix` or manual edits to CWR/SQL logic — **not** invoked by the watchdog cron.

## Adding fixtures

- **SQL cases:** add entries to `manifest.yaml` with `sql` or `sql_file` and `expect` block.
- **CWR samples:** place anonymized `.V21` files under `cwr/` (gitignored). Reference paths in manifest when ready.
- **No PII** in committed expected JSON.

## Local DB

SQL scalar/row cases require local MySQL (`orion-db`). Cases skip gracefully when MySQL is unavailable.
