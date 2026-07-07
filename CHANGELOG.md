# Changelog

All notable changes to **MeshForge Maps** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Created 2026-07-07 during a fleet-wide version audit — this repo had no
> CHANGELOG and its release history lived only in git and the README roadmap.
> Entries below are reconstructed from `git log`; **git remains the
> authoritative record** for finer detail. `src/__init__.py` `__version__` is the
> version SSOT (kept in sync with `pyproject.toml` and the README badge).
>
> Note the process gap this audit exposed: substantial work (security hardening,
> the mesh_client collector, the QA audit) shipped **after** the 2026-04-14
> `0.7.4-beta` bump without a further version bump. That backlog is captured
> under *Unreleased* below and should trigger the next bump.

## [Unreleased]

Work landed at `0.7.4-beta` after the 2026-04-14 bump, not yet version-bumped:

### Fixed
- QA maps audit hardening (#90, 2026-07-05): MQTT broker OOM cap, numeric node-id canonicalization, `/api/status` redaction.
- TUI hardened against null/malformed API data across every tab (#89).
- `mesh_client` surfaced in source enumerations; ISO-8601 `last_heard` coerced to epoch (#78).
- Security: rate-limiter trusts proxy IP, exempts health/static, caps buckets (#85).
- DB: clear per-node trajectory caches on prune to stop premature eviction (#84); default `node_history_retention_days` 3→1 (#81).

### Added
- `MeshClientCollector` — file-based GeoJSON ingest from meshing_around (#78).

## [0.7.4-beta] - 2026-04-14

### Added
- Viewport-driven counts; lite mode includes region-scoped MeshCore + AREDN worldmap.

### Changed
- Dropped redundant scope suffix; RSS relief.

## [0.7.2-beta] - 2026-04-14

### Changed
- Version sync across `src/__init__.py` + `pyproject.toml`.

## [0.7.1-beta] - 2026-04-14

### Changed
- Patch bump ahead of the 0.7.2–0.7.4 same-day series.

## [0.7.0-beta] - 2026-02-11

### Added
- Comprehensive README reflecting the v0.7.0-beta state and roadmap (multi-source map, node health scoring, alert delivery).

## [0.5.0-beta] - 2026-02-09

### Added
- Phase 4 complete — plugin lifecycle + 76 new tests.

## [0.3.0-beta] - 2026-02-08

### Added
- Supported hardware (Raspberry Pi) and OS section; early beta of the unified map.

## [0.1.0] - 2026-02-07

### Added
- Initial commit — MeshForge Maps extension scaffold.
