import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BoxGeometry, Group, InstancedMesh, Matrix4, Mesh, MeshBasicMaterial, PerspectiveCamera, Scene } from 'three';
import { createSheepInteraction } from '../src/sheep-interaction.js';

class Surface extends EventTarget {
  contains(target) { return target === this; }
  getBoundingClientRect() { return { left: 10, top: 20, width: 200, height: 200 }; }
}

function setup({ blocked = false, instancedWall = false } = {}) {
  const document = new Surface();
  document.defaultView = new Surface();
  const canvas = new Surface();
  canvas.ownerDocument = document;
  const wake = new Surface();
  const scene = new Scene();
  const sheep = new Group();
  sheep.add(new Mesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial()));
  scene.add(sheep);
  const contact = new Mesh(new BoxGeometry(5, 0.02, 5), new MeshBasicMaterial({ transparent: true }));
  contact.position.y = -0.5;
  sheep.add(contact);
  const wall = instancedWall
    ? new InstancedMesh(new BoxGeometry(2, 2, 0.2), new MeshBasicMaterial(), 1)
    : new Mesh(new BoxGeometry(2, 2, 0.2), new MeshBasicMaterial());
  wall.position.z = 2;
  wall.visible = blocked;
  scene.add(wall);
  const camera = new PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.z = 5;
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const taps = [];
  let now = 0;
  const interaction = createSheepInteraction({ domElement: canvas, wakeElement: wake,
    camera, root: sheep, scene, ignoredMeshes: [contact], onTap: () => taps.push(now), now: () => now });
  function pointer(type, { surface = canvas, ...props } = {}) {
    const event = Object.assign(new Event(type), { pointerId: 1, isPrimary: true,
      pointerType: 'touch', button: 0, clientX: 110, clientY: 120, ...props });
    // The browser boundary is simulated; 3D picking and the pointer state machine are real.
    Object.defineProperty(event, 'target', { value: surface });
    document.dispatchEvent(event);
  }
  return { document, canvas, wake, scene, sheep, wall, interaction, taps, pointer,
    advance(ms) { now += ms; } };
}

test('tapping the visible sheep, including the idle wake surface, triggers once', () => {
  const s = setup();
  s.pointer('pointerdown'); s.advance(80); s.pointer('pointerup');
  assert.equal(s.taps.length, 1);
  s.pointer('pointerdown', { surface: s.wake }); s.pointer('pointerup', { surface: s.wake });
  assert.equal(s.taps.length, 2);
  s.interaction.dispose();
});

test('screen picking misses empty ground and does not click through a visible house or tree', () => {
  const s = setup({ blocked: true });
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
  s.wall.visible = false;
  s.pointer('pointerdown', { clientX: 15, clientY: 25 }); s.pointer('pointerup', { clientX: 15, clientY: 25 });
  assert.equal(s.taps.length, 0);
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 1);
});

test('dragging, even back to the initial point, cannot trigger a pet tap', () => {
  const s = setup();
  s.pointer('pointerdown'); s.pointer('pointermove', { clientX: 135 });
  s.pointer('pointermove'); s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
});

test('instanced scenery still blocks taps after its visible instances move', () => {
  const s = setup({ blocked: true, instancedWall: true });
  s.wall.setMatrixAt(0, new Matrix4().makeTranslation(10, 0, 0));
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 1);
  s.wall.setMatrixAt(0, new Matrix4().identity());
  s.wall.instanceMatrix.needsUpdate = true;
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 1);
});

test('a second finger cancels the whole gesture and the next single tap recovers', () => {
  const s = setup();
  s.pointer('pointerdown');
  s.pointer('pointerdown', { pointerId: 2, isPrimary: false });
  s.pointer('pointerup', { pointerId: 2, isPrimary: false }); s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 1);
});

test('long press, cancellation, lost capture and UI controls are ignored', () => {
  const s = setup();
  s.pointer('pointerdown'); s.advance(700); s.pointer('pointerup');
  for (const type of ['pointercancel', 'lostpointercapture']) {
    s.pointer('pointerdown'); s.pointer(type); s.pointer('pointerup');
  }
  const control = new Surface();
  s.pointer('pointerdown', { surface: control }); s.pointer('pointerup', { surface: control });
  s.pointer('pointerdown', { button: 2 }); s.pointer('pointerup', { button: 2 });
  assert.equal(s.taps.length, 0);
});

test('hidden or detached sheep are not pickable and disposal removes gesture handlers', () => {
  const s = setup();
  s.sheep.visible = false;
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
  s.sheep.visible = true;
  s.pointer('pointerdown');
  s.document.hidden = true; s.document.dispatchEvent(new Event('visibilitychange'));
  s.document.hidden = false; s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
  s.interaction.dispose();
  s.pointer('pointerdown'); s.pointer('pointerup');
  assert.equal(s.taps.length, 0);
});
