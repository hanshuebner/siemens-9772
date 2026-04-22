# Siemens 9772

Reverse-engineering notes, EPROM dumps and a Python protocol exerciser
for the Siemens 9772 — a small, dot-matrix CRT slave terminal from the
early 1980s, designed for industrial / process-control multi-drop
networks rather than for general-purpose host attachment.

The full technical analysis lives in
[`siemens-9772-report.md`](siemens-9772-report.md). This README is the
quick orientation.

## The terminal

- Display: 40-column dot-matrix CRT. The number of rows is set by two
  DIP switches at boot to **2, 6 or 12 rows** (80 / 240 / 480 character
  cells total).
- Character cell: 5×7 dot matrix, with an always-on hardware underline
  on the unused 8th scan line.
- Character set: 7-bit, mostly standard ASCII. A hardware address-remap
  on the display board redirects the codes `0x5B..0x5E` and `0x7B..0x7E`
  to either the German DIN 66003 set (`Ä Ö Ü ^ ä ö ü ß`) or the original
  ASCII brackets / curly braces (`[ \ ] ^ { | } ~`), selected by a board
  strap on chargen pin A5.
- CPU: Intel **8035** (MCS-48, ROMless), 5.0688 MHz crystal.
- Program ROM: 2 KiB TMS2516 EPROM (only 1 KiB unique, mirrored).
- USART: Signetics **SCN2661B**.
- Three physical ports on the back: one DTE male DE9 (upstream — the
  SCN2661 driving an RS-422 / V.11 line transceiver), and two female
  DE9 downstream ports for daisy-chaining further slaves.

### Hidden power switch

There is a small toggle switch tucked behind the IEC mains inlet on the
rear of the case. Flipping it down keeps the terminal powered on
permanently (independent of any front-panel button).  The terminal can
also be switched on by a signal on the serial port, but I have not figured
out how that works.

## Hardware interface

The upstream port is electrically **RS-422 / V.11 differential**,
following Siemens's *SS97* multi-drop convention. The connector carries
the two differential pairs (TxD±, RxD±) and signal ground only — there
are no modem-control handshake lines on the cable, and the firmware
does not poll DSR / DCD / CTS. The receiver is enabled unconditionally
by the SCN2661 init.

### Serial line settings

| Parameter | Value |
|---|---|
| Baud rate | **19200** |
| Data bits | 8 |
| Parity | **odd** |
| Stop bits | 1 |
| Frame | 1 start + 8 data + 1 parity + 1 stop = 11 bit-times |

The SCN2661B's BRG synthesises 19200 from the 5.0688 MHz BRCLK by
alternating /16 and /17 dividers (average 16.5 → 19200 exactly). Scope
the TxD pin: one bit time is 52 µs.

To talk to the terminal you need an RS-422/V.11 USB adapter (any
half-decent FTDI / Silicon Labs one with a 4-wire mode), wired with the
usual crossover:

```
dongle TxD+ -> terminal RxD+ (A)
dongle TxD- -> terminal RxD- (B)
dongle RxD+ <- terminal TxD+ (A)
dongle RxD- <- terminal TxD- (B)
                  + signal ground
```

For a short bench lash-up no termination is needed; if you see
corrupted bytes, drop ~120 Ω across the receive pair at the far end.

## The protocol

Two-state stream protocol, all bytes 7-bit ASCII:

| State | Trigger | Behaviour |
|---|---|---|
| Default | (initial) | Looks for `STX`, `DC1`, `DC3`, `DC4`; everything else is dropped. |
| Text | `STX` (0x02) | Plain bytes are written at the cursor and advance it; `DC3 <byte>` does an in-line cursor move; `ETX` (0x03) returns to default. |

### Single-byte / two-byte commands

| Sequence | Effect |
|---|---|
| `DC1 <byte>` | Write one character at the cursor and advance. |
| `DC4 <byte>` | Slave-poll: terminal echoes `0x14 <byte>` back. |
| `DC3 0x08` | Status query: terminal transmits `0x12 0x20`. |
| `DC3 0x09` | Address query: terminal transmits `0x12 <DIP-address>`. |
| `DC3 0xC0` | Fill whole display with `0x00` (every cell hidden). |
| `DC3 0xC1` | Fill whole display with `0x20` (clear to spaces). |
| `DC3 0xC2` | Bit-7-clear every cell on every bank, fill bank 0 with `0x00`. |
| `DC3 0xC3` | Status report: terminal transmits `0x12 0x80`. |
| `DC3 0xC4` | Clear current row to `0x00`, cursor to column 0. |
| `DC3 0xC5` | Clear current row to `0x20`, cursor to column 0. |
| `DC3 0xC6` | Start cursor blink (P1.0). |
| `DC3 0xC7` | Start alt-blink (P1.1). |
| `DC3 0xC8` | Stop blinking. |

### Cursor positioning

Inside a `STX ... ETX` block, `DC3 <pos>` moves the cursor.

- `<pos> = 0x80 + row` — set row (0..15, capped by the DIP-selected
  display-size limit).
- `<pos> = 0x30 + col` — set column (0..39).

After reset the cursor is at row 0 / column 0 (upper-left).

### Power-on greeting

The firmware transmits **`0x12 0x90`** on the serial line as the very
first thing after a successful power-on self-test. Listening for those
two bytes is the cheapest possible "is RX wired correctly?" check; see
`exercise.py --listen-hello`.

## The analysis process

The work proceeded in this order:

1. **Read both EPROMs** (program + chargen) using a custom EPROM reading
   rig based on a Rasperry Pi Pico running MicroPython
   (see [read-eprom.py](./read-eprom.py)).
2. **Wrote a small MCS-48 disassembler** (`dis8048.py`) — flow-following,
   following the reset, external-INT and timer vectors and resolving
   `JMPP @A` jump tables. Output lives in `disasm48.txt`.
3. **Decoded the SCN2661 init sequence** at `0x0082..0x00A3` from the
   disassembly: MR1 = 0x5E, MR2 = 0x3E, CR = 0xB7 (loopback self-test
   with `0x55`) → CR = 0x15 (normal ops). This gives 19200 8O1.
4. **Rendered the chargen** (`render_chargen.py` → `siemens-9772-chargen.png`)
   to confirm what each code-point displays. The upper page (codes
   0x80..0xFF) holds the German letters and the displaced ASCII
   brackets, reached via the hardware address-remap on chargen pin A5.
5. **Walked the protocol state machine** at `0x011B` and built a full
   table of `DC3` escape codes, including the four "report" paths that
   make the terminal transmit (`0x12 0x90 / 0x80 / 0x20 / <addr>`).
6. **Verified on the real hardware**: BRCLK on the SCN2661 reads
   5.0688 MHz; bit-time on TxD reads ~50 µs (19200 baud); the power-on
   `0x12 0x90` greeting comes out as predicted.

## The exercise script

[`exercise.py`](exercise.py) is the bench tool. It opens the serial
port at 19200 8O1 and exposes the protocol as method calls on a
`Terminal` class, plus a handful of demos.

```
python3 exercise.py /dev/ttyUSB0 --listen-hello   # block until the terminal
                                                  # transmits 0x12 0x90
                                                  # (reset it after launching)

python3 exercise.py /dev/ttyUSB0 --demo bringup   # smallest possible
                                                  # "is the wiring right?"
                                                  # check: clear, write
                                                  # "HELLO 9772", query addr

python3 exercise.py /dev/ttyUSB0 --demo classic   # CLASSIC COMPUTING in a
                                                  # 3x5 block-letter font
python3 exercise.py /dev/ttyUSB0 --demo chargen   # all printable codes
python3 exercise.py /dev/ttyUSB0 --demo umlaut    # which way the MODE
                                                  # strap is set
python3 exercise.py /dev/ttyUSB0 --demo positions # walk one char across
                                                  # all 40 columns
python3 exercise.py /dev/ttyUSB0 --demo blink     # cursor / alt blink
python3 exercise.py /dev/ttyUSB0 --demo single    # DC1 one-char-at-cursor
python3 exercise.py /dev/ttyUSB0 --demo ping      # DC4 echo probes
python3 exercise.py /dev/ttyUSB0 --demo status    # all three response paths

python3 exercise.py /dev/ttyUSB0                  # = --demo cycle, runs
                                                  # everything in sequence
```

Recommended first-contact order on a unit that's never been talked to:
`--listen-hello` (power-cycle the terminal; you should see
`12 90`), then `--demo bringup`, then `--demo classic`.

If `--listen-hello` returns something other than `0x12 0x90`, the most
likely problems in order are: wrong baud (re-check 19200 8O1), wrong
parity (the firmware demands odd; 8N1 hosts will trigger framing errors
and the firmware will silently drop bytes), or the differential pair
swapped.

## Files

| File | Purpose |
|---|---|
| `siemens-9772-report.md` | Full technical analysis. |
| `siemens-9772.hex` / `.bin` | Program EPROM (corrected re-read). |
| `siemens-9772-O03-F01-C11802.bin` | Original (corrupted) program EPROM read, kept for reference. |
| `siemens-9772-chargen.hex` / `.bin` | Character-generator EPROM. |
| `siemens-9772-chargen.png` | Annotated render of all 256 chargen cells (4×4 PNG pixels per chargen pixel). |
| `siemens-9772-chargen-plain.png` | Same render without overlay. |
| `dis8048.py` | MCS-48 disassembler used for the analysis. |
| `disasm48-v2.txt` | Disassembly of the corrected dump. |
| `disasm48.txt` | Disassembly of the original (bad) dump, kept for diff. |
| `render_chargen.py` | Generates the chargen PNGs. |
| `read-eprom.py` | TL866-side script that produced the dumps. |
| `exercise.py` | Protocol exerciser / demo script. |
