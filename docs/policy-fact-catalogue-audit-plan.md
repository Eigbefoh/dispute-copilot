# Policy Fact Catalogue Audit — Revised Implementation Plan

> Implementation status: complete. The reviewed catalogue is provisioned as
> `take-home-v2`, imported append-only alongside `take-home-v1`, and covered by
> the repository test suite.

## Outcome

Create a versioned fact catalogue in which every fact requested by all eight
policies has reviewed, unambiguous semantics.

Every registered fact must state:

- what value is observed;
- who or what source asserted or observed it;
- its controlled value type;
- whether one or multiple values may exist;
- whether it comes from immutable case metadata or evidence.

The architecture must prevent a document assertion from silently becoming
established truth. For example:

```text
Carrier document says "delivered"
        ↓
carrier_reported_delivery_status = "delivered"
        ↓
later deterministic evaluation
```

It must not become:

```text
delivery_proven = true
```

This milestone audits and registers the fact vocabulary. It does not compare
facts, grade policy requirements, or make recommendations.

## Current baseline

The repository currently has:

- 8 executable policies;
- coverage for 8/8 unique policy keys and 10/10 supplied cases;
- 51 unique `required_fact` names;
- exact policy citations into the canonical transcript;
- immutable policy selections and append-only SQLite storage.

Some current names are already suitably atomic, including:

- `tracking_number`
- `carrier_name`
- `avs_result`
- `cvv_result`
- `three_ds_status`
- `first_authorisation_code`
- `second_transaction_timestamp`

Others encode broad evidence categories, unqualified claims, comparisons, or
conclusions and must be renamed, split, or removed.

## Primary control: registered semantics

The fact definition is the primary control. Name linting is only a secondary
guardrail.

A harmless-looking name such as `valid_delivery`, `acceptable_condition`, or
`confirmed_fraud` can still conceal a conclusion. Passing a naming test is
therefore never sufficient.

Each required fact must receive a human semantic review. The final catalogue
must contain the approved definition, source type, value type, and cardinality.
The importer must reject policies whose facts are missing from that catalogue.

The review must answer:

1. Is this directly observable?
2. Is it an assertion made by a document or actor?
3. If it is an assertion, does the name identify who made it?
4. Can its value be read without comparing it with another fact?
5. Can it be collected without applying a policy rule?
6. Could multiple values legitimately exist?
7. Is it already available in the immutable `CaseRun`?

A fact is excluded from the catalogue if answering it requires comparison,
policy grading, or recommendation.

## Versioning decision

Do not rewrite already imported `take-home-v1` records.

Changing `required_facts` changes policy bytes and hashes. Reusing the same
version would allow two installations to associate different policies with the
same versioned key and would conflict with append-only persistence.

Provision the reviewed catalogue as a new derived version:

```text
data/rulebooks/take-home-v2/
```

The source PDF content and citations may remain identical, but the executable
policy files, catalogue, and metadata hashes are new versioned artifacts.
Existing `take-home-v1` selections remain unchanged and retrievable.

## Versioned artifact layout

Use one authoritative fact catalogue per rulebook version:

```text
data/rulebooks/take-home-v2/
├── metadata.json
├── source/
│   └── reason-codes.pdf
├── transcript/
│   └── canonical-transcript.json
├── facts/
│   ├── catalogue.json
│   └── fact-review.csv
└── policies/
    ├── mastercard-4837.json
    ├── mastercard-4855.json
    ├── mastercard-4859.json
    ├── visa-10.5.json
    ├── visa-12.6.1.json
    ├── visa-13.1.json
    ├── visa-13.2.json
    └── visa-13.3.json
```

`catalogue.json` is the runtime source of truth.

`fact-review.csv` is the complete audit work product. It records how every
original `required_fact` was classified and whether it was kept, renamed,
split, or removed. It is not a second runtime registry.

## Controlled fact contract

Create `src/dispute_copilot/policy/fact_models.py`.

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FactDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_name: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    )
    description: str = Field(min_length=1)

    value_type: Literal[
        "STRING",
        "DATE",
        "DATETIME",
        "INTEGER",
        "DECIMAL",
        "BOOLEAN",
        "ADDRESS",
    ]

    source_type: Literal[
        "EVIDENCE_TEXT_ASSERTION",
        "EVIDENCE_VISUAL_OBSERVATION",
        "CASE_METADATA",
    ]

    cardinality: Literal["SINGLE", "MULTIPLE"] = "SINGLE"


class FactCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rulebook_version: str = Field(min_length=1)
    facts: tuple[FactDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def fact_names_are_unique(self) -> "FactCatalogue":
        names = [fact.fact_name for fact in self.facts]
        if len(names) != len(set(names)):
            raise ValueError("fact_name values must be unique")
        return self
```

`cardinality` is independent of `value_type`. For example:

```json
{
  "fact_name": "carrier_reported_tracking_event_date",
  "description": "Date reported for each tracking event in the carrier record.",
  "value_type": "DATE",
  "source_type": "EVIDENCE_TEXT_ASSERTION",
  "cardinality": "MULTIPLE"
}
```

This is preferable to an uncontrolled type such as `"list[date]"`.

The catalogue should not contain confidence, extracted values, or citations.
Those belong to the later immutable extraction result.

## Source-qualified semantics

Names for evidence assertions must identify the asserting source or actor when
the value could otherwise be mistaken for truth.

Preferred forms include:

- `carrier_reported_delivery_status`
- `carrier_reported_delivery_address`
- `carrier_reported_delivery_date`
- `merchant_stated_return_window_days`
- `merchant_stated_product_description`
- `issuer_assigned_reason_code`
- `booking_record_reported_cancellation_date`

Visual observations must be explicitly observational:

- `image_observed_item_colour`
- `image_observed_item_type`
- `image_observed_visible_damage`

These facts describe what is visible in an image. They do not decide whether
the item matches a listing, was delivered successfully, or is defective.

Descriptions must reinforce the same boundary. A source-qualified name with a
definition that claims truth is still invalid.

Example:

```json
{
  "fact_name": "carrier_reported_delivery_address",
  "description": "Delivery address printed or asserted by the carrier record; this does not establish that delivery occurred at that address.",
  "value_type": "ADDRESS",
  "source_type": "EVIDENCE_TEXT_ASSERTION",
  "cardinality": "SINGLE"
}
```

## Fact-review table

Create a deterministic exporter:

```bash
python -m dispute_copilot.policy.export_fact_review \
  data/rulebooks/take-home-v1 \
  --output data/rulebooks/take-home-v2/facts/fact-review.csv
```

The exporter reads all eight policies and writes one row per unique original
fact. It never modifies policies or SQLite.

Required columns:

| Column | Meaning |
|---|---|
| `original_fact_name` | Name currently referenced by a policy |
| `definition` | Operational meaning after human review |
| `value_type` | Controlled final value type |
| `source_type` | Controlled final source route |
| `cardinality` | `SINGLE` or `MULTIPLE` |
| `policies_using_it` | Stable list of `policy_id:requirement_id` references |
| `classification` | Semantic classification |
| `decision` | `KEEP`, `RENAME`, `SPLIT`, or `REMOVE` |
| `replacement_fact_names` | Final registered names, if applicable |
| `review_notes` | Reason for the decision |

Controlled review classifications:

```text
OBSERVABLE_ATOMIC_VALUE
DOCUMENT_ASSERTION
VISUAL_OBSERVATION
CASE_METADATA
COMPARISON
POLICY_CONCLUSION
RECOMMENDATION
```

Only the first four classifications may result in final registered facts.
Comparisons, policy conclusions, and recommendations must have a `REMOVE` or
`SPLIT` decision and may not appear in final `required_facts`.

The completed review table must have exactly one row for every unique original
fact and no blank review decision.

## Deterministic metadata boundary

`CASE_METADATA` facts must never be sent to an OCR, vision, or language-model
provider.

The runtime boundary will be:

```text
Selected policy facts
        │
        ├── CASE_METADATA
        │       ↓
        │   deterministic CaseRun metadata resolver
        │
        └── EVIDENCE_TEXT_ASSERTION
            EVIDENCE_VISUAL_OBSERVATION
                ↓
            evidence extraction provider
```

Create a routing component:

```text
src/dispute_copilot/extraction/fact_router.py
```

```python
class FactResolutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_metadata_facts: tuple[FactDefinition, ...]
    evidence_facts: tuple[FactDefinition, ...]


def build_fact_resolution_plan(
    definitions: tuple[FactDefinition, ...],
) -> FactResolutionPlan:
    ...
```

The partition must be disjoint, exhaustive, and order-preserving.

Create a deterministic resolver boundary:

```text
src/dispute_copilot/extraction/metadata_resolver.py
```

The resolver uses an explicit allowlist rather than unrestricted `getattr`:

```python
CASE_METADATA_RESOLVERS = {
    "chargeback_date": lambda run: run.case_snapshot.chargeback_date,
    "transaction_date": lambda run: (
        run.case_snapshot.transaction.transaction_date
    ),
    "transaction_amount": lambda run: (
        run.case_snapshot.transaction.amount.value
    ),
}
```

Every registered `CASE_METADATA` fact must have exactly one deterministic
resolver. Import validation should fail when a metadata fact lacks one.

The evidence-provider request builder accepts only
`resolution_plan.evidence_facts`. It must be impossible to pass the complete
unpartitioned list accidentally.

No AI component should rediscover:

- transaction amount;
- disputed amount;
- transaction date;
- chargeback date;
- scheme;
- reason code;
- or other values already frozen in `CaseRun`.

## Semantic audit rules

Implement validation in:

```text
src/dispute_copilot/policy/fact_catalogue.py
```

The primary validation performs these checks:

1. Collect all required-fact references from all policies.
2. Require every final policy fact to have exactly one definition.
3. Require every catalogue definition to be referenced.
4. Require every original fact to have exactly one completed review row.
5. Require every final name in the review table to match the catalogue.
6. Reject final facts classified as comparison, conclusion, or recommendation.
7. Require controlled value type, source type, and cardinality.
8. Require every `CASE_METADATA` fact to have a deterministic resolver.
9. Require source-qualified semantics for evidence assertions where an actor
   or document makes the claim.
10. Require source-observational semantics for visual facts.

Some semantic requirements cannot be proven reliably using string matching.
They are enforced through the completed review record, registered definition,
human approval, and targeted tests for known risky facts.

## Secondary name lint

Retain a small name lint as defence in depth:

```python
PROHIBITED_FACT_NAMES = {
    "proof_of_delivery",
    "product_matches_listing",
    "description_match_evidence",
    "prior_billing_details_match",
    "issuer_miscoding_evidence",
    "should_represent",
}
```

Also flag obvious patterns such as:

```text
proof_of_
_matches_
_satisfied
_eligible
should_
_recommendation
```

This lint produces an immediate failure for known mistakes, but passing it does
not establish that a fact is valid. The registered semantics remain the
architectural control.

## Initial review queue

The following current facts need explicit adjudication:

| Existing fact | Issue | Proposed direction |
|---|---|---|
| `description_match_evidence` | Performs or presupposes comparison | Remove. Evaluation compares listing assertions with visual or textual delivered-item observations. |
| `prior_billing_details_match` | Performs comparison | Split into source-qualified prior billing fields. Evaluation performs the match. |
| `issuer_miscoding_evidence` | Encodes a policy conclusion | Split into issuer-assigned classification and observable transaction-processing attributes. |
| `delivery_or_service_evidence` | Names a body of evidence | Split into source-reported fulfilment status, date, address, and service assertions. |
| `distinct_purchase_details` | Encodes distinctness | Extract first and second identifiers, dates, amounts, currencies, and descriptions independently. |
| `delivery_confirmation` | Sounds like established delivery | Replace with `carrier_reported_delivery_status` and related carrier-reported fields. |
| `delivery_address` | Source is ambiguous | Use source-qualified variants such as carrier-reported, merchant-provided, or case-recorded address. |
| `service_rendered` | Sounds established | Rename to an actor-qualified service-completion assertion. |
| `service_rendered_date` | Source is ambiguous | Rename to an actor-qualified reported completion date. |
| `policy_disclosure_at_booking` | Bundles content, time, and channel | Split into merchant-stated policy text, disclosure time, and disclosure channel. |
| `attendance_or_cancellation_record` | Names a document | Split into record-reported attendance status and cancellation events. |
| `reservation_or_booking` | Bundles a record | Split into record-reported reservation ID, booking time, service date, and status. |
| `upcoming_charge_notification` | Bundles content and delivery | Split into merchant-stated notification content, channel, recipient, and date. |
| `return_or_refund_route` | Bundles instructions | Split into merchant-stated return method, contact route, deadline, and remedy. |
| `first_purchase_documentation` | Document-presence label | Define a source document identifier/type, not proof of a distinct purchase. |
| `second_purchase_documentation` | Document-presence label | Define a source document identifier/type, not proof of a distinct purchase. |
| `post_complaint_usage_or_retention` | Bundles alternative behaviours | Split into source-reported usage events and retention assertions. |

Replacement fields must be supported by the cited rule text. The audit must not
invent new policy requirements merely to make the data model convenient.

## Rulebook metadata changes

Add a catalogue record to `metadata.json`:

```json
{
  "fact_catalogue": {
    "path": "facts/catalogue.json",
    "sha256": "<64-character SHA-256>",
    "fact_count": 0
  }
}
```

Add `FactCatalogueMetadata` to `rule_models.py`.

Each `PolicyMetadata` should also record `fact_catalogue_sha256`, binding each
executable policy to:

- its canonical transcript;
- its reviewed fact semantics.

The review CSV should also have a recorded SHA-256 in metadata so the completed
human audit is preserved as provenance.

## Implementation milestones

### Milestone A — Export the complete review table

Implement the read-only exporter and prove:

- all 51 current unique facts appear exactly once;
- all policy and requirement usages are recorded;
- export order is deterministic;
- no policy or database state is modified.

### Milestone B — Complete semantic review

For each exported row:

1. write the operational definition;
2. classify the fact;
3. identify the assertion source when applicable;
4. select controlled value type;
5. choose `SINGLE` or `MULTIPLE`;
6. record `KEEP`, `RENAME`, `SPLIT`, or `REMOVE`;
7. record final replacement names.

Review policies in this order:

1. Visa 13.1;
2. Visa 13.3;
3. Mastercard 4855;
4. Mastercard 4837;
5. Visa 10.5;
6. Visa 12.6.1;
7. Visa 13.2;
8. Mastercard 4859.

### Milestone C — Build the authoritative catalogue

Generate or manually create `facts/catalogue.json` from approved rows.

The catalogue must contain:

- one definition per final fact;
- no comparison, conclusion, or recommendation;
- source-qualified assertion names;
- explicit visual-observation names;
- controlled types and cardinality.

The catalogue must be reviewed independently of the automated name lint.

### Milestone D — Update executable policies

Change final `required_facts` to reference only registered facts.

Requirement descriptions may remain policy-oriented because evaluation will
grade requirements. Required facts beneath them must remain observable.

Do not change citations merely to fit renamed facts. Every citation must still
resolve exactly against the canonical transcript.

### Milestone E — Add metadata routing

Implement the fact router and deterministic metadata-resolver allowlist.

Validate that:

- every `CASE_METADATA` definition has a resolver;
- evidence facts never enter the metadata resolver;
- metadata facts never enter an extraction-provider request;
- partitioning preserves selected-policy order.

### Milestone F — Integrate importer validation

Extend `ValidatedRulebook` with the catalogue and its hash.

Before any SQLite write, `validate_rulebook()` must:

1. verify catalogue and review-table hashes;
2. validate the catalogue contract;
3. validate review completeness;
4. validate exact policy-to-catalogue references;
5. enforce semantic audit decisions;
6. enforce deterministic metadata-resolver coverage;
7. apply secondary prohibited-name lint;
8. run existing policy schema and citation validation.

### Milestone G — Persist definitions atomically

Add append-only storage:

```sql
CREATE TABLE fact_definitions (
    rulebook_version TEXT NOT NULL,
    fact_name TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (rulebook_version, fact_name)
);
```

Insert policies and definitions in one transaction. Any duplicate, conflict, or
validation error rolls back the entire import.

Provide read-only repository methods:

```python
def get_fact_definition(
    rulebook_version: str,
    fact_name: str,
) -> FactDefinition | None:
    ...


def list_fact_definitions(
    rulebook_version: str,
) -> tuple[FactDefinition, ...]:
    ...
```

### Milestone H — Re-prove coverage

Re-run:

- policy schema validation;
- exact rulebook citation validation;
- 8/8 unique policy-key coverage;
- 10/10 supplied-case coverage.

Existing `take-home-v1` rows and selections must remain unchanged.

## Required tests

Create `tests/test_policy_fact_catalogue.py`.

### Contract tests

- `FactDefinition` and `FactCatalogue` are immutable.
- Extra fields are rejected.
- Value type, source type, and cardinality are controlled.
- Canonical fact naming is enforced.
- Duplicate fact definitions are rejected.

### Review-completeness tests

- Every original unique policy fact has exactly one review row.
- Every review row records definition, classification, decision, source type,
  value type, cardinality, and usages.
- `COMPARISON`, `POLICY_CONCLUSION`, and `RECOMMENDATION` rows cannot be kept.
- Every replacement name resolves to exactly one catalogue definition.
- Every final catalogue definition traces to at least one review decision.

### Semantic tests

- Every policy fact has exactly one registered definition.
- No unregistered or unused definitions exist.
- Known comparisons and conclusions are absent.
- Source-dependent assertions have source-qualified names and definitions.
- Visual facts describe observations rather than product or policy outcomes.
- Multi-valued tracking events, addresses, records, and cancellation attempts
  can be represented with `cardinality="MULTIPLE"`.
- Prohibited-name lint still catches known bad names.
- Passing name lint alone is not treated as semantic approval.

### Metadata-routing tests

- Policy facts are partitioned exhaustively and without overlap.
- Partition order matches selected-policy order.
- Every `CASE_METADATA` fact has exactly one deterministic resolver.
- Metadata values come directly from the immutable `CaseRun`.
- A recording extraction provider receives no metadata facts.
- OCR, vision, and language-model interfaces are never called while resolving
  metadata.

### Rulebook and persistence tests

- All eight policies pass Pydantic validation.
- Every policy citation still matches canonical transcript text.
- Catalogue, review table, transcript, and policy hashes match metadata.
- Modified catalogue or review bytes cause import failure.
- Undefined policy facts cause import failure.
- Policy and definition rows import atomically.
- Duplicate or conflicting versioned fact keys are rejected.
- A fresh repository instance retrieves identical definitions.
- Existing `take-home-v1` rows remain unchanged.

### Coverage tests

- The new version contains all 8 unique policy keys.
- The new version covers all 10 supplied cases.
- No evidence document, source PDF parser, OCR, vision, or AI provider is
  accessed during catalogue audit or import.

## Definition of done

- All 51 original fact names have exactly one completed review record.
- All eight policies have been reviewed.
- Every final policy fact exists in exactly one registry.
- Every definition has controlled value type, source type, and cardinality.
- Source-dependent assertions preserve who or what made the claim.
- Visual facts describe observations rather than conclusions.
- All metadata facts have deterministic resolvers.
- No metadata fact can be sent to an extraction provider.
- Multi-valued facts are supported where required.
- No registered fact performs comparison, policy grading, or recommendation.
- Name lint remains a secondary guardrail only.
- Policy schema and exact citation validation pass.
- Coverage remains 8/8 policy keys and 10/10 cases.
- Existing immutable `take-home-v1` data remains untouched.
- The complete test suite passes.

## Explicit non-goals

This milestone does not:

- extract evidence values;
- assign confidence;
- create evidence citations;
- reconcile conflicting claims;
- compare a delivery address with a case address;
- compare an item image with a listing;
- grade policy requirements;
- recommend whether to represent a dispute;
- advance a run to `READY_FOR_EVALUATION`;
- invoke OCR, vision, or an LLM.

## Handoff to extraction

After this audit, extraction orchestration can safely perform:

```text
Selected policy required facts
        ↓
resolve definitions for exact rulebook version
        ↓
partition by source_type
        ├── CASE_METADATA
        │       ↓
        │   deterministic metadata resolver
        │
        └── evidence assertions and observations
                ↓
            extraction provider
        ↓
merge source-qualified observations
        ↓
later citation validation and immutable ExtractionResult
```

The future `ExtractionResult` must preserve the exact catalogue hash used so
the meaning of every extracted value remains reproducible.
