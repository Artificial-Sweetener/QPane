# Changelog

## v1.2.0 (2026-01-09)

### Features
- Simplify tool dispatch by removing reflective fallbacks and documenting default tool modes

### Fixes
- Prevent SAM feature install races in the test harness by enforcing deterministic setup

### Refactors
- Emit cache coordination signals for more reliable budget and usage diagnostics
- Tighten swap delegate conformance to the strict protocol implementation
- Enforce typed diagnostics snapshots for safer consumer handling

### Documentation
- Add PySide6 version badge to the README
- Clarify agent guardrails for host-facing scope

## v1.1.1 (2026-01-04)

### Fixes
- Key tiles, pyramids, and SAM predictor caches by image UUID to avoid pathless collisions
- Guard worker signal emissions during teardown to avoid spurious runtime errors

## v1.1.0 (2026-01-02)

### Features
- Add optional SAM checkpoint hash verification with built-in default hash support
- Warn when custom SAM model URLs are used without integrity verification

### Documentation
- Document SAM checkpoint hash configuration and demo usage

## v1.0.1 (2025-12-31)

### Documentation
- Note the PyPI-required version advance after removing 1.0.0

## v1.0.0 (2025-12-31)

### Features
- Initial public release
