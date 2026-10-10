// Regenerates fixtures/engine.json from the TS engine (the reference). Python must reproduce every entry.
import { writeFileSync } from 'node:fs';
import { apply, EngineError } from '../src/engine.ts';
import { renderManifest } from '../src/manifest.ts';
import { TEMPLATES } from '../src/templates.ts';
import { PRESETS } from '../src/schema.ts';
import { SCENARIOS } from './scenarios.ts';

const out = SCENARIOS.map((s) => {
  let p = s.initial;
  try {
    for (const c of s.commands) p = apply(p, c).project;
  } catch (e) {
    if (!(e instanceof EngineError)) throw e;
    return { ...s, error: e.code };
  }
  return { ...s, expected: p, manifest: renderManifest(p) };
});
writeFileSync(new URL('../fixtures/engine.json', import.meta.url), JSON.stringify(out, null, 1) + '\n');
console.log(`wrote ${out.length} fixtures`);
// The API ships the same templates/presets (it can't import TS); a Python test checks they stay identical.
writeFileSync(new URL('../../../services/api/app/data/editor_templates.json', import.meta.url),
  JSON.stringify({ presets: PRESETS, templates: TEMPLATES }, null, 1) + '\n');
