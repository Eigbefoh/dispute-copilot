# take-home-v4 fixture acceptance report

Date: 2026-07-30

Status: `FIXTURE_ACCEPTANCE_PASSED`; live v4 execution has not been run.

## Protected baseline

- Branch baseline: `agent/live-extraction-validation-baseline`
- Baseline commit: `5c83c6fed3004ba271e8a9fc47fc449c1ed9eb3b`
- `data/demo-validation.db` SHA-256:
  `953842e9b43731e1635fd81d36f2aee6f4f6ca6cd756efa65c79101e870e74fc`
- Every file in `data/rulebooks/take-home-v3` passes the preserved checksum
  manifest in `data/checksums/take-home-v3.sha256`.
- No historical v3 policy or persisted live result was changed.

## Fixture-backed outcomes

| Case | Requirement grades | Advisory recommendation |
|---|---|---|
| CB-2025-0001 | SATISFIED, NOT_APPLICABLE, SATISFIED, SATISFIED | RECOMMEND_REPRESENT |
| CB-2025-0002 | NOT_ESTABLISHED, NOT_APPLICABLE, SATISFIED, SATISFIED | INSUFFICIENT_EVIDENCE_TO_REPRESENT |
| CB-2025-0003 | SATISFIED, SATISFIED, NOT_ESTABLISHED, NOT_ESTABLISHED | RECOMMEND_REPRESENT |
| CB-2025-0004 | NOT_SATISFIED, NOT_SATISFIED, NOT_SATISFIED, NOT_ESTABLISHED | RECOMMEND_DO_NOT_REPRESENT |
| CB-2025-0005 | SATISFIED, SATISFIED, SATISFIED | RECOMMEND_REPRESENT |
| CB-2025-0006 | SATISFIED, NOT_ESTABLISHED, NOT_ESTABLISHED | INSUFFICIENT_EVIDENCE_TO_REPRESENT |
| CB-2025-0007 | SATISFIED, SATISFIED, SATISFIED | RECOMMEND_REPRESENT |
| CB-2025-0008 | SATISFIED, NOT_ESTABLISHED, NOT_ESTABLISHED, NOT_ESTABLISHED | INSUFFICIENT_EVIDENCE_TO_REPRESENT |
| CB-2025-0009 | SATISFIED, SATISFIED, SATISFIED, SATISFIED | RECOMMEND_REPRESENT |
| CB-2025-0010 | NOT_ESTABLISHED | RECOMMEND_DO_NOT_REPRESENT |

## Behavioural proof

- CB-2025-0004 uses AVS, CVV and 3DS case metadata plus the source-qualified
  first-transaction assertion. The report confidence is not a requested fact
  and cannot influence a predicate.
- CB-2025-0006 admits positive visible type and colour observations. A static
  deterministic limitation keeps material condition `NOT_ESTABLISHED`; no
  absent hidden defect, wobble or defect-free conclusion is inferred.
- CB-2025-0007 converts all ten pages, binds transaction `txn_7745MN` to page
  8, cites destination `LS9 8AA`, and compares it with shipping metadata.
- CB-2025-0008 admits general subscription terms but leaves customer-specific
  notice, cancellation-search and opt-in facts unresolved.
- CB-2025-0009 recognises normalized `NO-SHOW`. The generic late-cancellation
  predicate has tested timezone and exact-deadline boundaries.
- CB-2025-0010 preserves `NOT_ESTABLISHED` evaluation semantics while the
  versioned policy-specific mapping applies the default do-not-represent
  disposition.

## Verification

```text
python -m pytest -q tests/test_v4_case_behaviour.py
1 passed

python -m pytest -q
317 passed, 1 skipped
```

The skipped test is the explicitly gated real-provider integration test.
No live-provider call was made during this checkpoint.
