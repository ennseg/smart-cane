#include "ObstacleFeedback.h"
#include "VibrationMotor.h"
#include <Adafruit_VL53L0X.h>
#include <Wire.h>

namespace ObstacleFeedback {
namespace {
constexpr uint8_t shutdownPins[] = {18, 19, 25, 26};
constexpr uint16_t noReading = UINT16_MAX;
constexpr unsigned long maxAgeMs = 300;
struct Sensor {
  Adafruit_VL53L0X driver;
  bool online = false;
  uint16_t distanceMm = noReading;
  unsigned long measuredAt = 0;
};
Sensor sensors[sizeof(shutdownPins) / sizeof(shutdownPins[0])];

void startSensors() {
  for (uint8_t pin : shutdownPins) {
    pinMode(pin, OUTPUT_OPEN_DRAIN);
    digitalWrite(pin, LOW);
  }
  delay(10);
  if (!Wire.begin(21, 22)) {
    Serial.println("I2C init failed");
    return;
  }
  for (size_t i = 0; i < sizeof(shutdownPins) / sizeof(shutdownPins[0]); ++i) {
    auto& sensor = sensors[i];
    digitalWrite(shutdownPins[i], HIGH);
    delay(10);
    const uint8_t address = 0x30 + i;
    sensor.online = sensor.driver.begin(address, false, &Wire, Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE)
                    && sensor.driver.startRangeContinuous(80);
    if (!sensor.online) {
      digitalWrite(shutdownPins[i], LOW);
    }
    Serial.printf("ToF %u (0x%02X): %s\n", unsigned(i + 1), unsigned(address), sensor.online ? "OK" : "offline");
  }
}

void refresh(Sensor& sensor) {
  if (!sensor.online || !sensor.driver.isRangeComplete()) return;
  if (sensor.driver.Status != VL53L0X_ERROR_NONE) {
    sensor.distanceMm = noReading;
    return;
  }
  const uint16_t mm = sensor.driver.readRangeResult();
  const bool valid = sensor.driver.Status == VL53L0X_ERROR_NONE && sensor.driver.readRangeStatus() == 0;
  sensor.distanceMm = valid ? mm : noReading;
  sensor.measuredAt = millis();
}

void run(void*) {
  startSensors();
  unsigned long lastLog = 0;
  for (;;) {
    uint16_t nearest = noReading;
    for (auto& sensor : sensors) {
      refresh(sensor);
      if (millis() - sensor.measuredAt > maxAgeMs) sensor.distanceMm = noReading;
      nearest = min(nearest, sensor.distanceMm);
    }
    const unsigned long now = millis();
    VibrationMotor::update(nearest, now);
    if (now - lastLog >= 500) {
      Serial.print("ToF mm:");
      for (const auto& sensor : sensors) {
        if (sensor.distanceMm == noReading) Serial.print(" --");
        else Serial.printf(" %u", unsigned(sensor.distanceMm));
      }
      Serial.println();
      lastLog = now;
    }
    delay(20);
  }
}
}  // namespace

void begin() {
  VibrationMotor::begin();
  if (xTaskCreate(run, "obstacles", 8192, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("Obstacle task failed");
  }
}
}  // namespace ObstacleFeedback
