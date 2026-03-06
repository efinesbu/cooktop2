---
name: write-workflow-tests
description: Writes or updates tests for Velura workflow, CLI, DB, and state-machine behavior. Use when adding approval, scheduling, payload persistence, readiness, or bug-fix coverage across the pipeline.
---

# Write Workflow Tests

## Use When

- Changing state transitions in the content pipeline
- Fixing a workflow bug that needs regression coverage
- Adding CLI commands for review, approval, scheduling, or posting

## Instructions

1. Start with the contract or bug scenario to reproduce.
2. For bug fixes, write the failing test first.
3. Mirror production structure under `tests/` and reuse `tests/conftest.py` fixtures.
4. Mock all external APIs with `respx` or `unittest.mock.patch`.
5. Cover normal flow, boundary inputs, idempotency, and failure paths.
6. After each test batch, note any remaining edge cases that are not yet covered.

## Checklist

- [ ] Test reproduces the intended workflow or bug
- [ ] External services are mocked
- [ ] Assertions cover persisted state, not just return values
- [ ] Regression coverage exists for corrected behavior
