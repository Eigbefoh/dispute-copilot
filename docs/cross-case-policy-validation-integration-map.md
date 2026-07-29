# Cross-Case Policy Validation — Integration Map

Baseline recorded on 2026-07-29:

- Existing architecture: immutable case intake, exact policy selection,
  policy-directed evidence conversion and fact resolution, deterministic
  evaluation, deterministic recommendation mapping, analyst-review package,
  and presentation-only Gradio review.
- Existing completed acceptance case: CB-2025-0006 under
  `visa-13.3-take-home-v2`, ending in
  `INSUFFICIENT_EVIDENCE_TO_REPRESENT`.
- Test baseline before this phase: 265 passed and one intentionally gated
  live-provider skip.
- A later local analyst decision changed the real demo database from
  `READY_FOR_ANALYST_REVIEW` to `MORE_EVIDENCE_REQUESTED`. Seven tests that
  copied that database inherited the legitimate decision. Test fixtures must
  remove human decisions only from their temporary copies; the real
  append-only decision history must not be edited.

Integration approach:

1. Provision `take-home-v3`; do not mutate historical v2 policy artifacts.
2. Extend the existing declarative predicate schema with requirement
   applicability, policy aggregation, negation, and deterministic material
   address comparison.
3. Extend the existing fact catalogue and metadata resolver. Evidence
   extraction remains generated from the selected policy's frozen
   `required_facts`; no case ID enters production policy logic.
4. Preserve the existing overall evaluation enum as the stable input to the
   separately versioned recommendation mapping. Store an explicit aggregation
   summary in new evaluations.
5. Add isolated four-case end-to-end tests and render every resulting package
   through the existing case-agnostic review adapters.

Production branches on CB-2025-0001 through CB-2025-0004 are prohibited.
Expected outcomes belong only in tests and demo documentation.
