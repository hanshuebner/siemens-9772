#!/usr/bin/env python3
"""MCS-48 (8048/8039/8049) disassembler."""
import sys

# MCS-48 instruction set. Each entry: (length, mnemonic_template)
# Templates use $1, $2 for byte1 (after opcode), byte2, $A for addr11 target, $R for rel jump
TABLE = {
    0x00: (1, 'NOP'),
    0x02: (2, 'OUTL BUS,A'),
    0x03: (2, 'ADD  A,#$1'),
    0x04: (2, 'JMP  $A'),
    0x05: (1, 'EN   I'),
    0x07: (1, 'DEC  A'),
    0x08: (1, 'INS  A,BUS'),
    0x09: (1, 'IN   A,P1'),
    0x0A: (1, 'IN   A,P2'),
    0x0C: (1, 'MOVD A,P4'),
    0x0D: (1, 'MOVD A,P5'),
    0x0E: (1, 'MOVD A,P6'),
    0x0F: (1, 'MOVD A,P7'),
    0x10: (1, 'INC  @R0'),
    0x11: (1, 'INC  @R1'),
    0x12: (2, 'JB0  $R'),  # JB0 addr
    0x13: (2, 'ADDC A,#$1'),
    0x14: (2, 'CALL $A'),
    0x15: (1, 'DIS  I'),
    0x16: (2, 'JTF  $R'),
    0x17: (1, 'INC  A'),
    0x18: (1, 'INC  R0'),
    0x19: (1, 'INC  R1'),
    0x1A: (1, 'INC  R2'),
    0x1B: (1, 'INC  R3'),
    0x1C: (1, 'INC  R4'),
    0x1D: (1, 'INC  R5'),
    0x1E: (1, 'INC  R6'),
    0x1F: (1, 'INC  R7'),
    0x20: (1, 'XCH  A,@R0'),
    0x21: (1, 'XCH  A,@R1'),
    0x22: (2, 'DB   22h'),   # illegal
    0x23: (2, 'MOV  A,#$1'),
    0x24: (2, 'JMP  $A'),
    0x25: (1, 'EN   TCNTI'),
    0x26: (2, 'JNT0 $R'),
    0x27: (1, 'CLR  A'),
    0x28: (1, 'XCH  A,R0'),
    0x29: (1, 'XCH  A,R1'),
    0x2A: (1, 'XCH  A,R2'),
    0x2B: (1, 'XCH  A,R3'),
    0x2C: (1, 'XCH  A,R4'),
    0x2D: (1, 'XCH  A,R5'),
    0x2E: (1, 'XCH  A,R6'),
    0x2F: (1, 'XCH  A,R7'),
    0x30: (1, 'XCHD A,@R0'),
    0x31: (1, 'XCHD A,@R1'),
    0x32: (2, 'JB1  $R'),
    0x34: (2, 'CALL $A'),
    0x35: (1, 'DIS  TCNTI'),
    0x36: (2, 'JT0  $R'),
    0x37: (1, 'CPL  A'),
    0x39: (1, 'OUTL P1,A'),
    0x3A: (1, 'OUTL P2,A'),
    0x3C: (1, 'MOVD P4,A'),
    0x3D: (1, 'MOVD P5,A'),
    0x3E: (1, 'MOVD P6,A'),
    0x3F: (1, 'MOVD P7,A'),
    0x40: (1, 'ORL  A,@R0'),
    0x41: (1, 'ORL  A,@R1'),
    0x42: (1, 'MOV  A,T'),
    0x43: (2, 'ORL  A,#$1'),
    0x44: (2, 'JMP  $A'),
    0x45: (1, 'STRT CNT'),
    0x46: (2, 'JNT1 $R'),
    0x47: (1, 'SWAP A'),
    0x48: (1, 'ORL  A,R0'),
    0x49: (1, 'ORL  A,R1'),
    0x4A: (1, 'ORL  A,R2'),
    0x4B: (1, 'ORL  A,R3'),
    0x4C: (1, 'ORL  A,R4'),
    0x4D: (1, 'ORL  A,R5'),
    0x4E: (1, 'ORL  A,R6'),
    0x4F: (1, 'ORL  A,R7'),
    0x50: (1, 'ANL  A,@R0'),
    0x51: (1, 'ANL  A,@R1'),
    0x52: (2, 'JB2  $R'),
    0x53: (2, 'ANL  A,#$1'),
    0x54: (2, 'CALL $A'),
    0x55: (1, 'STRT T'),
    0x56: (2, 'JT1  $R'),
    0x57: (1, 'DA   A'),
    0x58: (1, 'ANL  A,R0'),
    0x59: (1, 'ANL  A,R1'),
    0x5A: (1, 'ANL  A,R2'),
    0x5B: (1, 'ANL  A,R3'),
    0x5C: (1, 'ANL  A,R4'),
    0x5D: (1, 'ANL  A,R5'),
    0x5E: (1, 'ANL  A,R6'),
    0x5F: (1, 'ANL  A,R7'),
    0x60: (1, 'ADD  A,@R0'),
    0x61: (1, 'ADD  A,@R1'),
    0x62: (1, 'MOV  T,A'),
    0x64: (2, 'JMP  $A'),
    0x65: (1, 'STOP TCNT'),
    0x67: (1, 'RRC  A'),
    0x68: (1, 'ADD  A,R0'),
    0x69: (1, 'ADD  A,R1'),
    0x6A: (1, 'ADD  A,R2'),
    0x6B: (1, 'ADD  A,R3'),
    0x6C: (1, 'ADD  A,R4'),
    0x6D: (1, 'ADD  A,R5'),
    0x6E: (1, 'ADD  A,R6'),
    0x6F: (1, 'ADD  A,R7'),
    0x70: (1, 'ADDC A,@R0'),
    0x71: (1, 'ADDC A,@R1'),
    0x72: (2, 'JB3  $R'),
    0x74: (2, 'CALL $A'),
    0x75: (1, 'ENT0 CLK'),
    0x76: (2, 'JF1  $R'),
    0x77: (1, 'RR   A'),
    0x78: (1, 'ADDC A,R0'),
    0x79: (1, 'ADDC A,R1'),
    0x7A: (1, 'ADDC A,R2'),
    0x7B: (1, 'ADDC A,R3'),
    0x7C: (1, 'ADDC A,R4'),
    0x7D: (1, 'ADDC A,R5'),
    0x7E: (1, 'ADDC A,R6'),
    0x7F: (1, 'ADDC A,R7'),
    0x80: (1, 'MOVX A,@R0'),
    0x81: (1, 'MOVX A,@R1'),
    0x83: (1, 'RET'),
    0x84: (2, 'JMP  $A'),
    0x85: (1, 'CLR  F0'),
    0x86: (2, 'JNI  $R'),
    0x88: (2, 'ORL  BUS,#$1'),
    0x89: (2, 'ORL  P1,#$1'),
    0x8A: (2, 'ORL  P2,#$1'),
    0x8C: (1, 'ORLD P4,A'),
    0x8D: (1, 'ORLD P5,A'),
    0x8E: (1, 'ORLD P6,A'),
    0x8F: (1, 'ORLD P7,A'),
    0x90: (1, 'MOVX @R0,A'),
    0x91: (1, 'MOVX @R1,A'),
    0x92: (2, 'JB4  $R'),
    0x93: (1, 'RETR'),
    0x94: (2, 'CALL $A'),
    0x95: (1, 'CPL  F0'),
    0x96: (2, 'JNZ  $R'),
    0x97: (1, 'CLR  C'),
    0x98: (2, 'ANL  BUS,#$1'),
    0x99: (2, 'ANL  P1,#$1'),
    0x9A: (2, 'ANL  P2,#$1'),
    0x9C: (1, 'ANLD P4,A'),
    0x9D: (1, 'ANLD P5,A'),
    0x9E: (1, 'ANLD P6,A'),
    0x9F: (1, 'ANLD P7,A'),
    0xA0: (1, 'MOV  @R0,A'),
    0xA1: (1, 'MOV  @R1,A'),
    0xA3: (1, 'MOVP A,@A'),
    0xA4: (2, 'JMP  $A'),
    0xA5: (1, 'CLR  F1'),
    0xA7: (1, 'CPL  C'),
    0xA8: (1, 'MOV  R0,A'),
    0xA9: (1, 'MOV  R1,A'),
    0xAA: (1, 'MOV  R2,A'),
    0xAB: (1, 'MOV  R3,A'),
    0xAC: (1, 'MOV  R4,A'),
    0xAD: (1, 'MOV  R5,A'),
    0xAE: (1, 'MOV  R6,A'),
    0xAF: (1, 'MOV  R7,A'),
    0xB0: (2, 'MOV  @R0,#$1'),
    0xB1: (2, 'MOV  @R1,#$1'),
    0xB2: (2, 'JB5  $R'),
    0xB3: (1, 'JMPP @A'),
    0xB4: (2, 'CALL $A'),
    0xB5: (1, 'CPL  F1'),
    0xB6: (2, 'JF0  $R'),
    0xB8: (2, 'MOV  R0,#$1'),
    0xB9: (2, 'MOV  R1,#$1'),
    0xBA: (2, 'MOV  R2,#$1'),
    0xBB: (2, 'MOV  R3,#$1'),
    0xBC: (2, 'MOV  R4,#$1'),
    0xBD: (2, 'MOV  R5,#$1'),
    0xBE: (2, 'MOV  R6,#$1'),
    0xBF: (2, 'MOV  R7,#$1'),
    0xC4: (2, 'JMP  $A'),
    0xC5: (1, 'SEL  RB0'),
    0xC6: (2, 'JZ   $R'),
    0xC7: (1, 'MOV  A,PSW'),
    0xC8: (1, 'DEC  R0'),
    0xC9: (1, 'DEC  R1'),
    0xCA: (1, 'DEC  R2'),
    0xCB: (1, 'DEC  R3'),
    0xCC: (1, 'DEC  R4'),
    0xCD: (1, 'DEC  R5'),
    0xCE: (1, 'DEC  R6'),
    0xCF: (1, 'DEC  R7'),
    0xD0: (1, 'XRL  A,@R0'),
    0xD1: (1, 'XRL  A,@R1'),
    0xD2: (2, 'JB6  $R'),
    0xD3: (2, 'XRL  A,#$1'),
    0xD4: (2, 'CALL $A'),
    0xD5: (1, 'SEL  RB1'),
    0xD6: (1, 'DB   D6h'),
    0xD7: (1, 'MOV  PSW,A'),
    0xD8: (1, 'XRL  A,R0'),
    0xD9: (1, 'XRL  A,R1'),
    0xDA: (1, 'XRL  A,R2'),
    0xDB: (1, 'XRL  A,R3'),
    0xDC: (1, 'XRL  A,R4'),
    0xDD: (1, 'XRL  A,R5'),
    0xDE: (1, 'XRL  A,R6'),
    0xDF: (1, 'XRL  A,R7'),
    0xE3: (1, 'MOVP3 A,@A'),
    0xE4: (2, 'JMP  $A'),
    0xE5: (1, 'SEL  MB0'),
    0xE6: (2, 'JNC  $R'),
    0xE7: (1, 'RL   A'),
    0xE8: (2, 'DJNZ R0,$R'),
    0xE9: (2, 'DJNZ R1,$R'),
    0xEA: (2, 'DJNZ R2,$R'),
    0xEB: (2, 'DJNZ R3,$R'),
    0xEC: (2, 'DJNZ R4,$R'),
    0xED: (2, 'DJNZ R5,$R'),
    0xEE: (2, 'DJNZ R6,$R'),
    0xEF: (2, 'DJNZ R7,$R'),
    0xF0: (1, 'MOV  A,@R0'),
    0xF1: (1, 'MOV  A,@R1'),
    0xF2: (2, 'JB7  $R'),
    0xF4: (2, 'CALL $A'),
    0xF5: (1, 'SEL  MB1'),
    0xF6: (2, 'JC   $R'),
    0xF7: (1, 'RLC  A'),
    0xF8: (1, 'MOV  A,R0'),
    0xF9: (1, 'MOV  A,R1'),
    0xFA: (1, 'MOV  A,R2'),
    0xFB: (1, 'MOV  A,R3'),
    0xFC: (1, 'MOV  A,R4'),
    0xFD: (1, 'MOV  A,R5'),
    0xFE: (1, 'MOV  A,R6'),
    0xFF: (1, 'MOV  A,R7'),
}

def disasm(mem, pc, mb=0):
    """Return (length, asm, flow_info) for instruction at pc.
    mb: current memory bank (0 or 1), affects address decoding
    flow_info: None, or ('jmp'|'call'|'cjmp', target_addr) or ('ret', None)
    """
    op = mem[pc]
    b1 = mem[pc+1] if pc+1 < len(mem) else 0

    if op not in TABLE:
        return 1, f'DB   {op:02X}h', None
    n, tmpl = TABLE[op]

    flow = None
    # Flow control analysis
    if op in (0x04, 0x24, 0x44, 0x64, 0x84, 0xA4, 0xC4, 0xE4):  # JMP
        # Address: bits 7-5 of opcode -> A10-A8; byte1 -> A7-A0
        a10_8 = (op >> 5) & 7
        target = ((mb & 1) << 11) | (a10_8 << 8) | b1
        flow = ('jmp', target)
    elif op in (0x14, 0x34, 0x54, 0x74, 0x94, 0xB4, 0xD4, 0xF4):  # CALL
        a10_8 = (op >> 5) & 7
        target = ((mb & 1) << 11) | (a10_8 << 8) | b1
        flow = ('call', target)
    elif op in (0x83, 0x93):  # RET / RETR
        flow = ('ret', None)
    elif op in (0xB3, 0x73):  # JMPP @A (indirect)
        flow = ('ret', None)  # indirect - stop linear decode
    # Conditional jumps (in-page, within 256-byte block)
    elif op in (0x12, 0x32, 0x52, 0x72, 0x92, 0xB2, 0xD2, 0xF2,
                0x16, 0x26, 0x36, 0x46, 0x56, 0x76, 0x86, 0x96,
                0xA6, 0xB6, 0xC6, 0xE6, 0xF6,
                0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF):
        # Branch within current 256-byte page (PC high 3 bits unchanged)
        target_page = (pc + 2) & 0xFF00
        target = target_page | b1
        flow = ('cjmp', target)

    # Format
    asm = tmpl
    if '$A' in asm:
        a10_8 = (op >> 5) & 7
        target = ((mb & 1) << 11) | (a10_8 << 8) | b1
        asm = asm.replace('$A', f'{target:04X}h')
    if '$R' in asm:
        target_page = (pc + 2) & 0xFF00
        target = target_page | b1
        asm = asm.replace('$R', f'{target:04X}h')
    if '$1' in asm:
        asm = asm.replace('$1', f'{b1:02X}h')
    return n, asm, flow

def trace(mem, entries):
    """Trace code from entry points, collecting code locations and labels."""
    visited = set()
    code = {}
    labels = set()
    calls = set()
    stack = [(pc, 0) for pc in entries]
    for pc in entries:
        labels.add(pc)
    while stack:
        pc, mb = stack.pop()
        while pc < len(mem) and pc not in visited:
            visited.add(pc)
            n, asm, flow = disasm(mem, pc, mb)
            code[pc] = (n, asm, mb)
            if flow:
                kind, target = flow
                if kind == 'ret':
                    break
                if target is not None and target < len(mem):
                    labels.add(target)
                    if kind == 'call':
                        calls.add(target)
                    if target not in visited:
                        stack.append((target, mb))
                if kind == 'jmp':
                    break
            pc += n
    return code, labels, calls

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'siemens-9772.bin'
    with open(path, 'rb') as f:
        mem = f.read()
    # MCS-48 reset vector = 0x0000. Timer interrupt = 0x0007. External interrupt = 0x0003.
    entries = [0x0000, 0x0003, 0x0007]
    code, labels, calls = trace(mem, entries)
    # Print
    pc = 0
    while pc < len(mem):
        if pc in code:
            n, asm, mb = code[pc]
            raw = ' '.join(f'{mem[pc+i]:02X}' for i in range(n))
            lbl = 'L' if pc in labels else ' '
            call = 'C' if pc in calls else ' '
            print(f'{pc:04X}:{lbl}{call} {raw:<6s}  {asm}')
            pc += n
        else:
            # Data block
            end = pc + 1
            while end < len(mem) and end not in code:
                end += 1
            while pc < end:
                line_end = min(pc + 16, end)
                raw = ' '.join(f'{mem[i]:02X}' for i in range(pc, line_end))
                ascii_str = ''.join(chr(mem[i]) if 32 <= mem[i] < 127 else '.' for i in range(pc, line_end))
                print(f'{pc:04X}:   {raw:<47s}  ; {ascii_str}')
                pc = line_end

if __name__ == '__main__':
    main()
