#include <Arduino.h>

#include "input/SosButton.h"
#include "obstacles/ObstacleFeedback.h"
#include "obstacles/SingleRangeMonitor.h"

void setup() {
  Serial.begin(115200);
  SosButton::begin();
  // ObstacleFeedback::begin();  // Режим четырёх датчиков.
  Serial.println("Тест: один GY-530, вибромотор и кнопка SOS.");
  SingleRangeMonitor::begin();
}

void loop() {
  SosButton::poll(millis());  // Кнопка сама выводит событие удержания в Serial.
  delay(10);
}
