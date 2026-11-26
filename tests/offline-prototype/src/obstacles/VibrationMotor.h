#pragma once
#include <Arduino.h>
namespace VibrationMotor {
void begin();
void update(uint16_t nearestMm, unsigned long now);
}
