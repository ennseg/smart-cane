#include "SosButton.h"

#include <Arduino.h>

namespace SosButton {
namespace {
constexpr int buttonPin = 13;
constexpr int ledPin = 2;
constexpr unsigned long debounceMs = 30;
constexpr unsigned long holdMs = 2000;

int lastReading = HIGH;
int stableReading = HIGH;
unsigned long lastChangeAt = 0;
unsigned long pressedAt = 0;
bool triggered = false;

void updateStableReading(int reading, unsigned long now) {
  stableReading = reading;
  if (reading == LOW) {
    pressedAt = now;
    triggered = false;
    Serial.println("Кнопка нажата: удерживай 2 с для SOS");
  } else {
    digitalWrite(ledPin, LOW);
    Serial.print("Кнопка отпущена через ");
    Serial.print(now - pressedAt);
    Serial.println(" мс");
  }
}
}  // namespace

void begin() {
  pinMode(buttonPin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW);
}

bool poll(unsigned long now) {
  const int reading = digitalRead(buttonPin);
  if (reading != lastReading) {
    lastChangeAt = now;
    lastReading = reading;
  }
  if (reading != stableReading && now - lastChangeAt > debounceMs) {
    updateStableReading(reading, now);
  }
  if (stableReading != LOW || triggered || now - pressedAt < holdMs) {
    return false;
  }
  triggered = true;
  digitalWrite(ledPin, HIGH);
  Serial.println("SOS: кнопка удержана 2 с");
  return true;
}
}  // namespace SosButton
