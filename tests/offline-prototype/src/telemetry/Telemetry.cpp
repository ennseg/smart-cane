#include "Telemetry.h"

#include <Arduino.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFiClient.h>
#include <mbedtls/md.h>
#include <cstring>

#include "network/WifiConnection.h"
#include "secrets.h"

namespace Telemetry {
namespace {
constexpr unsigned long positionIntervalMs = 30000;
constexpr int httpTimeoutMs = 5000;
Preferences preferences;
unsigned long lastPositionAt = 0;
bool firstPosition = true;

String makeBody(const char* packetType, uint32_t sequence) {
  String body = "{\"dev\":\"";
  body += CANE_DEVICE_ID;
  body += "\",\"seq\":";
  body += String(sequence);
  body += ",\"type\":\"";
  body += packetType;
  body += "\",\"fix\":false,\"net\":\"wifi\"}";
  return body;
}

String signBody(const String& body) {
  const auto* algorithm = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (algorithm == nullptr) {
    return "";
  }

  unsigned char digest[32];
  const int result = mbedtls_md_hmac(
      algorithm, reinterpret_cast<const unsigned char*>(CANE_HMAC_SECRET),
      strlen(CANE_HMAC_SECRET),
      reinterpret_cast<const unsigned char*>(body.c_str()), body.length(), digest);
  if (result != 0) {
    return "";
  }

  char hex[sizeof(digest) * 2 + 1];
  for (size_t i = 0; i < sizeof(digest); ++i) {
    snprintf(hex + i * 2, 3, "%02x", static_cast<unsigned>(digest[i]));
  }
  return String(hex);
}

void postPacket(const String& body, const String& signature,
                const char* packetType, uint32_t sequence) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(httpTimeoutMs);
  http.setTimeout(httpTimeoutMs);
  if (!http.begin(client, CANE_SERVER_URL)) {
    Serial.println("Cannot open server URL");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", CANE_TOKEN);
  http.addHeader("X-Signature", signature);

  Serial.print("Sending [");
  Serial.print(packetType);
  Serial.print("] packet: ");
  Serial.println(sequence);
  const int code = http.POST(body);
  Serial.print("HTTP result: ");
  Serial.println(code);
  if (code > 0) {
    Serial.println(http.getString());
  } else {
    Serial.println(HTTPClient::errorToString(code));
  }
  http.end();
}

void sendPacket(const char* packetType) {
  if (!WifiConnection::connected()) {
    Serial.println("Cannot send: Wi-Fi disconnected");
    return;
  }
  // Keep the existing NVS key so updating the firmware preserves numbering.
  const uint32_t sequence = preferences.getUInt("seq", 0) + 1;
  if (preferences.putUInt("seq", sequence) == 0) {
    Serial.println("Cannot save packet number");
    return;
  }

  const String body = makeBody(packetType, sequence);
  const String signature = signBody(body);
  if (signature.isEmpty()) {
    Serial.println("HMAC error");
    return;
  }
  postPacket(body, signature, packetType, sequence);
}
}  // namespace

bool begin() {
  return preferences.begin("cane-test", false);
}

void sendSos() {
  sendPacket("sos");
}

void sendPositionIfDue(unsigned long now) {
  if (!WifiConnection::connected() ||
      (!firstPosition && now - lastPositionAt < positionIntervalMs)) {
    return;
  }
  if (firstPosition) {
    WifiConnection::printConnectionInfo();
    firstPosition = false;
  }
  lastPositionAt = now;
  sendPacket("pos");
}
}  // namespace Telemetry
