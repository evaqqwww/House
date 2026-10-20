// Gesture state is separate from DOM capture so taps, holds and cancellation share one path.
export function createTimeGesture({ readHour, onHour, onOpen, onTap, onDragging }) {
  let current = null;
  let holdTimer;

  function activate() {
    if (!current || current.dragging) return;
    clearTimeout(holdTimer);
    current.dragging = true;
    current.hour = readHour();
    onOpen();
    onDragging(true);
    // Freeze an in-flight preset transition before applying relative movement.
    onHour(current.hour);
  }

  function cancel() {
    clearTimeout(holdTimer);
    if (current?.dragging) onDragging(false);
    current = null;
  }

  return {
    start(point, choice = null, immediate = false) {
      if (current) return false;
      current = { id: point.pointerId, x: point.clientX, y: point.clientY, choice, dragging: false };
      if (immediate) activate();
      else holdTimer = setTimeout(activate, 320);
      return true;
    },
    move(point) {
      if (!current || current.id !== point.pointerId) return;
      if (Math.hypot(point.clientX - current.x, point.clientY - current.y) > 6) activate();
      if (current.dragging) onHour(Math.min(24, Math.max(0, current.hour + (point.clientX - current.x) / 18)));
    },
    end(point, cancelled = false) {
      if (!current || current.id !== point.pointerId) return;
      const choice = !cancelled && !current.dragging ? current.choice : null;
      cancel();
      if (choice) onTap(choice);
    },
    cancel,
  };
}
