// Deterministic CSV generator for exercising the EMG app without electrodes.
// Prints: t_ms,A0,A1,A2,A3,A4,A5 at ~250 Hz with phase-shifted ramps.

const int ANALOG_PINS[] = {A0, A1, A2, A3, A4, A5};
const int NUM_PINS = sizeof(ANALOG_PINS) / sizeof(ANALOG_PINS[0]);
const unsigned long SAMPLE_INTERVAL_MS = 4;  // ~250 Hz

void setup() {
    Serial.begin(115200);
    while (!Serial) {
        ;
    }

    Serial.println("t_ms,A0,A1,A2,A3,A4,A5");
}

void loop() {
    static unsigned long t0 = millis();
    unsigned long t = millis() - t0;

    Serial.print(t);
    for (int idx = 0; idx < NUM_PINS; ++idx) {
        int value = ((t / 2) + idx * 100) % 1024;  // sawtooth wave per channel
        Serial.print(",");
        Serial.print(value);
    }
    Serial.println();

    delay(SAMPLE_INTERVAL_MS);
}
