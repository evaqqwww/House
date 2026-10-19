// GPU frame timing for the ?debug HUD, with EXT_disjoint_timer_query_webgl2 (desktop browsers; phones rarely expose
// it): begin() / end() around the GPU work of a frame, results arrive a few frames later and are averaged over the
// last 60 frames.
export function createGpuTimer(gl) {
  const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2');
  const pending = [];
  const samples = [];
  let active = null;
  let enabled = false;
  return {
    set enabled(v) {
      enabled = v && !!ext;
    },
    begin() {
      if (!enabled || active) return;
      active = gl.createQuery();
      gl.beginQuery(ext.TIME_ELAPSED_EXT, active);
    },
    end() {
      if (!active) return;
      gl.endQuery(ext.TIME_ELAPSED_EXT);
      pending.push(active);
      active = null;
      while (pending.length) {
        const q = pending[0];
        if (!gl.getQueryParameter(q, gl.QUERY_RESULT_AVAILABLE)) break;
        const disjoint = gl.getParameter(ext.GPU_DISJOINT_EXT);
        const ns = gl.getQueryParameter(q, gl.QUERY_RESULT);
        gl.deleteQuery(q);
        pending.shift();
        if (!disjoint) {
          samples.push(ns / 1e6);
          if (samples.length > 60) samples.shift();
        }
      }
    },
    /** average GPU milliseconds per frame (0 when unavailable) */
    get ms() {
      return samples.length ? samples.reduce((a, b) => a + b, 0) / samples.length : 0;
    },
  };
}
