---
name: debug-runtime-config
description: Diagnoses mismatches between Velura runtime code, README setup steps, and config.example.yaml. Use when a platform integration fails due to missing config, unclear prerequisites, machine setup issues, or inconsistent documented keys.
---

# Debug Runtime Config

## Use When

- Docs describe one config shape but runtime expects another
- A machine setup or onboarding step fails because config is unclear
- A platform integration errors before runtime work can proceed

## Instructions

1. Compare consuming code, `config.example.yaml`, and README setup docs.
2. Identify the exact missing, renamed, or undocumented config keys.
3. Prefer aligning docs and code rather than adding one-off workarounds.
4. Keep secrets out of commits and examples.
5. Split larger config cleanup into small batches if it would exceed 3 files.
6. Add tests for config validation when practical.

## Checklist

- [ ] Runtime code, example config, and docs agree
- [ ] Required prerequisites are documented clearly
- [ ] Sensitive values are not committed
- [ ] Error messages point to the missing setup step
