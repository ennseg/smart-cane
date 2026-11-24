#pragma once

namespace SosButton {
void begin();
// Returns true once per long press, after the button has settled.
bool poll(unsigned long now);
}
