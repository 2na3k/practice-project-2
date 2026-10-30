# Service prompt: api-migrate

## System prompt

You are reconstructing Remymy's one-shot application-database bootstrap job. Prioritize idempotency and preservation of user data. Reuse the API's shared schema/merge code; a successful run must mean validated recipe data is available.

## System bound

- Own `SystemCode/remymy/api/migrations/`; use the same image/schema as api.
- Read source data under read-only /data. Write unified /state/remymy.db on remymy-user-data.
- This is a CLI job, not an HTTP service. Do not add a second product database or a migration daemon.
- Never replace the database file: it also contains users, ratings, saves, and user-authored records. Never solve an ingest failure by deleting it.

## System contract

- Run `python -m migrations.bootstrap` with PYTHONPATH including migrations and src; restart policy "no".
- Configuration/flags: DATABASE_PATH / --database-path; LEGACY_FOOD_DATABASE_PATH / --legacy-database; RECIPES_SOURCE / --recipes; FDC_SOURCE_DIR / --fdc-dir; FDC_RELEASE / --fdc-release; --validate-only.
- Compose defaults target /state/remymy.db and legacy /data/remymy-food.db. New source ingestion requires both explicit recipe and FDC source paths if no usable target or legacy data exists.
- If the target already holds recipe data, validate SQLite quick_check, required recipe/recipe_ingredient/recipe_instruction tables, and nonempty recipes; exit 0 without reingestion.
- With --validate-only and no usable recipe data, exit nonzero without merging/ingesting.
- Otherwise validate and merge the legacy food database when present. If absent, validate source paths, ingest Open Recipes CSV/SQLite and FDC CSVs into a temporary sibling database, validate it, then merge.
- Use the shared food schema and transactional upserts. Update existing food rows without destructive REPLACE or deletes, preserving referring user records. Keep WAL behavior.
- Clean temporary database/WAL/SHM files even on failure. A source mount must remain unchanged.
- Exit 0 only after final target validation; log failures and exit 1 for handled invalid inputs/database/ingestion failures.
- The API depends on this job completing successfully. It has no published port or HTTP health route.
- Do not infer ingredient matching, recipe nutrition, or prices during basic FDC/Open Recipes ingestion; preserve source/provenance/quarantine behavior of the existing ingester.

## Build prompt

Reconstruct the bootstrap and ingestion CLI using existing API schema code and standard SQLite/file operations. Keep safe temporary staging and non-destructive merging. Reuse the API Docker build, mount /data read-only and /state writable, and gate API startup on completion. No automatic network downloads or implicit source discovery.

## Acceptance checks

- Use temporary fixture databases to test valid repeat runs, missing sources, invalid/corrupt databases, validate-only, legacy merge, failed ingestion cleanup, and user/history/save preservation.
- Use the relevant API startup/migration and ingestion tests. Do not run an exploratory migration against the live user volume.
- Report checks run, supplied sources, row-preservation evidence, and any missing prerequisites.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`
