---
name: review-content-pipeline
description: Reviews or implements changes to the Velura generation, review, scheduling, and posting pipeline. Use when modifying approval flow, preview behavior, payload persistence, scheduling, posting state, or end-to-end workflow transitions.
---

# Review Content Pipeline

## Use When

- Changing approval, preview, scheduling, or posting behavior
- Adding a new workflow state between generation and posting
- Refactoring `cli.py`, `src/db.py`, `src/models.py`, or `db/schema.sql` for pipeline reasons

## Instructions

1. Clarify any ambiguous workflow requirement before editing.
2. Identify the affected state transitions from generation through posting.
3. Keep workflow state durable in storage; do not rely on transient values.
4. Persist platform payloads before delayed posting or scheduling depends on them.
5. Keep CLI changes thin and move persistence logic into `src/db.py`.
6. If the task would touch more than 3 files, split it into smaller batches.
7. After each batch, list edge cases and the tests that should cover them.

## Checklist

- [ ] Storage contract updated if workflow state changed
- [ ] CLI surface matches the workflow state model
- [ ] Generated captions and hashtags remain durable
- [ ] Approval-first flow is not bypassed accidentally
- [ ] Tests cover new state transitions
