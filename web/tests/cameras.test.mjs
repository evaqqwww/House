import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PerspectiveCamera, Vector3 } from 'three';
import { createCameraRig } from '../src/cameras.js';

// Only the DOM boundary is simulated; gestures run through the real OrbitControls.
class Surface extends EventTarget {
  capture = new EventTarget();
  style = {};
  clientWidth = 800;
  clientHeight = 600;
  captured = new Set();

  addEventListener(type, listener, options) {
    if (options === true || options?.capture) this.capture.addEventListener(type, listener);
    else super.addEventListener(type, listener, options);
  }

  removeEventListener(type, listener, options) {
    if (options === true || options?.capture) this.capture.removeEventListener(type, listener);
    else super.removeEventListener(type, listener, options);
  }

  dispatchEvent(event) {
    this.capture.dispatchEvent(event);
    return super.dispatchEvent(event);
  }

  getRootNode() { return this.ownerDocument; }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  releasePointerCapture(id) {
    if (!this.captured.delete(id)) return;
    this.dispatchEvent(Object.assign(new Event('lostpointercapture'), { pointerId: id }));
  }
}

function setup() {
  const document = new Surface();
  document.defaultView = new EventTarget();
  document.hidden = false;
  const root = new Surface();
  const canvas = new Surface();
  canvas.ownerDocument = document;
  const cameras = ['CAM_Overview', 'CAM_Closeup'].map((name, i) => {
    const camera = new PerspectiveCamera(30, 4 / 3, 0.1, 200);
    camera.position.set(i ? 12 : 20, 10, 20);
    camera.lookAt(0, 1.5, 0);
    camera.userData = { name, lens_mm: 50, sensor_mm: 36, target: [0, 1.5, 0], fstop: 4 };
    return camera;
  });
  const rig = createCameraRig({
    gltf: { cameras },
    manifest: { cameras: Object.fromEntries(cameras.map((camera) => [camera.userData.name, {
      lookAt: [0, 1.5, 0], corridor: { hide: [] },
    }])) },
    forest: { setCorridor() {}, clearOccluders() {}, updateOccluders() {} },
    domElement: canvas,
    interactionRoot: root,
  });
  rig.setMode('overview', { instant: true });

  function pointer(type, id, x, y = 200, options = {}) {
    const { target = canvas, ...properties } = options;
    const event = Object.assign(new Event(type, { cancelable: true }), {
      pointerId: id, pointerType: 'touch', button: 0, buttons: 1,
      pageX: x, pageY: y, clientX: x, clientY: y, ...properties,
    });
    if (target === canvas && type === 'pointerdown') root.dispatchEvent(event);
    if (type === 'lostpointercapture') canvas.captured.delete(id);
    target.dispatchEvent(event);
    if (target !== document) document.dispatchEvent(event);
    if (type === 'pointerup' || type === 'pointercancel') canvas.releasePointerCapture(id);
  }

  function dragRotates(id = 3, options) {
    pointer('pointerdown', id, 300, 200, options);
    const before = rig.camera.quaternion.clone();
    pointer('pointermove', id, 400, 200, options);
    assert.ok(before.angleTo(rig.camera.quaternion) > 0.001, 'a fresh single-pointer drag must rotate the view');
    pointer('pointerup', id, 400, 200, options);
  }

  function wheel(deltaY) {
    const event = Object.assign(new Event('wheel', { cancelable: true }), { deltaY, deltaMode: 0 });
    root.dispatchEvent(event);
    canvas.dispatchEvent(event);
  }

  return { rig, canvas, document, pointer, dragRotates, wheel };
}

for (const lifted of [1, 2]) {
  test(`pinch returns to single-finger rotation when finger ${lifted} lifts, including normal capture loss`, () => {
    const { rig, pointer, dragRotates } = setup();
    pointer('pointerdown', 1, 200);
    pointer('pointerdown', 2, 400, 200, { isPrimary: false });
    const before = rig.camera.position.distanceTo(new Vector3(0, 1.5, 0));
    pointer('pointermove', 2, 500);
    assert.ok(rig.camera.position.distanceTo(new Vector3(0, 1.5, 0)) < before, 'spreading two fingers must zoom in');
    pointer('pointerup', lifted, lifted === 1 ? 200 : 500);
    const remaining = lifted === 1 ? 2 : 1;
    const rotation = rig.camera.quaternion.clone();
    pointer('pointermove', remaining, remaining === 1 ? 250 : 550);
    assert.ok(rotation.angleTo(rig.camera.quaternion) > 0.001, 'the remaining finger must rotate');
    pointer('pointerup', remaining, 550);
    dragRotates();
  });
}

test('single-finger rotation recovers when pointer capture is lost without pointerup', () => {
  const { pointer, dragRotates } = setup();
  pointer('pointerdown', 1, 200);
  pointer('lostpointercapture', 1, 200);
  dragRotates();
});

test('a cancelled second finger outside the canvas cannot leave pinch mode stuck', () => {
  const { pointer, document, dragRotates } = setup();
  pointer('pointerdown', 1, 200);
  pointer('pointerdown', 2, 400);
  pointer('pointercancel', 2, 400, 200, { target: document });
  pointer('pointerup', 1, 200);
  dragRotates();
});

for (const interruption of ['blur', 'visibilitychange']) {
  test(`single-finger rotation recovers after ${interruption} interrupts a pinch`, () => {
    const { rig, canvas, pointer, document, dragRotates } = setup();
    pointer('pointerdown', 1, 200);
    pointer('pointerdown', 2, 400);
    const position = rig.camera.position.clone();
    const rotation = rig.camera.quaternion.clone();
    if (interruption === 'blur') document.defaultView.dispatchEvent(new Event('blur'));
    else {
      document.hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
      document.hidden = false;
    }
    assert.deepEqual(rig.camera.position, position, 'interrupting a gesture must not jump the camera');
    assert.deepEqual(rig.camera.quaternion.toArray(), rotation.toArray());
    assert.equal(canvas.captured.size, 0, 'interruptions must release outstanding pointer capture');
    dragRotates();
  });
}

test('switching presets clears an interrupted pinch before returning to free rotation', () => {
  const { rig, pointer, dragRotates } = setup();
  pointer('pointerdown', 1, 200);
  pointer('pointerdown', 2, 400);
  rig.setMode('closeup', { instant: true });
  rig.setMode('free');
  dragRotates();
});

test('a new primary touch starts cleanly even if the previous touch never ended', () => {
  const { pointer, dragRotates } = setup();
  pointer('pointerdown', 1, 200, 200, { isPrimary: true });
  dragRotates(3, { isPrimary: true });
});

test('a normally cancelled touch can be reused for rotation', () => {
  const { pointer, dragRotates } = setup();
  pointer('pointerdown', 1, 200);
  pointer('pointercancel', 1, 200);
  dragRotates(1, { isPrimary: true });
});

test('mouse rotation recovers when the mouse button was released outside the window', () => {
  const { pointer, dragRotates } = setup();
  pointer('pointerdown', 1, 200, 200, { pointerType: 'mouse', button: 1, buttons: 4 });
  pointer('pointermove', 1, 200, 200, { pointerType: 'mouse', buttons: 0 });
  dragRotates(1, { pointerType: 'mouse' });
});

test('mouse rotation and wheel zoom still work after an interrupted zoom and a preset transition', () => {
  const { rig, pointer, document, dragRotates, wheel } = setup();
  pointer('pointerdown', 1, 200, 200, { pointerType: 'mouse', button: 1, buttons: 4 });
  document.defaultView.dispatchEvent(new Event('blur'));
  rig.setMode('overview', { instant: true });
  const zoomBefore = rig.camera.position.distanceTo(new Vector3(0, 1.5, 0));
  wheel(-100);
  assert.equal(rig.mode, 'free');
  assert.ok(rig.camera.position.distanceTo(new Vector3(0, 1.5, 0)) < zoomBefore, 'wheel must still zoom');
  dragRotates(1, { pointerType: 'mouse' });
});
