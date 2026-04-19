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
- programs the SCN2661 to **9600 baud, async 16× oversampling, 8 data
  bits, odd parity, 1 stop bit** (1 start + 8 data + 1 parity + 1 stop =
  11 bit times per character) using the chip's internal baud-rate
  generator. With the 5.0688 MHz system crystal the math is exact: the
  BRG produces 153.6 kHz (= 5.0688 MHz / 33), and 153.6 kHz / 16 = 9600
  baud;
- reads two DIP-switch bits on P1.4 and P1.5 to choose one of three
  display-size / multi-node modes;
- runs a small protocol state machine that recognises STX, ETX, DC1, DC3
  and DC4 as command introducers;
- can both **receive** characters into display memory and **transmit**
  them back to the host (DC4 is a "report" / response command), using T0
  as a TxRDY handshake to the SCN2661.

There is therefore a serial-line protocol with a clear multi-drop flavour:
the master sends a DC4 + slave-id, the addressed slave answers via the
local UART path. Display data uses STX as start-of-frame and ETX as
end-of-frame.

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
firmware reads, including the high bit. The chargen ROM has glyphs for
codes 0x00-0xFF (German umlauts at 0x80-0x87 and 0xA0-0xA7), so the wire
really does deliver 8-bit code points, and bit 7 is **software-level
addressing / control**, not a parity bit (the SCN2661 strips the parity
bit before the data hits the CPU).

### MR2 = 0x3E = `0011_1110`

| Bits | Field | Value | Meaning |
|------|-------|-------|---------|
| D3:D0 | BRG baud-rate code | `1110` (=14) | **9600 baud** in the standard SCN2661 BRG table |
| D4 | Tx clock select | `1` | from internal BRG |
| D5 | Rx clock select | `1` | from internal BRG |
| D7:D6 | reserved | `00` | — |

So the chip runs its **internal BRG at 9600 baud** and uses that clock
both for transmit and for receive. With 16× oversampling the BRG drives
RxC/TxC at 9600 × 16 = **153.6 kHz**. With the 5.0688 MHz system crystal
this lands exactly on the integer divider: 5.0688 MHz / 33 = 153.6 kHz
to the part-per-million. The Signetics SCN2661B variant ships with a
BRG table calibrated to 5.0688 MHz, so this is the chip variant the
board is using (or the board has a separate 4.9152 MHz crystal on the
SCN2661, which would also give exactly 9600 baud via the standard
SCN2661A table).

Easiest verification: scope the **TxC** or **RxC** pin of the SCN2661.
You should see exactly 153.6 kHz — anything else means the BRG input
clock isn't what the standard table assumes.

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
7. **Clear display** via `CALL 0x0256` (writes 0x00 to every cell across
   all banks).
8. Enable interrupts (`EN I`, then `EN TCNTI` after one synthetic
   counter-ISR call) and fall into the main loop at 0x00D2.

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
| 0x0F | reset / ETX | 0x011B | look for STX, DC1, DC3, DC4 in received byte; otherwise discard. |
| 0x10 | DC4 (0x14) | 0x0180 | wait for `T0` low (= TxRDY), transmit `0x14` then transmit the received byte. **Slave response path.** |
| 0x11 | STX (0x02) | 0x0162 | text mode: ETX returns to 0x0F; DC3 transitions to 0x14; otherwise display the byte at R1, advance cursor, stay in 0x11. |
| 0x12 | DC1 (0x11) | 0x014E | display the next byte at R1 once, then return to 0x0F. |
| 0x13 | DC3 (0x13) | 0x0115 | (handler points into a routine at 0x0115; not yet fully traced, but stays in the dispatcher area). |

Bytes with the high bit set are masked to `0x7F` (DEL) before being
written to display memory in both the DC1 and STX paths. This is
**software-level filtering of multi-drop control bytes**, not a parity
strip — the SCN2661 already removes the parity bit. Any wire byte with
bit 7 set is treated as a control / address byte and is *not* allowed to
appear directly on the screen via these handlers; the firmware uses bit
7 of the display-memory byte separately, as a "visible / cursor" flag
that the timer ISR toggles for blink.

So the user-visible protocol primitives are:

* `STX <chars...> ETX` — write a sequence of characters at the cursor.
* `DC1 <char>` — write one character at the cursor.
* `DC3` (inside STX block) — transition to a sub-state.
* `DC4 <char>` — slave response: terminal echoes `0x14 <char>` back over
  the line (this is the multi-drop poll-and-respond mechanism).
* Inside text mode, the high-nibble dispatcher at 0x0220 (in the
  previous report) further subdivides bytes `0x30..0x57` as cursor
  positions (decoded BCD via the page-3 table at 0x0300), `0xC0`/`0xC1`
  as fill commands, and so on.

---

## 9. Memory map (external, via `MOVX` with P2 upper nibble as bank)

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

## 10. What the corrected dump changed vs. the previous one

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

## 11. Recommendations for a replacement firmware

Now that we know the SCN2661 setup, we can simply reuse it (or change
it) and write to the same external addresses for display memory:

```assembly
; --- one-shot UART init for replacement firmware ---
MOV  R0,#88h       ; MR address
MOV  A,#5Eh
MOVX @R0,A         ; MR1: async 1x, 6 bits, odd parity, 2 stop
MOV  A,#3Eh
MOVX @R0,A         ; MR2: BRG @ 9600, both clocks from BRG

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

## 12. Open verification items

1. **Scope the SCN2661's RxC pin.** With 16× oversampling at 9600 baud
   it should be exactly 153.6 kHz. If you instead see 9600 Hz the chip
   is in 1× mode and our MR1 decode is wrong; if you see another
   frequency the BRG input clock isn't what the standard table assumes.
2. **Identify the T0 source on the PCB.** It should be wired to the
   SCN2661's TxRDY (or its complement). The DC4 handler busy-waits on
   T0 before each transmit byte.
3. **Find the second 8243 expander port destinations** to confirm the
   downstream-DE9 routing hypothesis.
4. **Re-dump the EPROM once more** and diff against the current dump to
   confirm those 5 remaining bit-level differences are reader noise and
   not real content.

---

## 13. Deliverables produced during this analysis

* `dis8048.py` — MCS-48 disassembler with flow-following trace.
* `disasm48.txt`, `disasm48-v2.txt` — disassembly listings (the v2 file
  is from the corrected dump).
* `siemens-9772.bin` — current binary derived from the latest hex dump.
* `siemens-9772-chargen.{bin,hex}`, `render_chargen.py`,
  `siemens-9772-chargen.png` — character-generator EPROM and rendering.
