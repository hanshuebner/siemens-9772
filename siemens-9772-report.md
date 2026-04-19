# Siemens 9772 Terminal — Firmware Analysis Report

Subject: Reverse-engineering of `siemens-9772.hex` (2 KiB TMS2516 EPROM dump).
Goal: gather enough information about the terminal's hardware and firmware to
write a replacement program that can receive async serial data and display it.

---

## 1. Executive summary

The ROM contains firmware for an **Intel 8035** (MCS-48, ROMless) running out
of a 2 KiB external program EPROM. The terminal is a display-only device
driven by an **SCN2661** USART and a TTL-based CRT/display controller. The
host protocol is a command-oriented, character-framed protocol with STX/ETX
and a BCD cursor-positioning scheme; there is clear evidence of a
multi-drop addressing model (the firmware reads its own address from
DIP-switches on the CPU BUS pins and gates reception on the 9th-bit /
address-marker).

The firmware never programs the SCN2661 mode registers (MR1/MR2), so the
UART character format and baud rate are set entirely by hardware straps and
an external clock. Practical work on a replacement firmware therefore
requires measuring the physical serial clock and settling on an async mode
explicitly in software.

---

## 2. Hardware identification

| Item | Value / evidence |
|------|-----------------|
| CPU | Intel 8035 (MCS-48, ROMless). Confirmed by user. The reset vector `04 1C = JMP 01Ch` and the interrupt-vector layout (0x0003 external, 0x0007 timer/counter) are classic MCS-48. |
| Program ROM | 2 KiB at 0x000-0x7FF (this EPROM). A11 is not used by the firmware. |
| RAM | Internal 64 bytes of the 8035 (two register banks used: `SEL RB1` on interrupt). External data RAM for the display and the USART; accessed via `MOVX`. |
| USART | SCN2661 at external addresses 0x80/0x84/0x88/0x8C (see map below). |
| Port expander | An 8243-class expander on P2's low nibble — `MOVD P5,A`, `ANLD P5,A`, `MOVD P7,A`, `ORLD P7,A` appear repeatedly. Gives ports P4..P7. |
| Display | TTL-based controller (per user). Character cell memory is externally mapped. Display is 40 cells (positions 0..39), with the firmware using BCD row/column encoding (`00h..39h`, i.e. `high nibble*10 + low nibble`). |
| Physical ports | Three DE-9 connectors: one DTE male (upstream host), two female (downstream devices, likely soft-UART). |

---

## 3. Memory map

### Program memory (internal view of the 8035)

| Range | Contents |
|-------|----------|
| 0x0000 | Reset vector: `JMP 0x001C`. |
| 0x0003 | External interrupt vector: `NOP; JMP 0x033A`. SCN2661 RxRDY handler. |
| 0x0007-0x001B | Timer/counter interrupt handler (inline). |
| 0x001C-0x002A | Start-of-reset: checksum page 0, jump to 0x0100. |
| 0x0100-0x0109 | Checksum of page 1, then `JMP 0x02DB`. |
| 0x010A-0x01B9 | Per-character state dispatcher (see §7) — first copy. |
| 0x0200-0x02D5 | Character high-nibble dispatcher and action table. |
| 0x02DB-0x02E4 | Setup continuation; `JMP 0x0329` to checksum page 3. |
| 0x0300-0x0339 | **Page-3 BCD position table**: index = (rx_char − 0x30), value = BCD position 00..39. |
| 0x033A-0x03B1 | SCN2661 RxRDY interrupt handler proper. |
| 0x0400-0x04FF | Mirror of 0x0000-0x00FF with small variations (the reset vector still points at 0x001C in the first half; the page exists because `JMPP @A` dispatch must be in the current page). |
| 0x0500-0x05FF | Second copy of the state dispatcher (mirror of 0x0100-0x01FF, a few extra cases). |
| 0x0600-0x06FF | Second copy of the high-nibble dispatcher (mirror of 0x0200). |
| 0x0700-0x0739 | Second copy of the BCD position table. |
| 0x0723-0x0770 | Post-self-test main loop entry. |

### External data memory (seen from `MOVX`)

| Address | Device / purpose |
|---------|-------------------|
| 0x00-0x7F | External RAM (display buffer; cell = 7 bits character + bit-7 = visible/cursor flag). |
| 0x80 | SCN2661 RHR / THR (data). |
| 0x84 | SCN2661 status register. |
| 0x88 | SCN2661 mode register (MR1, MR2) — **never written by this firmware**. |
| 0x8C | SCN2661 command register — written with `0x15`. |

Address decoding: bit 7 of the external address selects the SCN2661; CPU A2→RS0, CPU A3→RS1. Cells in display memory are selected by bits 0..6 (40-position organization plus possibly line flags).

The P2 upper nibble is driven via `OUTL P2,A` (with nibble-swapped data) before some `MOVX` accesses; this presumably selects between display-memory banks or between the display and the SCN2661 group.

### Internal RAM locations in use

| Addr | Purpose (inferred) |
|------|--------------------|
| 0x14 | Timer/counter reload value (loaded into T in the ISR). |
| 0x15 | P1 XOR mask applied every timer tick (cursor blink / LED toggle). |
| 0x16 | Terminal address limit/offset used in the 0x80-0x8F handler. |
| 0x17 | Current receive-protocol state (indexed via `JMPP @A`). |
| 0x3D..0x3F | Working area / RAM-test scratch (probably stack). |

---

## 4. I/O-pin usage summary

| Pin / port | Role |
|------------|------|
| BUS (P0) | Multiplexed address/data for external ROM+RAM; used as a byte port via `INS A,BUS` at 0x0160 to read the **DIP-switch address** when a high-bit frame arrives. |
| P1 | Bi-directional. Read (`IN A,P1`) many times; written (`OUTL P1,A`) and XOR-toggled by the timer ISR. Likely the bit-banged lines for the two downstream DE9 ports and/or a piezo/LED. |
| P2 low | External ROM A8..A11 (default MCS-48 behaviour). |
| P2 high | Written as chip-select / display-bank select before certain `MOVX` writes. |
| P4..P7 | Via 8243 expander on P2 low nibble (`MOVD`, `ANLD`, `ORLD`). Drives the TTL display controller and possibly the downstream soft-UART lines. |
| T0 | Tested at 0x0724 (`JT0 0737h`). Routes between the normal main loop and an on-board diagnostic checksum loop. |
| T1 | Counter clock input (`STRT CNT`). Feeds the periodic timer interrupt. Probably the baud-rate clock or a dedicated 50/60 Hz tick. |
| INT | SCN2661 interrupt request (RxRDY). |

---

## 5. Reset, self-test and main loop

1. **Reset** at 0x0000 → `JMP 0x001C`.
2. **Checksum loop** (0x001C) walks pages 0, 1 and 3 via `MOVP A,@A`, accumulating a rolling rotate/XOR checksum into R1. If the final compare at 0x002B fails, jumps to 0x009D (hang / error indicator).
3. **Internal-RAM test** at 0x0031: writes 0x3D..0x01 patterns, verifies, rotates pattern through all bit positions. On failure, same hang target.
4. **External RAM test** starting at 0x00E3 (`OUTL P2,#0`, then `MOVX @R0,A; XRL` through every external address). Note: this touches the SCN2661 register window; the subsequent explicit write to CR(0x8C) re-arms the USART.
5. **Main loop** at 0x0723:
   - `DIS TCNTI` (disables counter interrupt during setup).
   - `JT0 0x0737` — if T0 is high, proceed to the normal receive/display loop; otherwise fall through to an extra checksum loop (factory-test / power-on diagnostic).
   - 0x0737: `CALL 0x07FF`, then `SEL RB1`, read RHR from 0x80, read SR from 0x84, mask error bits (`ANL A,#38h` = FE|OE|PE), and on error write `0x15` to CR(0x8C) to reset the flags. Continues into the state dispatcher.

The firmware is interrupt-driven: each received byte triggers `INT`, the handler at 0x033A pulls the byte, stages it in R4, invokes the state machine, writes the glyph into display RAM and returns (`RETR`).

---

## 6. Interrupt handlers

### Timer/counter ISR (0x0007)

```
MOV  R7,A           ; save A
MOV  A,R0           ; save R0
MOV  R4,A
MOV  R0,#14h
MOV  A,@R0          ; load reload from RAM[14h]
MOV  T,A
STRT CNT            ; re-arm counter
IN   A,P1
MOV  R0,#15h
XRL  A,@R0          ; XOR with mask in RAM[15h]
OUTL P1,A           ; toggle P1 bits (LED/buzzer/soft-UART)
MOVX A,@R1
XRL  A,#80h
MOVX @R1,A          ; toggle bit 7 at display cursor cell (blink)
MOV  A,R4
MOV  R0,A
MOV  A,R7
RETR
```

Two observations:
* `STRT CNT` puts the timer in **counter** mode — T1 is a physical clock driving the tick. The tick period therefore depends on whatever T1 is wired to (likely a divided-down baud or line-rate clock).
* The ISR toggles bit 7 of the display cell at `@R1` each tick. That is the **cursor blink**.

### SCN2661 RxRDY handler (0x033A)

```
SEL  RB1              ; switch to bank 1
MOV  R7,A
MOV  A,R0; MOV R2,A   ; save R0
MOV  R0,#80h
MOVX A,@R0            ; A = received byte
MOV  R4,A
MOV  R0,#84h
MOVX A,@R0            ; A = status
ANL  A,#38h           ; mask FE|OE|PE
JZ   0x0350           ; no error -> continue
MOV  R0,#8Ch
MOV  A,#15h
MOVX @R0,A            ; reset errors
MOV  R4,#0FFh         ; mark byte as "bad"
...                   ; invoke state dispatcher with R2 (or R4) = received char
RETR
```

`0x15 = 0001_0101b` in the SCN2661 command register = TxEN + RxEN + ResetError, normal operation, no break, no DTR/RTS.

---

## 7. Receive-protocol state machine

The firmware keeps the current protocol state in **RAM[0x17]**. Every
received character goes through:

```
MOV  R0,#17h
MOV  A,@R0
JMPP @A              ; dispatch via page-local table
```

This jumps via a table at offset 0x10F (first copy) / 0x50F (second copy)
in the current code page. The two copies are byte-for-byte identical, which
confirms they are not two modes but two necessary copies for `JMPP @A`
page-locality.

### State values (from RAM[0x17])

| State | Entered on | Next-byte behaviour |
|-------|-----------|----------------------|
| 0x0F | Default / after ETX | Look for control chars STX/DC1/DC3/DC4 (and ETX in second copy). Everything else is ignored. |
| 0x10 | After DC4 (0x14) | Alt handler at 0x0180 (function not fully decoded). |
| 0x11 | After STX (0x02) | "Text mode". Next byte is routed through the high-nibble dispatcher at 0x0220 (position / displayable / command). |
| 0x12 | After DC1 (0x11) | Displays the next byte directly. |
| 0x13 | After DC3 (0x13) | Branches through `JMP 0x0220` (same as text mode but no position preamble). |

### Control characters recognised in state 0x0F

| Code | ASCII | Observed action |
|------|-------|-----------------|
| 0x02 | STX | state = 0x11 |
| 0x03 | ETX | state = 0x0F (only in second copy of the dispatcher) |
| 0x11 | DC1 / XON | state = 0x12 |
| 0x13 | DC3 / XOFF | state = 0x13 |
| 0x14 | DC4 | state = 0x10 |

Any other byte in state 0x0F is discarded.

### High-nibble dispatcher (0x0220, second copy at 0x02A0)

In text mode the received byte is dispatched by its high nibble:

| Byte range | Handler | Action |
|------------|---------|--------|
| 0x00-0x0F | 0x02E5 | control / effect (bell? line attribute?) |
| 0x10-0x2F | 0x024B | reject, reset state |
| 0x30-0x4F | 0x022A | **cursor-position byte**: `A -= 0x30`, then `MOVP3 A,@A` on page 3 gives BCD pos 00..39; R1 becomes the new display address; bit 7 of the new cell is set (cursor). |
| 0x50-0x57 | 0x0225→0x022A | same as above (extends position range up to 'W' → pos 39). |
| 0x58-0x7F | 0x024B | reject. |
| 0x80-0x8F | 0x0236 | **display byte with P2 bank-select**: the SWAP'd high nibble is OUT'd to P2 before the display write; used for addressing a second display bank or attribute byte. |
| 0x90-0xBF | 0x024B | reject. |
| 0xC0 | 0x0256→0x025C | **fill display with 0x00**. |
| 0xC1 | 0x025A→0x025C | **fill display with 0x20 (space) — screen clear**. |
| 0xC2-0xC8 | 0x02B* | additional indirect-dispatched commands (not fully decoded). |
| 0xC9-0xFF | 0x024B | reject. |

The displayable character written to memory is always masked to 7 bits
(`ANL A,#7Fh`) and bit 7 is re-set afterwards as the "valid / visible"
flag — this is what the timer ISR toggles for blink.

### BCD position table (page 3, 0x0300-0x0339)

Index (char − 0x30) → BCD(pos):

```
'0'(0x30) -> 00    '9'(0x39) -> 09    ':'(0x3A) -> 10    'W'(0x57) -> 39
```

i.e. pos = high_nibble*10 + low_nibble (pure BCD). The display therefore
has 40 logical positions, addressed as two BCD digits. This is almost
certainly a **hardware row/column decoding** scheme — the display
controller's address counter is likely driven from BCD decoders.

---

## 8. Multi-drop addressing (RS-422/485)

When a received byte has bit 7 set (i.e. is in 0x80..0xFF), the dispatcher
at 0x0236 SWAPs it and drives the high nibble onto P2 before writing the
data portion. Additionally, at 0x015E (reached via `JB7 015Eh` from the
state machine) the firmware executes:

```
INS  A,BUS   ; read the 8035 BUS port (tri-state external device)
DEC  A
...
```

This is the classic hardware pattern for reading **DIP-switch straps** into
the CPU. The value is then compared against an offset stored in RAM[0x16]
inside the 0x80-0x8F handler. The interpretation is:

* The upstream host uses a 9th-bit / high-bit-marker addressing scheme.
* A frame whose 9th bit is set is an *address* frame; only the addressed
  terminal proceeds to accept the subsequent data characters.
* Each terminal's address is latched from DIP switches on the BUS pins.

This fits the observed RS-422/485 signalling on the upstream port.

---

## 9. SCN2661 configuration

**There is no write to MR1 / MR2 (address 0x88) anywhere in the ROM.**

Consequences:
* The USART's character format (async/sync, data bits, parity, stop bits)
  is **not programmed in firmware**.
* The **baud rate** is not programmed either: the built-in BRG would
  require writing MR2; since that never happens, the chip is running on
  the external RxC/TxC clock inputs.
* The only SCN2661 register the firmware writes is CR (0x8C) with `0x15`:
  TxEN | RxEN | ResetError, operating mode 00 (normal).

The only plausible interpretation is that the host link is **synchronous**
(address / data framing with a 9th-bit marker, as per §8), clocked by an
external bit-rate generator shared by host and terminal. To pin this down
physically:

1. Scope pin 9 (RxC) and pin 25 (TxC) of the SCN2661 — that's the bit
   rate.
2. Scope the RS-422/485 differential pair — framing (SYN chars, address
   byte) will be visible once you capture a burst from the real host.

---

## 10. Three physical ports

There is only one SCN2661 in the design, so the two downstream female DE9
ports must be handled differently. Evidence:

* Heavy use of `IN A,P1` / `OUTL P1,A` and the timer-ISR XOR of P1 with a
  mask from RAM[0x15] is consistent with a **bit-banged soft UART** whose
  bit clock is provided by the T1-driven timer tick.
* `MOVD P5,A`, `ANLD P5,A`, `MOVD P7,A`, `ORLD P7,A` drive individual
  lines on the 8243 expander — candidates for the Tx/Rx/handshake lines of
  the two downstream async ports.
* The many `JT1` / `JNT1` tests scattered through the code are polls on T1
  — another signal that could be the incoming start-bit of a soft-UART
  channel.

The hypothesis is: **upstream sync link via SCN2661; downstream async
links via software, clocked by the timer interrupt**. That is consistent
with the "DTE male upstream, DTR/DCE female downstream" port assignment
and with typical late-70s retail/industrial terminals where the device
acted as a small multiplexer between a sync master and a couple of
character-mode accessories (keyboard, wand, printer).

---

## 11. What we *don't* know from the firmware alone

* The **character encoding** used on the wire — the firmware only knows
  7-bit codes and writes them to the display cell. The character ROM
  (not yet dumped) turns those codes into dot patterns. Until the
  char-gen EPROM is read we don't know whether the display font is ASCII,
  an EBCDIC subset, Siemens-specific, DIN-66003 with German diacritics,
  etc.
* The exact meaning of commands **0xC2..0xC8** (indirect-dispatched from
  the 0x024D handler). Likely cursor-home / attribute / line-clear
  variants.
* Whether the downstream ports are truly RS-232 async or something
  proprietary; without the schematic and a device to probe the port, we
  cannot verify.
* The T1 clock frequency — and therefore the timer-ISR period and the
  soft-UART bit rate — needs measurement.

---

## 12. Recommendations for a replacement firmware

1. **Physical measurements first**
   * Capture RxC/TxC on the SCN2661 — this tells you the line speed.
   * Capture T1 — tells you the soft-UART bit clock and cursor-blink
     period.
   * Read the character-generator EPROM and dump the font to confirm the
     display character set.

2. **UART initialisation (replacement firmware)**
   Explicitly program MR1 and MR2 once after reset. For 9600 baud 8N1
   async with a 4.9152 MHz BRG crystal on the SCN2661:

   ```
   MOV  R0,#8Ch  ; CR
   MOV  A,#10h
   MOVX @R0,A    ; reset chip

   MOV  R0,#88h  ; MR
   MOV  A,#4Eh   ; MR1: async 16x, 8 data, 1 stop, no parity
   MOVX @R0,A
   MOV  A,#EEh   ; MR2: internal BRG at 9600 baud, TxC+RxC from BRG
   MOVX @R0,A

   MOV  R0,#8Ch
   MOV  A,#27h   ; CR: DTR, RxEN, TxEN, normal operate
   MOVX @R0,A
   ```

   The exact MR2 nibble depends on the actual crystal; values from the
   SCN2661 data sheet table 5 (the bit-rate code) apply.

3. **Display write path — keep the existing contract**
   * Write 7-bit character to `@R1` via `MOVX`.
   * Follow with `MOVX A,@R1 ; ORL A,#80h ; MOVX @R1,A` to mark the cell
     visible.
   * Cursor auto-increment can just `INC R1`, wrapping at the BCD-encoded
     end of the row.

4. **Minimal command set the replacement should accept**
   * `0x0C` (FF) or `0xC1`: clear screen (fill with 0x20).
   * `0x0D` (CR): `R1 = R1 & 0xF0` (back to column 0 of current row).
   * `0x0A` (LF): advance to next row (BCD arithmetic on R1).
   * `0x08` (BS): `DEC R1` (with row-underflow guard).
   * Any other 0x20..0x7E: write at cursor, advance.
   * Optional ANSI-like `ESC [ y ; x H` for cursor positioning, or keep
     the legacy `STX <bcdpos>` if interoperability with the original host
     is needed.

5. **Skip the sync/multi-drop address decoding** unless you also need
   compatibility with the existing master. For a stand-alone async
   viewer, ignore the DIP switches and accept every byte.

6. **Timer ISR** still needed for cursor blink. Keep the existing
   toggle-bit-7-at-@R1 trick; with the replacement firmware you can drive
   the timer from the internal prescaler (`STRT T`) instead of the
   hardware-clocked counter (`STRT CNT`) so that the blink rate no longer
   depends on the upstream clock.

---

## 13. Deliverables produced during this analysis

* `dis8048.py` — a small MCS-48 disassembler, with flow-following trace
  from the reset and interrupt vectors.
* `disasm48.txt` — disassembly of the full 2 KiB ROM.
* `siemens-9772.bin` — the hex dump converted to a raw binary.

These are sufficient to continue the reverse-engineering, to experiment
with replacement code, and to cross-check anything noted above against
the exact byte sequences.
