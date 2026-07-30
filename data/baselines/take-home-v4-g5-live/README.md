# Take-home v4 G5 live checkpoint

This directory preserves a consistent SQLite online-backup snapshot of the
live `take-home-v4` analyst-demo database at the G5 default-version promotion
checkpoint.

The snapshot contains four independently persisted live attempts:

- `CB-2025-0001`
- `CB-2025-0002`
- `CB-2025-0004`
- `CB-2025-0007`

Each attempt is recorded as `COMPLETED` and retains its immutable intake,
policy-selection, fact-resolution, evaluation, recommendation, analyst-review,
and suggested-action lineage. Reopening the snapshot in cached mode must not
call a model provider.

Verify the frozen bytes from the repository root:

```bash
shasum -a 256 -c \
  data/baselines/take-home-v4-g5-live/checksums.sha256
```

The live working database remains at `data/v4-live-validation.db`; this
baseline copy must not be modified by future executions.
