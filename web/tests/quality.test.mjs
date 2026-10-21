import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createResolutionController } from '../src/quality.js';

function sampleFor(controller, { start, duration, frameMs }) {
  for (let now = start; now < start + duration; now += frameMs) controller.sample(frameMs, now);
}

test('fixed quality keeps its full resolution during sustained slow mobile frames', () => {
  const changes = [];
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => false, onChange: (ratio) => changes.push(ratio) });
  controller.setRange(0.75, 2, true);
  const start = performance.now() + 2000;
  sampleFor(controller, { start, duration: 60000, frameMs: 80 });

  assert.equal(controller.state.scale, 2);
  assert.deepEqual(changes, [2], 'slow frames must not resize a canvas with fixed quality');
});

test('automatic quality can still lower resolution under sustained load', () => {
  const changes = [];
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => true, onChange: (ratio) => changes.push(ratio) });
  controller.setRange(0.75, 2, true);
  const start = performance.now() + 2000;
  sampleFor(controller, { start, duration: 10000, frameMs: 80 });

  assert.ok(controller.state.scale < 2);
  assert.ok(controller.state.scale >= 0.75);
  assert.ok(changes.length > 1);
});

test('switching from automatic to fixed quality restores and retains full resolution', () => {
  let automatic = true;
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => automatic, onChange() {} });
  controller.setRange(0.75, 2, true);
  const start = performance.now() + 2000;
  sampleFor(controller, { start, duration: 10000, frameMs: 80 });
  assert.ok(controller.state.scale < 2);

  automatic = false;
  controller.setRange(0.75, 2, true);
  sampleFor(controller, { start: start + 10000, duration: 30000, frameMs: 80 });
  assert.equal(controller.state.scale, 2);
});

test('fixed quality still accepts resolution changes required by a viewport resize', () => {
  const changes = [];
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => false, onChange: (ratio) => changes.push(ratio) });
  controller.setRange(0.75, 2, true);
  controller.setRange(0.75, 1.5);
  const start = performance.now() + 2000;
  sampleFor(controller, { start, duration: 10000, frameMs: 80 });

  assert.equal(controller.state.scale, 1.5);
  assert.deepEqual(changes, [2, 1.5]);
});

test('fixed quality returns to the highest available resolution when the viewport budget increases', () => {
  const changes = [];
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => false, onChange: (ratio) => changes.push(ratio) });
  controller.setRange(0.6, 1.5, true);
  controller.setRange(0.6, 1.3);
  controller.setRange(0.6, 1.5);

  assert.equal(controller.state.scale, 1.5);
  assert.deepEqual(changes, [1.5, 1.3, 1.5]);
});

test('automatic quality retains its current resolution when the viewport budget increases', () => {
  const controller = createResolutionController({ targetFps: 30, canAdapt: () => true, onChange() {} });
  controller.setRange(0.6, 1.5, true);
  controller.setRange(0.6, 1.3);
  controller.setRange(0.6, 1.5);

  assert.equal(controller.state.scale, 1.3);
});
