# ADR 0002 — Durable generation queue in PostgreSQL

Status: accepted (2026-10-05)

## Context
Spec §22/§25/§27: a paid generation must never be lost or double-charged; Redis loss must not lose
financial state; workers die (spot preemption, OOM); traffic can spike 10×.

## Decision
`generation_jobs` is the queue. Workers claim with `SELECT … FOR UPDATE SKIP LOCKED` (one transaction
also creates the `model_runs` row and sets a lease). Workers heartbeat every lease/4; an expired lease
is reaped on the next claim (or `POST /internal/worker/reap` from a cron) and the job is re-queued
(attempts+1) or failed + refunded. Creating a job and debiting credits happen in the same DB
transaction under a per-user row lock.

## Consequences
+ exactly-once charging, no dual-write between DB and broker, simple ops at MVP scale
+ fair weighted classes and cost ceilings are plain SQL
− claim throughput bounded by Postgres (~hundreds of claims/s is far above GPU capacity at 1M users);
  if needed, add a Redis/SQS *notification* channel later while keeping Postgres as the source of truth.
