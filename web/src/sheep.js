// Follow the exported, clearance-checked graph by setting transforms directly. No animation clips or mixer.
import { Mesh, PlaneGeometry, Vector3 } from 'three';
import { createSheepContactMaterial, createSheepMaterial } from './materials/sheep.js';

const EPSILON = 1e-9;
const POSITION_EPSILON = 1e-8;
const TURN_RATE = Math.PI / 2; // 90 degrees per second, independent of render frame rate

function readNavigation(data) {
  const fail = () => { throw new Error('Invalid sheep navigation: expected a finite, connected-edge graph (version 1)'); };
  const point = (p) => {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) fail();
    return new Vector3().fromArray(p);
  };
  if (data?.version !== 1 || !Array.isArray(data.nodes) || !data.nodes.length || !Array.isArray(data.edges)) fail();
  if (!Number.isFinite(data.speed) || data.speed <= 0 || !Number.isFinite(data.radius) || data.radius <= 0) fail();
  if (!Array.isArray(data.pause) || data.pause.length !== 2 || !data.pause.every(Number.isFinite) || data.pause[0] < 0 || data.pause[1] < data.pause[0]) fail();
  const nodes = data.nodes.map(point);
  const validNode = (n) => Number.isInteger(n) && n >= 0 && n < nodes.length;
  if (!validNode(data.initialNode)) fail();
  const adjacency = nodes.map(() => []);
  for (const edge of data.edges) {
    if (!validNode(edge.a) || !validNode(edge.b) || edge.a === edge.b || !Array.isArray(edge.points) || edge.points.length < 2) fail();
    const points = edge.points.map(point);
    // Never invent an unvalidated connector between the graph and an edge's samples.
    if (points[0].distanceTo(nodes[edge.a]) > 0.002 || points.at(-1).distanceTo(nodes[edge.b]) > 0.002) fail();
    points[0] = nodes[edge.a];
    points[points.length - 1] = nodes[edge.b];
    let length = 0;
    for (let i = 1; i < points.length; i++) length += points[i].distanceTo(points[i - 1]);
    if (length <= EPSILON) fail();
    adjacency[edge.a].push({ to: edge.b, points, length });
    adjacency[edge.b].push({ to: edge.a, points: [...points].reverse(), length });
  }
  return { nodes, adjacency };
}

function shortestRoutes(adjacency, start) {
  const distance = adjacency.map(() => Infinity);
  const previous = adjacency.map(() => null);
  const visited = new Set();
  distance[start] = 0;
  for (;;) {
    let from = -1;
    for (let i = 0; i < distance.length; i++) {
      if (!visited.has(i) && Number.isFinite(distance[i]) && (from < 0 || distance[i] < distance[from])) from = i;
    }
    if (from < 0) break;
    visited.add(from);
    for (const edge of adjacency[from]) {
      const candidate = distance[from] + edge.length;
      if (candidate < distance[edge.to]) {
        distance[edge.to] = candidate;
        previous[edge.to] = { from, edge };
      }
    }
  }
  return { distance, previous };
}

export function createSheep({ gltf, navigation, lighting, random = Math.random }) {
  const { nodes, adjacency } = readNavigation(navigation);
  const root = gltf.scene.getObjectByName('Sheep_Root');
  if (!root) throw new Error('sheep.glb is missing Sheep_Root');
  root.removeFromParent();
  root.matrixAutoUpdate = true;
  root.position.copy(nodes[navigation.initialNode]);
  root.rotation.y = -0.5;

  const materials = [];
  const originalMaterials = new Set();
  root.traverse((object) => {
    if (!object.isMesh) return;
    const replace = (old) => {
      if (old) originalMaterials.add(old);
      const material = createSheepMaterial(lighting, { color: old?.color, vertexColors: object.geometry.hasAttribute('color') });
      materials.push(material);
      return material;
    };
    object.material = Array.isArray(object.material) ? object.material.map(replace) : replace(object.material);
    object.castShadow = false;
    object.receiveShadow = false; // the custom material samples the existing shadow map itself
  });
  for (const material of originalMaterials) material.dispose();

  // A local soft shadow moves with the sheep without invalidating the static directional shadow atlas.
  const contactShadow = new Mesh(new PlaneGeometry(navigation.radius * 2.0, navigation.radius * 1.4), createSheepContactMaterial(lighting));
  contactShadow.name = 'Sheep_ContactShadow';
  contactShadow.rotation.x = -Math.PI / 2;
  contactShadow.position.y = 0.025;
  contactShadow.renderOrder = 2;
  root.add(contactShadow);

  const unitRandom = () => Math.min(Math.max(Number(random()) || 0, 0), 1 - Number.EPSILON);
  const randomPause = () => navigation.pause[0] + unitRandom() * (navigation.pause[1] - navigation.pause[0]);
  const state = { node: navigation.initialNode, targetNode: null, pauseRemaining: randomPause(), moving: false };
  const delta = new Vector3();
  let route = [];
  let pointIndex = 0;

  function chooseRoute() {
    const { distance, previous } = shortestRoutes(adjacency, state.node);
    const reachable = distance.map((d, i) => i !== state.node && Number.isFinite(d) ? i : -1).filter((i) => i >= 0);
    if (!reachable.length) return false;
    state.targetNode = reachable[Math.floor(unitRandom() * reachable.length)];
    const edges = [];
    for (let at = state.targetNode; at !== state.node;) {
      const step = previous[at];
      edges.unshift(step.edge);
      at = step.from;
    }
    route = edges.flatMap((edge) => edge.points.slice(1));
    pointIndex = 0;
    state.moving = true;
    return true;
  }

  function update(dt) {
    if (!Number.isFinite(dt) || dt <= 0) return;
    dt = Math.min(dt, 0.1); // returning from a hidden tab must not jump through the landscape
    if (state.pauseRemaining > EPSILON) {
      state.pauseRemaining = Math.max(0, state.pauseRemaining - dt);
      return;
    }
    if (!state.moving && !chooseRoute()) return;
    while (pointIndex < route.length && root.position.distanceToSquared(route[pointIndex]) <= POSITION_EPSILON * POSITION_EPSILON) pointIndex++;
    let speedScale = 1;
    if (pointIndex < route.length) {
      delta.subVectors(route[pointIndex], root.position);
      if (delta.x * delta.x + delta.z * delta.z > POSITION_EPSILON * POSITION_EPSILON) {
        const target = Math.atan2(-delta.z, delta.x);
        const error = Math.atan2(Math.sin(target - root.rotation.y), Math.cos(target - root.rotation.y));
        const turn = Math.sign(error) * Math.min(Math.abs(error), TURN_RATE * dt);
        const heading = root.rotation.y + turn;
        root.rotation.y = Math.atan2(Math.sin(heading), Math.cos(heading));
        // Keep moving during a turn; regain walking speed as the body lines up with the safe path.
        speedScale = 0.25 + 0.75 * Math.max(0, Math.cos(error - turn));
      }
    }
    // Spend one angular budget per frame, even when this distance crosses multiple path samples.
    let remaining = navigation.speed * dt * speedScale;
    while (pointIndex < route.length && remaining > EPSILON) {
      delta.subVectors(route[pointIndex], root.position);
      const length = delta.length();
      if (length <= remaining + EPSILON) {
        root.position.copy(route[pointIndex++]);
        remaining = Math.max(0, remaining - length);
      } else {
        root.position.addScaledVector(delta, remaining / length);
        remaining = 0;
      }
    }
    if (pointIndex === route.length) {
      state.node = state.targetNode;
      state.targetNode = null;
      state.moving = false;
      state.pauseRemaining = randomPause();
    }
  }

  return { root, materials, contactShadow, state, update };
}
