#pragma once

namespace Telemetry {
bool begin();
void sendSos();
void sendPositionIfDue(unsigned long now);
}
