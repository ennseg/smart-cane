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
    Serial.println("Button pressed (hold 2 sec for SOS)...");
  } else {
    digitalWrite(ledPin, LOW);
    Serial.print("Button released after ");
    Serial.print(now - pressedAt);
    Serial.println(" ms");
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
  Serial.println("SOS triggered!");
  return true;
}
}  // namespace SosButton
