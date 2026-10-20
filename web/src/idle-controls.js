export function createIdleControls({ delay = 5000, onHide, onShow, canHide = () => true }) {
  let hidden = false;
  let timer;
  const held = new Set();

  function schedule() {
    clearTimeout(timer);
    if (hidden || held.size) return;
    timer = setTimeout(() => {
      if (!canHide()) {
        schedule();
        return;
      }
      hidden = true;
      onHide();
    }, delay);
  }

  schedule();

  return {
    activity() {
      if (!hidden) schedule();
    },
    hold(token) {
      held.add(token);
      clearTimeout(timer);
    },
    release(token) {
      held.delete(token);
      schedule();
    },
    wake() {
      const wasHidden = hidden;
      if (wasHidden) {
        hidden = false;
        onShow();
      }
      schedule();
      return wasHidden;
    },
    get hidden() { return hidden; },
  };
}
