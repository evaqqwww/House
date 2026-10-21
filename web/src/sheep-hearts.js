import { Box3, Vector3 } from 'three';

/** DOM icons above the canvas: always crisp, with no 3D light or depth-of-field treatment. */
export function createSheepHearts({ domElement, root, camera, iconUrl, now = () => performance.now() }) {
  const document = domElement.ownerDocument;
  const layer = document.createElement('div');
  layer.className = 'sheep-hearts';
  layer.setAttribute('aria-hidden', 'true');
  document.body.appendChild(layer);
  root.updateWorldMatrix(true, true);
  const height = new Box3().setFromObject(root).max.y - root.getWorldPosition(new Vector3()).y;
  const projected = new Vector3();
  const bursts = [];

  function emit() {
    if (document.hidden) return;
    const origin = root.localToWorld(new Vector3(0.35, height + 0.12, 0));
    const node = document.createElement('div');
    node.className = 'sheep-heart-burst';
    for (let i = 0; i < 3; i++) {
      const heart = document.createElement('img');
      heart.src = iconUrl;
      heart.alt = '';
      heart.draggable = false;
      heart.className = 'sheep-heart';
      heart.style.setProperty('--heart-drift', `${[-22, 4, 26][i]}px`);
      heart.style.setProperty('--heart-rise', `${[85, 115, 95][i]}px`);
      heart.style.setProperty('--heart-delay', `${i * 0.10}s`);
      heart.style.width = `${[28, 36, 30][i]}px`;
      node.appendChild(heart);
    }
    layer.appendChild(node);
    bursts.push({ node, origin, start: now() });
    update();
  }
  function update() {
    if (!bursts.length) return;
    const time = now();
    const rect = domElement.getBoundingClientRect();
    for (let i = bursts.length - 1; i >= 0; i--) {
      const burst = bursts[i];
      if (time - burst.start > 1600) {
        burst.node.remove();
        bursts.splice(i, 1);
        continue;
      }
      projected.copy(burst.origin).project(camera);
      burst.node.hidden = projected.z < -1 || projected.z > 1;
      const x = rect.left + (projected.x * 0.5 + 0.5) * rect.width;
      const y = rect.top + (0.5 - projected.y * 0.5) * rect.height;
      burst.node.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    }
  }
  function clear() { for (const burst of bursts) burst.node.remove(); bursts.length = 0; }
  const visibility = () => { if (document.hidden) clear(); };
  document.addEventListener('visibilitychange', visibility);
  return { emit, update, dispose() { clear(); layer.remove(); document.removeEventListener('visibilitychange', visibility); } };
}
