# Siemens 9772 Terminal — Firmware Analysis Report

Subject: Reverse-engineering of `siemens-9772.hex` (2 KiB TMS2516 EPROM dump,
re-read after the original dump was found to contain hundreds of bit errors).
Goal: gather enough information about the terminal's hardware and firmware to
write a replacement program that can receive async serial data and display it.

---

## 1. Executive summary

The ROM contains firmware for an **Intel 8035** (MCS-48, ROMless) running out
of a 2 KiB external EPROM. The 2 KiB EPROM is in fact a **1 KiB program
mirrored twice** (only five bytes differ between the low and high halves and
all five are isolated single-bit flips, almost certainly residual read flake
rather than meaningful content).

The terminal is driven by an **SCN2661** USART and a TTL-based CRT
controller. The firmware:

- runs a power-on ROM checksum, internal-RAM walking-bit test, external-RAM
  test (across 16 P2-selected memory banks), and a SCN2661 internal
  loopback test;
- programs the SCN2661 to **19200 baud, async 16× oversampling, 8 data
  bits, odd parity, 1 stop bit** (1 start + 8 data + 1 parity + 1 stop =
  11 bit times per character) using the chip's internal baud-rate
  generator. With the 5.0688 MHz system crystal the BRG synthesises
  19200 by alternating /16 and /17 dividers (5.0688 MHz / 16.5 / 16 =
  19200 exactly on average); confirmed against scope measurements of
  ~50 µs/bit on TxD;
- reads two DIP-switch bits on P1.4 and P1.5 to choose one of three
  display-size / multi-node modes;
- runs a small protocol state machine that recognises STX, ETX, DC1, DC3
  and DC4 as command introducers;
- can both **receive** characters into display memory and **transmit**
  them back to the host (DC4 is a "report" / response command), using T0
  as a TxRDY handshake to the SCN2661.

There is therefore a serial-line protocol with a clear multi-drop flavour:
the master sends a DC4 + payload, the addressed slave echoes it back; the
master can also query a slave's address explicitly with `DC3 0x09` to which
each terminal answers with one of `0x01`/`0x02`/`0x04` (encoded by the same
DIP switches that pick the display size). Display data uses STX as
start-of-frame and ETX as end-of-frame.

Replacing the firmware is straightforward: keep the SCN2661 init constants
the original code uses (or change them), and write to the same external
addresses for display memory.

---

## 2. Hardware identification

| Item | Value / evidence |
|------|-----------------|
| CPU | Intel 8035 (MCS-48, ROMless). Reset vector at 0x0000 is `04 1C` = `JMP 001Ch`; external-INT vector at 0x0003 is `JMP 033Ah`; timer/counter ISR is inline at 0x0007 ending in `RETR`. |
| System clock | 5.0688 MHz crystal (per user — feeds the 8035 and probably also the SCN2661 BRG). |
| Program ROM | 2 KiB EPROM, but only 1 KiB of unique content (mirrored). A11 is unused. |
| RAM (internal) | 64 bytes of 8035 internal RAM, two register banks. ISR uses `SEL RB1`. |
| RAM (external) | Display memory + ring buffer for received characters; addressed via `MOVX` with the upper nibble of P2 used as a bank-select. |
| USART | SCN2661 at external addresses 0x80/0x84/0x88/0x8C. |
| Port expander | 8243-class on P2 low nibble: `MOVD P5,A`, `ANLD P5,A`, `MOVD P7,A`, `ORLD P7,A` are used in a few code paths (probably display attribute / row drivers). |
| Three physical ports | One DTE male DE9 upstream (the SCN2661), two female DE9 downstream (almost certainly bit-banged via P1 and the 8243; not yet fully traced). |

---

## 3. SCN2661 configuration (the headline result)

The fresh dump contains a clean three-step init sequence that the previous
corrupted dump had completely garbled:

```
0082: B8 88     MOV  R0,#88h       ; mode-register address
0084: 23 5E     MOV  A,#5Eh
0086: 90        MOVX @R0,A         ; MR1 = 0x5E
0087: 23 3E     MOV  A,#3Eh        ; (chip auto-toggles to MR2 on second access)
0089: 90        MOVX @R0,A         ; MR2 = 0x3E
008A: B8 8C     MOV  R0,#8Ch       ; command-register address
008C: 23 B7     MOV  A,#B7h
008E: 90        MOVX @R0,A         ; CR = 0xB7  (LOCAL LOOPBACK + TxEN+RxEN+DTR+RTS)
008F: B8 80     MOV  R0,#80h
0091: 23 55     MOV  A,#55h
0093: 90        MOVX @R0,A         ; transmit 0x55 (test pattern)
0094: 86 98     JNI  0098h         ; wait for /INT (RxRDY in loopback)
0096: 04 94     JMP  0094h
0098: 80        MOVX A,@R0         ; read RHR
0099: D3 55     XRL  A,#55h        ; equal?
009B: C6 9F     JZ   009Fh         ; yes -> continue
009D: 04 9D     JMP  009Dh         ; no -> hang here
009F: B8 8C     MOV  R0,#8Ch
00A1: 23 15     MOV  A,#15h
00A3: 90        MOVX @R0,A         ; CR = 0x15  (normal ops, TxEN+RxEN, clear errors)
```

Decoding the constants against a Signetics SCN2661 datasheet:

### MR1 = 0x5E = `0101_1110`

The SCN2661 datasheet defines the MR1 fields as (high to low):

| Bits | Field | Value | Meaning |
|------|-------|-------|---------|
| D7 D6 | Stop bits | `01` | **1 stop bit** |
| D5 | Parity type | `0` | **odd** |
| D4 | Parity enable | `1` | **enabled** |
| D3 D2 | Character length | `11` | **8 data bits** |
| D1 D0 | Mode / baud factor | `10` | **async, 16× oversampling** |

Total frame: 1 start + 8 data + 1 parity + 1 stop = **11 bit times per
character** = standard **8O1**.

Important consequence: the 8 data bits carry the full byte that the
firmware reads, including the high bit. Bit 7 of an incoming byte is
**software-level addressing / control** (the SCN2661 strips the parity
bit before the data hits the CPU), and the firmware uses bit 7 to
distinguish address / control bytes from displayable data — every byte
with bit 7 set that arrives in the display path is forced to `0x7F`
(DEL) by the firmware before the cell write. Display memory therefore
only ever holds 7-bit codes, and the chargen address is the same 7-bit
code; the umlauts and the relocated ASCII brackets / curly braces in
the upper half of the chargen are reached not by 8-bit codes but by a
hardware address-remap on the display board (see §11).

### MR2 = 0x3E = `0011_1110`

| Bits | Field | Value | Meaning |
|------|-------|-------|---------|
| D3:D0 | BRG baud-rate code | `1110` (=14) | **19200 baud** in the SCN2661B BRG table |
| D4 | Tx clock select | `1` | from internal BRG |
| D5 | Rx clock select | `1` | from internal BRG |
| D7:D6 | reserved | `00` | — |

So the chip runs its **internal BRG at 19200 baud** and uses that clock
both for transmit and for receive. With 16× oversampling the BRG drives
RxC/TxC at 19200 × 16 = **307.2 kHz** nominal. With the 5.0688 MHz system
crystal the divider needed is 5.0688 MHz / 307.2 kHz = 16.5 — not an
integer, so the SCN2661B BRG synthesises it by alternating between /16
and /17. The instantaneous TxC/RxC half-period therefore jitters by ~6%
between two values; the average is exact and the receiver's 16×
oversampling absorbs the jitter trivially.

Easiest verification: **scope TxD on the SCN2661 and measure the bit
width** — at 19200 baud one bit is 52 µs (a "~50 µs" reading on a
typical scope). The TxC/RxC pins should average 307.2 kHz but will show
two alternating half-cycle widths (≈3.16 µs and ≈3.36 µs) due to the
fractional divider; that two-valued waveform is normal and is the
fingerprint of a 19200-from-5.0688-MHz BRG configuration.

**Earlier versions of this report claimed 9600 baud** based on a wrong
mapping of code 14 against the older 2651 baud table. The 2661B table
doubles the rates in the upper half (code 12 = 7200, code 13 = 9600,
code 14 = 19200, code 15 = 38400). Bit-width measurement on the actual
hardware put it beyond doubt: this terminal is **19200 8O1**.

### CR after self-test: 0x15 = `0001_0101`

| Bits | Field | Value | Meaning |
|------|-------|-------|---------|
| D0 | TxEN | `1` | transmitter enabled |
| D1 | DTR | `0` | DTR off |
| D2 | RxEN | `1` | receiver enabled |
| D3 | Force break | `0` | — |
| D4 | Reset error | `1` | clear error flags |
| D5 | RTS | `0` | RTS off |
| D7:D6 | Operating mode | `00` | normal operation |

DTR and RTS are deliberately deasserted; the design uses **T0 as TxRDY
handshake** instead of the modem-control lines.

---

## 4. Reset / self-test sequence

1. **Reset** at 0x0000 → `JMP 0x001C`.
2. **ROM checksum** loops over pages 0, 1, 3 with a rotate-XOR accumulator
   in R1. After page 3 the accumulator is XOR'd with the byte at offset
   0x0002 (which is 0x28 in the low half, 0xAC in the high half). On
   mismatch the code jumps to `0x009D` (a `JMP $` deliberate hang).
3. **Internal-RAM walking-bit test** at 0x0031: writes RAM[0x01..0x3F]
   with the address itself, then with each rotate-shift of pattern 0x01;
   any mismatch hangs at 0x009D.
4. **External-RAM test** at 0x0061: walks all 16 P2-upper-nibble banks
   (`IN P2`, `ADD #0x10`, `OUTL P2`) and within each bank tests the 64
   addresses `R0 = 0..0x3F` (the loop bails when R0 bit 6 goes high so
   the SCN2661 window at 0x80+ is **not** clobbered).
5. **SCN2661 LOCAL LOOPBACK self-test** as shown in §3.
6. Final `OUTL P1, #0xF7` (initial P1 value), `MOV @R0,#0Fh` to
   `RAM[0x17]` (initial state-machine state = 0x0F), then DIP-switch
   read on P1.4 / P1.5 to choose timer-reload and display-size limits
   (see §5).
7. **Clear display** via `CALL 0x0256` (writes `0x00` to every cell
   across all banks). At the end of this routine `R1` is left at `0x00`
   and `P2 upper = 0`, then bit 7 of the cell at that position is set
   so the cursor appears at **row 0, column 0 — upper-left corner**.
8. **Power-on "hello"**: `MOV R6,#90h; CALL 0x02AD` transmits
   `0x12 0x90` on the serial line. Any listening host (or logic
   analyser) sees that pair of bytes as the first traffic after reset
   — handy sanity check that RX wiring / baud are correct.
9. Enable interrupts (`EN I`, then `EN TCNTI` after one synthetic
   counter-ISR call) and fall into the main loop at 0x00D2.

The "screen full of dots" you see on a freshly-booted terminal is
exactly this: chargen code `0x00` is a single-pixel glyph in the
middle of the 5×7 cell, so every cleared cell shows a dot. The
hardware underline (see §11) is present under every cell regardless.
The one blinking cell is `(row 0, col 0)`.

---

## 5. Hardware-strap (DIP-switch) reads at start-up

```
00AE: 09        IN A,P1
00AF: B1 E5     MOV @R1,#E5h     ; default RAM[0x14]
00B1: B0 20     MOV @R0,#20h     ; default RAM[0x16]
00B3: B2 BD     JB5 00BDh        ; if P1.5 set, keep defaults
00B5: B1 F2     MOV @R1,#F2h     ; else RAM[0x14] = 0xF2
00B7: B0 C0     MOV @R0,#C0h     ;      RAM[0x16] = 0xC0
00B9: 92 BD     JB4 00BDh        ; if P1.4 set, keep those values
00BB: B0 60     MOV @R0,#60h     ; else RAM[0x16] = 0x60
```

So two switch bits select one of three operating modes:

| P1.5 | P1.4 | RAM[0x14] (timer reload) | RAM[0x16] (display-bank limit) |
|------|------|---------------------------|--------------------------------|
| 1 | x | 0xE5 | 0x20 |
| 0 | 1 | 0xF2 | 0xC0 |
| 0 | 0 | 0xF2 | 0x60 |

`RAM[0x16]` is the upper-nibble cap for the display-bank-advance helper at
0x0393 (`IN P2 → ADD #0x10 → wrap when nibble matches RAM[0x16]`). With a
40-cell-wide row addressed by `R1 = 0..0x3F`, the three modes correspond
to **2, 6 or 12 display rows** (for 80, 240 or 480 character cells total).

`RAM[0x14]` is loaded into the 8035 timer/counter (`MOV T,A`) by the
counter ISR on each tick; together with the T1 input clock it sets the
interrupt frequency, which drives cursor blink.

---

## 6. Main loop and ring buffer

```
00D2: MOV  R0,#19h
00D4: MOV  A,@R0       ; A = RAM[0x19] (write head, set by RX ISR)
00D5: DEC  R0          ; R0 = 0x18
00D6: XRL  A,@R0       ; A ^= RAM[0x18] (read tail)
00D7: JZ   00D2h       ; ring empty -> idle
00D9: STOP TCNT
00DA: IN   A,P2        ; save P2
00DB: MOV  R3,A
00DC: INC  @R0         ; advance read tail
00DD: MOV  A,@R0
00DE-00E2: rotate twice + ORL #0xC0 + OUTL P2  ; select display bank for next access
00E3: MOV  A,@R0
00E4: ANL  A,#3Fh
00E6: MOV  R0,A        ; R0 = ring index modulo 64
00E7: MOVX A,@R0       ; pull next received char from ring
00E8: MOV  R2,A        ; R2 = char (now in `received-character` register convention)
00E9: MOV  A,R3
00EA: OUTL P2,A        ; restore P2
00EB: CALL 010Ah       ; dispatch
00ED: STRT CNT
00EE: JMP  00D2h
```

So the system uses a **64-byte ring buffer in external RAM** (low 0x40),
with `RAM[0x18]` as read tail and `RAM[0x19]` as write head. The RX ISR
appends, the main loop drains. The dispatcher (`CALL 010Ah`) is the same
state machine that the previous report described; the cleaned-up dump
just lets us read the state-handler code reliably.

---

## 7. Receive ISR (0x033A)

```
SEL  RB1
MOV  R7,A; MOV A,R0; MOV R2,A    ; save A and R0 in bank 1
MOV  R0,#80h
MOVX A,@R0                       ; A = received byte
MOV  R4,A
MOV  R0,#84h
MOVX A,@R0                       ; A = status
ANL  A,#38h                      ; mask FE | OE | PE
JZ   continue
   MOV R0,#8Ch
   MOV A,#15h
   MOVX @R0,A                    ; reset error flags via CR
   MOV R4,#0FFh                  ; mark byte invalid
continue:
MOV  A,R1                        ; R1 = ring write head
INC  A
XRL  A,R2                        ; about to overwrite the read tail?
JZ   skip                        ; yes -> drop the byte (full buffer)
INC  R1
IN   A,P2                        ; save P2
MOV  R3,A
MOV  A,R1
ANL  A,#3Fh
MOV  R0,A                        ; ring index
MOV  A,R1
RR A; RR A; ORL A,#0C0h
OUTL P2,A                        ; select RX-buffer bank in P2 upper nibble
MOV  A,R4
MOVX @R0,A                       ; store byte in ring
MOV  A,R3
OUTL P2,A                        ; restore P2
skip:
MOV  A,R2
MOV  R0,A                        ; restore R0
MOV  A,R7
RETR
```

That's the standard "single-producer-from-ISR / single-consumer-from-main"
pattern with overflow drop.

---

## 8. Protocol state machine

State held in `RAM[0x17]`. Initialised to `0x0F`. Dispatched via
`JMPP @A` at 0x010E using a 16-byte table at 0x010F:

```
010F:  1B 80 62 4E 15 17 44 20 54 20 24 46 FA F2 38 D3
         ^state 0x0F              ^state 0x14    ^state 0x18
        0x10: 0x80 -> 0x0180   0x14: 0x15 -> 0x0115  ...
```

The active states and their handlers (low-byte-of-PC after `JMPP`):

| State | Entered after | Handler | What it does |
|-------|--------------|---------|--------------|
| 0x0F | reset / ETX / end-of-command | 0x011B | look for STX, DC1, DC3, DC4 in the received byte; everything else is silently discarded. |
| 0x10 | DC4 (0x14) | 0x0180 | wait for `T0` low (= SCN2661 TxRDY), transmit `0x14`, wait for `T0` low again, transmit the received byte. **Slave-poll response path.** Returns to 0x0F. |
| 0x11 | STX (0x02) | 0x0162 | text mode: ETX returns to 0x0F; DC3 transitions to 0x14; otherwise display the byte at R1 (cursor) and advance, then stay in 0x11. |
| 0x12 | DC1 (0x11) | 0x014E | display the single next byte at R1, then return to 0x0F. |
| 0x13 | DC3 (0x13) in default mode | 0x0115 = `JMP 0x0220` | high-nibble dispatcher with the next received byte. Each leaf handler ends with `JMP 0x0133` so state ends back at 0x0F. |
| 0x14 | DC3 (0x13) in text mode | 0x0117 = `CALL 0x0220` then `JMP 0x0146` | high-nibble dispatcher (subroutine call); on return the trailer at 0x0146 sets the state back to 0x11 so text mode continues. |

Bytes with the high bit set are masked to `0x7F` (DEL) before being
written to display memory in the DC1 and STX paths. This is
**software-level filtering of multi-drop control bytes**, not a parity
strip — the SCN2661 already removes the parity bit. Any wire byte with
bit 7 set is treated as a control / address byte and is *not* allowed to
appear directly on the screen via these handlers; the firmware uses bit
7 of the display-memory byte separately, as a "visible / cursor" flag
that the timer ISR toggles for blink.

### DC3-escape command set (full)

The high-nibble dispatcher at 0x0220 (`MOV A,R2; ANL A,#0xF0; SWAP A;
JMPP @A` with table at 0x0200) routes the byte that follows DC3:

| `DC3 + byte` | Action |
|--------------|--------|
| `0x00..0x07`, `0x0A..0x0F` | reset state; no other side effect |
| `0x08` | transmit `0x12 0x20` back to host (status response with sub-code 0x20) |
| `0x09` | transmit `0x12 <id>` back to host, where `<id> = 0x01` (P1.5=1), `0x04` (P1.5=0,P1.4=1) or `0x02` (both 0). **Slave-address query.** |
| `0x10..0x2F` | reject (reset state) |
| `0x30..0x57` | move cursor to BCD column 0..39 within current row; the page-3 lookup at 0x0300 maps `'0'..'9',':'..'W'` to positions 0..39 |
| `0x58..0x7F` | reject |
| `0x80..0x8F` | select display row/bank: `OUTL P2, (byte<<4)`. The bank is range-checked against `RAM[0x16]` (the DIP-switch-set limit) |
| `0x90..0xBF` | reject |
| `0xC0` | fill current bank with `0x00` |
| `0xC1` | fill current bank with `0x20` (clear screen to spaces) |
| `0xC2` | walk every bank `0x10..RAM[0x16]` and clear bit 7 of every cell, then fill bank 0 with `0x00` (full screen "go invisible") |
| `0xC3` | transmit `0x12 0x80` back to host (status response with sub-code 0x80) |
| `0xC4` | clear current row to `0x00`, cursor home |
| `0xC5` | clear current row to `0x20`, cursor home |
| `0xC6` | start attribute-blink mode A: timer ISR toggles `P1.0` every tick |
| `0xC7` | start attribute-blink mode B: timer ISR toggles `P1.1` every tick |
| `0xC8` | stop attribute blink (helper at 0x029B clears the toggle mask in `RAM[0x15]` and freezes P1 in its initial post-self-test state: `P1.0=1`, `P1.1=0`) |
| `0xC9..0xFF` | reject (`ADD A,#0x37` carries; goes to reset-state path) |

So the user-visible protocol primitives are:

* `STX <chars...> ETX` — write a sequence of characters at the cursor.
* `STX ... DC3 <esc> ...` — issue an escape command in the middle of
  text (e.g. `STX DC3 <bank> DC3 <col> "Hello" ETX` to position then
  write).
* `DC1 <char>` — write one character at the cursor.
* `DC3 <esc>` (outside STX) — issue a one-shot escape command (clear,
  position, status, blink-mode, …) and stay in default mode.
* `DC4 <char>` — slave-poll: terminal echoes `0x14 <char>` back. Used
  by the master to ping/probe slaves on the RS-422/485 bus.

The bus-level multi-drop addressing falls out of three primitives in
combination: (1) the boot DIP-switch read sets per-slave behaviour, (2)
`DC3 0x09` lets the master query each slave's address bit (`0x01`,
`0x02`, or `0x04`), (3) `DC4 <byte>` lets the master probe a specific
slave and have it echo back. Three-slave bus, single-bit collision-free
addressing.

---

## 9. Cursor model

The firmware keeps cursor state in three places:

| State | Where | Encoding |
|-------|-------|----------|
| Column within the current row | `R1` register | BCD byte in `{0x00..0x09, 0x10..0x19, 0x20..0x29, 0x30..0x39}`. The high nibble is the tens digit, the low nibble is the ones digit; only these 40 of the 64 possible `R1` values map to real display cells (the gaps `0x0A-0x0F`, `0x1A-0x1F`, `0x2A-0x2F`, `0x3A-0x3F` are unused). |
| Current row / bank | P2 upper nibble | OUTL'd as `0x00`, `0x10`, `0x20`, … stepping by `0x10` up to `RAM[0x16]` (2, 6 or 12 rows depending on the DIP-switch mode). |
| Visible / cursor-marker | bit 7 of the display cell at `(P2-upper, R1)` | Set to 1 when the cursor is on that cell, cleared when the cursor moves off. The timer ISR toggles this bit every tick, producing the blink. |

Column 0 is the **leftmost** column; row 0 is the **topmost** row, so the
cursor starts at the upper-left after clear (confirmed on the real
hardware).

### Cursor-move primitives

1. **Absolute column** — `DC3 <0x30..0x57>` runs the wire byte through
   the page-3 BCD lookup at `0x0300` (`'0'..'9',':','..','W'` → BCD
   0..39) and stores the result in `R1`. The handler at `0x022A`
   clears bit 7 of the cell being left and sets bit 7 of the new cell.
2. **Absolute row / bank** — `DC3 <0x80..0x8F>` bounds-checks against
   `RAM[0x16]` and then `OUTL P2, (byte<<4)` to select the bank. The
   cell at the new `(P2, R1)` is marked visible.
3. **Auto-advance** — helpers `0x038B` and `0x0393` run after every
   character-write in STX mode and DC1 mode. `0x038B` does `INC R5;
   MOVP3 A,@A; MOV R1,A`, so `R5` walks 0→1→…→39 and `R1` follows the
   BCD table; on the fortieth call `MOVP3` returns `0` which (a) wraps
   `R5` back to 0 and (b) triggers `CALL 0x0393` to advance to the
   next bank. `0x0393` wraps `P2` when it reaches `RAM[0x16]`. So a
   long text run walks left-to-right, top-to-bottom, and wraps to the
   top-left at end-of-screen.
4. **Home-in-row** — `DC3 0xC4` clears the current row to `0x00` and
   puts the cursor at column 0. `DC3 0xC5` does the same with space
   fill.

### Cursor blink control

Bit 7 of the cell at the cursor position is always being XOR-toggled
by the timer ISR; the *visibility* of that toggle on the CRT is
determined by the two attribute bits `P1.0` and `P1.1` which the ISR
also XORs with `RAM[0x15]`:

- `DC3 0xC6` — start blink mode A, i.e. `RAM[0x15] = 0x01`, so `P1.0`
  toggles each tick.
- `DC3 0xC7` — start blink mode B, `RAM[0x15] = 0x02`, so `P1.1`
  toggles each tick.
- `DC3 0xC8` — stop: the helper at `0x029B` zeroes `RAM[0x15]` and
  forces `P1.0 = 1`, `P1.1 = 0` (the initial post-self-test state).

Exactly which of `P1.0` / `P1.1` is the global cursor-visible enable
to the CRT, and which is a secondary attribute (reverse / alt-blink /
bell LED), is not distinguishable from the firmware alone — the two
commands are symmetric in the code. A quick "send `DC3 0xC6`, see what
starts blinking; send `DC3 0xC7`, see what starts blinking" test on
the live hardware settles it.

---

## 10. Memory map (external, via `MOVX` with P2 upper nibble as bank)

| `MOVX` address | Meaning |
|----------------|---------|
| `R1 = 0x00..0x3F`, P2 upper = `0xC?` | RX ring buffer (64 bytes) |
| `R0` arbitrary, P2 upper = bank `0x00..RAM[0x16]` step `0x10` | display memory cells |
| `R0 = 0x80`, P2 = (any) | SCN2661 RHR/THR |
| `R0 = 0x84` | SCN2661 status register |
| `R0 = 0x88` | SCN2661 mode registers (MR1 then MR2 on consecutive accesses) |
| `R0 = 0x8C` | SCN2661 command register |

The SCN2661 is selected by CPU address bit A7; CPU A2/A3 drive the
USART's RS0/RS1.

---

## 11. What the corrected dump changed vs. the previous one

Compared to the earlier (corrupted) read, the new dump:

* **Halves are now 99.5 % identical** (only 5 isolated bytes differ, all
  single-bit flips that look like residual reader noise). The previous
  dump had hundreds of differing bytes between halves which led to a
  spurious "two co-existing variants" hypothesis.
* **Adds the entire SCN2661 init block** at 0x0082-0x00A3. This wasn't
  visible in the previous dump because the bytes were garbled into
  nonsense. The earlier report's claim that "MR1 / MR2 are never
  programmed" is incorrect — that was an artefact of the bad dump.
* **Adds the DIP-switch reads** at 0x00AE-0x00BB.
* **Adds the main loop** at 0x00D2-0x00EE (ring buffer drain).
* **Adds the slave-response transmit code** at 0x0183-0x018E (the DC4
  handler that proves the terminal does transmit). The previous report
  said "no write to THR found"; this is also wrong — there is one in the
  init self-test (0x0093) and one in the DC4 handler (0x0189).

---

## 12. Character generator and the bracket / umlaut hardware remap

The 2 KiB chargen EPROM stores 256 cells of 8 bytes each (5×7 glyph in
the first 5 column-bytes, 3 columns of inter-character spacing). Bit 7
of every chargen byte is unused — character cells are 7 pixels tall, so
the unused 8th scan-line per cell is what the **always-on hardware
underline** uses (no firmware involvement; see §6).

The chargen ROM is wired so that the **lower 128 entries** are the
straight ASCII glyph table (digits, letters, punctuation, with
`0x5B-0x5E` and `0x7B-0x7E` left as solid-block placeholders). The
**upper 128 entries** are reached by hardware address-rewrite on the
display board, not by 8-bit codes from the firmware:

| Wire byte | Address rewrite | Glyph (MODE = 0, German) | Glyph (MODE = 1, ASCII) |
|-----------|-----------------|--------------------------|------------------------|
| `0x5B` | → chargen `0x83` | **Ä** | `[` (chargen `0x87`) |
| `0x5C` | → chargen `0x80` | **Ö** | `\` (chargen `0x84`) |
| `0x5D` | → chargen `0x81` | **Ü** | `]` (chargen `0x85`) |
| `0x5E` | → chargen `0x82` | `^` | `^` (chargen `0x86`) |
| `0x7B` | → chargen `0xA3` | **ä** | `{` (chargen `0xA5`) |
| `0x7C` | → chargen `0xA0` | **ö** | `\|` (chargen `0xA4`) |
| `0x7D` | → chargen `0xA1` | **ü** | `}` (chargen `0xA7`) |
| `0x7E` | → chargen `0xA2` | **ß** | `~` (chargen `0xA6`) |
| `0x5F` | (no remap) | `_` | `_` |
| `0x7F` | (no remap) | solid block | solid block |

The TTL detect logic on the display board fires when the cell value
matches `(b6=1) AND (b4=1) AND (b3=1)` AND the lower three bits are
`011`, `100`, `101` or `110` — i.e. exactly the 8 codes in the table
above. When it fires, the chargen address bits are forced as follows:

| chargen line | normal source | rewritten value |
|--------------|---------------|-----------------|
| A10 (=cell b7) | 0 | **forced 1** |
| A9 (=cell b6) | 1 | **forced 0** |
| A8 (=cell b5) | 0 / 1 | **preserved** (this is what keeps the 0x5x → 0x80x and 0x7x → 0xA0x distinction) |
| A7 (=cell b4) | 1 | **forced 0** |
| A6 (=cell b3) | 1 | **forced 0** |
| A5 (=cell b2) | varies | **driven by the MODE strap** |
| A4, A3 | (cell b1, b0) | **preserved** |

The MODE strap on the display board therefore picks which of the two
upper-page subsets is shown for codes `0x5B-0x5E` / `0x7B-0x7E`.
Nothing in the firmware moves between displaying "Z" and displaying
"`{`" — the firmware just writes the 7-bit code; the hardware decides
which glyph appears.

This explains why the chargen has *both* the German DIN 66003 glyphs
(`Ä Ö Ü ^`, `ä ö ü ß`) and the relocated ASCII brackets / curly braces
(`\ ] ^ [`, `| { ~ }`) at *adjacent* upper-page positions — they are
the two outputs of one MODE pin. To swap the terminal between German
and ASCII display you flip that pin, no firmware change.

---

## 13. Recommendations for a replacement firmware

Now that we know the SCN2661 setup, we can simply reuse it (or change
it) and write to the same external addresses for display memory:

```assembly
; --- one-shot UART init for replacement firmware ---
MOV  R0,#88h       ; MR address
MOV  A,#5Eh
MOVX @R0,A         ; MR1: async 16x, 8 bits, odd parity, 1 stop
MOV  A,#3Eh
MOVX @R0,A         ; MR2: BRG @ 19200, both clocks from BRG

MOV  R0,#8Ch       ; CR address
MOV  A,#15h
MOVX @R0,A         ; normal ops, TxEN, RxEN, clear errors
```

For a parity-less 8-bit replacement we can change MR1 to `0x4E` (drop
the parity-enable bit) — same framing minus parity, useful while
debugging since most async test gear defaults to 8N1.

Display writes, cursor advance and screen clear can copy the original
code 1:1 (`MOVX @R1,A`, `OR bit 7`, `OUTL P2` for bank select). The BCD
position table at 0x0300 is a useful starting point if you want to honour
the original `STX <bcdpos> <char> ETX` framing.

---

## 14. Open verification items

1. **Scope the SCN2661's TxD pin** (better target than RxC, since RxC is
   a two-valued waveform from the fractional /16-and-/17 BRG and a
   simple frequency reading averages it). One bit time should be 52 µs;
   "around 50 µs" on a typical scope confirms 19200 baud. RxC/TxC will
   average 307.2 kHz with ~6% cycle-to-cycle jitter — that jitter is the
   signature of the fractional divider and is normal.
2. **Identify the T0 source on the PCB.** It should be wired to the
   SCN2661's TxRDY (or its complement). The DC4 handler busy-waits on
   T0 before each transmit byte.
3. **Find the second 8243 expander port destinations** to confirm the
   downstream-DE9 routing hypothesis.
4. **Re-dump the EPROM once more** and diff against the current dump to
   confirm those 5 remaining bit-level differences are reader noise and
   not real content.
5. **Identify the chargen MODE strap** on the display board. It picks
   ASCII vs German DIN 66003 for the 8 wire codes `0x5B-0x5E` /
   `0x7B-0x7E`. Looking at chargen pin A5: in normal operation it is
   driven by code bit 2; the remap forces it to the MODE pin's value.
   Probably a jumper or a single TTL gate connected to a board strap.

---

## 15. Deliverables produced during this analysis

* `dis8048.py` — MCS-48 disassembler with flow-following trace.
* `disasm48.txt`, `disasm48-v2.txt` — disassembly listings (the v2 file
  is from the corrected dump).
* `siemens-9772.bin` — current binary derived from the latest hex dump.
* `siemens-9772-chargen.{bin,hex}`, `render_chargen.py`,
  `siemens-9772-chargen.png` — character-generator EPROM and rendering.
