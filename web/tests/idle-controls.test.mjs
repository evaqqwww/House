import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createIdleControls } from '../src/idle-controls.js';

function controls(t, options = {}) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const state = { hides: 0, shows: 0 };
  const idle = createIdleControls({
    onHide: () => { state.hides++; },
    onShow: () => { state.shows++; },
    ...options,
  });
  return { idle, state };
}

test('controls hide after five seconds by default and remain hidden without repeated callbacks', (t) => {
  const { idle, state } = controls(t);
  t.mock.timers.tick(4999);
  assert.equal(idle.hidden, false);
  assert.equal(state.hides, 0);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.equal(state.hides, 1);
  t.mock.timers.tick(15000);
  assert.deepEqual(state, { hides: 1, shows: 0 });
});

test('visible activity restarts the full idle delay', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  t.mock.timers.tick(600);
  idle.activity();
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  assert.equal(state.hides, 0);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.equal(state.hides, 1);
});

test('a held input prevents hiding until a full idle delay after release', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  t.mock.timers.tick(900);
  idle.hold('pointer:1');
  t.mock.timers.tick(5000);
  idle.activity();
  t.mock.timers.tick(5000);
  assert.equal(idle.hidden, false);
  assert.equal(state.hides, 0);
  idle.release('pointer:1');
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
});

test('all distinct held inputs must release, while repeated holds use the same token', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  idle.hold('pointer:1');
  idle.hold('pointer:1');
  idle.hold('pointer:2');
  idle.hold('key:ArrowLeft');
  idle.release('pointer:1');
  idle.release('unknown');
  t.mock.timers.tick(5000);
  idle.release('pointer:2');
  t.mock.timers.tick(5000);
  assert.equal(idle.hidden, false);
  assert.equal(state.hides, 0);
  idle.release('key:ArrowLeft');
  t.mock.timers.tick(1000);
  assert.equal(idle.hidden, true);
});

test('a blocked hide retries after another complete idle delay', (t) => {
  let allowed = false;
  const { idle, state } = controls(t, { delay: 1000, canHide: () => allowed });
  t.mock.timers.tick(1000);
  assert.equal(idle.hidden, false);
  assert.equal(state.hides, 0);
  allowed = true;
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.equal(state.hides, 1);
});

test('hidden activity does not wake controls; explicit wake starts a fresh idle delay', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  t.mock.timers.tick(1000);
  assert.equal(idle.hidden, true);
  idle.activity();
  t.mock.timers.tick(5000);
  assert.equal(idle.hidden, true);
  assert.deepEqual(state, { hides: 1, shows: 0 });
  assert.equal(idle.wake(), true);
  assert.equal(idle.hidden, false);
  assert.deepEqual(state, { hides: 1, shows: 1 });
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.deepEqual(state, { hides: 2, shows: 1 });
});

test('waking visible controls only restarts their idle delay', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  t.mock.timers.tick(900);
  assert.equal(idle.wake(), false);
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  assert.deepEqual(state, { hides: 0, shows: 0 });
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.deepEqual(state, { hides: 1, shows: 0 });
});

test('held inputs survive waking hidden controls', (t) => {
  const { idle, state } = controls(t, { delay: 1000 });
  t.mock.timers.tick(1000);
  idle.hold('pointer:1');
  assert.equal(idle.wake(), true);
  t.mock.timers.tick(5000);
  assert.equal(idle.hidden, false);
  assert.deepEqual(state, { hides: 1, shows: 1 });
  idle.release('pointer:1');
  t.mock.timers.tick(999);
  assert.equal(idle.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(idle.hidden, true);
  assert.deepEqual(state, { hides: 2, shows: 1 });
});
