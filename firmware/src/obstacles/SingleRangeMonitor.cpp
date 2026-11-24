#include "SingleRangeMonitor.h"

#include <Adafruit_VL53L0X.h>
#include <Arduino.h>
#include <Wire.h>

namespace SingleRangeMonitor {
namespace {
constexpr int shutdownPin = 18;  // XSHUT первого GY-530.
Adafruit_VL53L0X sensor;

bool startSensor() {
  // Сброс возвращает адрес 0x29 даже после прошивки на четыре датчика.
  pinMode(shutdownPin, OUTPUT_OPEN_DRAIN);
  digitalWrite(shutdownPin, LOW);
  delay(10);
  digitalWrite(shutdownPin, HIGH);  // Отпускаем XSHUT к подтяжке модуля.
  delay(10);
  return sensor.begin(0x29, false, &Wire, Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE);
}

void run(void*) {
  if (!Wire.begin(21, 22)) {  // SDA, затем SCL.
    Serial.println("GY-530: ошибка запуска I2C");
    vTaskDelete(nullptr);
    return;
  }
  for (;;) {
    if (!startSensor()) {
      Serial.println("GY-530: проверь питание, SDA, SCL и XSHUT. Повтор через 2 с.");
      delay(2000);
      continue;
    }
    Serial.println("GY-530: датчик готов");
    for (;;) {
      VL53L0X_RangingMeasurementData_t reading{};
      if (sensor.rangingTest(&reading) != VL53L0X_ERROR_NONE) {
        Serial.println("GY-530: ошибка обмена, повтор подключения");
        delay(2000);
        break;
      }
      if (reading.RangeStatus == 0) {
        Serial.printf("Расстояние: %.1f см\n", reading.RangeMilliMeter / 10.0);
      } else {
        Serial.println("GY-530: нет достоверного измерения");
      }
      delay(250);
    }
  }
}
}  // namespace

void begin() {
  // Ожидание измерения и паузы не задерживают опрос кнопки SOS.
  if (xTaskCreate(run, "range-monitor", 8192, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("GY-530: ошибка запуска задачи");
  }
}
}  // namespace SingleRangeMonitor
