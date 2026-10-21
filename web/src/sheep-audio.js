/** A gesture-started sheep sound; onPlay runs only once playback has begun. */
export function createSheepAudio({
  url,
  onPlay = () => {},
  audioFactory = () => new Audio(),
  now = () => performance.now(),
  visibilityTarget = globalThis.document,
}) {
  const audio = audioFactory();
  let active = null;
  let startedAt = null;
  let cooldownUntil = 0;
  let disposed = false;

  function updateDuration() {
    if (startedAt !== null && Number.isFinite(audio.duration) && audio.duration > 0) {
      cooldownUntil = startedAt + audio.duration * 1000;
    }
  }

  function reset() {
    active = null;
    startedAt = null;
    cooldownUntil = 0;
    audio.pause();
    // Some browsers cannot seek an element until its metadata has arrived.
    try { audio.currentTime = 0; } catch {}
  }

  function ended() {
    updateDuration();
    active = null;
  }

  function visibility() {
    if (visibilityTarget.hidden) reset();
  }

  audio.addEventListener('loadedmetadata', updateDuration);
  audio.addEventListener('durationchange', updateDuration);
  audio.addEventListener('ended', ended);
  audio.addEventListener('error', reset);
  visibilityTarget?.addEventListener('visibilitychange', visibility);
  audio.preload = 'auto';
  audio.src = url;
  audio.loop = false;
  audio.load();

  return {
    trigger() {
      if (disposed || visibilityTarget?.hidden || active || now() < cooldownUntil) return Promise.resolve(false);
      const attempt = {};
      active = attempt;
      startedAt = null;
      try {
        audio.currentTime = 0;
      } catch {}
      let playback;
      try {
        if (audio.error) audio.load();
        // Keep this in the gesture's call stack so mobile browsers can unlock it.
        playback = audio.play();
      } catch {
        reset();
        return Promise.resolve(false);
      }
      return Promise.resolve(playback).then(
        () => {
          if (active !== attempt) {
            // A canceled request may settle after hide/show or disposal.
            if (!active) audio.pause();
            return false;
          }
          startedAt = now();
          updateDuration();
          onPlay();
          return true;
        },
        () => {
          if (active === attempt) reset();
          return false;
        },
      );
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      reset();
      audio.removeEventListener('loadedmetadata', updateDuration);
      audio.removeEventListener('durationchange', updateDuration);
      audio.removeEventListener('ended', ended);
      audio.removeEventListener('error', reset);
      visibilityTarget?.removeEventListener('visibilitychange', visibility);
      audio.removeAttribute('src');
      audio.load();
    },
  };
}
