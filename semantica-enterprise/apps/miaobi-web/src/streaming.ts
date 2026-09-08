export type FrameHandle = number;

export function createFrameDeltaBuffer(
  onFlush: (value: string) => void,
  schedule: (callback: FrameRequestCallback) => FrameHandle = (callback) => window.requestAnimationFrame(callback),
  cancel: (handle: FrameHandle) => void = (handle) => window.cancelAnimationFrame(handle),
) {
  let pending = '';
  let frame: FrameHandle | null = null;

  const flush = () => {
    if (frame !== null) cancel(frame);
    frame = null;
    const value = pending;
    pending = '';
    if (value) onFlush(value);
  };

  return {
    enqueue(value: string) {
      if (!value) return;
      pending += value;
      if (frame !== null) return;
      frame = schedule(() => flush());
    },
    flush,
    dispose() {
      if (frame !== null) cancel(frame);
      frame = null;
      pending = '';
    },
  };
}
