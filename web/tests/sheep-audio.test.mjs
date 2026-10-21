import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createSheepAudio } from '../src/sheep-audio.js';
const settle = async () => { await new Promise(setImmediate); };
const immediate = (attempt) => Promise.race([attempt, Promise.resolve('still pending')]);

function environment(t, { duration = 1.75 } = {}) {
  class TestDocument extends EventTarget {
    hidden = false;
    listeners = new Set();
    addEventListener(type, listener, options) {
      this.listeners.add(listener);
      super.addEventListener(type, listener, options);
    }
    removeEventListener(type, listener, options) {
      this.listeners.delete(listener);
      super.removeEventListener(type, listener, options);
    }
  }
  class TestAudio extends EventTarget {
    duration = duration;
    currentTime = 0;
    paused = true;
    ended = false;
    error = null;
    plays = [];
    pauses = 0;
    loads = 0;
    listeners = new Set();
    addEventListener(type, listener, options) {
      this.listeners.add(listener);
      super.addEventListener(type, listener, options);
    }
    removeEventListener(type, listener, options) {
      this.listeners.delete(listener);
      super.removeEventListener(type, listener, options);
    }
    load() { this.loads++; this.error = null; }
    play() {
      this.ended = false;
      return new Promise((resolve, reject) => {
        this.plays.push({
          start: () => { this.paused = false; resolve(); },
          fail: () => { reject(new Error('Playback blocked')); },
        });
      });
    }
    pause() { this.paused = true; this.pauses++; }
    removeAttribute(name) { delete this[name]; }
    finish() {
      this.currentTime = this.duration;
      this.paused = true;
      this.ended = true;
      this.dispatchEvent(new Event('ended'));
    }
  }
  const document = new TestDocument();
  const audio = new TestAudio();
  let time = 1000;
  let hearts = 0;
  const controller = () => {
    const result = createSheepAudio({
      url: '/scene/assets/cute_lamb.ogg',
      onPlay: () => hearts++,
      audioFactory: () => audio,
      now: () => time,
      visibilityTarget: document,
    });
    t.after(() => result.dispose());
    return result;
  };
  return {
    audio, document, controller,
    get hearts() { return hearts; },
    advance(milliseconds) { time += milliseconds; },
    visibility(hidden) {
      document.hidden = hidden;
      document.dispatchEvent(new Event('visibilitychange'));
    },
  };
}

test('a tap starts playback immediately and emits its heart only after playback starts', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  assert.equal(env.audio.src, '/scene/assets/cute_lamb.ogg');
  assert.equal(env.audio.preload, 'auto');
  assert.equal(env.audio.loads, 1);
  const attempt = sound.trigger();
  assert.equal(env.audio.plays.length, 1, 'play runs in the original gesture stack');
  assert.equal(env.hearts, 0);
  env.audio.plays[0].start();
  assert.equal(await attempt, true);
  assert.equal(env.hearts, 1);
});

test('rapid taps cannot overlap a pending playback request', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  assert.equal(await immediate(sound.trigger()), false);
  env.advance(10000);
  assert.equal(await immediate(sound.trigger()), false, 'waiting for media does not expire its reservation');
  assert.equal(env.audio.plays.length, 1);
  env.audio.plays[0].start();
  assert.equal(await first, true);
  assert.equal(env.hearts, 1);
});

test('cooldown uses clip duration starting when playback succeeds', async (t) => {
  const env = environment(t, { duration: 2.4 });
  const sound = env.controller();
  const first = sound.trigger();
  env.advance(8000);
  env.audio.plays[0].start();
  await first;
  env.advance(2399);
  env.audio.finish();
  assert.equal(await immediate(sound.trigger()), false, 'the entire clip duration is reserved from its start');
  env.advance(1);
  const next = sound.trigger();
  assert.equal(env.audio.plays.length, 2);
  assert.equal(env.audio.currentTime, 0, 'new playback begins at the start of the clip');
  env.audio.plays[1].start();
  assert.equal(await next, true);
  assert.equal(env.hearts, 2);
});

test('playback remains exclusive after the estimated duration until the media actually ends', async (t) => {
  const env = environment(t, { duration: 0.45 });
  const sound = env.controller();
  const first = sound.trigger();
  env.audio.plays[0].start();
  await first;
  env.advance(5000);
  assert.equal(await immediate(sound.trigger()), false);
  assert.equal(env.audio.plays.length, 1);
  env.audio.finish();
  const next = sound.trigger();
  assert.equal(env.audio.plays.length, 2);
  env.audio.plays[1].start();
  assert.equal(await next, true);
});

test('unknown metadata blocks replay until ended and late duration metadata sets the cooldown', async (t) => {
  const env = environment(t, { duration: NaN });
  const sound = env.controller();
  const first = sound.trigger();
  env.audio.plays[0].start();
  await first;
  assert.equal(await immediate(sound.trigger()), false);
  env.audio.duration = 1.2;
  env.audio.dispatchEvent(new Event('loadedmetadata'));
  env.advance(1199);
  env.audio.finish();
  assert.equal(await immediate(sound.trigger()), false);
  env.advance(1);
  const next = sound.trigger();
  env.audio.plays[1].start();
  assert.equal(await next, true);
});

test('a rejected playback request emits no heart and allows the next tap to retry', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  env.audio.plays[0].fail();
  assert.equal(await first, false);
  assert.equal(env.hearts, 0);
  const retry = sound.trigger();
  assert.equal(env.audio.plays.length, 2);
  env.audio.plays[1].start();
  assert.equal(await retry, true);
  assert.equal(env.hearts, 1);
});

test('a media error cancels the active attempt and a fresh tap can retry', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  env.audio.dispatchEvent(new Event('error'));
  env.audio.plays[0].start();
  assert.equal(await first, false, 'a late successful promise cannot resurrect a failed attempt');
  assert.equal(env.hearts, 0);
  const retry = sound.trigger();
  env.audio.plays[1].start();
  assert.equal(await retry, true);
});

test('hiding the page stops and resets the clip, and returning requires a fresh tap', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  env.audio.plays[0].start();
  await first;
  env.audio.currentTime = 0.2;
  env.visibility(true);
  assert.equal(env.audio.paused, true);
  assert.equal(env.audio.currentTime, 0);
  assert.equal(await immediate(sound.trigger()), false);
  env.visibility(false);
  await settle();
  assert.equal(env.audio.plays.length, 1, 'returning never starts the clip on its own');
  const retry = sound.trigger();
  assert.equal(env.audio.plays.length, 2, 'hide cancels the previous cooldown');
  env.audio.plays[1].start();
  assert.equal(await retry, true);
});

test('a pending start canceled by page hide cannot emit a late heart or leave audio playing', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  env.visibility(true);
  env.visibility(false);
  env.audio.plays[0].start();
  assert.equal(await first, false);
  assert.equal(env.hearts, 0);
  assert.equal(env.audio.paused, true);
  const retry = sound.trigger();
  env.audio.plays[1].start();
  assert.equal(await retry, true);
});

test('an older canceled request cannot cancel a newer playback when its promise rejects', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const old = sound.trigger();
  env.visibility(true);
  env.visibility(false);
  const fresh = sound.trigger();
  assert.equal(env.audio.plays.length, 2);
  env.audio.plays[1].start();
  await fresh;
  const pauses = env.audio.pauses;
  env.audio.plays[0].fail();
  assert.equal(await old, false);
  assert.equal(env.audio.pauses, pauses);
  assert.equal(env.audio.paused, false);
  assert.equal(await immediate(sound.trigger()), false, 'new playback remains reserved');
  assert.equal(env.hearts, 1);
});

test('disposing cancels delayed starts, removes listeners and prevents future playback', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const first = sound.trigger();
  sound.dispose();
  env.audio.plays[0].start();
  assert.equal(await first, false);
  assert.equal(env.audio.paused, true);
  assert.equal(env.hearts, 0);
  assert.equal(env.audio.listeners.size, 0);
  assert.equal(env.document.listeners.size, 0);
  assert.equal(await immediate(sound.trigger()), false);
  env.visibility(true);
  env.visibility(false);
  assert.equal(env.audio.plays.length, 1);
  const pauses = env.audio.pauses;
  sound.dispose();
  assert.equal(env.audio.pauses, pauses, 'disposing twice is safe');
});

test('synchronous browser playback errors are contained and allow retry', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  const play = env.audio.play;
  env.audio.play = () => { throw new Error('Audio not ready'); };
  assert.equal(await sound.trigger(), false);
  assert.equal(env.hearts, 0);
  env.audio.play = play;
  const retry = sound.trigger();
  env.audio.plays[0].start();
  assert.equal(await retry, true);
});

test('a fresh tap reloads media after a source error before retrying play', async (t) => {
  const env = environment(t);
  const sound = env.controller();
  env.audio.error = { code: 4 };
  env.audio.dispatchEvent(new Event('error'));
  const attempt = sound.trigger();
  assert.equal(env.audio.error, null, 'a failed source must be reset before play can succeed');
  assert.equal(env.audio.loads, 2);
  env.audio.plays[0].start();
  assert.equal(await attempt, true);
});
