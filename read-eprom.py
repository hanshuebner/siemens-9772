import time
from machine import Pin

# A0..A10 -> Pico GP numbers (LSB first)
ADDR_GPIO = [12, 11, 10, 9, 8, 7, 6, 5, 28, 27, 22]
# D0..D7 -> Pico GP numbers
DATA_GPIO = [13, 14, 15, 16, 17, 18, 19, 20]
OE_GPIO = 26
CE_GPIO = 21

EPROM_SIZE = 2048
# TMS2516-45 tACC max is 450 ns. 2 us gives ~4x margin and covers slower grades.
SETTLE_US = 2


def setup():
    addr = [Pin(n, Pin.OUT, value=0) for n in ADDR_GPIO]
    data = [Pin(n, Pin.IN, Pin.PULL_UP) for n in DATA_GPIO]
    oe = Pin(OE_GPIO, Pin.OUT, value=1)
    ce = Pin(CE_GPIO, Pin.OUT, value=1)
    return addr, data, oe, ce


def read_eprom(addr_pins, data_pins, oe, ce):
    buf = bytearray(EPROM_SIZE)
    ce.value(0)
    oe.value(0)
    time.sleep_us(SETTLE_US)
    for a in range(EPROM_SIZE):
        for i, p in enumerate(addr_pins):
            p.value((a >> i) & 1)
        time.sleep_us(SETTLE_US)
        b = 0
        for i, p in enumerate(data_pins):
            b |= p.value() << i
        buf[a] = b
    oe.value(1)
    ce.value(1)
    return buf


def emit_hex(data, bytes_per_line=16):
    for off in range(0, len(data), bytes_per_line):
        chunk = data[off:off + bytes_per_line]
        n = len(chunk)
        rec = bytes([n, (off >> 8) & 0xFF, off & 0xFF, 0x00]) + chunk
        chk = (-sum(rec)) & 0xFF
        print(':' + ''.join('{:02X}'.format(b) for b in rec) + '{:02X}'.format(chk))
    print(':00000001FF')


def main():
    pins = setup()
    while True:
        # input() blocks until the host has opened USB CDC and sent a line,
        # which satisfies both the "wait for USB" and "confirm with Enter" requirements.
        try:
            input('TMS2516 ready. Press Enter to read: ')
        except EOFError:
            time.sleep(1)
            continue
        data = read_eprom(*pins)
        emit_hex(data)


main()
