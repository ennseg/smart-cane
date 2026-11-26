#include "VibrationMotor.h"

namespace VibrationMotor {
namespace {
constexpr int motorPin = 27;
constexpr unsigned long pulseMs = 150;
unsigned long pulseStartedAt = 0;
bool vibrating = false;
}  // namespace

void begin() {
  pinMode(motorPin, OUTPUT);
  digitalWrite(motorPin, LOW);
}

void update(uint16_t nearestMm, unsigned long now) {
  if (nearestMm >= 2000) {
    vibrating = false;
    digitalWrite(motorPin, LOW);
    return;
  }
  const unsigned long periodMs = map(max(nearestMm, uint16_t(300)), 300, 2000, pulseMs, 1000);
  if (!vibrating || now - pulseStartedAt >= periodMs) {
    pulseStartedAt = now;
    vibrating = true;
  }
  digitalWrite(motorPin, now - pulseStartedAt < pulseMs ? HIGH : LOW);
}
}  // namespace VibrationMotor
