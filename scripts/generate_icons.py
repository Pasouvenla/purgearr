"""Generate PNG icons for Purgearr PWA — no external dependencies."""
import os
import struct
import zlib

BG = (15, 15, 26)     # #0f0f1a
ACCENT = (229, 160, 13)  # #e5a00d


def make_png(size: int, bg: tuple, accent: tuple) -> bytes:
    """Create a simple PNG: dark bg with a centered rounded accent square."""
    r_bg, g_bg, b_bg = bg
    r_ac, g_ac, b_ac = accent

    border = max(size // 8, 4)
    inner = size - 2 * border

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter byte: None
        for x in range(size):
            if border <= x < border + inner and border <= y < border + inner:
                rows += bytes([r_ac, g_ac, b_ac])
            else:
                rows += bytes([r_bg, g_bg, b_bg])

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "..", "app", "static")
    os.makedirs(out_dir, exist_ok=True)
    for size in (180, 192, 512):
        path = os.path.join(out_dir, f"icon-{size}.png")
        with open(path, "wb") as f:
            f.write(make_png(size, BG, ACCENT))
        print(f"Generated {path} ({size}x{size})")
