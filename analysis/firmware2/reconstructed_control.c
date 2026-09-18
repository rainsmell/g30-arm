/* Evidence-led pseudocode for gd32_firmware_2.bin, not flashable source.
 * Function addresses refer to this binary only. Names are analyst-assigned.
 * Partial reconstruction: omitted protection/FOC paths are explicitly marked.
 */
#include <stdint.h>

#define U8(a)  (*(volatile uint8_t *)(uintptr_t)(a))
#define U16(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define S16(a) (*(volatile int16_t *)(uintptr_t)(a))
#define S32(a) (*(volatile int32_t *)(uintptr_t)(a))
#define F32(a) (*(volatile float *)(uintptr_t)(a))
#define BANK_BASE 0x2000063Cu
#define BANK_BYTES 0x48u
#define REG(s, r) U16(BANK_BASE + ((s)-1u)*BANK_BYTES + 2u*(r))
#define FLAG(s, r) U8(0x200007ECu + ((s)-1u)*0x24u + (r))
#define HALL 0x200002C8u
#define CONTROL 0x20000368u

/* 0x0800E808, 0x0800E8AE, 0x0800E8D0: reached after CRC validation.
 * Address 1,4,5,6 requests are rejected by the dispatcher in this build.
 * Original implementation lacks a bank-boundary check: never emulate that
 * in a host driver. Valid per-bank range is 0x00..0x23.
 */
void write_decoded_word(uint8_t slave, uint16_t reg, uint16_t value) {
    if ((slave != 2 && slave != 3) || reg >= 0x24) return;
    REG(slave, reg) = value;
    FLAG(slave, reg) = 1;
}

/* 0x0800C484..0x0800C4B8. The position is internally 32-bit; the wire
 * exposes only its low 16 bits at register 1. Register 0 is independent.
 */
void publish_slave3_feedback(void) {
    REG(3, 0) = U16(HALL + 0x20);
    REG(3, 1) = (uint16_t)S32(HALL + 0x14);
    REG(3, 2) = U16(0x200001BCu + 0xDC);
    REG(3, 3) = (uint16_t)(uint32_t)F32(0x20000174u + 0x44);
    REG(3, 4) = U16(0x200001BCu + 0xDC);
    REG(3, 0x17) = U16(0x200002BCu + 2);
    REG(3, 0x18) = U8(0x20000110u + 1);
}

/* 0x08008C6C reads PB6,PB7,PB8 as a three-bit state.
 * 0x08008A3A uses a six-state transition-order lookup to choose +/-1.
 * 0x0800B858..0x0800B860 accumulates that direction in a 32-bit word.
 * Invalid transitions take an error-count path not reproduced here.
 */
void valid_hall_edge(int8_t direction) {
    S32(HALL + 0x14) += direction;
}

/* 0x0800E9CA; caller at 0x0800B1DC..0x0800B1F2. */
int32_t approach_target(int32_t current, int32_t target, int16_t step) {
    if (current < target) {
        current += step;
        if (current >= target) current = target;
    } else if (current > target) {
        current -= step;
        if (current <= target) current = target;
    }
    return current;
}

void position_loop_reference_update(void) {
    S32(CONTROL + 0x14) = approach_target(
        S32(CONTROL + 0x14), S32(CONTROL + 0x9C), S16(CONTROL + 0x2C));
    S32(CONTROL + 0x7C) = S32(CONTROL + 0x14) - S32(HALL + 0x14);
    /* Remaining loop: deadband, gain, speed-envelope limit, speed PI,
     * current regulation and three-phase PWM. See 0x0800B1F4 onward. */
}

extern void phase_outputs_disable(void); /* 0x0800C1C0 */
extern void phase_outputs_enable(void);  /* 0x0800C208 */
extern void reset_control_terms(void);   /* 0x0800C954 */

/* Selected ordinary-control branches of 0x0800C250.
 * Excludes fault clear, homing (reg5=AA11), and test (reg5=A5A5).
 * Do not assume simultaneous conflicting writes have safe precedence.
 */
void slave3_normal_command_paths(void) {
    if (REG(3, 8) == 0 && FLAG(3, 8) == 1) { /* 0x0800C2D0 */
        FLAG(3, 8) = 0;
        phase_outputs_disable();
        S32(CONTROL + 0x9C) = S32(HALL + 0x14);
        REG(3, 5) = 0;
        REG(3, 0xB) = (uint16_t)S32(HALL + 0x14);
        REG(3, 0xC) = 0;
    } else if (REG(3, 8) == 1 && FLAG(3, 8) == 1) {
        /* The inspected enable branch does not clear FLAG(3,8). */
        phase_outputs_enable();
        REG(3, 8) = 1;
    }
    if (FLAG(3, 0xB) == 1) { /* 0x0800C3DC */
        FLAG(3, 0xB) = 0;
        phase_outputs_enable();
        REG(3, 8) = 1;
        if (REG(3, 7) == 1) {
            U8(0x200002BDu) = 1;
            S32(CONTROL + 0x9C) = (int16_t)REG(3, 0xB);
        }
    }
    if (FLAG(3, 0xA) == 1) { /* 0x0800C3FA */
        FLAG(3, 0xA) = 0;
        int16_t speed = (int16_t)REG(3, 0xA);
        if (speed < 0) speed = (int16_t)-speed;
        S16(0x20000116u) = speed;
        if (speed != 0) S32(0x20000128u) = speed;
        S32(CONTROL + 0x20) = S16(0x20000128u); /* 0x0800D260 */
    }
    if (REG(3, 7) == 2) { /* 0x0800C42C: continuous speed mode */
        phase_outputs_enable();
        REG(3, 8) = 1;
        U8(0x200002BDu) = 2;
        S16(CONTROL + 0x96) = (int16_t)REG(3, 0xA);
    }
    if (REG(3, 8) == 0) { /* 0x0800C448 */
        reset_control_terms();
        S32(CONTROL + 0x9C) = S32(HALL + 0x14);
        S32(CONTROL + 0x14) = S32(HALL + 0x14);
    }
}

/* 0x0800C484 tail: reg13 is scaled by 1000; reg14 by 16.
 * Physical meanings require deeper current-loop tracing. */
void slave3_limit_parameters(void) {
    F32(0x200001BCu + 0xE8) = REG(3, 0x13) ? REG(3, 0x13) / 1000.0f : 1.5f;
    U16(0x200001BCu + 0xEC) = REG(3, 0x14) ? (uint16_t)(REG(3, 0x14) << 4) : 16000;
}
