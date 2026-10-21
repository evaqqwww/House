import { Raycaster, Vector2 } from 'three';

const TAP_SLOP = 8;
const TAP_DURATION = 450;

function visible(object) {
  for (let current = object; current; current = current.parent) if (!current.visible) return false;
  return true;
}

/** Recognize a real tap on the visible animal, independently of orbit/zoom gestures. */
export function createSheepInteraction({ domElement, wakeElement, camera, root, scene,
  ignoredMeshes = [], onTap, now = () => performance.now() }) {
  const document = domElement.ownerDocument;
  const ray = new Raycaster();
  const pointer = new Vector2();
  const ignored = new Set(ignoredMeshes);
  const targets = [];
  root.traverse((object) => {
    if (object.isMesh && !ignored.has(object)) targets.push(object);
  });
  const targetSet = new Set(targets);
  const occluders = [];
  scene.traverse((object) => {
    if (!object.isMesh || targetSet.has(object) || ignored.has(object)) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    if (materials.every((material) => material.transparent || material.depthWrite === false)) return;
    occluders.push(object);
  });

  function pick(x, y) {
    if (!root.parent || !visible(root)) return false;
    const box = domElement.getBoundingClientRect();
    if (!box.width || !box.height || x < box.left || y < box.top || x > box.left + box.width || y > box.top + box.height) return false;
    scene.updateMatrixWorld(true);
    camera.updateMatrixWorld();
    pointer.set((x - box.left) / box.width * 2 - 1, 1 - (y - box.top) / box.height * 2);
    ray.far = Infinity;
    ray.setFromCamera(pointer, camera);
    const hit = ray.intersectObjects(targets.filter(visible), false)[0];
    if (!hit) return false;
    ray.far = Math.max(0, hit.distance - 0.01);
    // Ignore grass and transparent effects; solid scenery still blocks taps through it.
    const blockers = occluders.filter(visible);
    // Forest LOD changes instance matrices; refresh Three's cached bounds only on taps.
    for (const object of blockers) if (object.isInstancedMesh) object.computeBoundingSphere();
    return !ray.intersectObjects(blockers, false).length;
  }

  const active = new Set();
  let pending = null;
  const accepted = (target) => domElement.contains(target) || Boolean(wakeElement?.contains(target));
  function down(event) {
    if (active.size && !active.has(event.pointerId)) pending = null;
    if (!accepted(event.target)) return;
    active.add(event.pointerId);
    if (active.size !== 1 || event.isPrimary === false || event.button !== 0 || document.hidden) return;
    pending = pick(event.clientX, event.clientY)
      ? { id: event.pointerId, x: event.clientX, y: event.clientY, started: now(), cancelled: false } : null;
  }
  function move(event) {
    if (pending?.id === event.pointerId && Math.hypot(event.clientX - pending.x, event.clientY - pending.y) > TAP_SLOP) pending.cancelled = true;
  }
  function up(event) {
    active.delete(event.pointerId);
    if (pending?.id !== event.pointerId) return;
    const tap = pending;
    pending = null;
    if (tap.cancelled || active.size || event.button !== 0 || document.hidden || now() - tap.started > TAP_DURATION) return;
    if (Math.hypot(event.clientX - tap.x, event.clientY - tap.y) > TAP_SLOP) return;
    if (pick(event.clientX, event.clientY)) onTap();
  }
  function cancel(event) {
    active.delete(event.pointerId);
    if (pending?.id === event.pointerId) pending = null;
  }
  function reset() { active.clear(); pending = null; }
  function visibility() { if (document.hidden) reset(); }
  const handlers = { pointerdown: down, pointermove: move, pointerup: up,
    pointercancel: cancel, lostpointercapture: cancel };
  for (const [type, handler] of Object.entries(handlers)) document.addEventListener(type, handler, { capture: true, passive: true });
  document.defaultView.addEventListener('blur', reset);
  document.addEventListener('visibilitychange', visibility);
  return {
    dispose() {
      reset();
      for (const [type, handler] of Object.entries(handlers)) document.removeEventListener(type, handler, { capture: true });
      document.defaultView.removeEventListener('blur', reset);
      document.removeEventListener('visibilitychange', visibility);
    },
  };
}
