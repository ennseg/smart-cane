#include "SingleRangeMonitor.h"
#include "VibrationMotor.h"

#include <Adafruit_VL53L0X.h>
#include <Arduino.h>
#include <Wire.h>

namespace SingleRangeMonitor {
namespace {
constexpr int shutdownPin = 18;
constexpr uint16_t noReading = UINT16_MAX;
constexpr unsigned long staleMs = 300;
Adafruit_VL53L0X sensor;

bool startSensor() {
  pinMode(shutdownPin, OUTPUT_OPEN_DRAIN);
  digitalWrite(shutdownPin, LOW);
  delay(10);
  digitalWrite(shutdownPin, HIGH);  // Отпускаем XSHUT к подтяжке модуля.
  delay(10);
  return sensor.begin(0x29, false, &Wire, Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE)
      && sensor.startRangeContinuous(80);
}

void monitorSensor() {
  uint16_t distanceMm = noReading;
  unsigned long lastSample = millis();
  unsigned long lastPrint = lastSample;
  for (;;) {
    const bool ready = sensor.isRangeComplete();
    if (sensor.Status != VL53L0X_ERROR_NONE) break;
    if (ready) {
      const uint16_t mm = sensor.readRangeResult();
      if (sensor.Status != VL53L0X_ERROR_NONE) break;
      distanceMm = sensor.readRangeStatus() == 0 ? mm : noReading;
      lastSample = millis();
    }
    const unsigned long now = millis();
    if (now - lastSample > staleMs) distanceMm = noReading;
    VibrationMotor::update(distanceMm, now);
    if (now - lastPrint >= 250) {
      if (distanceMm == noReading) {
        Serial.println("GY-530: нет достоверного измерения");
      } else {
        Serial.printf("Расстояние: %.1f см\n", distanceMm / 10.0);
      }
      lastPrint = now;
    }
    if (now - lastSample >= 2000) break;  // Датчик перестал выдавать новые измерения.
    delay(10);
  }
  VibrationMotor::update(noReading, millis());  // Выключаем до повторного подключения.
}

void run(void*) {
  if (!Wire.begin(21, 22)) {
    Serial.println("GY-530: ошибка запуска I2C");
    vTaskDelete(nullptr);
    return;
  }
  for (;;) {
    if (startSensor()) {
      Serial.println("GY-530: датчик готов");
      monitorSensor();
      Serial.println("GY-530: связь потеряна, повтор через 2 с");
    } else {
      Serial.println("GY-530: проверь питание, SDA, SCL и XSHUT. Повтор через 2 с.");
    }
    delay(2000);
  }
}
}  // namespace

void begin() {
  VibrationMotor::begin();
  if (xTaskCreate(run, "range-monitor", 8192, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("GY-530: ошибка запуска задачи");
  }
}
}  // namespace SingleRangeMonitor
