/*
 * Reconstructed control logic for the GD32F103 joint MCU.
 *
 * This is C-like pseudocode built from static disassembly + live observations.
 * Field names are inferred; offsets are kept in comments where useful.
 */

#include <stdint.h>
#include <stdbool.h>

#define PWM_MOTOR_ID   1
#define PWM_MAX        100
#define PWM_MIN       (-100)

typedef struct {
    uint8_t  bytes[0x60];
} StateBlock;

typedef struct {
    uint8_t  bytes[0x80];
} RegMap;

static inline uint16_t u16(const uint8_t *p) {
    return (uint16_t)((p[0] << 8) | p[1]);
}

static inline void set_u16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)(v & 0xFF);
}

// 0x0800B220
// Exact behavior from disassembly: one motor id only, signed speed, clamp to [-100,100].
// Direction is encoded by timer channel:
//   > 0 => CH2 active, CH3 off
//   < 0 => CH2 off, CH3 active
//   = 0 => both off
int pwm_helper(uint8_t motor_id, int8_t speed)
{
    if (motor_id != PWM_MOTOR_ID) {
        return -1;
    }

    if (speed > PWM_MAX) {
        speed = PWM_MAX;
    } else if (speed < PWM_MIN) {
        speed = PWM_MIN;
    }

    if (speed > 0) {
        // channel 2 on, channel 3 off
        return 1;
    }
    if (speed < 0) {
        // channel 2 off, channel 3 on
        return 2;
    }

    // stop
    return 0;
}

// 0x0800B004
// Converts a pair of motion values into PWM duty percent.
// Important speed-control discovery:
//   reg[0x001F] is read from register-map offset 0x3E.
//   If reg[0x001F] is nonzero, it overrides the normal numerator.
// Therefore:
//   duty = min(100, floor(numerator * 100 / denominator))
//   if reg[0x001F] != 0: numerator = reg[0x001F]
//   if reg[0x001F] == 0: use the state-machine's normal numerator
// Live:
//   reg[0x001F]=1 stopped/inhibited the motor.
//   reg[0x001F]=0 cleared the override and resumed the previous motion.
// Correction from deeper tracing:
//   The denominator is not reg[0x0014]=1000.  The sustained running caller at
//   0x08008E84 passes denominator=*(state+0x18), numerator=*(state+0x20).
//   state+0x18 is recomputed from ADC math at 0x08009E68:
//       state_18 ~= adc0 * 3300 * 0.002075
//   so realistic denominators are likely around 10k~20k.  Therefore override
//   values 300/500/800 only request a few percent duty and may look inactive.
uint8_t pwm_duty_compute(uint32_t numerator, uint32_t denominator, RegMap *regs)
{
    if (numerator == 0 || denominator == 0) {
        return 0;
    }

    uint16_t override = u16(&regs->bytes[0x001F * 2]);
    if (override != 0) {
        numerator = override;
    }

    uint32_t duty = (numerator * 100u) / denominator;
    if (duty > 100) {
        duty = 100;
    }
    return (uint8_t)duty;
}

// 0x0800C235-ish init chain
// PWM is driven by TIMER2, with CH2/CH3 as the motor pair.
// The firmware enables AFIO/GPIOB/TIMER2, resets the timer, then programs:
//   PSC = 0x23
//   ARR = 0x03E7
//   CH2/CH3 PWM mode + zero duty
// Likely pins: PB0/PB1 (default TIMER2_CH2/CH3 mapping).
// Startup path: 0x0800b02c calls this before entering the serial dispatcher.
void timer2_pwm_init(void)
{
    // Pseudocode only; helper names are inferred from disassembly.
    // rcu_periph_clock_enable(AFIO);
    // rcu_periph_clock_enable(GPIOB);
    // rcu_periph_clock_enable(TIMER2);
    // gpio_af_pushpull(GPIOB, PB0 | PB1);
    // timer_reset(TIMER2);
    // timer_init(TIMER2, prescaler=0x23, period=0x03E7);
    // timer_channel_config(TIMER2, CH2, PWM);
    // timer_channel_config(TIMER2, CH3, PWM);
    // timer_channel_compare(TIMER2, CH2, 0);
    // timer_channel_compare(TIMER2, CH3, 0);
    // timer_enable(TIMER2);
}

// Boot-time GPIO helper seen near 0x0800b148 / 0x0800b5b0.
// Likely uses a packed GPIO source encoding; 0x25 and 0x32 are plausible
// GPIOC5 and GPIOD2 if the common port<<4 | pin scheme is used.
void board_gpio_init_hypothesis(void)
{
    // Probably configures one or more board lines used for limit/enable/IO.
}

// 0x08009394
// Copies a small template buffer into TX storage, appends CRC, sends 8 bytes.
// The source length is table-driven in the firmware.
void send_template_response_8(const uint8_t *src, uint8_t *tx8, uint8_t n)
{
    for (uint8_t i = 0; i < n; i++) {
        tx8[i] = src[i];
    }
    uint16_t crc = crc16_modbus(tx8, n);
    tx8[n + 0] = (uint8_t)(crc >> 8);
    tx8[n + 1] = (uint8_t)crc;
}

// 0x080093D0
// Another response builder: copies 6 bytes, computes CRC, transmits 8 bytes.
void send_template_response_6(const uint8_t *src6, uint8_t *tx8)
{
    for (int i = 0; i < 6; i++) {
        tx8[i] = src6[i];
    }
    uint16_t crc = crc16_modbus(tx8, 6);
    tx8[6] = (uint8_t)(crc >> 8);
    tx8[7] = (uint8_t)crc;
}

// 0x08008B34
uint16_t crc16_modbus(const uint8_t *buf, uint32_t len)
{
    uint16_t crc = 0xFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= buf[i];
        for (int b = 0; b < 8; b++) {
            if (crc & 1) {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

// Dispatcher skeleton: 0x08008FFC
// addr=0x01 is normal; addr=0xAA is a special maintenance path.
void protocol_dispatch(uint8_t addr, uint8_t func, uint16_t reg, uint16_t value,
                       StateBlock *st, RegMap *regs)
{
    if (addr == 0xAA) {
        // special maintenance/reset family
        // 0xAA 06 00 05 AA 55 -> reset/maintenance branch
        if (func == 0x06 && reg == 0x0005 && value == 0xAA55) {
            // jumps into the maintenance path at 0x08008ED8
        }
        return;
    }

    if (addr != 0x01) {
        return;
    }

    switch (func) {
    case 0x03:
        // Read holding registers from the register map.
        // Observed response format:
        //   01 03 bytecount data... crc_lo crc_hi
        // The code copies words out of regs into the TX buffer and appends CRC.
        return;

    case 0x06:
        // single write / control path
        // Most values are simply stored into the register map; motion happens later
        // in the periodic service loop.
        set_u16(&regs->bytes[reg * 2], value);

        // reg 0x0009: unlock / clear-state branch
        if (reg == 0x0009 && value == 0x1234) {
            // clears state[0x12] and a nearby latch
        }

        if (reg == 0x0005) {
            if (value == 0xAA11) {
                // special control value; live test showed no visible motion by itself.
                // Static disasm still suggests it seeds a motion branch.
            } else if (value == 0x22AA) {
                // special maintenance/reset-like value
            }
        }

        if (reg == 0x000B) {
            // direction/phase control register family.
            // The actual PWM action still comes from the periodic task.
        }
        return;

    case 0x10:
        // write multiple registers
        // This path appears to copy a burst of 16-bit words into the register map
        // and then return an acknowledgment frame.
        return;

    default:
        return;
    }
}

// 0x08008C30
// Periodic motion service. This is where the real motor action is decided.
void motion_service(StateBlock *st, RegMap *regs)
{
    uint8_t *s = st->bytes;
    uint8_t *r = regs->bytes;

    // Observed live: limit button press causes a brief motor pause while running.
    bool limit_raw = (r[0x0002 * 2] != 0) || (r[0x0002 * 2 + 1] != 0);
    bool limit_latched = (r[0x0018 * 2] != 0) || (r[0x0018 * 2 + 1] != 0);

    // Phase/state byte at +0x05
    uint8_t motion_state = s[0x05];

    // Timer/counter at +0x28
    uint32_t elapsed = *(uint32_t *)&s[0x28];

    // The firmware uses several config words from the register map.
    // Offsets below are inferred from the branches seen in 0x08008C30.
    // reg 0x0013 and 0x0014 appear to seed timing/threshold values.
    uint16_t cfg_a = u16(&r[0x26]);
    uint16_t cfg_b = u16(&r[0x28]);
    uint16_t target = u16(&r[0x18]);
    uint16_t ref    = u16(&r[0x20]);

    (void)cfg_a;
    (void)cfg_b;
    (void)target;
    (void)ref;

    if (motion_state == 1 || motion_state == 2) {
        // Running state.
        // The important practical behavior is:
        //   - if limit trips, output pauses briefly
        //   - signed speed determines direction
        if (limit_raw || limit_latched) {
            pwm_helper(PWM_MOTOR_ID, 0);
            return;
        }

        elapsed++;
        *(uint32_t *)&s[0x28] = elapsed;

        // The exact timing thresholds are inferred from branches.
        if (elapsed < 0x32) {
            // start/arming delay
            pwm_helper(PWM_MOTOR_ID, 0);
            return;
        }

        if (elapsed >= 0xC8) {
            // later phase: compute signed PWM and drive
            // direction is encoded by motion_state (1 vs 2) and register values.
            int8_t pwm = 0; // computed from a helper in the firmware
            if (motion_state != 1) {
                pwm = (int8_t)-pwm;
            }
            pwm_helper(PWM_MOTOR_ID, pwm);
            return;
        }

        // mid-phase: firmware often keeps output suppressed here
        pwm_helper(PWM_MOTOR_ID, 0);
        return;
    }

    // Idle / stop phase.
    pwm_helper(PWM_MOTOR_ID, 0);
}

/*
 * High-confidence takeaways:
 * - The motor is not commanded directly by a single write frame in all cases.
 * - The serial command writes registers; the periodic service loop turns those
 *   register values into PWM.
 * - Pressing the limit switch while moving briefly halts the motor, so the
 *   limit input is definitely wired into the motion service path.
 * - Several "commands" are actually register writes that seed later motion-state
 *   transitions rather than directly driving the motor.
 */
