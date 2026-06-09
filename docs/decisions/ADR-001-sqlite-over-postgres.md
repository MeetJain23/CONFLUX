# ADR-001: SQLite over Postgres for Phase 1

**Status:** Accepted (Phase 1)
**Date:** initial commit

## Context
CONFLUX needs a persistent store for metadata graph and time-series data.
Phase 1 universe is ~500 stocks × ~400 days × multiple vectors ≈ low six-figure rows.
Single-machine, single-user. No concurrent writes from multiple processes.

## Decision
Use SQLite via SQLAlchemy ORM.

## Rationale
- Zero-config: no service to run, no network, no auth
- File-based: easy to back up, share, version
- Sufficient for Phase 1 scale (well under a million rows)
- SQLAlchemy lets us migrate to Postgres later without rewriting models

## Consequences
- No concurrent writers — fine, we have one orchestrator
- No row-level locks for fine-grained updates — fine for batch jobs
- Migration to Postgres deferred to when (a) we add multi-user dashboard,
  or (b) we exceed ~10M rows, whichever comes first
