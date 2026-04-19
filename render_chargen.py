#!/usr/bin/env python3
"""Render the Siemens 9772 character-generator EPROM into a PNG.

Layout of the EPROM (2 KiB = 256 chars x 8 bytes):
 - Each character is 8 consecutive bytes.
 - Each byte is one COLUMN of the 7-pixel-high glyph.
 - Within a byte, bit 0 is the TOP pixel and bit 6 is the BOTTOM pixel.
   Bit 7 is never set.
 - Columns 0..4 carry the 5x7 glyph; columns 5..7 are the inter-character
   spacing.

Of the 256 slots, 152 are the "unused placeholder" pattern (a full 5x7
solid block, bytes 7f 7f 7f 7f 7f 00 00 00). The slot at 0x20 (SPACE) is
the only all-zero cell.

The PNG contains all 256 character cells in a 16x16 grid. Each chargen
pixel is drawn as a 4x4 block of PNG pixels.
"""
from PIL import Image, ImageDraw, ImageFont

SCALE = 4
CELL_W = 8
CELL_H = 7
GRID_COLS = 16
GRID_ROWS = 16
UNUSED = bytes.fromhex('7f7f7f7f7f000000')


def char_pixels(rom: bytes, code: int):
    """Yield (col, row, on) for each pixel in the glyph for `code`."""
    cell = rom[code * 8:code * 8 + 8]
    for col in range(CELL_W):
        colbyte = cell[col]
        for row in range(CELL_H):
            yield col, row, bool((colbyte >> row) & 1)


def render(rom: bytes, out_path: str,
           labelled: bool = True,
           fg=(220, 220, 220), bg=(0, 0, 0),
           grid=(40, 40, 40),
           fade_unused=True, unused_fg=(60, 30, 30)):
    # Sizes
    cell_px_w = CELL_W * SCALE
    cell_px_h = CELL_H * SCALE

    if labelled:
        # Leave margins for hex labels on top (row) and left (col)
        margin_top = 14
        margin_left = 22
        gap = 2  # 1-chargen-pixel gap between cells, expressed in output px
    else:
        margin_top = 0
        margin_left = 0
        gap = 0

    img_w = margin_left + GRID_COLS * cell_px_w + (GRID_COLS - 1) * gap
    img_h = margin_top + GRID_ROWS * cell_px_h + (GRID_ROWS - 1) * gap

    img = Image.new('RGB', (img_w, img_h), bg)
    draw = ImageDraw.Draw(img)

    if labelled:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        # Column labels (low nibble)
        for c in range(GRID_COLS):
            x = margin_left + c * (cell_px_w + gap) + cell_px_w // 2 - 3
            draw.text((x, 2), f'{c:X}', fill=grid, font=font)
        # Row labels (high nibble)
        for r in range(GRID_ROWS):
            y = margin_top + r * (cell_px_h + gap) + cell_px_h // 2 - 6
            draw.text((2, y), f'{r:X}', fill=grid, font=font)

    for code in range(256):
        gy, gx = divmod(code, GRID_COLS)
        x0 = margin_left + gx * (cell_px_w + gap)
        y0 = margin_top + gy * (cell_px_h + gap)

        cell_bytes = bytes(rom[code * 8:code * 8 + 8])
        pix_fg = unused_fg if (fade_unused and cell_bytes == UNUSED) else fg

        for col, row, on in char_pixels(rom, code):
            if not on:
                continue
            px = x0 + col * SCALE
            py = y0 + row * SCALE
            for dy in range(SCALE):
                for dx in range(SCALE):
                    img.putpixel((px + dx, py + dy), pix_fg)

    img.save(out_path)
    print(f'Wrote {out_path}: {img_w}x{img_h}')


def main():
    with open('siemens-9772-chargen.bin', 'rb') as f:
        rom = f.read()
    assert len(rom) == 2048
    render(rom, 'siemens-9772-chargen.png', labelled=True)
    render(rom, 'siemens-9772-chargen-plain.png', labelled=False)


if __name__ == '__main__':
    main()
