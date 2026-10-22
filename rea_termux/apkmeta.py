"""Lectura del AndroidManifest.xml binario (AXML) sin aapt.

En Termux no siempre hay `aapt`; este modulo extrae el package id y algunos
datos basicos directamente del APK (zip -> AXML), con parser propio.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any

RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
RES_XML_RESOURCE_MAP = 0x0180

TYPE_STRING = 0x03


class AXML:
    def __init__(self, data: bytes):
        self.data = data
        self.strings: list[str] = []
        self.resource_ids: list[int] = []

    # ------------------------------------------------------------------
    def parse(self) -> None:
        off = 8  # salta la cabecera del documento
        n = len(self.data)
        while off + 8 <= n:
            ctype, hsize, csize = struct.unpack_from("<HHI", self.data, off)
            if csize < 8:
                break
            body = self.data[off + hsize: off + csize]
            if ctype == RES_STRING_POOL:
                # se pasa el chunk completo: stringsStart es relativo a su inicio
                self.strings = self._parse_strings(self.data[off:off + csize])
            elif ctype == RES_XML_RESOURCE_MAP:
                count = len(body) // 4
                self.resource_ids = list(struct.unpack_from(f"<{count}I", body, 0))
            off += csize

    @staticmethod
    def _parse_strings(chunk: bytes) -> list[str]:
        """chunk incluye la cabecera ResChunk_header (8 bytes)."""
        if len(chunk) < 28:
            return []
        strcount, _stylecount, flags, strings_start, _styles_start = struct.unpack_from(
            "<IIIII", chunk, 8
        )
        if strings_start <= 0 or strings_start > len(chunk):
            return []
        utf8 = bool(flags & 0x100)
        if 28 + strcount * 4 > len(chunk):
            return []
        offsets = struct.unpack_from(f"<{strcount}I", chunk, 28)
        out: list[str] = []
        for o in offsets:
            p = strings_start + o
            if p >= len(chunk):
                out.append("")
                continue
            try:
                if utf8:
                    def varint(q: int) -> tuple[int, int]:
                        b0 = chunk[q]
                        if b0 & 0x80:
                            return ((b0 & 0x7F) << 8) | chunk[q + 1], q + 2
                        return b0, q + 1
                    _chars, p = varint(p)
                    blen, p = varint(p)
                    out.append(chunk[p:p + blen].decode("utf-8", "replace"))
                else:
                    (slen,) = struct.unpack_from("<H", chunk, p)
                    p += 2
                    out.append(chunk[p:p + slen * 2].decode("utf-16-le", "replace"))
            except (struct.error, IndexError):
                out.append("")
        return out

    # ------------------------------------------------------------------
    def manifest_package(self) -> str | None:
        """Recorre los start-element buscando <manifest package=...>."""
        off = 8
        n = len(self.data)
        while off + 8 <= n:
            ctype, hsize, csize = struct.unpack_from("<HHI", self.data, off)
            if csize < 8:
                break
            if ctype == RES_XML_START_ELEMENT:
                pkg = self._start_element_package(off + hsize)
                if pkg:
                    return pkg
            off += csize
        return None

    def _start_element_package(self, p: int) -> str | None:
        try:
            _ns, name_idx = struct.unpack_from("<II", self.data, p)
            (attr_start,) = struct.unpack_from("<H", self.data, p + 8)
            (attr_size, attr_count) = struct.unpack_from("<HH", self.data, p + 10)
            tag = self._str(name_idx)
            if tag != "manifest":
                return None
            a = p + attr_start
            for _ in range(attr_count):
                ns_i, name_i, raw_i = struct.unpack_from("<III", self.data, a)
                typed_size, _res0, dtype = struct.unpack_from("<HBB", self.data, a + 12)
                data_i = struct.unpack_from("<I", self.data, a + 16)[0]
                aname = self._str(name_i)
                if aname == "package":
                    if raw_i != 0xFFFFFFFF:
                        return self._str(raw_i) or None
                    if dtype == TYPE_STRING:
                        return self._str(data_i) or None
                    # resource-id 0x0101021b == android:package
                    return None
                a += attr_size
        except (struct.error, IndexError):
            return None
        return None

    def _str(self, i: int) -> str:
        if 0 <= i < len(self.strings):
            return self.strings[i]
        return ""


def read_apk_meta(apk: str | Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(apk), "package": None, "error": None}
    try:
        with zipfile.ZipFile(apk) as zf:
            names = zf.namelist()
            out["entries"] = len(names)
            out["has_native_libs"] = any(n.startswith("lib/") for n in names)
            if "AndroidManifest.xml" in names:
                ax = AXML(zf.read("AndroidManifest.xml"))
                ax.parse()
                out["package"] = ax.manifest_package()
                out["strings"] = len(ax.strings)
            else:
                out["error"] = "sin AndroidManifest.xml (¿es un APK/AAB?)"
    except (zipfile.BadZipFile, OSError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def detect_package(apk: str | Path) -> str | None:
    return read_apk_meta(apk).get("package")
