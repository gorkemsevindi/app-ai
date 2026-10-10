import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isShareToken, tokenFromUrl } from '../shareToken.ts';

const TOK = 'AbC_dEf-123456.ZyX987_-abcdEF';

test('accepts well-formed tokens only', () => {
  assert.equal(isShareToken(TOK), true);
  assert.equal(isShareToken('nodot'), false);
  assert.equal(isShareToken('a.b'), false);
  assert.equal(isShareToken('<script>.alert(1)xx'), false);
  assert.equal(isShareToken(null), false);
});

test('extracts tokens from universal links, scheme links and install referrers', () => {
  assert.equal(tokenFromUrl(`https://example.app/t/${TOK}`), TOK);
  assert.equal(tokenFromUrl(`aivideo://t/${TOK}?x=1`), TOK);
  assert.equal(tokenFromUrl(`utm_source=x&referrer=${encodeURIComponent(TOK)}`), TOK);
  assert.equal(tokenFromUrl('https://example.app/templates/123'), null);
  assert.equal(tokenFromUrl('aivideo://t/forged'), null);
});
