/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { displayProgress, formatEta, nextPollMs, stageKey } from '../progress.ts';

const job = (status: string, progress = 0) => ({ status, progress, queue_position: 1, est_seconds_remaining: 90 });

test('polling stops on terminal states and backs off while queued', () => {
  assert.equal(nextPollMs(job('completed'), 0), null);
  assert.equal(nextPollMs(job('failed'), 3), null);
  assert.equal(nextPollMs(job('queued'), 0), 2000);
  assert.equal(nextPollMs(job('queued'), 100), 8000);
  assert.equal(nextPollMs(job('generating'), 9), 1500);
});

test('progress never looks frozen or complete too early', () => {
  assert.equal(displayProgress(job('queued')), 0.03);
  assert.equal(displayProgress(job('generating', 0)), 0.05);
  assert.equal(displayProgress(job('moderation', 1)), 0.98);
  assert.equal(displayProgress(job('completed', 0.5)), 1);
});

test('stage labels and eta', () => {
  assert.equal(stageKey('moderation'), 'job.stage.finishing');
  assert.equal(formatEta(42), '40s');
  assert.equal(formatEta(125), '3 min');
  assert.equal(formatEta(null), null);
});
