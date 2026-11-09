"""Draw ScriptHub's icon (a colourful puzzle cube, top layer twisted, a play sticker on top)
and write static/img/scripthub.ico (16-256 px: exe, tray and browser tab) and docs/icon.png (256 px).

No dependencies: 27 cube pieces are projected isometrically and painted back to front into a
supersampled buffer, then averaged; PNG and ICO are written by hand.

    python tools/make_icon.py
"""
import math
import random
import struct
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ICO = BASE / "static" / "img" / "scripthub.ico"
PNG = BASE / "docs" / "icon.png"
SIZES = [256, 128, 64, 48, 32, 24, 16]

BODY = (0x17, 0x1A, 0x22)
PALETTE = [(0x3D, 0x7B, 0xFF), (0x14, 0xC8, 0xB0), (0xFF, 0xC6, 0x2E),
           (0xFF, 0x7A, 0x2F), (0xFF, 0x3D, 0x6E), (0x8F, 0x5B, 0xFF)]
WHITE, PLAY = (0xFF, 0xFF, 0xFF), (0x3D, 0x7B, 0xFF)
TWIST = math.radians(22)
SEED = 7
_L = (-0.35, 1.0, 0.55)
LIGHT = tuple(c / math.sqrt(sum(v * v for v in _L)) for c in _L)
C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def project(p):
    x, y, z = p
    return ((x - z) * C30, -y + (x + z) * S30)


def rot_y(p, a):
    x, y, z = p
    c, s = math.cos(a), math.sin(a)
    return (x * c - z * s, y, x * s + z * c)


def shade(rgb, n):
    f = 0.62 + 0.38 * max(0.0, sum(a * b for a, b in zip(n, LIGHT)))
    return tuple(min(255, round(c * f)) for c in rgb)


def build():
    """Polygons in screen space with their colour, in painting order."""
    rng = random.Random(SEED)
    polys = []
    for layer in (-1, 0, 1):                                      # bottom to top: upper layers cover lower
        a = TWIST if layer == 1 else 0.0
        cubies = sorted(((i, layer, k) for i in (-1, 0, 1) for k in (-1, 0, 1)),
                        key=lambda c: rot_y(c, a)[0] + rot_y(c, a)[2])   # back to front
        for c in cubies:
            for axis in range(3):
                for sign in (-1, 1):
                    n = [0, 0, 0]
                    n[axis] = sign
                    nr = rot_y(tuple(n), a)
                    if sum(nr) <= 1e-9:                           # faces away from the viewer
                        continue
                    u_ax, v_ax = [ax for ax in range(3) if ax != axis]

                    def at(du, dv, c=c, axis=axis, sign=sign, u_ax=u_ax, v_ax=v_ax, a=a):
                        p = list(c)
                        p[axis] += sign * 0.5
                        p[u_ax] += du
                        p[v_ax] += dv
                        return project(rot_y(tuple(p), a))

                    polys.append(([at(-.5, -.5), at(.5, -.5), at(.5, .5), at(-.5, .5)], shade(BODY, nr)))
                    if c[axis] != sign:                           # inside the cube: no sticker
                        continue
                    play = axis == 1 and sign == 1 and c == (0, 1, 0)
                    color = PALETTE[rng.randrange(len(PALETTE))]  # drawn for every sticker: keeps the pattern
                    if play:
                        color = WHITE
                    polys.append(([at(-.4, -.4), at(.4, -.4), at(.4, .4), at(-.4, .4)], shade(color, nr)))
                    if play:                                      # pointing right on screen, whatever the twist
                        ca, sa = math.cos(-a), math.sin(-a)
                        r = (0.7071 * ca + 0.7071 * sa, 0.7071 * sa - 0.7071 * ca)
                        d = (0.7071 * ca - 0.7071 * sa, 0.7071 * sa + 0.7071 * ca)

                        def pt(ar, ad):
                            return at(ar * r[0] + ad * d[0], ar * r[1] + ad * d[1])
                        polys.append(([pt(-.19, -.29), pt(.33, 0), pt(-.19, .29)], shade(PLAY, nr)))
    return polys


def fit(polys, size, margin):
    xs = [x for q, _ in polys for x, _y in q]
    ys = [y for q, _ in polys for _x, y in q]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    s = (size - 2 * margin) / max(w, h)
    ox, oy = (size - w * s) / 2 - min(xs) * s, (size - h * s) / 2 - min(ys) * s
    return [([(x * s + ox, y * s + oy) for x, y in q], col) for q, col in polys]


def raster(polys, size, ss):
    """RGBA bytes: each convex polygon fills the samples it covers, later ones on top."""
    n = size * ss
    buf = [None] * (n * n)
    for quad, col in polys:
        q = [(x * ss, y * ss) for x, y in quad]
        area = sum(q[i][0] * q[(i + 1) % len(q)][1] - q[(i + 1) % len(q)][0] * q[i][1] for i in range(len(q)))
        if abs(area) < 1e-9:
            continue
        sgn = 1 if area > 0 else -1
        edges = [(q[i], q[(i + 1) % len(q)]) for i in range(len(q))]
        x0, x1 = max(0, int(min(p[0] for p in q))), min(n - 1, int(max(p[0] for p in q)) + 1)
        y0, y1 = max(0, int(min(p[1] for p in q))), min(n - 1, int(max(p[1] for p in q)) + 1)
        for j in range(y0, y1 + 1):
            y = j + .5
            for i in range(x0, x1 + 1):
                x = i + .5
                if all(sgn * ((bx - ax) * (y - ay) - (by - ay) * (x - ax)) >= 0 for (ax, ay), (bx, by) in edges):
                    buf[j * n + i] = col
    px = bytearray()
    for j in range(size):
        for i in range(size):
            r = g = b = cnt = 0
            for sj in range(ss):
                row = (j * ss + sj) * n + i * ss
                for c in buf[row:row + ss]:
                    if c is not None:
                        r += c[0]; g += c[1]; b += c[2]; cnt += 1
            px += bytes((round(r / cnt), round(g / cnt), round(b / cnt), round(255 * cnt / (ss * ss)))) \
                if cnt else bytes(4)
    return size, size, bytes(px)


def png(img) -> bytes:
    w, h, px = img
    raw = b"".join(b"\x00" + px[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images) -> bytes:
    """ICO with PNG-compressed entries (Windows Vista and later)."""
    blobs = [png(im) for im in images]
    out, offset = struct.pack("<HHH", 0, 1, len(blobs)), 6 + 16 * len(blobs)
    for im, blob in zip(images, blobs):
        side = im[0] if im[0] < 256 else 0
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return out + b"".join(blobs)


def main():
    polys = build()
    images = [raster(fit(polys, s, 0.02 * s), s, 4 if s > 64 else 6) for s in SIZES]
    ICO.parent.mkdir(parents=True, exist_ok=True)
    ICO.write_bytes(ico(images))
    PNG.write_bytes(png(images[0]))
    print(f"wrote {ICO.relative_to(BASE)} and {PNG.relative_to(BASE)}")


if __name__ == "__main__":
    main()
