import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BoxGeometry, Color, Group, Mesh, MeshStandardMaterial } from 'three';
import { createSheep } from '../src/sheep.js';

const near = (actual, expected, tolerance = 1e-7) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is near ${expected}`);
const angleDelta = (from, to) => Math.atan2(Math.sin(to - from), Math.cos(to - from));

function fixture(navigation, random = () => 0) {
  const scene = new Group();
  const root = new Group();
  root.name = 'Sheep_Root';
  root.add(new Mesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial({ color: 0xe8dfcc })));
  scene.add(root);
  const lighting = { U: { uSunRad: { value: new Color(1, 1, 1) }, uNight: { value: 0 } } };
  const sheep = createSheep({ gltf: { scene }, navigation, lighting, random });
  return { ...sheep, lighting };
}

function advance(sheep, seconds) {
  for (let left = seconds; left > 1e-9; left -= 0.1) sheep.update(Math.min(left, 0.1));
}

function graph(overrides = {}) {
  return {
    version: 1, radius: 1.05, speed: 1, pause: [3, 6], initialNode: 0,
    nodes: [[0, 0, 0], [0, 0.5, 2], [2, 1, 2], [30, 0, 30]],
    edges: [
      { a: 0, b: 1, points: [[0, 0, 0], [0, 0.25, 1], [0, 0.5, 2]] },
      { a: 1, b: 2, points: [[0, 0.5, 2], [1, 0.75, 2], [2, 1, 2]] },
      // This direct edge exists but is longer than the route through node 1.
      { a: 0, b: 2, points: [[0, 0, 0], [20, 0, 0], [20, 1, 2], [2, 1, 2]] },
    ],
    ...overrides,
  };
}

test('sheep waits a random 3–6 seconds before moving and pauses again at its destination', () => {
  const minimum = fixture(graph(), () => 0);
  advance(minimum, 3);
  assert.deepEqual(minimum.root.position.toArray(), [0, 0, 0]);
  minimum.update(0.1);
  assert.ok(minimum.root.position.z > 0);

  const maximum = fixture(graph(), () => 1 - Number.EPSILON);
  advance(maximum, 6);
  assert.deepEqual(maximum.root.position.toArray(), [0, 0, 0]);
  for (let i = 0; i < 100 && maximum.state.node !== 2; i++) maximum.update(0.1);
  assert.deepEqual(maximum.root.position.toArray(), [2, 1, 2]);
  advance(maximum, 5.8);
  assert.deepEqual(maximum.root.position.toArray(), [2, 1, 2]);
});

test('sheep takes the shortest reachable graph route without cutting across the blocked diagonal', () => {
  const sheep = fixture(graph({ pause: [0, 0] }), () => 1 - Number.EPSILON);
  const visited = [];
  for (let i = 0; i < 100 && sheep.state.node !== 2; i++) {
    sheep.update(0.1);
    const { x, y, z } = sheep.root.position;
    visited.push([x, y, z]);
    assert.ok(x >= 0 && x <= 2 && z >= 0 && z <= 2);
    if (z < 2 - 1e-7) {
      near(x, 0);
      near(y, z * 0.25);
    } else {
      near(z, 2);
      near(y, 0.5 + x * 0.25);
    }
  }
  assert.ok(visited.some(([x]) => x > 0));
  assert.ok(visited.some(([x, y, z]) => x === 2 && y === 1 && z === 2));
});

test('reverse traversal follows the edge samples and their ground heights', () => {
  const sheep = fixture(graph({ pause: [0, 0], initialNode: 2 }));
  sheep.update(0.1);
  near(sheep.root.position.z, 2);
  assert.ok(sheep.root.position.x < 2);
  near(sheep.root.position.y, 0.5 + sheep.root.position.x * 0.25);
  assert.ok(Math.abs(angleDelta(-0.5, sheep.root.rotation.y)) <= Math.PI / 20 + 1e-8);
});

test('a large or invalid frame delta cannot skip the pause or teleport the sheep', () => {
  const waiting = fixture(graph());
  waiting.update(3600);
  assert.deepEqual(waiting.root.position.toArray(), [0, 0, 0]);

  const sheep = fixture(graph({ speed: 0.45, pause: [0, 0] }));
  sheep.root.rotation.y = -Math.PI / 2;
  sheep.update(3600);
  near(sheep.root.position.length(), 0.045);
  const position = sheep.root.position.clone();
  const heading = sheep.root.rotation.y;
  for (const dt of [-1, NaN, Infinity]) sheep.update(dt);
  assert.ok(sheep.root.position.equals(position));
  near(sheep.root.rotation.y, heading);
});

function straightGraph(angle = 0) {
  const end = [10 * Math.cos(angle), 0, -10 * Math.sin(angle)];
  return graph({ speed: 0.45, pause: [0, 0], nodes: [[0, 0, 0], end],
    edges: [{ a: 0, b: 1, points: [[0, 0, 0], end] }] });
}

test('sheep turns at 90 degrees per second while moving, slowing into the turn and regaining walking speed', () => {
  const sheep = fixture(straightGraph());
  sheep.root.rotation.y = Math.PI / 2;
  let lastDistance = 0;
  for (let i = 0; i < 10; i++) {
    const position = sheep.root.position.clone();
    const angle = sheep.root.rotation.y;
    sheep.update(0.1);
    near(angleDelta(angle, sheep.root.rotation.y), -Math.PI / 20);
    near(sheep.root.rotation.y, Math.PI / 2 - (i + 1) * Math.PI / 20);
    const distance = sheep.root.position.distanceTo(position);
    assert.ok(distance > 0, 'keeps moving throughout the turn');
    assert.ok(distance >= lastDistance - 1e-9, 'movement coordinates with improving alignment');
    near(distance, 0.045 * (0.25 + 0.75 * Math.cos(sheep.root.rotation.y)));
    near(sheep.root.position.z, 0);
    lastDistance = distance;
  }
  const position = sheep.root.position.clone();
  sheep.update(0.1);
  near(sheep.root.rotation.y, 0);
  near(sheep.root.position.distanceTo(position), 0.045);
});

test('turning crosses the angle wrap by the shortest arc and stops exactly on the target', () => {
  const target = -Math.PI + 0.04;
  const sheep = fixture(straightGraph(target));
  sheep.root.rotation.y = Math.PI - 0.03;
  for (let i = 0; i < 5; i++) {
    const before = sheep.root.rotation.y;
    const remaining = angleDelta(before, target);
    sheep.update(0.01);
    near(angleDelta(before, sheep.root.rotation.y), Math.min(remaining, Math.PI / 200));
  }
  near(angleDelta(sheep.root.rotation.y, target), 0);
});

test('one frame has one turning budget even when crossing several path samples', () => {
  const sheep = fixture(graph({ speed: 1000, pause: [0, 0],
    nodes: [[0, 0, 0], [0.02, 0, 0.02]],
    edges: [{ a: 0, b: 1, points: [[0, 0, 0], [0.01, 0, 0], [0.01, 0, 0.01], [0.02, 0, 0.02]] }],
  }));
  sheep.root.rotation.y = Math.PI / 2;
  sheep.update(0.1);
  near(sheep.root.rotation.y, Math.PI / 2 - Math.PI / 20);
  assert.deepEqual(sheep.root.position.toArray(), [0.02, 0, 0.02]);
});

test('turn rate is frame independent and a large frame still respects the time cap', () => {
  const a = fixture(straightGraph());
  const b = fixture(straightGraph());
  a.root.rotation.y = b.root.rotation.y = Math.PI;
  for (let i = 0; i < 10; i++) a.update(0.05);
  for (let i = 0; i < 5; i++) b.update(0.1);
  near(angleDelta(a.root.rotation.y, b.root.rotation.y), 0);
  const before = a.root.rotation.y;
  a.update(3600);
  near(Math.abs(angleDelta(before, a.root.rotation.y)), Math.PI / 20);
});

test('an isolated sheep remains at its ground node, while malformed edges fail safely', () => {
  const sheep = fixture(graph({ initialNode: 3, pause: [0, 0] }));
  advance(sheep, 5);
  assert.deepEqual(sheep.root.position.toArray(), [30, 0, 30]);
  assert.throws(() => fixture(graph({ edges: [{ a: 0, b: 1, points: [[9, 0, 0], [0, 0.5, 2]] }] })), /navigation/i);
});

test('moving sheep materials share live lighting and retain model colors without baked attributes', () => {
  const sheep = fixture(graph());
  const mesh = sheep.root.getObjectByProperty('isMesh', true);
  assert.equal(mesh.material.uniforms.uSunRad, sheep.lighting.U.uSunRad);
  assert.equal(mesh.material.uniforms.uNight, sheep.lighting.U.uNight);
  near(mesh.material.uniforms.uAlbedo.value.r, new Color(0xe8dfcc).r);
  assert.equal(mesh.material.vertexColors, false);
  assert.equal(mesh.geometry.getAttribute('aSky'), undefined);
  assert.equal(sheep.root.animations.length, 0);
});
