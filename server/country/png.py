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
    palettes = [body for kind, body in chunks if kind == b"PLTE"]
    if len(palettes) > 1 or any(len(p) % 3 or not p or len(p) > 768 for p in palettes):
        raise PngError("PNG の色の表が正しくありません")
    if palettes and color in (0, 4):
        raise PngError("PNG の色の形式に対応していません")
    _check_pixels(
        b"".join(body for kind, body in chunks if kind == b"IDAT"), width, height, depth, color
    )
    # 決まった順（IHDR・補助・PLTE・tRNS・IDAT 1 つ・IEND）で作り直す。画像のデータは 1 つにまとめる
    body_of = {kind: body for kind, body in chunks if kind in KEEP and kind != b"IDAT"}
    idat = b"".join(body for kind, body in chunks if kind == b"IDAT")
    order = [b"IHDR", b"gAMA", b"sRGB", b"cHRM", b"pHYs", b"sBIT", b"PLTE", b"tRNS", b"IDAT"]
    out = [SIGNATURE]
    for kind in [*order, b"IEND"]:
        body = idat if kind == b"IDAT" else body_of.get(kind)
        if body is not None:
            out.append(struct.pack(">I", len(body)) + kind + body)
            out.append(struct.pack(">I", zlib.crc32(kind + body)))
    return b"".join(out), width, height


def _check_pixels(idat: bytes, width: int, height: int, depth: int, color: int) -> None:
    """画像のデータが、大きさから決まる長さのとおりにちょうど開けること。開く長さには上限を付け
    （残りの入力は開かない）、各行の頭のフィルタの印が 0〜4 であることも見る。"""
    row = (width * CHANNELS[color] * depth + 7) // 8 + 1
    expected = row * height
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(idat, expected + 1)
    except zlib.error as e:
        raise PngError("PNG の画像のデータが壊れています") from e
    if len(raw) != expected or not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
        raise PngError("PNG の画像のデータの長さが大きさと合いません")
    if any(raw[i] > 4 for i in range(0, expected, row)):
        raise PngError("PNG の画像のデータが壊れています")


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
    if digest[4] % 4 == 0:  # 一部の国は新しい型（5〜8）。残りの国は今までの型のまま
        shape = 5 + digest[5] % 4
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
    elif shape == 4:  # 斜め
        body = (
            f'<rect width="90" height="60" fill="{a}"/><polygon points="0,60 90,0 90,60" '
            f'fill="{b}"/><circle cx="26" cy="20" r="8" fill="{c}"/>'
        )
    elif shape == 5:  # 横の帯と星
        body = (
            f'<rect width="90" height="60" fill="{a}"/><rect y="21" width="90" height="18" '
            f'fill="{b}"/><polygon points="45,20 47.4,26.8 54.5,26.9 48.8,31.2 50.9,38.1 '
            f'45,34 39.1,38.1 41.2,31.2 35.5,26.9 42.6,26.8" fill="{c}"/>'
        )
    elif shape == 6:  # 地に星
        body = (
            f'<rect width="90" height="60" fill="{a}"/><polygon points="45,14 48.8,24.8 '
            f'60.2,25.1 51.1,32 54.4,42.9 45,36.4 35.6,42.9 38.9,32 29.8,25.1 41.2,24.8" '
            f'fill="{b}"/>'
        )
    elif shape == 7:  # 山並みと日
        body = (
            f'<rect width="90" height="60" fill="{a}"/><circle cx="68" cy="15" r="7" '
            f'fill="{c}"/><polygon points="0,60 24,28 46,60" fill="{b}"/>'
            f'<polygon points="34,60 60,24 90,60" fill="{b}"/>'
        )
    else:  # 紋章（盾）
        body = (
            f'<rect width="90" height="60" fill="{a}"/><path d="M45 8 L69 15 V31 '
            f'C69 43 59 51 45 57 C31 51 21 43 21 31 V15 Z" fill="{b}"/>'
            f'<circle cx="45" cy="29" r="8" fill="{c}"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 60" width="90" height="60">'
        f'{body}<rect width="90" height="60" fill="none" stroke="#00000033"/></svg>'
    )


def provisional_monument(island_id: int, number: int) -> str:
    """自作の記念碑の仮の絵の SVG（石の碑）。同じ碑はいつも同じ色になる。"""
    digest = hashlib.sha256(f"monument:{island_id}:{number}".encode()).digest()
    tint = PALETTE[digest[0] % len(PALETTE)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">'
        '<rect width="96" height="96" fill="#e8e4da"/>'
        '<polygon points="36,84 40,16 56,16 60,84" fill="#8d8a82"/>'
        '<polygon points="40,16 48,8 56,16" fill="#a9a69d"/>'
        f'<rect x="30" y="84" width="36" height="6" fill="{tint}"/>'
        "</svg>"
    )
