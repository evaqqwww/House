import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTimeGesture } from '../src/time-gesture.js';

function controls(t, initialHour = 9) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const state = { hour: initialHour, taps: [], opened: 0, dragging: false };
  const gesture = createTimeGesture({
    readHour: () => state.hour,
    onHour: (hour) => { state.hour = hour; },
    onOpen: () => { state.opened++; },
    onTap: (choice) => { state.taps.push(choice); },
    onDragging: (dragging) => { state.dragging = dragging; },
  });
  return { gesture, state };
}

const point = (x, pointerId = 1) => ({ pointerId, clientX: x, clientY: 100 });

test('a short press selects its preset once without changing the hour by dragging', (t) => {
  const { gesture, state } = controls(t);
  gesture.start(point(200), 'night');
  gesture.end(point(200));
  t.mock.timers.tick(1000);
  assert.deepEqual(state, { hour: 9, taps: ['night'], opened: 0, dragging: false });
});

test('holding opens at the current animated hour; right advances and left rewinds without selecting the preset', (t) => {
  const { gesture, state } = controls(t);
  gesture.start(point(200), 'day');
  state.hour = 10;
  t.mock.timers.tick(350);
  assert.equal(state.opened, 1);
  assert.equal(state.hour, 10);
  assert.equal(state.dragging, true);
  gesture.move(point(218));
  assert.equal(state.hour, 11);
  gesture.move(point(182));
  assert.equal(state.hour, 9);
  gesture.end(point(182));
  assert.equal(state.dragging, false);
  assert.deepEqual(state.taps, []);
});

test('a swipe opens immediately and clamps time at both ends of the day', (t) => {
  const { gesture, state } = controls(t);
  gesture.start(point(200), 'morning');
  gesture.move(point(218));
  assert.equal(state.hour, 10);
  assert.equal(state.opened, 1);
  gesture.move(point(800));
  assert.equal(state.hour, 24);
  gesture.move(point(-400));
  assert.equal(state.hour, 0);
  gesture.end(point(-400));
  assert.deepEqual(state.taps, []);
});

test('cancellation clears a pending hold and never selects a preset', (t) => {
  const { gesture, state } = controls(t);
  gesture.start(point(200), 'dusk');
  gesture.cancel();
  t.mock.timers.tick(1000);
  gesture.end(point(200));
  assert.deepEqual(state, { hour: 9, taps: [], opened: 0, dragging: false });
});

test('a second finger cannot replace or finish the active gesture', (t) => {
  const { gesture, state } = controls(t);
  gesture.start(point(200), 'morning');
  gesture.start(point(100, 2), 'night');
  gesture.move(point(10, 2));
  gesture.end(point(10, 2));
  gesture.end(point(200));
  assert.deepEqual(state.taps, ['morning']);
  assert.equal(state.opened, 0);
});
