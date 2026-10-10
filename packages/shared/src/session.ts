/** Client editing session (web + mobile share it): optimistic local apply, undo/redo stacks, an outbox of
 * pending commands for autosave / offline, and rebase on the server's authoritative revision (V8 §6). */

import type { Command } from './engine.ts';
import { apply, EngineError } from './engine.ts';
import type { Project } from './schema.ts';

export interface PendingBatch { idempotency_key: string; base_revision: number; commands: Command[] }
interface Step { forward: Command[]; inverse: Command[] }

function run(p: Project, cmds: Command[]): { project: Project; inverse: Command[] } {
  const inverse: Command[] = [];
  for (const c of cmds) {
    const r = apply(p, c);
    p = r.project;
    inverse.unshift(...r.inverse);
  }
  return { project: p, inverse };
}

export class EditorSession {
  project: Project;
  revision: number;
  outbox: Command[] = [];
  private undoStack: Step[] = [];
  private redoStack: Step[] = [];
  private seq = 0;

  private keyPrefix: string;

  constructor(project: Project, revision: number, keyPrefix = 'k') {
    this.project = project;
    this.revision = revision;
    this.keyPrefix = keyPrefix;
  }

  /** Apply locally (throws EngineError on an invalid edit, leaving the state unchanged) and queue for the server. */
  exec(...cmds: Command[]): void {
    const r = run(this.project, cmds);
    this.project = r.project;
    this.undoStack.push({ forward: cmds, inverse: r.inverse });
    this.redoStack = [];
    this.outbox.push(...cmds);
  }

  canUndo(): boolean { return this.undoStack.length > 0; }
  canRedo(): boolean { return this.redoStack.length > 0; }

  undo(): void {
    const step = this.undoStack.pop();
    if (!step) return;
    this.project = run(this.project, step.inverse).project;
    this.redoStack.push(step);
    this.outbox.push(...step.inverse);
  }

  redo(): void {
    const step = this.redoStack.pop();
    if (!step) return;
    const r = run(this.project, step.forward);
    this.project = r.project;
    this.undoStack.push({ forward: step.forward, inverse: r.inverse });
    this.outbox.push(...step.forward);
  }

  /** Next batch to send (autosave). The key is stable until acknowledged, so retries are idempotent. */
  nextBatch(): PendingBatch | null {
    if (!this.outbox.length) return null;
    return { idempotency_key: `${this.keyPrefix}-${this.revision}-${this.seq}`, base_revision: this.revision,
             commands: [...this.outbox] };
  }

  acknowledge(batch: PendingBatch, revision: number, project: Project): void {
    this.outbox = this.outbox.slice(batch.commands.length);
    this.seq += 1;
    this.revision = revision;
    this.project = run(project, this.outbox).project;  // server is authoritative; replay what is still pending
  }

  /** After a 409 conflict: take the server state and replay pending commands; report those that no longer apply. */
  rebase(project: Project, revision: number): { dropped: Command[] } {
    const dropped: Command[] = [];
    const kept: Command[] = [];
    let p = project;
    for (const c of this.outbox) {
      try { p = apply(p, c).project; kept.push(c); } catch (e) { if (e instanceof EngineError) dropped.push(c); else throw e; }
    }
    this.project = p;
    this.revision = revision;
    this.outbox = kept;
    this.undoStack = [];
    this.redoStack = [];
    return { dropped };
  }

  /** Serializable snapshot for an offline draft queue (AsyncStorage / localStorage). */
  toDraft(): { project_id: string; revision: number; outbox: Command[] } {
    return { project_id: this.project.project_id, revision: this.revision, outbox: [...this.outbox] };
  }
}
