"""Crude stdlib text extraction for Kalshi's contract PDFs (no pypdf/poppler needed).

Decodes each FlateDecode content stream, maps glyph codes through the font's
ToUnicode CMap (font resource names like /F7 are resolved to font objects across
the whole file, which holds for these single-generator PDFs), and starts a new
line whenever a text-positioning operator moves vertically.

Usage: python -I research/scripts/kalshi_ffpts_pdftext.py IN.pdf > OUT.txt
"""

import re
import sys
import zlib


def objects(data: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", data, re.S)}


def stream(obj: bytes) -> bytes | None:
    m = re.search(rb"stream\r?\n(.*?)\r?\nendstream", obj, re.S)
    if not m:
        return None
    try:
        return zlib.decompress(m.group(1))
    except zlib.error:
        return m.group(1)


def parse_cmap(cmap: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            out[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be", "replace")
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst, arr in re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:<([0-9A-Fa-f]+)>|\[([^\]]*)\])", block
        ):
            codes = range(int(lo, 16), int(hi, 16) + 1)
            if dst:
                for i, code in enumerate(codes):
                    out[code] = chr(int(dst, 16) + i)
            else:
                for code, item in zip(codes, re.findall(rb"<([0-9A-Fa-f]+)>", arr)):
                    out[code] = bytes.fromhex(item.decode()).decode("utf-16-be", "replace")
    return out


TOKEN = re.compile(
    rb"/(\w+) [\d.]+ Tf|(-?[\d.]+) (-?[\d.]+) T[dD]|(?:[\d.-]+ ){5}(-?[\d.]+) Tm|<([0-9A-Fa-f]+)> Tj|\[(.*?)\] ?TJ|\((.*?)\) ?Tj|T\*",
    re.S,
)


def extract(path: str) -> str:
    data = open(path, "rb").read()
    objs = objects(data)
    font_cmaps: dict[bytes, tuple[dict[int, str], int]] = {}
    for name, num in re.findall(rb"/(F\w*) (\d+) 0 R", data):
        fobj = objs.get(int(num), b"")
        tu = re.search(rb"/ToUnicode (\d+) 0 R", fobj)
        if tu:
            cmap = stream(objs[int(tu.group(1))]) or b""
            width = 1 if b"<00> <FF>" in cmap else 2
            font_cmaps[name] = (parse_cmap(cmap), width)
    pages = re.findall(rb"/Contents (\d+) 0 R", data)
    out = []
    for num in pages:
        s = stream(objs[int(num)]) or b""
        cmap, width = {}, 2
        for t in TOKEN.finditer(s):
            if t.group(1):
                cmap, width = font_cmaps.get(t.group(1), ({}, 2))
            elif t.group(2) is not None:
                if abs(float(t.group(3))) > 1:
                    out.append("\n")
            elif t.group(4) is not None:
                out.append("\n")
            elif t.group(5) is not None:
                h = t.group(5).decode()
                step = 2 * width
                out.extend(cmap.get(int(h[i : i + step], 16), "?") for i in range(0, len(h), step))
            elif t.group(6) is not None:
                for hx in re.findall(rb"<([0-9A-Fa-f]+)>", t.group(6)):
                    h = hx.decode()
                    step = 2 * width
                    out.extend(cmap.get(int(h[i : i + step], 16), "?") for i in range(0, len(h), step))
            elif t.group(0) == b"T*":
                out.append("\n")
        out.append("\n\f\n")
    text = "".join(out)
    text = re.sub(r"\n[ ]*\n(?=\S)", " ", text)  # word-per-Td output -> running text
    return re.sub(r"[ ]{2,}", " ", text)


if __name__ == "__main__":
    sys.stdout.write(extract(sys.argv[1]))
