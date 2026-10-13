#pragma once

#include <stdint.h>

// Application-level AIMD for the synchronous WebSocket writer. Healthy
// delivery increases one FPS at a time; confirmed congestion decreases it.
// The floor stays at 3 FPS after a successful write, so one radio retry can
// never trap the video at sub-1 FPS.
class AdaptiveSendPacer {
 public:
  static constexpr uint8_t kMinFps = 3;
  static constexpr uint8_t kMaxFps = 15;
  static constexpr uint8_t kHealthyFramesToIncrease = 12;

  void beginConnection() {
    target_fps_ = kMinFps;
    healthy_streak_ = 0;
    slow_streak_ = 0;
  }

  void recordSuccess(uint32_t send_ms) {
    if (send_ms <= 100) {
      slow_streak_ = 0;
      // Above 10 FPS, only climb while the TCP path has ample headroom.
      const uint32_t increase_limit_ms = target_fps_ >= 10 ? 50 : 100;
      if (send_ms <= increase_limit_ms && target_fps_ < kMaxFps &&
          ++healthy_streak_ >= kHealthyFramesToIncrease) {
        ++target_fps_;
        healthy_streak_ = 0;
      } else if (send_ms > increase_limit_ms) {
        healthy_streak_ = 0;
      }
      return;
    }

    healthy_streak_ = 0;
    ++slow_streak_;

    // A very long blocking write is equivalent to congestion loss: halve the
    // offered rate immediately, but never go below the known-good 3 FPS.
    if (send_ms >= 700) {
      const uint8_t halved = (target_fps_ + 1) / 2;
      target_fps_ = halved < kMinFps ? kMinFps : halved;
      slow_streak_ = 0;
      return;
    }

    // Ignore an isolated retry. Two slow writes in a row confirm pressure and
    // reduce only one FPS, avoiding the old NORMAL -> SEVERE overreaction.
    if (slow_streak_ >= 2) {
      if (target_fps_ > kMinFps) --target_fps_;
      slow_streak_ = 0;
    }
  }

  void recordFailure() { beginConnection(); }

  uint32_t intervalMs() const { return 1000U / target_fps_; }
  uint8_t targetFps() const { return target_fps_; }

 private:
  uint8_t target_fps_ = kMinFps;
  uint8_t healthy_streak_ = 0;
  uint8_t slow_streak_ = 0;
};
