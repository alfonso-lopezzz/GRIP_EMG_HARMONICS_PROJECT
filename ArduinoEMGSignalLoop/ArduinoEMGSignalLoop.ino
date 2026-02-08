// Minimal EMG → Serial bridge for GRIP_EMG_Harmonics_Project.
// Samples A0, prints raw ADC counts at 115200 baud.

const uint8_t EMG_PIN = A0;
const uint32_t SAMPLE_RATE_HZ = 1000;           // 1 kHz
const uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;
const uint16_t LED_THRESHOLD = 520;             // tweak or remove as needed

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    Serial.begin(115200);                        // must match Python
    while (!Serial) { /* wait for USB CDC */ }
}

void loop() {
    static uint32_t lastSampleUs = micros();
    const uint32_t nowUs = micros();

    if ((int32_t)(nowUs - lastSampleUs) >= (int32_t)SAMPLE_INTERVAL_US) {
        lastSampleUs += SAMPLE_INTERVAL_US;

        const int raw = analogRead(EMG_PIN);      // 0–1023 on Uno
        Serial.println(raw);                      // or Serial.print(millis()); Serial.print(','); Serial.println(raw);

        digitalWrite(LED_BUILTIN, raw > LED_THRESHOLD ? HIGH : LOW);
    }
}