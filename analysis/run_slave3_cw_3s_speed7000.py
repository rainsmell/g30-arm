import time
import serial

PORT = 'COM8'
BAUD = 115200
SLAVE = 3
TIMEOUT = 0.5
SPEED_OVERRIDE = 0x1B58  # 7000
RUN_SECONDS = 3.0


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def write10(reg: int, value: int) -> bytes:
    body = bytes([SLAVE, 0x10, reg >> 8, reg & 0xFF, 0, 1, 2,
                  value >> 8, value & 0xFF])
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def send(ser, frame: bytes, label: str) -> None:
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    rx = ser.read(8)
    print(f'{label} TX: {frame.hex(" ").upper()}')
    print(f'{label} RX: {rx.hex(" ").upper() if rx else "<none>"}')


speed = write10(0x001F, SPEED_OVERRIDE)
cw = write10(0x000B, 0x0000)
stop = write10(0x001F, 0x0001)

with serial.Serial(PORT, BAUD, bytesize=8, parity=serial.PARITY_NONE,
                   stopbits=1, timeout=TIMEOUT) as ser:
    try:
        send(ser, speed, 'SPEED_7000')
        time.sleep(0.12)
        send(ser, cw, 'CW_START_EXPAND')
        print(f'RUNNING: {RUN_SECONDS:.1f} seconds')
        time.sleep(RUN_SECONDS)
    finally:
        send(ser, stop, 'STOP_INHIBIT')
        print('DONE: reg 0x001F = 0x0001, PWM inhibit left active')
