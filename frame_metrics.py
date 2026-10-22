"""Frame counts over elapsed time, rather than averages of reciprocal gaps."""

import time


class FrameRateMeter:
    def __init__(self, window=2.0, clock=time.perf_counter):
        self.clock = clock
        self.window = window
        self.started = clock()
        self.last_frame = None
        self.count = 0
        self.rate = 0.0

    def tick(self):
        now = self.clock()
        self.count += 1
        self.last_frame = now
        elapsed = now - self.started
        if elapsed >= self.window:
            self.rate = self.count / elapsed
            self.count = 0
            self.started = now
        return self.value()

    def value(self):
        if self.last_frame is None or self.clock() - self.last_frame >= self.window:
            return 0.0
        return self.rate
