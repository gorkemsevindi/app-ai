# Security, consent & safety

## Threat model (summary)
| Threat | Control |
|---|---|
| Non-consensual deepfakes (incl. sexual) | likeness consent per identity profile; rights + consent attestation per uploaded video; only the requester's own profiles assignable; input text rules; template safety tags; output moderation scores (worker) + policy (API); visible watermark (forced for real-footage replacement) + AI provenance metadata; report → moderation queue → remove/ban |
| Minors | age gate (18+) at signup; analysis rejects videos with `minor_suspected`; text rules; output `minor_presence` score blocks; report reason `minor_safety` prioritized |
| Celebrities / impersonation / fraud | templates never contain real people; Terms prohibit public-figure use; impersonation keywords blocked; report + admin removal + ban. Generating real celebrities is **not** offered |
| IDOR | every user resource looked up with owner check; 404 for foreign ids (no oracle); tests in `test_generation.py`, `test_multiperson_api.py` |
| Upload abuse | presigned POST with server-chosen key, MIME allowlist, `content-length-range`, magic-byte sniffing after upload, size limits; workers never execute user media, only decode with ffmpeg/OpenCV in a sandboxed container with no credentials |
| Credential stuffing / abuse | per-IP + per-email login rate limits, per-user generation limits, max active jobs, global queue hard limit; WAF/bot rules at the edge |
| Token theft | 15 min access JWT, rotating refresh tokens stored hashed, reuse detection revokes the family; tokens in iOS Keychain / Android Keystore |
| Worker compromise | workers hold a revocable worker token + per-job short-lived signed URLs only; no DB access |
| Financial tampering | append-only ledger enforced by DB trigger, idempotency keys, row locks, reconciliation (`reconcile_user`) |
| Admin abuse | role scopes (admin/support), audit log on every mutation; MFA + SSO at the identity provider (prod); support never gets identity media URLs |

## Retention (defaults, configurable)
| Data | Retention |
|---|---|
| Identity photos / source videos | until the user deletes them or the account; hard-deleted from storage immediately on deletion |
| Generated videos | 90 days hot, then deleted unless saved by the user (V2 lifecycle rule) |
| Tracks/thumbnails of source videos | deleted with the source video |
| Ledger, purchases | 10 years (tax), pseudonymized after account deletion |
| Backups | 35 days PITR; deleted data ages out of backups within that window (documented to users) |

## Account deletion
In-app: Profile → Delete account → `DELETE /account`: sessions revoked, in-flight jobs cancelled, all objects
under `users/{id}/` deleted, PII scrubbed, rows soft-deleted, audit entry written. A web deletion page is
required for Google Play (STORE_RELEASE.md).

## Reporting a vulnerability
security@<company-domain> (to be set up before launch).
