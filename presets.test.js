// Run with: node --test
const test = require('node:test');
const assert = require('node:assert');
const { PRESETS, getPreset, safeRadius, fitLogo } = require('./presets.js');

test('every preset is a square size with padding under half', () => {
  for (const p of PRESETS) {
    assert.ok(Number.isInteger(p.size) && p.size > 0, p.id);
    assert.ok(p.padding >= 0 && p.padding < 0.5, p.id);
    assert.ok(['image/png', 'image/jpeg'].includes(p.format), p.id);
  }
});

test('expected sizes per target', () => {
  assert.strictEqual(getPreset('ios').size, 1024);
  assert.strictEqual(getPreset('android').size, 720);
  assert.strictEqual(getPreset('google').size, 720);
  assert.strictEqual(getPreset('outlook').size, 648);
  assert.strictEqual(getPreset('nope'), null);
});

test('fitted logo corners stay inside the safe circle', () => {
  for (const p of PRESETS) {
    for (const [w, h] of [[100, 100], [1000, 200], [37, 400], [1, 1]]) {
      const box = fitLogo(w, h, p);
      const c = p.size / 2;
      const corner = Math.hypot(box.x - c, box.y - c);
      assert.ok(corner <= safeRadius(p) + 1e-9, `${p.id} ${w}x${h}`);
      // Largest safe fit: corners touch the safe circle.
      assert.ok(Math.abs(corner - safeRadius(p)) < 1e-9);
      // Centred.
      assert.ok(Math.abs(box.x + box.width / 2 - c) < 1e-9);
      assert.ok(Math.abs(box.y + box.height / 2 - c) < 1e-9);
    }
  }
});

test('zoom scales the fitted size', () => {
  const p = getPreset('ios');
  const a = fitLogo(200, 100, p, 1);
  const b = fitLogo(200, 100, p, 0.5);
  assert.ok(Math.abs(b.width - a.width / 2) < 1e-9);
});

test('rejects an image with no size', () => {
  assert.throws(() => fitLogo(0, 10, getPreset('ios')));
});
