---
name: add-platform-integration
description: Adds or updates social posting and analytics integrations for Velura. Use when working on src/posters, src/analytics, platform auth, upload requirements, retries, or adapter-specific config and tests.
---

# Add Platform Integration

## Use When

- Adding a new platform adapter
- Changing YouTube, Instagram, TikTok, or X posting behavior
- Changing analytics pullers, auth requirements, upload prerequisites, or retry logic

## Instructions

1. Clarify the platform requirement and any missing API prerequisites first.
2. Keep platform-specific logic inside the relevant adapter module.
3. Validate required config early and return actionable errors.
4. Reuse shared retry and persistence helpers instead of duplicating logic.
5. Update docs and example config whenever runtime config changes.
6. Keep the batch under the 3-file limit or split it before editing.
7. Add or update adapter contract tests with mocked APIs.

## Checklist

- [ ] Config keys are documented and consumed consistently
- [ ] Retry and timeout behavior follows shared conventions
- [ ] External prerequisites are documented clearly
- [ ] Tests cover success, config failure, and transient API failure cases
