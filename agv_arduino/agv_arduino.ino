/*
 * ============================================================================
 *  APEx Systems AGV  -  Arduino Uno R4 WiFi low-level controller firmware
 * ============================================================================
 *
 *  ROLE: SLAVE to the Raspberry Pi 5 (connected by USB serial, 115200 8N1).
 *        This firmware executes motion/actuator commands and reports sensor
 *        data. It makes NO navigation decisions. Its only autonomous actions
 *        are safety reflexes (hardware E-stop, optional bumpers, command
 *        watchdog) and the operator's manual RC override for commissioning.
 *
 *  Hardware (see docs/WIRING.md and the midterm report):
 *    - Cytron MDD20A dual motor driver, PWM/DIR inputs (20 A cont / 60 A peak)
 *    - 2 x goBILDA 5303-2402-0139 planetary gear motors (139:1) with built-in
 *      quadrature encoders (~3,896 counts/output rev with x4 decoding)
 *    - 2 x front IR sensors in front of the drive wheels (analog or digital)
 *    - Hall-effect sensor: home-dock (charging / waiting) position
 *    - Linear actuator for the return bin via a forward/reverse relay pair
 *    - Flysky FS-i6 transmitter + FS-iA6B receiver on i-BUS (Serial1, D0)
 *    - Hardwired E-stop: NC contact #1 drops contactor K1 (motion power);
 *      NC contact #2 / K1 auxiliary is sensed on A3
 *    - 24 V 12 Ah SLA battery (OSI OSA170) via a 56k/10k divider on A2
 *
 *  Protocol (must match common/protocol/serial_protocol.py, docs/PROTOCOL.md)
 *    Frame:  <BODY>*<CS>\n     CS = XOR of all BODY bytes, 2 upper-case hex
 *    Pi -> Arduino:
 *      HB,<seq>               heartbeat (resets watchdog)
 *      CMD,<left>,<right>     target PWM -255..255, ramped (resets watchdog)
 *      STOP                   immediate stop (bare "STOP" w/o checksum accepted)
 *      CLR                    clear latched faults (if cause is gone) -> ACK,CLR
 *      RST                    zero encoders                            -> ACK,RST
 *      CFG,<ramp>,<stat_ms>   ramp PWM/s (1..5000), STAT period 20..1000 -> ACK,CFG
 *      ACT,<dir>,<ms>         actuator 1 extend / -1 retract / 0 stop    -> ACK,ACT
 *      PING                   -> PONG,<fw>
 *    Arduino -> Pi:
 *      BOOT,<fw>
 *      STAT,<ms>,<encL>,<encR>,<spdL>,<spdR>,<ir1>,<ir2>,<bump>,<estop>,
 *           <fault>,<pwmL>,<pwmR>,<vbat_mv>,<hb_seq>,<dock>,<rc>,<act>
 *      ACK,<name>   ERR,<code>,<detail>   PONG,<fw>
 *    Examples (checksums verified by tests/test_protocol.py):
 *      CMD,120,-80*5C    STOP*18    HB,7*11
 *
 *  Rules: non-blocking only. No delay() in loop(); everything is scheduled
 *  with millis()/micros().
 *
 *  Board: "Arduino UNO R4 WiFi" (Arduino UNO R4 Boards core, Renesas RA4M1).
 * ============================================================================
 */

#include <Arduino.h>

// ============================================================================
//  CONFIGURATION  -  edit this block only
// ============================================================================

#define FW_VERSION "2.0.0"

// ---- Front IR sensor type: choose exactly ONE --------------------------------
#define IR_ANALOG            // analog distance sensors -> raw ADC 0..1023 reported
// #define IR_DIGITAL        // active-LOW obstacle modules -> 1 = obstacle

#define USE_LED_MATRIX  1    // 1 = show status on the built-in LED matrix
#define VBAT_ENABLED    1    // 1 = battery voltage divider wired to PIN_VBAT
#define RC_ENABLED      1    // 1 = Flysky FS-iA6B i-BUS on Serial1 (D0)
#define BUMPERS_FITTED  0    // 1 = bumper switches wired to A4/A5 (not in the current BOM)
#define RELAY_ACTIVE_LOW 1   // 1 = relay module energises on LOW (most opto relay boards)

// ============================================================================
//  PIN ASSIGNMENTS  (see docs/WIRING.md for the full wiring table)
// ============================================================================
//  Uno R4 interrupt-capable pins: D0 D1 D2 D3 D8 D12 D13 A1..A5
//  Uno R4 PWM pins:               D3 D5 D6 D9 D10 D11

// Wheel encoders, goBILDA 5303 (channel A on an interrupt pin, B plain input)
const uint8_t PIN_ENC_L_A = 2;
const uint8_t PIN_ENC_L_B = 4;
const uint8_t PIN_ENC_R_A = 3;
const uint8_t PIN_ENC_R_B = 7;

// Cytron MDD20A (PWM/DIR mode)
const uint8_t PIN_MOT_L_PWM = 9;    // MDD20A PWM1
const uint8_t PIN_MOT_L_DIR = 8;    // MDD20A DIR1
const uint8_t PIN_MOT_R_PWM = 10;   // MDD20A PWM2
const uint8_t PIN_MOT_R_DIR = 12;   // MDD20A DIR2

// Return-bin linear actuator (forward/reverse relay pair, interlocked in software)
const uint8_t PIN_ACT_EXTEND  = 5;
const uint8_t PIN_ACT_RETRACT = 6;

// Sensors
const uint8_t PIN_DOCK     = 11;    // Hall-effect home-dock sensor (open collector, LOW = magnet)
const uint8_t PIN_IR_1     = A0;    // front-left IR (in front of the left drive wheel)
const uint8_t PIN_IR_2     = A1;    // front-right IR
const uint8_t PIN_VBAT     = A2;    // battery divider 56k / 10k
const uint8_t PIN_ESTOP    = A3;    // E-stop NC contact #2 / K1 aux to GND (INPUT_PULLUP: HIGH = STOP)
const uint8_t PIN_BUMPER_L = A4;    // optional NO switch to GND (LOW = pressed)
const uint8_t PIN_BUMPER_R = A5;
// D0 = Serial1 RX  <- FS-iA6B i-BUS "SERVO" output (signal only; D1 TX unused)

// ---- Direction / polarity --------------------------------------------------
// Flip these so that a positive PWM drives the wheel FORWARD and forward
// motion gives POSITIVE encoder counts on both sides (right side is mirrored).
const bool INVERT_MOTOR_L = false;
const bool INVERT_MOTOR_R = true;
const bool INVERT_ENC_L   = false;
const bool INVERT_ENC_R   = true;

// ---- Timing / safety -------------------------------------------------------
const uint32_t WATCHDOG_MS        = 500;   // must match agv.arduino.watchdog_ms
const uint32_t DEFAULT_STAT_MS    = 50;    // 20 Hz status
const uint32_t SPEED_WINDOW_MS    = 50;    // encoder speed update period
const uint32_t ESTOP_RELEASE_MS   = 50;    // E-stop must read released this long
const uint16_t DEFAULT_RAMP_PWM_S = 600;   // PWM units per second (0 -> 255 in ~0.4 s)
const int      PWM_MAX            = 255;
const uint32_t ACT_MAX_MS         = 15000; // longest single actuator run
const uint32_t DOCK_DEBOUNCE_MS   = 30;

// ---- Manual RC (Flysky FS-i6 / FS-iA6B, i-BUS) ------------------------------
const uint32_t RC_TIMEOUT_MS   = 100;      // no fresh frame -> signal lost
const uint8_t  RC_CH_STEER     = 0;        // CH1 (right stick horizontal, mode 2)
const uint8_t  RC_CH_THROTTLE  = 1;        // CH2 (right stick vertical)
const uint8_t  RC_CH_ENABLE    = 4;        // CH5 = SwA: manual override switch
const uint16_t RC_ENABLE_ABOVE = 1700;     // us; switch "on"
const uint16_t RC_DEADBAND_US  = 30;
const int      RC_MAX_PWM      = 120;      // manual speed limit (keep low for commissioning)

// ---- Battery divider -------------------------------------------------------
// 24 V SLA charges up to ~28.8 V: 28.8 V / 6.6 = 4.36 V on A2 (< 5 V max).
const float ADC_VREF_MV     = 5000.0f;     // R4 analog reference (default 5 V)
const float VBAT_DIVIDER    = 6.6f;        // (R1+R2)/R2 = (56k+10k)/10k   VERIFY with a meter

// ============================================================================
//  END OF CONFIGURATION
// ============================================================================

#if USE_LED_MATRIX
#include "Arduino_LED_Matrix.h"
ArduinoLEDMatrix matrix;
// 12x8 frames (generated row-major, MSB first)
const uint32_t FRAME_IDLE[3]   = {0x0000F010, 0x81081081, 0x080F0000};
const uint32_t FRAME_MOVING[3] = {0x00002003, 0x03F83FC3, 0xF8030020};
const uint32_t FRAME_FAULT[3]  = {0x00010809, 0x00600600, 0x90108000};
const uint32_t FRAME_ESTOP[3]  = {0xFFF801BB, 0x9A21BB9A, 0x09BB9FFF};
#endif

// Fault bits (match FaultFlags in serial_protocol.py)
const uint8_t FAULT_WATCHDOG = 1;
const uint8_t FAULT_ESTOP    = 2;
const uint8_t FAULT_BUMPER   = 4;

// Error codes (match ErrCode in serial_protocol.py)
const uint8_t ERR_CHECKSUM      = 1;
const uint8_t ERR_UNKNOWN_CMD   = 2;
const uint8_t ERR_BAD_ARGS      = 3;
const uint8_t ERR_LINE_TOO_LONG = 4;
const uint8_t ERR_REJECTED      = 5;

// ---------------------------------------------------------------- state
volatile int32_t encTicksL = 0;    // written in ISRs
volatile int32_t encTicksR = 0;

int32_t  lastSpeedTicksL = 0, lastSpeedTicksR = 0;
int32_t  speedL = 0, speedR = 0;   // ticks per second
uint32_t lastSpeedMs = 0;

int      targetPwmL = 0, targetPwmR = 0;   // requested by the Pi (or the RC sticks)
float    currentPwmL = 0, currentPwmR = 0; // after ramping
uint32_t lastRampUs = 0;
uint16_t rampPwmPerS = DEFAULT_RAMP_PWM_S;

uint32_t statPeriodMs = DEFAULT_STAT_MS;
uint32_t lastStatMs = 0;

uint32_t lastValidCmdMs = 0;
bool     watchdogArmed = false;    // armed after the first valid HB/CMD
uint16_t lastHbSeq = 0;

uint8_t  faultFlags = 0;           // latched faults
bool     estopActive = false;      // live (debounced on release) E-stop input
uint32_t estopReleasedSinceMs = 0;
uint8_t  bumperBits = 0;

bool     docked = false;           // debounced Hall input
bool     dockRaw = false;
uint32_t dockChangedMs = 0;

int8_t   actDir = 0;               // 1 extend, -1 retract, 0 idle
uint32_t actStartMs = 0, actRunMs = 0;

uint16_t rcCh[14];                 // i-BUS channels (us)
uint32_t rcLastFrameMs = 0;
bool     rcSeen = false;
bool     manualActive = false;     // RC override driving the motors
uint8_t  ibusBuf[32];
uint8_t  ibusLen = 0;

int      ir1 = 0, ir2 = 0;
uint16_t vbatMv = 0;

const size_t RX_MAX = 128;         // matches MAX_LINE in serial_protocol.py
char     rxBuf[RX_MAX + 1];
size_t   rxLen = 0;
bool     rxDiscarding = false;

uint8_t  lastLedState = 255;

// ============================================================================
//  Encoders (x2 decoding: interrupt on every edge of channel A)
// ============================================================================
void isrEncL() {
  // A == B after an A edge -> one direction; A != B -> the other.
  bool a = digitalRead(PIN_ENC_L_A);
  bool b = digitalRead(PIN_ENC_L_B);
  int8_t step = (a == b) ? 1 : -1;
  encTicksL += INVERT_ENC_L ? -step : step;
}

void isrEncR() {
  bool a = digitalRead(PIN_ENC_R_A);
  bool b = digitalRead(PIN_ENC_R_B);
  int8_t step = (a == b) ? 1 : -1;
  encTicksR += INVERT_ENC_R ? -step : step;
}

void readEncoders(int32_t &l, int32_t &r) {
  noInterrupts();
  l = encTicksL;
  r = encTicksR;
  interrupts();
}

// ============================================================================
//  Motors (Cytron MDD20A, PWM/DIR). PWM = 0 brakes while the driver is powered.
// ============================================================================
void writeMotor(bool left, int pwm) {
  pwm = constrain(pwm, -PWM_MAX, PWM_MAX);
  if (left ? INVERT_MOTOR_L : INVERT_MOTOR_R) pwm = -pwm;
  digitalWrite(left ? PIN_MOT_L_DIR : PIN_MOT_R_DIR, pwm >= 0 ? HIGH : LOW);
  analogWrite(left ? PIN_MOT_L_PWM : PIN_MOT_R_PWM, abs(pwm));
}

/** Stop immediately (no ramp) and forget the targets. */
void hardStop() {
  targetPwmL = targetPwmR = 0;
  currentPwmL = currentPwmR = 0;
  writeMotor(true, 0);
  writeMotor(false, 0);
}

/** Move the applied PWM toward the target at rampPwmPerS. */
void updateRamp() {
  uint32_t nowUs = micros();
  float dt = (nowUs - lastRampUs) * 1e-6f;
  lastRampUs = nowUs;
  if (dt <= 0 || dt > 0.5f) dt = 0.001f;   // first call / micros wrap

  if (faultFlags != 0 || estopActive) {    // safety: never drive with a fault latched
    if (currentPwmL != 0 || currentPwmR != 0 || targetPwmL != 0 || targetPwmR != 0) hardStop();
    return;
  }
  float maxStep = rampPwmPerS * dt;
  // Decelerating toward zero / reversing is allowed at 2x the ramp rate.
  float stepL = (fabsf(targetPwmL) < fabsf(currentPwmL)) ? maxStep * 2 : maxStep;
  float stepR = (fabsf(targetPwmR) < fabsf(currentPwmR)) ? maxStep * 2 : maxStep;
  currentPwmL += constrain(targetPwmL - currentPwmL, -stepL, stepL);
  currentPwmR += constrain(targetPwmR - currentPwmR, -stepR, stepR);
  writeMotor(true, (int)lroundf(currentPwmL));
  writeMotor(false, (int)lroundf(currentPwmR));
}

// ============================================================================
//  Return-bin actuator (relays interlocked: never both energised)
// ============================================================================
void relayWrite(uint8_t pin, bool on) {
  digitalWrite(pin, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void actuatorStop() {
  relayWrite(PIN_ACT_EXTEND, false);
  relayWrite(PIN_ACT_RETRACT, false);
  actDir = 0;
}

void actuatorStart(int8_t dir, uint32_t ms) {
  actuatorStop();                          // break before make
  if (dir == 0 || ms == 0) return;
  actDir = dir;
  actStartMs = millis();
  actRunMs = min(ms, ACT_MAX_MS);
  relayWrite(dir > 0 ? PIN_ACT_EXTEND : PIN_ACT_RETRACT, true);
}

void updateActuator() {
  if (actDir == 0) return;
  if (faultFlags || estopActive || manualActive || millis() - actStartMs >= actRunMs) actuatorStop();
}

// ============================================================================
//  Flysky FS-iA6B i-BUS (115200 8N1, 32-byte frames: 0x20 0x40, 14 x uint16 LE,
//  checksum = 0xFFFF - sum of the first 30 bytes)
// ============================================================================
void pollIbus() {
#if RC_ENABLED
  while (Serial1.available() > 0) {
    uint8_t b = (uint8_t)Serial1.read();
    if (ibusLen == 0 && b != 0x20) continue;
    if (ibusLen == 1 && b != 0x40) { ibusLen = 0; continue; }
    ibusBuf[ibusLen++] = b;
    if (ibusLen == 32) {
      uint16_t sum = 0xFFFF;
      for (uint8_t i = 0; i < 30; i++) sum -= ibusBuf[i];
      uint16_t cs = ibusBuf[30] | (ibusBuf[31] << 8);
      if (sum == cs) {
        for (uint8_t c = 0; c < 14; c++) rcCh[c] = ibusBuf[2 + 2 * c] | (ibusBuf[3 + 2 * c] << 8);
        rcLastFrameMs = millis();
        rcSeen = true;
      }
      ibusLen = 0;
    }
  }
#endif
}

bool rcValid() {
  return rcSeen && (millis() - rcLastFrameMs) < RC_TIMEOUT_MS;
}

int stickToPwm(uint16_t us) {
  int v = (int)us - 1500;
  if (abs(v) < (int)RC_DEADBAND_US) return 0;
  return constrain(v * RC_MAX_PWM / 500, -RC_MAX_PWM, RC_MAX_PWM);
}

/** Manual commissioning: operator drives with the sticks while SwA is on. */
void updateManual() {
  bool want = rcValid() && rcCh[RC_CH_ENABLE] > RC_ENABLE_ABOVE;
  if (want != manualActive) {
    manualActive = want;
    hardStop();                            // clean hand-over in both directions
    if (!want) watchdogArmed = false;      // the Pi re-arms with its next HB/CMD
  }
  if (!manualActive) return;
  int thr = stickToPwm(rcCh[RC_CH_THROTTLE]);
  int str = stickToPwm(rcCh[RC_CH_STEER]);
  targetPwmL = constrain(thr + str, -RC_MAX_PWM, RC_MAX_PWM);
  targetPwmR = constrain(thr - str, -RC_MAX_PWM, RC_MAX_PWM);
}

uint8_t rcState() {
  if (manualActive) return 2;
  return rcValid() ? 1 : 0;
}

// ============================================================================
//  Serial protocol
// ============================================================================
uint8_t xorChecksum(const char *s, size_t n) {
  uint8_t cs = 0;
  for (size_t i = 0; i < n; i++) cs ^= (uint8_t)s[i];
  return cs;
}

/**
 * Send BODY*CS\n.
 * On the UNO R4 WiFi, `Serial` is a hardware UART to the on-board ESP32-S3
 * USB bridge, so it always drains at line rate even if the Pi is not reading.
 * Budget: a STAT frame (~100 bytes) at 20 Hz uses ~17 % of 115200 baud, so
 * writes never stall the loop.
 */
void sendFrame(const char *body) {
  size_t n = strlen(body);
  char tail[6];
  snprintf(tail, sizeof(tail), "*%02X\n", xorChecksum(body, n));
  Serial.write(body, n);
  Serial.write(tail, strlen(tail));
}

void sendError(uint8_t code, const char *detail) {
  char body[48];
  snprintf(body, sizeof(body), "ERR,%u,%s", code, detail);
  sendFrame(body);
}

/** Parse a decimal integer field; returns false on garbage. */
bool parseLong(const char *s, long &out) {
  if (s == nullptr || *s == '\0') return false;
  char *end = nullptr;
  out = strtol(s, &end, 10);
  return end != s && *end == '\0';
}

void markValidCommand() {
  lastValidCmdMs = millis();
  watchdogArmed = true;
}

/** Execute one verified frame body (already split into fields). */
void handleCommand(char *fields[], int count) {
  const char *name = fields[0];

  if (strcmp(name, "HB") == 0) {
    long seq;
    if (count != 2 || !parseLong(fields[1], seq)) { sendError(ERR_BAD_ARGS, "HB"); return; }
    lastHbSeq = (uint16_t)seq;
    markValidCommand();

  } else if (strcmp(name, "CMD") == 0) {
    long l, r;
    if (count != 3 || !parseLong(fields[1], l) || !parseLong(fields[2], r)) {
      sendError(ERR_BAD_ARGS, "CMD"); return;
    }
    markValidCommand();
    // ignored while a fault is latched or the RC override has control
    if (faultFlags == 0 && !estopActive && !manualActive) {
      targetPwmL = constrain((int)l, -PWM_MAX, PWM_MAX);
      targetPwmR = constrain((int)r, -PWM_MAX, PWM_MAX);
    }

  } else if (strcmp(name, "STOP") == 0) {
    hardStop();
    actuatorStop();
    markValidCommand();

  } else if (strcmp(name, "CLR") == 0) {
    // Faults only clear if their cause is gone.
    uint8_t keep = 0;
    if (estopActive) keep |= FAULT_ESTOP;
    if (bumperBits)  keep |= FAULT_BUMPER;
    faultFlags = keep;
    hardStop();
    markValidCommand();
    sendFrame("ACK,CLR");

  } else if (strcmp(name, "RST") == 0) {
    noInterrupts();
    encTicksL = encTicksR = 0;
    interrupts();
    lastSpeedTicksL = lastSpeedTicksR = 0;
    sendFrame("ACK,RST");

  } else if (strcmp(name, "CFG") == 0) {
    long ramp, statMs;
    if (count != 3 || !parseLong(fields[1], ramp) || !parseLong(fields[2], statMs) ||
        ramp < 1 || ramp > 5000 || statMs < 20 || statMs > 1000) {
      sendError(ERR_BAD_ARGS, "CFG"); return;
    }
    rampPwmPerS = (uint16_t)ramp;
    statPeriodMs = (uint32_t)statMs;
    sendFrame("ACK,CFG");

  } else if (strcmp(name, "ACT") == 0) {
    long dir, ms;
    if (count != 3 || !parseLong(fields[1], dir) || !parseLong(fields[2], ms) ||
        dir < -1 || dir > 1 || ms < 0 || ms > (long)ACT_MAX_MS) {
      sendError(ERR_BAD_ARGS, "ACT"); return;
    }
    if (dir != 0 && (faultFlags || estopActive || manualActive)) { sendError(ERR_REJECTED, "ACT"); return; }
    actuatorStart((int8_t)dir, (uint32_t)ms);
    sendFrame("ACK,ACT");

  } else if (strcmp(name, "PING") == 0) {
    sendFrame("PONG," FW_VERSION);

  } else {
    sendError(ERR_UNKNOWN_CMD, "?");
  }
}

/** Verify checksum, split, dispatch. `line` is NUL-terminated, no newline. */
void processLine(char *line) {
  if (strcmp(line, "STOP") == 0) {           // bare STOP always accepted (safe)
    hardStop();
    actuatorStop();
    markValidCommand();
    return;
  }
  char *star = strrchr(line, '*');
  if (star == nullptr || strlen(star) != 3) { sendError(ERR_CHECKSUM, "missing"); return; }
  *star = '\0';
  long cs = strtol(star + 1, nullptr, 16);
  if ((uint8_t)cs != xorChecksum(line, strlen(line))) { sendError(ERR_CHECKSUM, "bad"); return; }

  char *fields[6];
  int count = 0;
  char *save = nullptr;
  for (char *tok = strtok_r(line, ",", &save); tok && count < 6; tok = strtok_r(nullptr, ",", &save)) {
    fields[count++] = tok;
  }
  if (count == 0) { sendError(ERR_BAD_ARGS, "empty"); return; }
  handleCommand(fields, count);
}

/** Drain the USB serial RX buffer without blocking. */
void pollSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (!rxDiscarding && rxLen > 0) {
        rxBuf[rxLen] = '\0';
        processLine(rxBuf);
      }
      rxLen = 0;
      rxDiscarding = false;
    } else if (!rxDiscarding) {
      if (rxLen >= RX_MAX) {                 // overlong line: drop until newline
        rxDiscarding = true;
        rxLen = 0;
        sendError(ERR_LINE_TOO_LONG, "rx");
      } else {
        rxBuf[rxLen++] = c;
      }
    }
  }
}

// ============================================================================
//  Sensors & safety
// ============================================================================
void updateSafetyInputs() {
  uint32_t now = millis();

  // E-stop: NC contact to GND with pull-up. HIGH = pressed OR wire broken
  // (fail-safe). Trip instantly, release only after ESTOP_RELEASE_MS stable.
  // (Contact #1 of the same button drops contactor K1 in hardware.)
  bool raw = digitalRead(PIN_ESTOP) == HIGH;
  if (raw) {
    if (!estopActive) {
      estopActive = true;
      faultFlags |= FAULT_ESTOP;
      hardStop();
      actuatorStop();
    }
    estopReleasedSinceMs = 0;
  } else if (estopActive) {
    if (estopReleasedSinceMs == 0) estopReleasedSinceMs = now;
    else if (now - estopReleasedSinceMs >= ESTOP_RELEASE_MS) estopActive = false;  // fault stays latched until CLR
  }

#if BUMPERS_FITTED
  uint8_t b = 0;
  if (digitalRead(PIN_BUMPER_L) == LOW) b |= 1;
  if (digitalRead(PIN_BUMPER_R) == LOW) b |= 2;
  if (b && !bumperBits) {
    faultFlags |= FAULT_BUMPER;
    hardStop();
  }
  bumperBits = b;
#endif

  // Command watchdog (not applied while the operator drives with the RC override).
  if (!manualActive && watchdogArmed && (now - lastValidCmdMs > WATCHDOG_MS)) {
    if (!(faultFlags & FAULT_WATCHDOG)) {
      faultFlags |= FAULT_WATCHDOG;
      hardStop();
      actuatorStop();
    }
    watchdogArmed = false;   // re-armed by the next valid HB/CMD; fault needs CLR
  }

  // Hall-effect home-dock sensor (debounced)
  bool d = digitalRead(PIN_DOCK) == LOW;
  if (d != dockRaw) { dockRaw = d; dockChangedMs = now; }
  if (dockRaw != docked && now - dockChangedMs >= DOCK_DEBOUNCE_MS) docked = dockRaw;
}

void updateSpeeds() {
  uint32_t now = millis();
  uint32_t dt = now - lastSpeedMs;
  if (dt < SPEED_WINDOW_MS) return;
  int32_t l, r;
  readEncoders(l, r);
  speedL = (int32_t)((int64_t)(l - lastSpeedTicksL) * 1000 / (int32_t)dt);
  speedR = (int32_t)((int64_t)(r - lastSpeedTicksR) * 1000 / (int32_t)dt);
  lastSpeedTicksL = l;
  lastSpeedTicksR = r;
  lastSpeedMs = now;
}

void readSlowSensors() {
#if defined(IR_ANALOG)
  ir1 = analogRead(PIN_IR_1);
  ir2 = analogRead(PIN_IR_2);
#else
  ir1 = digitalRead(PIN_IR_1) == LOW ? 1 : 0;   // active-LOW modules
  ir2 = digitalRead(PIN_IR_2) == LOW ? 1 : 0;
#endif
#if VBAT_ENABLED
  vbatMv = (uint16_t)(analogRead(PIN_VBAT) * (ADC_VREF_MV / 1023.0f) * VBAT_DIVIDER);
#else
  vbatMv = 0;
#endif
}

void sendStatus() {
  int32_t l, r;
  readEncoders(l, r);
  char body[RX_MAX];
  snprintf(body, sizeof(body), "STAT,%lu,%ld,%ld,%ld,%ld,%d,%d,%u,%u,%u,%d,%d,%u,%u,%u,%u,%d",
           (unsigned long)millis(), (long)l, (long)r, (long)speedL, (long)speedR,
           ir1, ir2, bumperBits, estopActive ? 1u : 0u, faultFlags,
           (int)lroundf(currentPwmL), (int)lroundf(currentPwmR), vbatMv, lastHbSeq,
           docked ? 1u : 0u, rcState(), (int)actDir);
  sendFrame(body);
}

void updateLed() {
#if USE_LED_MATRIX
  uint8_t state;
  if (estopActive)                                state = 3;
  else if (faultFlags)                            state = 2;
  else if (currentPwmL != 0 || currentPwmR != 0)  state = 1;
  else                                            state = 0;
  if (state == lastLedState) return;
  lastLedState = state;
  switch (state) {
    case 0: matrix.loadFrame(FRAME_IDLE);   break;
    case 1: matrix.loadFrame(FRAME_MOVING); break;
    case 2: matrix.loadFrame(FRAME_FAULT);  break;
    default: matrix.loadFrame(FRAME_ESTOP); break;
  }
#endif
}

// ============================================================================
//  setup / loop
// ============================================================================
void setup() {
  // Motor and relay outputs first, so they are driven safe as early as possible.
  pinMode(PIN_MOT_L_PWM, OUTPUT); pinMode(PIN_MOT_L_DIR, OUTPUT);
  pinMode(PIN_MOT_R_PWM, OUTPUT); pinMode(PIN_MOT_R_DIR, OUTPUT);
  hardStop();
  pinMode(PIN_ACT_EXTEND, OUTPUT);
  pinMode(PIN_ACT_RETRACT, OUTPUT);
  actuatorStop();

  pinMode(PIN_ENC_L_A, INPUT_PULLUP);
  pinMode(PIN_ENC_L_B, INPUT_PULLUP);
  pinMode(PIN_ENC_R_A, INPUT_PULLUP);
  pinMode(PIN_ENC_R_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_L_A), isrEncL, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_R_A), isrEncR, CHANGE);

  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_DOCK, INPUT_PULLUP);
  pinMode(PIN_BUMPER_L, INPUT_PULLUP);
  pinMode(PIN_BUMPER_R, INPUT_PULLUP);
#if defined(IR_DIGITAL)
  pinMode(PIN_IR_1, INPUT_PULLUP);
  pinMode(PIN_IR_2, INPUT_PULLUP);
#endif
  analogReadResolution(10);   // keep 0..1023 (Pi side assumes agv.ir.adc_bits = 10)

#if USE_LED_MATRIX
  matrix.begin();
#endif

  Serial.begin(115200);       // USB (via the ESP32-S3 bridge) to the Pi
#if RC_ENABLED
  Serial1.begin(115200);      // i-BUS from the FS-iA6B on D0
#endif
  lastSpeedMs = lastStatMs = millis();
  lastRampUs = micros();
  sendFrame("BOOT," FW_VERSION);
}

void loop() {
  pollSerial();           // commands from the Pi
  pollIbus();             // RC receiver
  updateSafetyInputs();   // E-stop, bumpers, watchdog, dock (highest priority)
  updateManual();         // RC override (operator in control)
  updateRamp();           // motor outputs
  updateActuator();
  updateSpeeds();

  uint32_t now = millis();
  if (now - lastStatMs >= statPeriodMs) {
    lastStatMs = now;
    readSlowSensors();
    sendStatus();
  }
  updateLed();
}
