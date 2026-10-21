import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ambienceGains, createAmbience } from '../src/ambience.js';

const sunPath = { riseHour: 5.5, setHour: 18.5 };
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≈ ${expected}`);
const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function browser(t, { failedFetches = 0, holdFetch = false, holdSuspend = false, deferFirstResume = false } = {}) {
  class TestDocument extends EventTarget {
    hidden = false;
    listeners = [];
    addEventListener(type, listener, options) {
      this.listeners.push({ type, listener, capture: options === true || !!options?.capture });
      super.addEventListener(type, listener, options);
    }
    removeEventListener(type, listener, options) {
      const capture = options === true || !!options?.capture;
      this.listeners = this.listeners.filter((entry) => entry.type !== type || entry.listener !== listener || entry.capture !== capture);
      super.removeEventListener(type, listener, options);
    }
  }

  const document = new TestDocument();
  const contexts = [];
  const requests = [];
  const held = [];
  const suspensions = [];
  const cleanups = [];
  let pendingResume;
  class AudioContext {
    state = 'suspended';
    currentTime = 10;
    destination = {};
    sources = [];
    gains = [];
    resumes = 0;
    suspends = 0;
    closes = 0;
    constructor() { contexts.push(this); }
    async resume() {
      this.resumes++;
      if (deferFirstResume && this.resumes === 1) {
        await new Promise((resolve) => { pendingResume = resolve; });
        return;
      }
      this.state = 'running';
      pendingResume?.();
      pendingResume = null;
    }
    async suspend() {
      this.suspends++;
      if (holdSuspend) await new Promise((resolve) => suspensions.push(resolve));
      this.state = 'suspended';
    }
    async close() {
      this.closes++;
      this.state = 'closed';
      pendingResume?.();
      pendingResume = null;
    }
    async decodeAudioData(data) {
      return { duration: 20, length: 960000, sampleRate: 48000, numberOfChannels: 2, url: data.url };
    }
    createGain() {
      const updates = [];
      const node = {
        gain: {
          value: 1,
          updates,
          setValueAtTime(value, time) { this.value = value; updates.push({ value, time }); },
          setTargetAtTime(value, time, timeConstant) { this.value = value; updates.push({ value, time, timeConstant }); },
          cancelScheduledValues() {},
          cancelAndHoldAtTime() {},
        },
        connect() {},
        disconnect() {},
      };
      this.gains.push(node);
      return node;
    }
    createBufferSource() {
      const node = {
        buffer: null,
        loop: false,
        outputs: [],
        starts: 0,
        stops: 0,
        connect(output) { this.outputs.push(output); },
        disconnect() {},
        start() { this.starts++; },
        stop() { this.stops++; },
      };
      this.sources.push(node);
      return node;
    }
  }
  const fetch = async (url) => {
    requests.push(String(url));
    if (failedFetches-- > 0) throw new Error('temporary connection failure');
    if (holdFetch) await new Promise((resolve) => held.push(resolve));
    const data = new ArrayBuffer(1);
    data.url = String(url);
    return { ok: true, status: 200, arrayBuffer: async () => data };
  };
  const replacements = { document, AudioContext, fetch };
  const previous = Object.fromEntries(Object.keys(replacements).map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  for (const [name, value] of Object.entries(replacements)) {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  t.after(async () => {
    try {
      for (const cleanup of cleanups.reverse()) await cleanup();
      await settle();
    } finally {
      for (const [name, descriptor] of Object.entries(previous)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    }
  });
  return {
    document, contexts, requests,
    cleanup(callback) { cleanups.push(callback); },
    gesture: (type = 'pointerdown') => document.dispatchEvent(new Event(type)),
    visibility(hidden) { document.hidden = hidden; document.dispatchEvent(new Event('visibilitychange')); },
    releaseFetch() { for (const resolve of held.splice(0)) resolve(); },
    releaseSuspend() { for (const resolve of suspensions.splice(0)) resolve(); },
  };
}

function tracks(context) {
  return Object.fromEntries(context.sources.map((source) => [
    source.buffer.url.endsWith('/daytime.ogg') ? 'day' : 'night',
    { source, gain: source.outputs[0].gain },
  ]));
}

test('day and night gains cover the full daytime and nighttime ranges, wrapping midnight', () => {
  for (const hour of [0, 4.5, 19.5, 22, 24, -2, 48]) {
    const gains = ambienceGains(hour, sunPath);
    near(gains.day, 0);
    near(gains.night, 1);
  }
  for (const hour of [6.5, 9, 13, 17.5]) {
    const gains = ambienceGains(hour, sunPath);
    near(gains.day, 1);
    near(gains.night, 0);
  }
});

test('sunrise and sunset blend with equal power and follow the supplied sun path', () => {
  for (const hour of [5.5, 18.5]) {
    const gains = ambienceGains(hour, sunPath);
    near(gains.day, Math.SQRT1_2);
    near(gains.night, Math.SQRT1_2);
  }
  for (let hour = 0; hour <= 24; hour += 0.25) {
    const { day, night } = ambienceGains(hour, sunPath);
    assert.ok(day >= 0 && day <= 1 && night >= 0 && night <= 1);
    near(day * day + night * night, 1);
  }
  const shifted = { riseHour: 7, setHour: 17 };
  near(ambienceGains(7, shifted).day, Math.SQRT1_2);
  near(ambienceGains(8, shifted).day, 1);
  near(ambienceGains(16, shifted).day, 1);
  near(ambienceGains(18, shifted).night, 1);
});

test('audio starts on initialization without a gesture and repeated gestures do not duplicate its loops', async (t) => {
  const env = browser(t);
  const ambience = createAmbience({ baseUrl: '/demo/assets/audio/', sunPath, initialHour: 22 });
  env.cleanup(() => ambience.dispose());
  assert.equal(env.contexts.length, 1);
  for (const type of ['pointerdown', 'pointerup', 'keydown', 'click']) {
    assert.ok(env.document.listeners.some((entry) => entry.type === type && entry.capture), `${type} listens during capture`);
  }
  await settle();
  assert.deepEqual(env.requests.sort(), ['/demo/assets/audio/daytime.ogg', '/demo/assets/audio/night.ogg']);
  assert.equal(env.contexts.length, 1);
  const context = env.contexts[0];
  assert.equal(context.state, 'running');
  assert.equal(context.sources.length, 2);
  for (const source of context.sources) {
    assert.equal(source.loop, true);
    assert.equal(source.starts, 1);
    assert.equal(source.buffer.duration, 20);
  }
  const { day, night } = tracks(context);
  near(day.gain.value, 0);
  near(night.gain.value, 1);
  for (const type of ['pointerdown', 'pointerup', 'keydown', 'click']) env.gesture(type);
  await settle();
  assert.equal(env.contexts.length, 1);
  assert.equal(context.sources.length, 2);
  assert.equal(env.requests.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('rapid time changes smoothly retarget the existing loops and honor the latest hour', async (t) => {
  const env = browser(t);
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  ambience.setHour(22);
  env.gesture('keydown');
  await settle();
  const context = env.contexts[0];
  const { day, night } = tracks(context);
  near(night.gain.value, 1);
  const before = [day.gain.updates.length, night.gain.updates.length];
  for (const hour of [5.5, 12, 18.5, 22, 12]) ambience.setHour(hour);
  for (const [index, track] of [day, night].entries()) {
    const changes = track.gain.updates.slice(before[index]);
    assert.ok(changes.length > 0);
    assert.ok(changes.every((change) => Number.isFinite(change.timeConstant) && change.timeConstant > 0));
  }
  near(day.gain.value, 1);
  near(night.gain.value, 0);
  assert.equal(context.sources.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('a gesture can unlock playback when the initial autoplay resume is blocked', async (t) => {
  const env = browser(t, { deferFirstResume: true });
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  await settle();
  assert.equal(env.contexts.length, 1);
  const context = env.contexts[0];
  assert.equal(context.sources.length, 0);
  env.gesture('pointerup');
  await settle();
  assert.equal(context.state, 'running');
  assert.equal(context.sources.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('an initially hidden page starts audio when it becomes visible without requiring a gesture', async (t) => {
  const env = browser(t);
  env.document.hidden = true;
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  await settle();
  assert.equal(env.contexts.length, 0);
  env.visibility(false);
  await settle();
  assert.equal(env.contexts.length, 1);
  assert.equal(env.contexts[0].state, 'running');
  assert.equal(env.contexts[0].sources.length, 2);
});

test('a failed audio load is contained and the next gesture retries playback', async (t) => {
  const env = browser(t, { failedFetches: 1 });
  t.mock.method(console, 'warn', () => {});
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  assert.doesNotThrow(() => env.gesture());
  await settle();
  assert.equal(env.contexts.flatMap((context) => context.sources).filter((source) => source.starts).length, 0);
  const failedRequests = env.requests.length;
  env.gesture('click');
  await settle();
  assert.ok(env.requests.length > failedRequests);
  const playing = env.contexts.flatMap((context) => context.sources).filter((source) => source.starts);
  assert.equal(playing.length, 2);
  assert.ok(playing.every((source) => source.loop && source.starts === 1));
});

test('hidden pages suspend audio and returning resumes the same loops', async (t) => {
  const env = browser(t);
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  env.gesture();
  await settle();
  const context = env.contexts[0];
  env.visibility(true);
  await settle();
  assert.equal(context.state, 'suspended');
  assert.ok(context.suspends > 0);
  const resumes = context.resumes;
  env.visibility(false);
  await settle();
  assert.equal(context.state, 'running');
  assert.ok(context.resumes > resumes);
  assert.equal(context.sources.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('returning to a visible page while its first audio load is pending resumes playback after loading', async (t) => {
  const env = browser(t, { holdFetch: true });
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  env.gesture();
  await settle();
  const context = env.contexts[0];
  env.visibility(true);
  await settle();
  assert.equal(context.state, 'suspended');
  env.visibility(false);
  await settle();
  env.releaseFetch();
  await settle();
  assert.equal(context.state, 'running');
  assert.equal(context.sources.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('a quick hide and show resumes audio even when suspension finishes after the page returns', async (t) => {
  const env = browser(t, { holdSuspend: true });
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.cleanup(() => ambience.dispose());
  env.gesture();
  await settle();
  const context = env.contexts[0];
  env.visibility(true);
  await settle();
  assert.equal(context.suspends, 1);
  env.visibility(false);
  await settle();
  env.releaseSuspend();
  await settle();
  assert.equal(context.state, 'running');
  assert.equal(context.sources.length, 2);
  assert.ok(context.sources.every((source) => source.starts === 1));
});

test('disposing stops audio, closes its context and removes gesture and visibility handlers', async (t) => {
  const env = browser(t);
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.gesture();
  await settle();
  const context = env.contexts[0];
  ambience.dispose();
  await settle();
  assert.equal(context.state, 'closed');
  assert.equal(context.closes, 1);
  assert.ok(context.sources.every((source) => source.stops === 1));
  assert.equal(env.document.listeners.length, 0);
  const requests = env.requests.length;
  env.gesture();
  env.visibility(true);
  env.visibility(false);
  ambience.setHour(22);
  await settle();
  assert.equal(env.requests.length, requests);
  assert.equal(env.contexts.length, 1);
});

test('disposing while assets load prevents late playback', async (t) => {
  const env = browser(t, { holdFetch: true });
  const ambience = createAmbience({ baseUrl: '/audio/', sunPath });
  env.gesture();
  await settle();
  ambience.dispose();
  env.releaseFetch();
  await settle();
  assert.ok(env.contexts.every((context) => context.state === 'closed'));
  assert.equal(env.contexts.flatMap((context) => context.sources).filter((source) => source.starts).length, 0);
  assert.equal(env.document.listeners.length, 0);
});
