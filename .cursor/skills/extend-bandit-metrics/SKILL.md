---
name: extend-bandit-metrics
description: Updates Velura recommendation logic, metric interpretation, and reporting behavior. Use when changing bandit priors, success definitions, metrics thresholds, attribution logic, or recommendation and reporting calculations.
---

# Extend Bandit Metrics

## Use When

- Changing the success metric definition
- Adjusting bandit priors or bootstrap behavior
- Adding attribution-aware optimization or new reporting calculations

## Instructions

1. Clarify the metric definition and decision rule before editing.
2. Identify how the change affects recommendation, update, and reporting paths.
3. Preserve correct first-observation behavior for unseen arms.
4. Update calculations in one small batch per layer when possible.
5. Start bug fixes with a failing test, then implement the fix.
6. After the batch, list edge cases such as no metrics, zero views, or sparse history.

## Checklist

- [ ] Success and failure semantics are explicit
- [ ] Bootstrap behavior is covered by tests
- [ ] Reporting reflects the same metric definitions as the optimizer
- [ ] No-metric and low-volume cases are handled safely
