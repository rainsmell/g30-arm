import time
import serial

PORT = 'COM8'
BAUD = 115200
SLAVE = 3
TIMEOUT = 0.5


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def frame_write10(reg: int, value: int) -> bytes:
    body = bytes([SLAVE, 0x10, reg >> 8, reg & 0xFF, 0, 1, 2,
                  value >> 8, value & 0xFF])
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def send(ser, frame: bytes, label: str) -> None:
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    response = ser.read(8)
    print(f'{label} TX: {frame.hex(" ").upper()}')
    print(f'{label} RX: {response.hex(" ").upper() if response else "<none>"}')


low_speed = frame_write10(0x001F, 0x1482)
ccw_start = frame_write10(0x000B, 0xAA11)
stop_inhibit = frame_write10(0x001F, 0x0001)

with serial.Serial(PORT, BAUD, bytesize=8, parity=serial.PARITY_NONE,
                   stopbits=1, timeout=TIMEOUT) as ser:
    try:
        send(ser, low_speed, 'LOW_SPEED')
        time.sleep(0.12)
        send(ser, ccw_start, 'CCW_START')
        print('RUNNING: 5.0 seconds')
        time.sleep(5.0)
    finally:
        send(ser, stop_inhibit, 'STOP_INHIBIT')
        print('DONE: PWM inhibit left active (reg 0x001F = 0x0001)')
