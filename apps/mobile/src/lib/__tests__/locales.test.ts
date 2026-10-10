import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

// Every locale must have exactly the same keys as English and keep the same {{placeholders}}, so no screen
// shows a raw key or loses a value in another language.
const dir = new URL('../../locales/', import.meta.url);
const load = (f: string) => JSON.parse(readFileSync(new URL(f, dir), 'utf8'));

function flatten(o: Record<string, unknown>, prefix = ''): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(o)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') {
      for (const [kk, vv] of flatten(v as Record<string, unknown>, key)) out.set(kk, vv);
    } else {
      out.set(key, String(v));
    }
  }
  return out;
}
const vars = (s: string) => [...s.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort().join(',');

test('all locales match the English keys and placeholders', () => {
  const en = flatten(load('en.json'));
  const files = readdirSync(dir).filter((f) => f.endsWith('.json') && f !== 'en.json');
  assert.ok(files.length >= 1);
  for (const f of files) {
    const other = flatten(load(f));
    const missing = [...en.keys()].filter((k) => !other.has(k));
    const extra = [...other.keys()].filter((k) => !en.has(k));
    assert.deepEqual({ f, missing, extra }, { f, missing: [], extra: [] });
    for (const [k, v] of en) assert.equal(vars(other.get(k) ?? ''), vars(v), `${f}:${k} placeholders`);
  }
});
