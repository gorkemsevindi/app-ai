/** Autosave / sync controller shared by every editor view. It owns no UI: it pushes the session outbox to a
 * transport, keeps the idempotency key stable across retries (so a lost response never applies twice), rebases on
 * a revision conflict and persists an offline draft so a closed tab or dropped network loses nothing. */

import type { Command } from '../../../packages/shared/src/engine.ts';
import type { Project } from '../../../packages/shared/src/schema.ts';
import { EditorSession } from '../../../packages/shared/src/session.ts';

export interface CommandsResult { revision: number; project: Project }
export class TransportError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}
export interface Transport {
  commands(base_revision: number, commands: Command[], key: string): Promise<CommandsResult>;
  get(): Promise<CommandsResult>;
}
export interface DraftStore {
  load(id: string): { revision: number; outbox: Command[] } | null;
  save(id: string, draft: { revision: number; outbox: Command[] }): void;
  clear(id: string): void;
}
export type SyncState = 'saved' | 'pending' | 'saving' | 'offline' | 'conflict' | 'error';

export class SyncController {
  session: EditorSession;
  state: SyncState = 'saved';
  lastError: string | null = null;
  dropped: Command[] = [];
  private inflight: Promise<void> | null = null;
  private transport: Transport;
  private drafts: DraftStore | null;
  private onChange: () => void;

  constructor(session: EditorSession, transport: Transport, drafts: DraftStore | null, onChange: () => void = () => {}) {
    this.session = session;
    this.transport = transport;
    this.drafts = drafts;
    this.onChange = onChange;
  }

  /** Re-apply an offline draft left by a previous tab/session on top of the server document. */
  restoreDraft(): number {
    const d = this.drafts?.load(this.session.project.project_id);
    if (!d || !d.outbox.length) return 0;
    this.session.outbox = [...d.outbox];
    const { dropped } = this.session.rebase(this.session.project, this.session.revision);
    this.dropped = dropped;
    this.state = 'pending';
    this.persist();
    this.onChange();
    return this.session.outbox.length;
  }

  /** The user has seen the list of edits that could not be applied after a conflict. */
  dismissConflict(): void {
    this.dropped = [];
    if (this.state === 'conflict') this.state = this.session.outbox.length ? 'pending' : 'saved';
    this.onChange();
  }

  exec(...cmds: Command[]): void {
    this.session.exec(...cmds);
    this.changed();
  }
  undo(): void { this.session.undo(); this.changed(); }
  redo(): void { this.session.redo(); this.changed(); }

  private changed(): void {
    this.state = 'pending';
    this.persist();
    this.onChange();
  }

  private persist(): void {
    if (!this.drafts) return;
    const id = this.session.project.project_id;
    if (this.session.outbox.length) this.drafts.save(id, { revision: this.session.revision, outbox: this.session.outbox });
    else this.drafts.clear(id);
  }

  /** Send everything pending; resolves when the outbox is empty or a non-retryable state is reached. */
  flush(): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = this.loop().finally(() => { this.inflight = null; });
    return this.inflight;
  }

  private async loop(): Promise<void> {
    for (let guard = 0; guard < 20; guard++) {
      const batch = this.session.nextBatch();
      if (!batch) {
        this.state = this.dropped.length ? 'conflict' : 'saved';
        this.persist();
        this.onChange();
        return;
      }
      this.state = 'saving';
      this.onChange();
      try {
        const r = await this.transport.commands(batch.base_revision, batch.commands, batch.idempotency_key);
        this.session.acknowledge(batch, r.revision, r.project);
        this.lastError = null;
        this.persist();
      } catch (e) {
        if (e instanceof TransportError && e.code === 'revision_conflict') {
          const cur = e.details.project ? { revision: e.details.revision as number, project: e.details.project as Project }
                                        : await this.transport.get();
          const { dropped } = this.session.rebase(cur.project, cur.revision);
          this.dropped.push(...dropped);
          this.state = dropped.length ? 'conflict' : 'pending';
          this.persist();
          this.onChange();
          continue;
        }
        if (e instanceof TransportError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) {
          // the server refused the batch (validation/moderation): take the server state, keep a record
          this.lastError = e.message;
          const cur = await this.transport.get().catch(() => null);
          if (cur) {
            this.dropped.push(...this.session.outbox);
            this.session.outbox = [];
            this.session.rebase(cur.project, cur.revision);
          }
          this.state = 'error';
          this.persist();
          this.onChange();
          return;
        }
        // network / 5xx: keep the batch (same idempotency key) for a later retry
        this.lastError = e instanceof Error ? e.message : String(e);
        this.state = 'offline';
        this.persist();
        this.onChange();
        return;
      }
    }
  }
}
