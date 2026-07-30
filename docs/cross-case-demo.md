# Cross-Case Policy Demo

The automated acceptance proof is:

```bash
pytest -q \
  tests/test_cross_case_policy_rules.py \
  tests/test_cross_case_end_to_end.py
```

It provisions `take-home-v3` in isolated SQLite databases and runs
CB-2025-0001 through CB-2025-0004 from immutable intake to analyst-review
package. The extraction provider is a deterministic, provenance-checked test
fixture; policy selection, fact admission, evaluation, recommendation,
persistence, package assembly, and evidence display use production code.

Expected outcomes:

| Case | Requirement results | Recommendation |
|---|---|---|
| CB-2025-0001 | SATISFIED, NOT_APPLICABLE, SATISFIED, SATISFIED | RECOMMEND_REPRESENT |
| CB-2025-0002 | NOT_ESTABLISHED, NOT_APPLICABLE, SATISFIED, SATISFIED | INSUFFICIENT_EVIDENCE_TO_REPRESENT |
| CB-2025-0003 | SATISFIED, SATISFIED, NOT_ESTABLISHED, NOT_ESTABLISHED | RECOMMEND_REPRESENT |
| CB-2025-0004 | NOT_SATISFIED, NOT_SATISFIED, NOT_SATISFIED, NOT_ESTABLISHED | RECOMMEND_DO_NOT_REPRESENT |

For a live provider-backed demonstration, first configure the explicit
`MODEL_*` environment variables. Then repeat this sequence for each case:

```bash
python -m dispute_copilot.cli CB-2025-0001

python -m dispute_copilot.cli select-policy <run-id> \
  --rulebook-version take-home-v3

python -m dispute_copilot.extraction.resolve_facts <selection-id>

python -m dispute_copilot.evaluation.evaluate_requirements <selection-id>

python -m dispute_copilot.recommendation.recommend \
  --selection-id <selection-id> \
  --mapping-key default-representment-eligibility-v1 \
  --mapping-version 1.0.0

python -m dispute_copilot.analyst_review.review \
  --selection-id <selection-id> \
  --presentation-rules-version 1.1.0
```

Copy the emitted review-package ID and launch the same UI for every case:

```bash
export ANALYST_IDENTITY="vic"
python -m dispute_copilot.human_review.app \
  --review-package-id <review-package-id>
```

Suggested walkthrough:

1. Compare CB-2025-0001 with CB-2025-0002. Both select the exact same Visa
   policy and `ALL_APPLICABLE` implementation. The first has signed delivery
   aligned with billing and shipping; the second establishes delivery to the
   checkout shipping postcode but retains the billing/address-attribution
   conflict and missing signature.
2. Compare CB-2025-0003 with CB-2025-0004. Both select the same Mastercard
   policy and `ANY_TWO` implementation. The third establishes exactly two
   qualifying routes. The fourth explicitly fails three routes; its confident
   merchant risk opinion is inspectable but is not converted into a fifth
   scheme route.

The UI rechecks evidence hashes before display. Native text is page-grounded
and image evidence remains whole-image context unless persisted coordinates
actually exist.
