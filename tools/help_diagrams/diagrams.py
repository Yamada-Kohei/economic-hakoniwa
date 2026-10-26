"""手引きの図（SVG）を作る。使い方: python tools/help_diagrams/diagrams.py [出力フォルダ]

出力の既定は server/static/img/help/。手引き（server/templates/web/help.html と help_hakoniwa.html）が
<img> で読む。図には設定の数字を書かない（数字は手引きの表に出る）。文字は図の中の文字列を直せば変わる。
幅 480 の縦長にそろえてある（スマホで縮んでも読める大きさ）。標準ライブラリだけで動く。
"""

# ruff: noqa: E501  (SVG の部品の文字列が長い)
from __future__ import annotations

import sys
from pathlib import Path

FONT = '"Noto Sans JP","Hiragino Kaku Gothic ProN","Meiryo","WenQuanYi Zen Hei",sans-serif'
FG, ACC, BORDER, PALE, HEAD = "#102a6b", "#1f4fa8", "#6f8fd0", "#f4f8ff", "#c9d9ff"
WARM, WARM_BG = "#8a4b00", "#fff1d0"
GOOD, GOOD_BG = "#1b6b2f", "#e2f4e5"
BAD, BAD_BG = "#a32020", "#fde7e7"
GREY = "#5a6a90"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tw(s, size):
    return sum(size if ord(c) > 255 else size * 0.55 for c in s)


class Svg:
    def __init__(self, w, h, title, desc=""):
        self.w, self.h, self.title, self.desc = w, h, title, desc
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=14, anchor="middle", color=FG, bold=False, italic=False):
        w = ' font-weight="bold"' if bold else ""
        self.add(
            f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color}"{w}>{esc(s)}</text>'
        )

    def lines(self, cx, top, lines, size=14, gap=1.35, anchor="middle", color=FG, first_bold=True):
        """上から順に行を並べる。top は最初の行の上端。"""
        y = top + size
        for i, ln in enumerate(lines):
            small = first_bold and i > 0 and size > 13
            sz = size - 1 if small else size
            self.text(cx, round(y, 1), ln, sz, anchor, color, bold=(first_bold and i == 0))
            y += size * gap

    def box(
        self,
        x,
        y,
        w,
        h,
        lines,
        fill=PALE,
        stroke=BORDER,
        size=14,
        color=FG,
        first_bold=True,
        rx=6,
        sw=1.5,
        dash=None,
        pad=None,
    ):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        for i, ln in enumerate(lines):
            sz = size - 1 if first_bold and i > 0 and size > 13 else size
            if tw(ln, sz) > w - 8:
                print(f"WARN {self.title}: '{ln}' {tw(ln, sz):.0f} > {w - 8}")
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'
        )
        n = len(lines)
        total = size * 1.35 * (n - 1) + size
        top = y + (h - total) / 2 - size * 0.12
        self.lines(x + w / 2, top, lines, size, 1.35, "middle", color, first_bold)

    def arrow(self, x1, y1, x2, y2, color=ACC, sw=2, dash=None, both=False, head=True):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        me = ' marker-end="url(#ah)"' if head else ""
        ms = ' marker-start="url(#ah)"' if both else ""
        self.add(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d}{me}{ms}/>'
        )

    def path(self, d, color=ACC, sw=2, dash=None, fill="none", head=False):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        me = ' marker-end="url(#ah)"' if head else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{color}" stroke-width="{sw}"{dd}{me}/>')

    def label(self, x, y, s, size=13, color=ACC, bg="#ffffff", anchor="middle", pad=3):
        """矢印のそばの小さな札。背景の白で線を隠す。"""
        w = sum(size if ord(c) > 255 else size * 0.55 for c in s) + pad * 2
        lx = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.add(
            f'<rect x="{lx:.1f}" y="{y - size:.1f}" width="{w:.1f}" height="{size + 5}" fill="{bg}" opacity="0.92"/>'
        )
        self.text(round(lx + w / 2, 1), y, s, size, "middle", color)

    def render(self):
        defs = (
            "<defs>"
            '<marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>'
            "</defs>"
        )
        body = "\n".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
            f'font-family=\'{FONT}\' role="img" aria-labelledby="t d">\n'
            f'<title id="t">{esc(self.title)}</title><desc id="d">{esc(self.desc)}</desc>\n'
            f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>\n{defs}\n{body}\n</svg>\n'
        )


def overview():
    s = Svg(
        480,
        545,
        "お金と物の流れの全体図",
        "国庫、銀行、民間（家計・施設と企業・市場）、外国の間で、税、公共事業、賃金、預金、融資、国債、貿易がどう流れるかを示す図。",
    )
    s.box(10, 12, 200, 62, ["国庫（政府）", "税を集めて、事業と賃金に使う"], fill=HEAD, size=14)
    s.box(270, 12, 200, 62, ["銀行・中央銀行", "預金を預かり、融資する"], fill=HEAD, size=14)
    s.arrow(210, 43, 270, 43, both=True)
    s.label(240, 36, "国債", 13)
    # 民間の枠
    s.add(
        f'<rect x="10" y="165" width="460" height="260" rx="10" fill="#fbfdff" stroke="{BORDER}" stroke-width="1.5" stroke-dasharray="6 4"/>'
    )
    s.text(22, 185, "民間", 13, "start", GREY, bold=True)
    s.box(25, 205, 150, 62, ["家計", "働き、買い、暮らす"], size=14)
    s.box(305, 205, 150, 62, ["施設・企業", "作って、売って、雇う"], size=14)
    s.box(
        25,
        345,
        430,
        56,
        ["市場", "財ごとに売りと買いを合わせて値段が決まる"],
        fill="#eaf1ff",
        size=14,
    )
    s.arrow(305, 222, 175, 222)
    s.label(240, 216, "賃金", 13)
    s.arrow(175, 250, 305, 250)
    s.label(240, 244, "働き手", 13)
    s.arrow(100, 267, 100, 345)
    s.label(108, 312, "食べ物・品物を買う", 13, anchor="start")
    s.arrow(380, 267, 380, 345, both=True)
    s.label(372, 312, "売る・原料を買う", 13, anchor="end")
    # 国庫と民間
    s.arrow(40, 165, 40, 74)
    s.label(47, 125, "税", 13, anchor="start")
    s.arrow(135, 74, 135, 165)
    s.label(142, 112, "公共事業", 13, anchor="start")
    s.label(142, 130, "公務員の賃金", 13, anchor="start")
    s.label(142, 148, "失業手当", 13, anchor="start")
    # 銀行と民間
    s.arrow(350, 74, 350, 165)
    s.label(343, 125, "融資", 13, anchor="end")
    s.arrow(430, 165, 430, 74)
    s.label(423, 125, "預金", 13, anchor="end")
    # 外国
    s.arrow(90, 401, 90, 462, both=True)
    s.box(10, 462, 160, 44, ["港・商社・船"], size=14)
    s.arrow(170, 484, 310, 484, both=True)
    s.label(240, 478, "輸出・輸入", 13)
    s.label(240, 502, "為替", 13)
    s.box(310, 462, 160, 44, ["外国（ほかの国）"], fill=HEAD, size=14)
    s.text(240, 530, "商社が島の値段と外国の値段の差を見て、自動で売り買いします。", 13, color=GREY)
    return s


def turn():
    steps = [
        ("命令を実行する", "列の上から、行政力の範囲で。政策も建設もここ"),
        ("予算を取り置き、働き手を割り当てる", "賃金の高い施設から、通勤圏の人を雇う"),
        ("電力・生産・工事", "そろった割合だけ作る。資材が届けば工事が進む"),
        ("賃金を払い、市場で売り買い", "賃金が先なので、同じ月に食べ物が買える"),
        ("貿易と為替", "荷下ろし・契約・為替・船出"),
        ("銀行・国債・決算・倒産と参入", "利払いと返済、決算、赤字の整理"),
        ("人口・町・移民・政治・教育・災害", "人が増減し、町が育ち、政情が動く"),
        ("記録", "統計を残し、帳簿が合うか確かめる"),
    ]
    h, gap, top = 50, 16, 14
    H = top + len(steps) * h + (len(steps) - 1) * gap + 14
    s = Svg(
        480,
        H,
        "1か月（1ターン）の更新の流れ",
        "命令の実行、生産、賃金と市場、貿易、銀行と国債、人口と政治、記録の順に、1か月の更新が進むことを示す図。",
    )
    for i, (a, b) in enumerate(steps):
        y = top + i * (h + gap)
        fill = HEAD if i in (0, 3) else PALE
        s.box(48, y, 392, h, [a, b], fill=fill, size=14)
        s.add(f'<circle cx="25" cy="{y + h / 2}" r="12" fill="{ACC}"/>')
        s.text(25, y + h / 2 + 5, str(i + 1), 14, color="#ffffff", bold=True)
        if i < len(steps) - 1:
            s.arrow(250, y + h, 250, y + h + gap)
    y_last = top + (len(steps) - 1) * (h + gap) + h / 2
    s.path(
        f"M440,{y_last} L458,{y_last} L458,{top + h / 2} L440,{top + h / 2}",
        color=GREY,
        dash="5 4",
        head=True,
    )
    my = (y_last + top + h / 2) / 2
    s.add(
        f'<text transform="translate(474 {my}) rotate(-90)" font-size="12" text-anchor="middle" fill="{GREY}">次の月へ</text>'
    )
    return s


def town_growth():
    s = Svg(
        480,
        590,
        "町が育つしくみ",
        "通勤圏の中に人手の足りない施設があり、住まいが足りず、資材がそろったときに町の床が増え、段階が上がることを示す図。",
    )
    # 地図の絵
    s.add(
        f'<rect x="10" y="10" width="460" height="180" rx="8" fill="#eef7ec" stroke="{BORDER}" stroke-width="1.5"/>'
    )
    s.add(
        f'<ellipse cx="150" cy="100" rx="135" ry="78" fill="#ffffff" fill-opacity="0.55" stroke="{ACC}" stroke-width="2" stroke-dasharray="7 5"/>'
    )
    s.text(150, 38, "通勤圏", 13, color=ACC, bold=True)
    # 町（家）
    for dx, dy in ((0, 0), (26, 6), (-26, 8), (10, 30), (-14, 34)):
        x, y = 130 + dx, 92 + dy
        s.add(f'<polygon points="{x},{y} {x + 22},{y} {x + 11},{y - 12}" fill="#c0504d"/>')
        s.add(
            f'<rect x="{x + 2}" y="{y}" width="18" height="14" fill="#ffffff" stroke="{FG}" stroke-width="1"/>'
        )
    s.text(130, 160, "町（住む人）", 13, bold=True)

    # 工場（圏の中）
    def factory(x, y, color):
        s.add(
            f'<rect x="{x}" y="{y}" width="46" height="26" fill="{color}" stroke="{FG}" stroke-width="1.2"/>'
        )
        s.add(
            f'<rect x="{x + 6}" y="{y - 14}" width="8" height="14" fill="{color}" stroke="{FG}" stroke-width="1.2"/>'
        )
        s.add(
            f'<rect x="{x + 28}" y="{y - 8}" width="8" height="8" fill="{color}" stroke="{FG}" stroke-width="1.2"/>'
        )

    factory(212, 70, "#f4c06a")
    s.text(236, 118, "人手不足の工場", 13, bold=True)
    s.arrow(165, 104, 214, 90, color=GOOD, sw=2.5)
    s.text(182, 78, "通勤", 13, color=GOOD)
    factory(390, 112, "#d6d6d6")
    s.text(413, 160, "遠すぎて通えない", 13, color=GREY)
    # 条件
    s.text(240, 222, "次が全部そろったときだけ、床が増えます", 15, bold=True)
    items = [
        "通勤圏に、人手が足りない施設がある",
        "近くの町の空きと、仕事のない人では足りない",
        "住む人が望む広さで使う床が、今の床にほぼ届く",
        "床を足す資材がそろっている",
        "上の段階へは、上下水道の整備度も足りている",
    ]
    y = 240
    for i, t in enumerate(items):
        s.box(20, y, 440, 38, [t], fill=PALE, size=14)
        s.add(f'<circle cx="40" cy="{y + 19}" r="11" fill="{ACC}"/>')
        s.text(40, y + 24, str(i + 1), 13, color="#ffffff", bold=True)
        y += 38 + 8
    s.arrow(240, y - 8, 240, y + 12)
    s.box(
        20,
        y + 12,
        440,
        50,
        ["町のマスの床が増える", "段階の上限を超えると、次の段階（村・町・都市…）に上がる"],
        fill=GOOD_BG,
        stroke=GOOD,
        size=14,
    )
    s.text(240, y + 86, "資材は家計が市場で買う（計画経済では国が買う）。", 13, color=GREY)
    s.box(
        20,
        y + 100,
        440,
        40,
        ["近道: 国は命令で、国の住宅の床を足せる"],
        fill=WARM_BG,
        stroke=WARM,
        size=13,
        dash="5 4",
    )
    s.h = y + 152
    s.w = 480
    return s


def ladder():
    names = ["村", "町", "都市", "大都市", "高層都市", "超高層都市"]
    heights = [1, 2, 4, 6, 9, 12]
    water = ["", "", "必要", "もっと", "ほぼ全部", "全部"]
    s = Svg(
        480,
        392,
        "町の6つの段階",
        "村・町・都市・大都市・高層都市・超高層都市の順に建物が高くなる。都市から上は上下水道、高層都市から上は土地が足りないときだけ育つ。",
    )
    unit, base, w, gap, x0 = 17, 236, 66, 9, 19
    for i, (n, hh) in enumerate(zip(names, heights, strict=True)):
        x = x0 + i * (w + gap)
        top = base - hh * unit
        shade = ["#dbe6ff", "#c4d6ff", "#a9c2f5", "#8fb0ee", "#6f98e4", "#4f7fd8"][i]
        s.add(
            f'<rect x="{x}" y="{top}" width="{w}" height="{hh * unit}" fill="{shade}" stroke="{FG}" stroke-width="1.2"/>'
        )
        for r in range(hh):
            for c in range(3):
                wx, wy = x + 8 + c * 19, top + 4 + r * unit
                s.add(
                    f'<rect x="{wx}" y="{wy}" width="11" height="{unit - 8}" fill="#fffbe0" stroke="{FG}" stroke-width="0.6"/>'
                )
        s.text(x + w / 2, base + 20, n, 12 if len(n) > 3 else 13, bold=True)
        if water[i]:
            s.box(x, base + 30, w, 24, [water[i]], fill=PALE, size=13, rx=4, sw=1)
    s.text(240, base + 78, "↑ 上下水道の整備度（上の段階ほど高く要る）", 13, color=GREY)
    s.add(
        f'<line x1="{x0}" y1="{base}" x2="{x0 + 6 * w + 5 * gap}" y2="{base}" stroke="{FG}" stroke-width="2"/>'
    )
    # 土地が足りないとき
    bx = x0 + 4 * (w + gap)
    s.add(
        f'<rect x="{bx - 3}" y="14" width="{2 * w + gap + 6}" height="{base - 14 + 60}" rx="6" fill="none" stroke="{WARM}" stroke-width="1.5" stroke-dasharray="5 4"/>'
    )
    s.box(
        bx - 3,
        base + 88,
        2 * w + gap + 6,
        40,
        ["空いた平地が", "ないときだけ"],
        fill=WARM_BG,
        stroke=WARM,
        size=13,
        color=WARM,
        first_bold=False,
    )
    s.box(
        x0,
        base + 88,
        4 * (w + gap) - 9,
        40,
        ["大都市が満ちても、空いた平地があるうちは", "高く建てずに近くへ村が広がる"],
        fill=PALE,
        size=13,
        first_bold=False,
    )
    return s


def person(s, x, y, color=FG):
    s.add(
        f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"/><line x1="{x}" y1="{y + 5}" x2="{x}" y2="{y + 18}" stroke="{color}" stroke-width="2.5"/>'
        f'<line x1="{x - 6}" y1="{y + 10}" x2="{x + 6}" y2="{y + 10}" stroke="{color}" stroke-width="2.5"/>'
        f'<line x1="{x}" y1="{y + 18}" x2="{x - 5}" y2="{y + 28}" stroke="{color}" stroke-width="2.5"/><line x1="{x}" y1="{y + 18}" x2="{x + 5}" y2="{y + 28}" stroke="{color}" stroke-width="2.5"/>'
    )


def tractor(s, x, y):
    s.add(
        f'<rect x="{x}" y="{y}" width="26" height="14" fill="#e0a030" stroke="{FG}" stroke-width="1.2"/><rect x="{x + 14}" y="{y - 10}" width="10" height="10" fill="#e0a030" stroke="{FG}" stroke-width="1.2"/>'
        f'<circle cx="{x + 7}" cy="{y + 17}" r="8" fill="#555" stroke="{FG}"/><circle cx="{x + 23}" cy="{y + 19}" r="5" fill="#555" stroke="{FG}"/>'
    )


def sheaf(s, x, y):
    s.add(
        f'<ellipse cx="{x}" cy="{y}" rx="6" ry="9" fill="#e8c04a" stroke="{FG}" stroke-width="1"/><line x1="{x - 5}" y1="{y + 3}" x2="{x + 5}" y2="{y + 3}" stroke="{FG}" stroke-width="1"/>'
    )


def farm_grades():
    s = Svg(
        480,
        388,
        "農場の段階",
        "手作業、機械化、近代化の順に、働き手が減り、収穫が増える。機械化は燃料と機械の傷み、近代化は用水路と肥料が要る。",
    )
    xs = [8, 166, 324]
    titles = ["手作業", "機械化", "近代化"]
    for i, x in enumerate(xs):
        s.add(
            f'<rect x="{x}" y="8" width="148" height="224" rx="6" fill="{PALE}" stroke="{BORDER}" stroke-width="1.5"/>'
        )
        s.text(x + 74, 31, titles[i], 15, bold=True)
        # 畑
        s.add(
            f'<rect x="{x + 10}" y="42" width="128" height="72" fill="#d9c27a" stroke="{FG}" stroke-width="1"/>'
        )
        for r in range(4):
            s.add(
                f'<line x1="{x + 10}" y1="{54 + r * 16}" x2="{x + 138}" y2="{54 + r * 16}" stroke="#a88f3a" stroke-width="1.5"/>'
            )
        if i == 2:
            s.add(f'<rect x="{x + 10}" y="108" width="128" height="6" fill="#5aa7e0"/>')
            s.text(x + 74, 106, "用水路", 11, color="#ffffff")
    # 人
    for k in range(4):
        person(s, 28 + k * 28, 124)
    for k in range(2):
        person(s, 196 + k * 28, 124)
        person(s, 354 + k * 28, 124)
    tractor(s, 210, 54)
    tractor(s, 368, 54)
    # 収穫
    for k in range(3):
        sheaf(s, 40 + k * 22, 196)
    for k in range(3):
        sheaf(s, 194 + k * 22, 196)
    for k in range(6):
        sheaf(s, 340 + k * 19, 196)
    s.text(xs[0] + 74, 224, "収穫", 13, color=GREY)
    s.text(xs[1] + 74, 224, "収穫", 13, color=GREY)
    s.text(xs[2] + 74, 224, "収穫", 13, color=GREY)
    for i in (0, 1):
        s.arrow(xs[i] + 148, 128, xs[i + 1], 128, color=ACC)
    s.text(xs[0] + 153, 114, "工事", 13, color=ACC)
    s.text(xs[1] + 153, 114, "工事", 13, color=ACC)
    notes = [
        ["働き手が多い", "収穫は少なめ", "機械も肥料もなし", ""],
        ["働き手が大きく減る", "収穫は少し増える", "燃料を毎月使い、", "機械は傷む"],
        ["収穫が大きく増える", "用水路を引き、", "肥料（化学品）を", "たくさん使う"],
    ]
    for i, x in enumerate(xs):
        s.box(x, 242, 148, 80, notes[i], fill="#ffffff", size=13, first_bold=False)
    s.box(
        8,
        334,
        464,
        44,
        [
            "標準の計画の国は、人手が足りないとき機械化し、",
            "食料が足りず新しい平地もないとき近代化します",
        ],
        fill=WARM_BG,
        stroke=WARM,
        size=13,
        first_bold=False,
    )
    return s


def orders():
    s = Svg(
        480,
        360,
        "命令の列と行政力",
        "命令は列の上から行政力の範囲で実行され、足りなくなった所から下は次の月に繰り越される。重い命令が先頭なら、その月の行政力を全部使って実行される。",
    )
    s.text(14, 24, "今月の行政力（枠）", 13, "start", FG, bold=True)
    x0, W = 14, 452
    s.add(
        f'<rect x="{x0}" y="34" width="{W}" height="42" fill="#ffffff" stroke="{ACC}" stroke-width="2"/>'
    )
    segs = [("命令1", 110), ("命令2", 80), ("命令3", 100)]
    x = x0
    for n, w in segs:
        s.box(x, 34, w, 42, [n], fill=HEAD, size=14, rx=0, sw=1.2)
        x += w
    s.text(x + (x0 + W - x) / 2, 60, "残り", 13, color=GREY)
    s.arrow(x, 82, x, 128, color=WARM)
    s.text(x + 8, 102, "ここで止まる", 13, "start", WARM, bold=True)
    s.text(x + 8, 120, "（命令4は枠に入らない）", 13, "start", WARM)
    s.text(14, 140, "来月の先頭に繰り越し", 13, "start", FG, bold=True)
    s.add(
        f'<rect x="{x0}" y="150" width="{W}" height="42" fill="#ffffff" stroke="{GREY}" stroke-width="1.5" stroke-dasharray="5 4"/>'
    )
    s.box(x0, 150, 150, 42, ["命令4"], fill=WARM_BG, stroke=WARM, size=14, rx=0, sw=1.2)
    s.box(x0 + 150, 150, 60, 42, ["命令5"], fill=WARM_BG, stroke=WARM, size=14, rx=0, sw=1.2)
    s.text(x0 + 330, 176, "（来月、上から実行）", 13, color=GREY)
    s.text(14, 228, "例外: 1つで枠を超える重い命令（宣戦など）", 13, "start", FG, bold=True)
    s.add(
        f'<rect x="{x0}" y="238" width="{W}" height="42" fill="#ffffff" stroke="{ACC}" stroke-width="2"/>'
    )
    s.box(x0, 238, W, 42, ["宣戦（重い命令）が列の先頭"], fill=HEAD, size=14, rx=0, sw=1.2)
    s.text(240, 304, "先頭で、その月の行政力がまだ使われていなければ、", 13)
    s.text(240, 324, "枠を全部使って実行されます", 13)
    return s


def military():
    steps = [
        ("兵舎・軍港を建てる", "陸の部隊は兵舎、艦隊は軍港（建ち終わってから）"),
        ("兵・乗員を集める", "志願か徴兵。賃金が低いと失業者しか集まらない"),
        ("装備をそろえる", "武器や軍艦。国防費の装備の分で、倉庫と市場から"),
        ("編成する", "そろうと「そろった」、足りないうちは「編成中」"),
        ("燃料を備える", "国の倉庫に補給の目標を置く。航海と機甲の移動で使う"),
        ("動かす・宣戦する", "部隊は島の地図から、艦隊は世界マップから"),
    ]
    h, gap, top = 50, 16, 12
    H = top + len(steps) * h + (len(steps) - 1) * gap + 112
    s = Svg(
        480,
        H,
        "軍をつくって動かすまでの手順",
        "兵舎か軍港を建て、兵を集め、装備をそろえ、編成し、燃料を備えてから動かす手順の図。足りない物があると編成中のまま止まる。",
    )
    for i, (a, b) in enumerate(steps):
        y = top + i * (h + gap)
        s.box(48, y, 410, h, [a, b], fill=HEAD if i == 3 else PALE, size=14)
        s.add(f'<circle cx="24" cy="{y + h / 2}" r="12" fill="{ACC}"/>')
        s.text(24, y + h / 2 + 5, str(i + 1), 14, color="#ffffff", bold=True)
        if i < len(steps) - 1:
            s.arrow(253, y + h, 253, y + h + gap)
    # 足りないとき
    y3 = top + 3 * (h + gap) + h / 2
    y2 = top + 2 * (h + gap) + h / 2
    s.path(f"M458,{y3} L472,{y3} L472,{y2} L458,{y2}", color=WARM, dash="5 4", head=True)
    yb = top + len(steps) * h + (len(steps) - 1) * gap + 14
    s.box(
        14,
        yb,
        452,
        84,
        [
            "維持するには",
            "兵の賃金と食べ物（国防費）が毎月要ります。",
            "賃金が遅れると士気が下がり、",
            "食べ物が尽きると練度が落ちます",
        ],
        fill=WARM_BG,
        stroke=WARM,
        size=13,
    )
    return s


def politics():
    s = Svg(
        480,
        486,
        "政情が動くしくみ",
        "幸福度・失業・物価・格差・体制の特色・出来事が政情の目標を決め、政情は目標へゆっくり近づく。段階は落ち着き、デモ、暴動、反乱の前触れの順に悪化し、行政力が減り、暴動では施設が止まる。",
    )
    chips = ["幸福度", "失業", "物価上昇", "格差", "体制の特色", "出来事"]
    for i, c in enumerate(chips):
        x = 14 + (i % 3) * 154
        y = 12 + (i // 3) * 40
        s.box(x, y, 142, 32, [c], fill=PALE, size=14)
    s.arrow(240, 96, 240, 124)
    s.box(
        80,
        124,
        320,
        44,
        ["政情の目標", "失業は一定の率を超えた分だけ強く目標を下げる"],
        fill=HEAD,
        size=14,
    )
    s.arrow(240, 168, 240, 200)
    s.label(330, 190, "少しずつ近づく", 13)
    s.box(80, 200, 320, 36, ["現在の政情"], fill=HEAD, size=14)
    s.arrow(240, 236, 240, 256)
    cols = [
        ("落ち着き", GOOD_BG, GOOD, ["ふつうに", "命令が通る"]),
        ("デモ", "#fff8cc", "#8a7a00", ["行政力が", "減る"]),
        ("暴動", WARM_BG, WARM, ["さらに行政力が", "減る。町の近く", "の施設が止まる", "・壊れる"]),
        (
            "反乱の前触れ",
            BAD_BG,
            BAD,
            ["行政力も被害も", "いっそう厳しい", "続くと反乱・", "内戦へ"],
        ),
    ]
    w, gap, x0 = 104, 10, 14
    for i, (n, bg, st, eff) in enumerate(cols):
        x = x0 + i * (w + gap)
        s.box(x, 262, w, 40, [n], fill=bg, stroke=st, size=13 if len(n) > 4 else 14)
        s.box(x, 308, w, 78, eff, fill="#ffffff", stroke=st, size=13, first_bold=False)
        if i < 3:
            s.arrow(x + w, 282, x + w + gap, 282, color=GREY, sw=1.5)
    s.text(330, 252, "悪くなるほど右の段階へ", 13, "start", GREY)
    # 警察署
    s.box(
        14,
        404,
        452,
        66,
        [
            "警察署（治安費で警察官を雇う）",
            "人手が足りるほど、暴動で止まる・壊れる施設が減る",
            "農林水産業は、暴動では止まらない",
        ],
        fill=PALE,
        size=13,
    )
    s.arrow(14 + 2 * 114 + 52, 404, 14 + 2 * 114 + 52, 388, color=GOOD)
    return s


def trade():
    s = Svg(
        480,
        436,
        "貿易の2つの道",
        "貿易圏を通す道では、商社が島の市場と貿易圏の市場の値段の差を見て、港と船で運ぶ。二国間契約は、売る国の倉庫から船で運び、買う国の市場か倉庫に届ける。",
    )
    s.text(14, 24, "① 貿易圏を通す（商社が自動で売り買い）", 14, "start", FG, bold=True)
    s.box(10, 40, 130, 70, ["島の市場", "自国の通貨"], size=14)
    s.box(
        175, 40, 130, 70, ["商社・港・船", "差が取り分と運賃を", "超えると売買"], fill=HEAD, size=14
    )
    s.box(340, 40, 130, 70, ["貿易圏の市場", "決済通貨"], size=14)
    s.arrow(140, 75, 175, 75, both=True)
    s.arrow(305, 75, 340, 75, both=True)
    s.text(240, 134, "為替のレートで、自国の通貨と決済通貨を替えます", 13, color=GREY)
    s.text(14, 184, "② 二国間契約（政府どうし）", 14, "start", FG, bold=True)
    s.box(10, 196, 130, 82, ["売る国", "国の倉庫から出す", "足りなければ", "市場で買う"], size=14)
    s.box(175, 196, 130, 82, ["船", "片道の日数は", "距離で決まる"], fill=HEAD, size=14)
    s.box(340, 196, 130, 82, ["買う国", "市場に出す（既定）", "か国の倉庫に置く"], size=14)
    s.arrow(140, 237, 175, 237)
    s.arrow(305, 237, 340, 237)
    s.text(240, 300, "代金は、約束した通貨で買う国が払います", 13, color=GREY)
    s.box(
        10,
        326,
        460,
        92,
        [
            "荷が動くために要る物",
            "港（荷を積み下ろす）・船（燃料を使い、古くなる）",
            "代金の通貨（外貨は為替か中央銀行の手持ちから）",
            "電力は運べません",
        ],
        fill=WARM_BG,
        stroke=WARM,
        size=13,
    )
    return s


def diamond(s, cx, cy, w, h, lines, fill=HEAD, stroke=BORDER, size=13):
    pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
    s.add(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
    top = cy - (size * 1.35 * (len(lines) - 1) + size) / 2 - size * 0.12
    s.lines(cx, top, lines, size, 1.35, "middle", FG, False)


def bonds():
    s = Svg(
        480,
        500,
        "国庫が足りないときの国債",
        "国庫が足りない分は国債を入札で発行する。売れ残った分は、政策が中央銀行の引き受けで中央銀行が独立していなければ中央銀行が引き受け、そうでなければ発行せず支出が削られる。",
    )
    s.box(110, 10, 260, 40, ["国庫が足りない"], fill=BAD_BG, stroke=BAD, size=14)
    s.arrow(240, 50, 240, 76)
    s.box(
        40,
        76,
        400,
        56,
        ["国債を入札で発行する", "利回りは政策金利・期間・物価・「返す力の比」で決まる"],
        fill=HEAD,
        size=14,
    )
    s.arrow(150, 132, 150, 168)
    s.arrow(330, 132, 330, 168)
    s.box(
        40,
        168,
        220,
        44,
        ["売れた分", "銀行などが買い、国庫に入る"],
        fill=GOOD_BG,
        stroke=GOOD,
        size=14,
    )
    s.box(280, 168, 190, 44, ["売れ残り", "新しい支出の分"], fill=WARM_BG, stroke=WARM, size=14)
    s.arrow(375, 212, 375, 258, color=WARM)
    diamond(
        s,
        330,
        296,
        280,
        118,
        ["政策が中央銀行の", "引き受けで、かつ中央銀行が", "独立していない？"],
        size=13,
    )
    s.arrow(190, 296, 150, 296, color=GOOD)
    s.text(172, 288, "はい", 13, color=GOOD, bold=True)
    s.arrow(330, 355, 330, 384, color=BAD)
    s.text(342, 376, "いいえ", 13, "start", BAD, bold=True)
    s.box(14, 270, 136, 52, ["中央銀行が", "引き受ける"], fill=GOOD_BG, stroke=GOOD, size=14)
    s.box(
        190,
        384,
        280,
        52,
        ["発行しない", "支払いの順番の後ろから支出が削られる"],
        fill=BAD_BG,
        stroke=BAD,
        size=14,
    )
    s.box(
        14,
        452,
        452,
        36,
        ["満期の国債は別: 中央銀行の持ち分は同じ期間の国債に乗り換えます"],
        fill=PALE,
        size=13,
    )
    return s


DIAGRAMS = {
    "overview": overview,
    "turn-flow": turn,
    "town-growth": town_growth,
    "town-stages": ladder,
    "farm-grades": farm_grades,
    "orders-queue": orders,
    "military-build": military,
    "politics-stages": politics,
    "trade-routes": trade,
    "bond-issue": bonds,
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "server/static/img/help")
    out.mkdir(parents=True, exist_ok=True)
    for name, build in DIAGRAMS.items():
        (out / f"{name}.svg").write_text(build().render(), encoding="utf-8")


if __name__ == "__main__":
    main()
