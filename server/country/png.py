"""国旗の PNG の検査と、仮の旗（SVG）。標準ライブラリだけで動かす。

上げられた PNG はそのまま出さず、検査して、決まった種類のチャンクだけで作り直す。確かめること:
PNG の印・IHDR（大きさの範囲・色の種類と深さ・インターレースなし）・全チャンクの CRC・
画像のデータが IHDR の大きさとぴったり合うこと（圧縮を決まった大きさまでしか開かない）。
文字や EXIF などの付け足し（tEXt・iTXt・eXIf など）は捨てる。
"""

from __future__ import annotations

import hashlib
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"sRGB", b"cHRM", b"pHYs", b"sBIT"}
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
MIN_SIDE = 16


class PngError(ValueError):
    """国旗にできないわけ（text は画面と API にそのまま出す）。"""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def clean(data: bytes, max_bytes: int, max_side: int) -> tuple[bytes, int, int]:
    """PNG を検査して、決まったチャンクだけで作り直した（PNG, 幅, 高さ）を返す。"""
    if len(data) > max_bytes:
        raise PngError(
            f"国旗の PNG は {max_bytes // 1024} KB までです（いま {len(data) // 1024} KB）"
        )
    if not data.startswith(SIGNATURE):
        raise PngError("PNG の画像ではありません")
    pos, chunks = len(SIGNATURE), []
    while True:
        if pos + 12 > len(data):
            raise PngError("PNG が途中で切れています")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise PngError("PNG が途中で切れています")
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(kind + body) != crc:
            raise PngError("PNG が壊れています（CRC が合いません）")
        chunks.append((kind, body))
        pos = end
        if kind == b"IEND":
            break
    if chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise PngError("PNG の始まりが正しくありません")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if color not in CHANNELS or depth not in DEPTHS[color]:
        raise PngError("PNG の色の形式に対応していません")
    if compression != 0 or filtering != 0:
        raise PngError("PNG の形式が正しくありません")
    if interlace != 0:
        raise PngError(
            "インターレースの PNG は使えません（インターレースなしで保存し直してください）"
        )
    if not (MIN_SIDE <= width <= max_side and MIN_SIDE <= height <= max_side):
        raise PngError(f"国旗の大きさは縦横とも {MIN_SIDE}〜{max_side} ピクセルです")
    names = [kind for kind, _ in chunks]
    if names.count(b"IHDR") != 1 or names.count(b"IEND") != 1 or b"IDAT" not in names:
        raise PngError("PNG の形式が正しくありません")
    if color == 3 and b"PLTE" not in names:
        raise PngError("パレットのある PNG に色の表がありません")
    _check_pixels(
        b"".join(body for kind, body in chunks if kind == b"IDAT"), width, height, depth, color
    )
    out = [SIGNATURE]
    for kind, body in chunks:
        if kind in KEEP:
            head = struct.pack(">I", len(body)) + kind + body
            out.append(head + struct.pack(">I", zlib.crc32(kind + body)))
    return b"".join(out), width, height


def _check_pixels(idat: bytes, width: int, height: int, depth: int, color: int) -> None:
    """画像のデータが、大きさから決まる長さのとおりに開けること（開く長さには上限を付ける）。"""
    row = (width * CHANNELS[color] * depth + 7) // 8 + 1
    expected = row * height
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(idat, expected + 1)
        raw += inflater.flush()
    except zlib.error as e:
        raise PngError("PNG の画像のデータが壊れています") from e
    if len(raw) != expected or inflater.unconsumed_tail:
        raise PngError("PNG の画像のデータの長さが大きさと合いません")


# --- 仮の旗 -----------------------------------------------------------------------------------

# 国旗を上げていない国（や、管理者が隠した国）の仮の旗。国の番号と名前から決まる色と形の SVG。
PALETTE = [
    "#c0392b",
    "#2874a6",
    "#1e8449",
    "#d4ac0d",
    "#7d3c98",
    "#ca6f1e",
    "#17202a",
    "#148f77",
    "#f2f3f4",
]


def provisional_flag(island_id: int, name: str) -> str:
    """仮の旗の SVG（3:2）。同じ国はいつも同じ旗になる。"""
    digest = hashlib.sha256(f"{island_id}:{name}".encode()).digest()
    colors = [PALETTE[b % len(PALETTE)] for b in digest[:3]]
    if colors[1] == colors[0]:
        colors[1] = PALETTE[(digest[1] + 1) % len(PALETTE)]
    if colors[2] in colors[:2]:
        colors[2] = PALETTE[(digest[2] + 2) % len(PALETTE)]
    a, b, c = colors
    shape = digest[3] % 5
    if shape == 0:  # 横の三色
        body = (
            f'<rect width="90" height="20" fill="{a}"/><rect y="20" width="90" height="20" '
            f'fill="{b}"/><rect y="40" width="90" height="20" fill="{c}"/>'
        )
    elif shape == 1:  # 縦の三色
        body = (
            f'<rect width="30" height="60" fill="{a}"/><rect x="30" width="30" height="60" '
            f'fill="{b}"/><rect x="60" width="30" height="60" fill="{c}"/>'
        )
    elif shape == 2:  # 地に円
        body = (
            f'<rect width="90" height="60" fill="{a}"/><circle cx="45" cy="30" r="17" fill="{b}"/>'
        )
    elif shape == 3:  # 十字
        body = (
            f'<rect width="90" height="60" fill="{a}"/><rect x="24" width="14" height="60" '
            f'fill="{b}"/><rect y="23" width="90" height="14" fill="{b}"/>'
        )
    else:  # 斜め
        body = (
            f'<rect width="90" height="60" fill="{a}"/><polygon points="0,60 90,0 90,60" '
            f'fill="{b}"/><circle cx="26" cy="20" r="8" fill="{c}"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 60" width="90" height="60">'
        f'{body}<rect width="90" height="60" fill="none" stroke="#00000033"/></svg>'
    )
