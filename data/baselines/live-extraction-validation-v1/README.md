# Live Extraction Validation Baseline v1

This directory freezes the system immediately before live extraction
validation.

It contains:

- `fixture-demo.db`: a portable SQLite database with complete immutable
  fixture-backed chains for CB-2025-0001 through CB-2025-0004.
- `fixture-outcomes.json`: IDs, policy hashes, admitted deterministic grades,
  aggregations, recommendations, and content hashes for those chains.
- `test-results.txt`: the exact regression command and result at the freeze.
- `integrity-manifest.json`: SHA-256 hashes for the baseline's material code,
  database, rulebook, presentation, and fixture artifacts.

The extraction records in `fixture-demo.db` explicitly identify the
deterministic fixture provider. They are not represented as live-model output.
Every later live run must use a new immutable run and selection; this baseline
must never be updated to make a live result agree with it.

Git identity:

- Branch: `agent/live-extraction-validation-baseline`
- Tag: `live-extraction-validation-v1`

The tag resolves the exact code commit. The integrity manifest resolves the
exact database and data artifacts inside that commit.
