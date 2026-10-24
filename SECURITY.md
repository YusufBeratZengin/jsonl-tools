# Security policy

## Supported versions

Security fixes target the latest 0.1.x release. Older prereleases are unsupported.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/YusufBeratZengin/jsonl-tools/security/advisories/new).
Include the affected version, impact, reproduction steps and a minimal synthetic
sample. Do not post exploit details in public issues or include private datasets,
credentials or personal data. If private reporting is unavailable, wait until
maintainers enable it rather than publishing sensitive details.

## Input handling

The tool parses JSON and never evaluates Python expressions. User-provided regexes
can consume excessive CPU; use trusted patterns. Extremely large lines or deeply
nested data can exhaust resources. Dedupe memory grows with unique keys; writes
can leave partial output after errors. Use resource limits for untrusted input
and choose output paths with care. There is no telemetry or runtime network access.
