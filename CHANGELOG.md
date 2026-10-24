# Changelog

All notable changes follow [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Bilingual documentation and a reproducible terminal SVG from actual CLI output.
- Regression tests for streaming, invalid paths, numeric extremes and output safety.

### Fixed
- Reject input/output aliases before truncating files and invalid CLI limits early.
- Bound skipped-line previews and close the raw file of each gzip split part.
- Handle malformed negative indexes, BOM-only lines in tail and extreme numeric summaries.

## [0.1.0] - 2026-10-24

### Added
- Eleven streaming CLI commands for JSONL and gzip input.
- Typed Python API for lazy reading, expressions, statistics and schema inference.
- MIT license, contributor templates and Linux/Windows CI with a coverage gate.

[Unreleased]: https://github.com/YusufBeratZengin/jsonl-tools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/YusufBeratZengin/jsonl-tools/releases/tag/v0.1.0
