#include "WifiConnection.h"

#include <Arduino.h>
#include <WiFi.h>
#include "secrets.h"

namespace WifiConnection {
void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("Connecting to Wi-Fi...");
}

bool connected() {
  return WiFi.status() == WL_CONNECTED;
}

void printConnectionInfo() {
  Serial.println("Wi-Fi connected");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}
}  // namespace WifiConnection
