"""Decodificador PNG minimo + extraccion de paleta dominante.

Se usa para sacar los colores reales de la UI capturada sin depender de PIL
(que en Termux arrastra compilacion nativa). Soporta PNG de 8 bits,
no entrelazado, en escala de grises / RGB / RGBA / paleta.
"""
from __future__ import annotations

import struct
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

SIG = b"\x89PNG\r\n\x1a\n"


class PNGError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(path: str | Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    """Devuelve (ancho, alto, pixeles RGB)."""
    data = Path(path).read_bytes()
    if not data.startswith(SIG):
        raise PNGError("no es PNG")
    off = 8
    width = height = bitdepth = colortype = interlace = 0
    idat = bytearray()
    palette: list[tuple[int, int, int]] = []
    while off + 8 <= len(data):
        (length,) = struct.unpack_from(">I", data, off)
        ctype = data[off + 4: off + 8]
        body = data[off + 8: off + 8 + length]
        off += 12 + length
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body
            )
        elif ctype == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]  # type: ignore
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if interlace:
        raise PNGError("PNG entrelazado no soportado")
    if bitdepth != 8:
        raise PNGError(f"bitdepth {bitdepth} no soportado (solo 8)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colortype)
    if not channels:
        raise PNGError(f"colortype {colortype} no soportado")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    prev = bytearray(stride)
    pixels: list[tuple[int, int, int]] = []
    pos = 0
    for _y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos: pos + stride])
        pos += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upleft = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        prev = line
        for x in range(width):
            base = x * channels
            if colortype == 0:
                g = line[base]
                pixels.append((g, g, g))
            elif colortype == 2:
                pixels.append((line[base], line[base + 1], line[base + 2]))
            elif colortype == 3:
                idx = line[base]
                pixels.append(palette[idx] if idx < len(palette) else (0, 0, 0))
            elif colortype == 4:
                g = line[base]
                pixels.append((g, g, g))
            else:  # RGBA
                pixels.append((line[base], line[base + 1], line[base + 2]))
    return width, height, pixels


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def palette(path: str | Path, *, top: int = 8, tolerance: int = 10) -> list[dict[str, Any]]:
    """Colores dominantes: conteo exacto + fusion de tonos casi identicos."""
    w, h, px = decode(path)
    counts: Counter[tuple[int, int, int]] = Counter()
    step = max(1, (w * h) // 80000)  # muestreo para pantallas grandes
    for i in range(0, len(px), step):
        counts[px[i]] += 1
    total = sum(counts.values()) or 1

    merged: list[list[Any]] = []  # [rgb, count]
    for rgb, n in counts.most_common(512):
        for entry in merged:
            base = entry[0]
            if all(abs(rgb[i] - base[i]) <= tolerance for i in range(3)):
                entry[1] += n
                break
        else:
            merged.append([rgb, n])
    merged.sort(key=lambda e: e[1], reverse=True)
    out = []
    for rgb, n in merged[:top]:
        out.append({
            "hex": hex_of(rgb),
            "rgb": list(rgb),
            "share": round(n / total, 4),
            "luminance": round(luminance(rgb), 3),
        })
    return out


def suggest_theme(colors: list[dict[str, Any]]) -> dict[str, Any]:
    """Deriva un tema (primario, superficie, texto) con contraste legible."""
    if not colors:
        return {"primary": "#3D5AFE", "surface": "#FFFFFF", "on_surface": "#111111"}
    ordered = sorted(colors, key=lambda c: c["share"], reverse=True)
    surface = ordered[0]
    # color mas saturado entre los relevantes = candidato a primario
    def sat(c: dict[str, Any]) -> int:
        r, g, b = c["rgb"]
        return max(r, g, b) - min(r, g, b)
    vivid = max(ordered[:6], key=sat)
    primary = vivid if sat(vivid) >= 24 else ordered[min(1, len(ordered) - 1)]
    dark = luminance(tuple(surface["rgb"])) < 0.4
    on_surface = "#FFFFFF" if dark else "#111111"
    if contrast(tuple(surface["rgb"]), tuple(primary["rgb"])) < 3:
        primary = {"hex": "#3D5AFE", "rgb": [61, 90, 254]}
    return {
        "primary": primary["hex"],
        "surface": surface["hex"],
        "on_surface": on_surface,
        "dark_ui": dark,
    }
