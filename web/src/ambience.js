const FADE_SECONDS = 0.4;
const VOLUME = 0.6;

function smoothstep(a, b, value) {
  const t = Math.min(1, Math.max(0, (value - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

/** Equal-power blend across the hour before/after sunrise and sunset. */
export function ambienceGains(hour, { riseHour, setHour }) {
  const h = ((hour % 24) + 24) % 24;
  const daylight = smoothstep(riseHour - 1, riseHour + 1, h)
    * (1 - smoothstep(setHour - 1, setHour + 1, h));
  return {
    day: Math.sin(daylight * Math.PI / 2),
    night: Math.cos(daylight * Math.PI / 2),
  };
}

/** Two continuously running loops; scene time changes their gains, never their playback positions. */
export function createAmbience({ baseUrl, sunPath, initialHour = 9 }) {
  const events = ['pointerdown', 'pointerup', 'keydown', 'click'];
  let hour = initialHour;
  let context;
  let master;
  let tracks = [];
  let starting = null;
  let unlocked = false;
  let disposed = false;

  function setHour(value) {
    hour = value;
    if (disposed || !tracks.length) return;
    const gains = ambienceGains(hour, sunPath);
    for (const track of tracks) {
      if (track.target === gains[track.name]) continue;
      track.target = gains[track.name];
      // New targets continue from the current envelope, including rapid reversals during dragging.
      track.node.gain.setTargetAtTime(track.target, context.currentTime, FADE_SECONDS);
    }
  }

  async function start() {
    if (disposed || document.hidden) return;
    const AudioContext = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!AudioContext) return;
    context ||= new AudioContext();
    // Try autoplay immediately; later gestures can retry resume if browser policy blocks it.
    if (context.state !== 'running') await context.resume();
    unlocked = context.state === 'running';
    if (!unlocked || disposed) return;
    if (!tracks.length) {
      const buffers = await Promise.all(['daytime', 'night'].map(async (name) => {
        const response = await fetch(`${baseUrl}${name}.ogg`);
        if (!response.ok) throw new Error(`Audio ${name}: HTTP ${response.status}`);
        const data = await response.arrayBuffer();
        if (disposed) return null;
        return context.decodeAudioData(data);
      }));
      if (disposed) return;
      master = context.createGain();
      master.gain.setValueAtTime(0, context.currentTime);
      master.connect(context.destination);
      const gains = ambienceGains(hour, sunPath);
      const startTime = context.currentTime + 0.03;
      tracks = buffers.map((buffer, i) => {
        const name = i === 0 ? 'day' : 'night';
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.loop = true;
        const node = context.createGain();
        node.gain.setValueAtTime(gains[name], context.currentTime);
        source.connect(node);
        node.connect(master);
        source.start(startTime);
        return { name, source, node, target: gains[name] };
      });
    }
    if (document.hidden) suspend();
    else master.gain.setTargetAtTime(VOLUME, context.currentTime, FADE_SECONDS);
  }

  function activate() {
    if (disposed || document.hidden) return;
    if (starting) {
      // Touch pointerdown (or an inactive key) can leave resume pending. Try again in pointerup/click.
      if (context && context.state !== 'running') context.resume().catch(() => {});
      return;
    }
    if (tracks.length && context.state === 'running') return;
    starting = start().catch((error) => {
      if (!disposed) console.warn('Background audio could not start; will retry on interaction.', error);
    }).finally(() => { starting = null; });
  }

  function suspend() {
    context.suspend().then(() => {
      // A quick hide/show may finish suspending after the page is already visible again.
      if (!disposed && !document.hidden && unlocked) activate();
    }).catch(() => {});
  }

  function visibility() {
    if (disposed) return;
    if (document.hidden) {
      if (!context) return;
      // Resume fades in again, and preserves both loop positions while the page is in the background.
      if (master) master.gain.setValueAtTime(0, context.currentTime);
      suspend();
    } else activate();
  }

  for (const type of events) document.addEventListener(type, activate, { capture: true, passive: true });
  document.addEventListener('visibilitychange', visibility);
  activate();

  return {
    setHour,
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const type of events) document.removeEventListener(type, activate, true);
      document.removeEventListener('visibilitychange', visibility);
      for (const { source, node } of tracks) {
        source.stop();
        source.disconnect();
        node.disconnect();
      }
      master?.disconnect();
      context?.close().catch(() => {});
    },
  };
}
