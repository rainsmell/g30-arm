import argparse
import time
import serial
from safe_rs485_probe import probe_read


def main():
    ap = argparse.ArgumentParser(description='Repeated read-only limit/status sampling')
    ap.add_argument('--port', default='COM8')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--slaves', default='3,4,6')
    ap.add_argument('--rounds', type=int, default=5)
    ap.add_argument('--timeout', type=float, default=0.4)
    ap.add_argument('--delay', type=float, default=0.2)
    args = ap.parse_args()
    slaves = [int(x, 0) for x in args.slaves.split(',') if x.strip()]
    regs = [0x0001, 0x0002, 0x000B, 0x0017, 0x0018, 0x001F]
    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=1, timeout=args.timeout) as ser:
        for rnd in range(1, args.rounds + 1):
            print(f'round {rnd}:')
            for slave in slaves:
                vals=[]
                for reg in regs:
                    resp, err = probe_read(ser, slave, reg, 1, args.timeout, quiet=True)
                    if err:
                        vals.append(f'{reg:04X}=ERR')
                    else:
                        d=resp[3:-2]
                        v=(d[0]<<8)|d[1]
                        vals.append(f'{reg:04X}={v:04X}')
                    time.sleep(0.06)
                print(f'  slave {slave}: ' + ' '.join(vals))
            time.sleep(args.delay)

if __name__ == '__main__':
    main()
