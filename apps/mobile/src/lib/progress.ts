/** Pure helpers for the generation progress screen (unit-tested, no React Native imports). */

export type JobLike = {
  status: string;
  progress: number;
  queue_position: number | null;
  est_seconds_remaining: number | null;
};

export const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export function stageKey(status: string): string {
  switch (status) {
    case 'queued':
      return 'job.stage.queued';
    case 'preprocessing':
      return 'job.stage.preparing';
    case 'generating':
      return 'job.stage.generating';
    case 'postprocessing':
    case 'moderation':
      return 'job.stage.finishing';
    default:
      return `job.stage.${status}`;
  }
}

/** Poll faster while work is moving, back off while queued, stop when terminal. */
export function nextPollMs(job: JobLike, attempt: number): number | null {
  if (TERMINAL.has(job.status)) return null;
  if (job.status === 'queued') return Math.min(2000 + attempt * 500, 8000);
  return 1500;
}

/** Overall bar value: queue counts as the first 5%, so the user always sees motion. */
export function displayProgress(job: JobLike): number {
  if (job.status === 'completed') return 1;
  if (job.status === 'queued') return 0.03;
  return Math.max(0.05, Math.min(0.98, job.progress));
}

export function formatEta(seconds: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${Math.max(5, Math.round(seconds / 5) * 5)}s`;
  return `${Math.ceil(seconds / 60)} min`;
}
