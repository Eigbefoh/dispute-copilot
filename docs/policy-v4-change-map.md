# take-home-v4 implementation change map

This document records the repository map and immutable baseline established
before implementing `take-home-v4`. It does not redefine historical policy
semantics.

## Baseline

- Historical bundle: `data/rulebooks/take-home-v3`
- Predicate schema: `requirement-predicates/2.0.0`
- Fact catalogue: 74 flat fact definitions
- Executable policies: 8
- Baseline commit: `ba21d84228cbd55cf8fad3d970e742aa52ce9fff`
- Baseline tests on 2026-07-30: 284 passed, 1 explicitly gated live-provider
  test skipped
- Historical main and fixture SQLite integrity checks: `ok`

The exact v3 asset set and hashes are frozen in
`tests/fixtures/take-home-v3-asset-hashes.json`. Registry lifecycle metadata is
stored outside the hashed bundle in `data/rulebooks/registry.json`.

## Current flow

1. `orchestration/service.py` creates an immutable case and evidence snapshot.
2. `policy/selection_service.py` freezes an exact versioned policy.
3. `extraction/fact_router.py` separates deterministic case metadata from
   evidence facts.
4. `extraction/fact_resolution_service.py` converts evidence, obtains
   provider candidates, validates them, and persists one immutable result.
5. `evaluation/predicate_catalogue.py` validates the policy predicate manifest.
6. `evaluation/interpreter.py` evaluates allow-listed three-valued predicates.
7. `evaluation/service.py` persists requirement evaluations and workflow state.
8. `recommendation/service.py` applies a versioned mapping and persists an
   advisory recommendation.
9. `analyst_review/service.py` creates an immutable review package.
10. `human_review/decision_service.py` appends a human decision.

## Planned extension map

| Area | Existing modules to extend | New versioned assets/modules |
|---|---|---|
| Semantic contracts | Policy and predicate tests | v4 source-derived contract fixtures |
| Facts | `policy/fact_models.py`, extraction contracts and validation | Structured fact model v2 and record schemas |
| Predicate language | `evaluation/predicate_models.py`, `predicate_catalogue.py`, `interpreter.py` | Predicate schema v3 and structured traces |
| Domain comparisons | Existing material-address logic | Versioned deterministic domain-predicate registry |
| Recommendations | `recommendation/mapping_models.py`, `mapper.py`, registry | Policy-specific outcome mapping manifest |
| Policies | Importer and exact selection remain generic | New `data/rulebooks/take-home-v4` bundle |
| Review package | Analyst-review models, assembler, guards | Presentation rules and package schema v2 |
| Persistence | Append-only repositories and lineage guards | Additive v4-compatible payloads; no historical rewrites |

## Open semantic decisions

- Visa 13.2 notification threshold is not defined by the supplied source.
- Visa 13.2 lead time is described as “typically 7+ days”, not as a complete
  deterministic rule.
- Product materiality must use approved attribute mappings; arbitrary free-text
  similarity is prohibited.
- Visa 10.5 requires an approved product vocabulary for the source’s default
  `accept_liability` outcome.

Affected v4 paths must remain blocked until these values or rules are approved.
