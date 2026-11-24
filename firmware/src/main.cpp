#include <Arduino.h>

#include "obstacles/ObstacleFeedback.h"
#include "obstacles/SingleRangeMonitor.h"
#include "input/SosButton.h"
#include "network/WifiConnection.h"
#include "telemetry/Telemetry.h"

void setup() {
  Serial.begin(115200);
  delay(1000);
  SosButton::begin();
  // ObstacleFeedback::begin();  // Четыре датчика и вибросигнал: вернуть после теста.
  pinMode(27, OUTPUT);
  digitalWrite(27, LOW);  // Во время теста ключ мотора закрыт.
  SingleRangeMonitor::begin();
  if (!Telemetry::begin()) {
    Serial.println("Storage error");
    while (true) {
      delay(1000);
    }
  }
  WifiConnection::begin();
}

void loop() {
  const unsigned long now = millis();
  if (SosButton::poll(now)) {
    Telemetry::sendSos();
  }
  Telemetry::sendPositionIfDue(now);
  delay(10);
}
