# Human-in-the-Loop Review UI

The local Gradio interface presents one immutable
`AnalystReviewPackage`, lets an analyst inspect its admitted sources and
unresolved gaps, and records one separate immutable `AnalystDecision`.

It does not change the system recommendation, fact resolution, requirement
evaluation, or review package. It does not invoke a model, OCR service, policy
PDF parser, or external network during review.

## Start locally

Install the project dependencies, then set a trusted local analyst identity in
the server environment:

```bash
source .venv/bin/activate
export ANALYST_IDENTITY="vic"
python -m dispute_copilot.human_review.app \
  --review-package-id ff9d317b-ca4e-4047-b081-4dbd3ea204f5
```

The server binds to `127.0.0.1:7860` by default. The analyst identity comes
from `ANALYST_IDENTITY`; the browser cannot supply or override it.

Optional arguments:

```text
--database data/dispute_copilot.db
--project-root .
--server-name 127.0.0.1
--server-port 7860
```

## Review behaviour

The left panel displays:

- case and transaction context;
- the advisory system recommendation;
- the deterministic rationale;
- policy-ordered requirement outcomes;
- admitted supporting facts and unresolved evidence gaps; and
- explicit confirm, override, request-evidence, and close actions.

The right panel displays only a source linked through the frozen package. The
server rechecks the source filename, byte size, and SHA-256 before rendering
it. Source paths are never accepted from the browser.

Every final action has a separate review step. On submission, the service
reloads and revalidates the complete persisted lineage, verifies the displayed
package hash, applies the exact versioned decision rules, and writes the
decision plus workflow event in one SQLite transaction.

Closing without deciding creates no decision or workflow event. A second final
decision for the package is rejected. An identical replay with the same
idempotency key returns the original decision.

## Highlight capability

The current CB-2025-0006 provenance stores document and one-based page
references, but it does not store grounded bounding boxes.

- Native PDF text sources are rendered as `PAGE_ONLY`.
- Delivery-photo observations are rendered as `WHOLE_IMAGE_CONTEXT` and
  labelled `VISUAL_MODEL_OBSERVATION`.
- Evidence gaps are rendered as `NOT_APPLICABLE` with the declared source
  bundle listed for context.

No precise yellow rectangle is drawn for these records. In particular, the
chair image is shown in full with an explicit caveat; the UI never invents a
chair-region box.

The complete capability audit is in
`docs/evidence-region-capability-audit.md`.

## Persisted records

`analyst_decisions` is append-only and stores:

- the exact review package and recommendation identities;
- the package content hash shown to the analyst;
- action, selected outcome, allowed reason, comment, and selected gap IDs;
- the trusted analyst identity and server-generated UTC timestamp;
- the decision-rules key, version, and integrity hash;
- complete lineage to run, policy selection, fact resolution, evaluation,
  recommendation, and package;
- the derived next workflow status; and
- a canonical SHA-256 content hash.

The accompanying `workflow_events` row moves the effective state from
`READY_FOR_ANALYST_REVIEW` to the exact analyst outcome state. Neither row is
committed if either insert fails.
