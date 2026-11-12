# -*- coding: utf-8 -*-
"""运行时生成线性图标（对应 design/preview.html 里的 SVG 图标）。

Tk 的 PhotoImage 不认 SVG，引入图标字体又要往现场塞字体文件；
这里用已在 requirements.txt 里的 Pillow 在内存里画 24x24 线性图标，
4x 超采样后 LANCZOS 缩小，得到带抗锯齿的「矢量感」位图：
不落盘、不新增依赖、高 DPI 不发糊、主题切换换色即可。

对外接口：
    get(name, color, size=18) -> ImageTk.PhotoImage（带缓存）
    names() / clear_cache()

注意：PhotoImage 必须被控件持有引用，否则会被 GC 导致图标消失；
main_window 里统一挂在 widget._ntk_icon 上。

Python 3.8 兼容：类型注解一律 typing，不用 walrus / X | None。
"""
from typing import Dict, Tuple

from PIL import Image, ImageDraw

_SUPERSAMPLE = 4
_STROKE = 1.8        # 与 HTML 里 SVG 的 stroke-width 完全一致
_CACHE = {}          # (name, color, size) -> PhotoImage


def _pen(draw, color, k):
    """返回 4 个归一化绘制原语，坐标按 24x24 设计、乘 k 超采样。

    对齐 HTML 里 SVG 的 stroke-linecap/linejoin=\"round\"：PIL 的 line 默认是
    平切端点，缩到 16px 后每条线的两端都是硬切口，看起来就是「歪七扭八」。
    这里给线段端点和多边形顶点补一个半径 = 线宽/2 的圆点来模拟 round cap。
    """
    def _dot(cx, cy, width):
        r = width / 2.0
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    def line(pts, w=_STROKE):
        coords = [(x * k, y * k) for x, y in pts]
        width = max(1, int(round(_STROKE * k)))
        draw.line(coords, fill=color, width=width, joint="curve")
        for cx, cy in (coords[0], coords[-1]):
            _dot(cx, cy, width)

    def rect(box, w=_STROKE, radius=0):
        b = [v * k for v in box]
        width = max(1, int(round(_STROKE * k)))
        if radius:
            draw.rounded_rectangle(b, radius=int(radius * k),
                                   outline=color, width=width)
        else:
            draw.rectangle(b, outline=color, width=width)
            for cx, cy in ((b[0], b[1]), (b[2], b[1]),
                           (b[2], b[3]), (b[0], b[3])):
                _dot(cx, cy, width)

    def oval(box, w=_STROKE):
        draw.ellipse([v * k for v in box], outline=color,
                     width=max(1, int(round(_STROKE * k))))

    def poly(pts, w=_STROKE, fill=False):
        xy = [(x * k, y * k) for x, y in pts]
        width = max(1, int(round(_STROKE * k)))
        if fill:
            draw.polygon(xy, fill=color)
        else:
            draw.line(xy + [xy[0]], fill=color, width=width, joint="curve")
            for cx, cy in xy:
                _dot(cx, cy, width)

    return line, rect, oval, poly


# —— 图标定义（与 HTML sprite 的语义一一对应）——
def i_search(d, c, k):
    L, R, O, P = _pen(d, c, k)
    O((3.2, 3.2, 15.4, 15.4))
    L([(15.6, 15.6), (21, 21)], 2.2)


def i_key(d, c, k):
    L, R, O, P = _pen(d, c, k)
    O((2.8, 8.2, 9.2, 14.6))
    L([(8.4, 11.4), (21, 2.6)], 2.0)
    L([(16.4, 6.6), (19, 9.2)], 2.0)
    L([(13.8, 9.2), (16.4, 11.8)], 2.0)


def i_clock(d, c, k):
    L, R, O, P = _pen(d, c, k)
    O((3, 3, 21, 21))
    L([(12, 6.6), (12, 12.2), (16.2, 14.6)], 2.0)


def i_info(d, c, k):
    L, R, O, P = _pen(d, c, k)
    O((3, 3, 21, 21))
    L([(12, 10.8), (12, 17)], 2.0)
    O((10.8, 6.2, 13.2, 8.6), 1.2)


def i_sun(d, c, k):
    import math
    L, R, O, P = _pen(d, c, k)
    O((7.2, 7.2, 16.8, 16.8))
    for a in range(0, 360, 45):
        rad = math.radians(a)
        L([(12 + 8.6 * math.cos(rad), 12 + 8.6 * math.sin(rad)),
           (12 + 11 * math.cos(rad), 12 + 11 * math.sin(rad))], 1.8)


def i_contrast(d, c, k):
    """主题切换：半填充圆（比月牙好画，语义也更直观）。"""
    L, R, O, P = _pen(d, c, k)
    O((3, 3, 21, 21))
    d.pieslice([3 * k, 3 * k, 21 * k, 21 * k], start=90, end=270, fill=c)


def i_play(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(7, 4.5), (19.5, 12), (7, 19.5)], fill=True)


def i_square(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((6, 6, 18, 18), 2.0, radius=1.5)


def i_folder(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(2.8, 6.5), (9, 6.5), (11, 9), (21, 9),
       (21, 19.5), (2.8, 19.5)], w=1.9)


def i_chevron_up(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(6, 15), (12, 9), (18, 15)], 2.1)


def i_chevron_down(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(6, 9), (12, 15), (18, 9)], 2.1)


def i_activity(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(2, 12), (7, 12), (10, 20), (14, 4), (17, 12), (22, 12)], 2.0)


def i_scan(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(3, 8), (3, 3), (8, 3)], 2.0)
    L([(16, 3), (21, 3), (21, 8)], 2.0)
    L([(21, 16), (21, 21), (16, 21)], 2.0)
    L([(8, 21), (3, 21), (3, 16)], 2.0)
    L([(3, 12), (21, 12)], 1.8)


def i_calc(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((4, 2.8, 20, 21.2), 1.9, radius=2)
    L([(4, 8.4), (20, 8.4)], 1.7)
    for x in (8.2, 12, 15.8):
        O((x - 0.7, 11.6, x + 0.7, 13), 1.0)
        O((x - 0.7, 16, x + 0.7, 17.4), 1.0)


def i_shield(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(12, 2.8), (4.5, 6), (4.5, 12), (12, 21),
       (19.5, 12), (19.5, 6)], w=1.9)
    L([(8.8, 12), (11.2, 14.4), (15.4, 9.8)], 2.0)


def i_terminal(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((2.8, 4, 21.2, 20), 1.9, radius=2)
    L([(6.5, 9), (10, 12), (6.5, 15)], 2.0)
    L([(12, 16), (17, 16)], 2.0)


def i_beaker(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(9, 2.8), (15, 2.8)], 2.0)
    L([(10.2, 2.8), (10.2, 9), (5.5, 18), (5.5, 20),
       (18.5, 20), (18.5, 18), (13.8, 9), (13.8, 2.8)], 1.9)
    L([(7.2, 14), (16.8, 14)], 1.7)


def i_table(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((3, 4, 21, 20), 1.9, radius=1.5)
    L([(3, 9.3), (21, 9.3)], 1.7)
    L([(3, 14.6), (21, 14.6)], 1.7)
    L([(10, 9.3), (10, 20)], 1.7)


def i_chart(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(3.5, 3), (3.5, 20), (21, 20)], 1.9)
    R((7, 13, 10, 20), 1.6)
    R((12.5, 8, 15.5, 20), 1.6)
    R((18, 4, 20.5, 20), 1.6)


def i_clipboard(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((5, 4.5, 19, 21), 1.9, radius=1.5)
    R((9, 2.5, 15, 6.5), 1.7, radius=1)
    L([(8.5, 10.5), (15.5, 10.5)], 1.7)
    L([(8.5, 14), (15.5, 14)], 1.7)
    L([(8.5, 17.5), (13, 17.5)], 1.7)


def i_sliders(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(3, 7), (21, 7)], 1.8)
    L([(3, 12), (21, 12)], 1.8)
    L([(3, 17), (21, 17)], 1.8)
    for cx, cy in ((8, 7), (15, 12), (10, 17)):
        O((cx - 2, cy - 2, cx + 2, cy + 2), 1.8)


def i_download(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(12, 3), (12, 15)], 2.0)
    L([(7, 10), (12, 15), (17, 10)], 2.0)
    L([(4, 18), (4, 20), (20, 20), (20, 18)], 2.0)


def i_filter(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(3, 4), (21, 4), (13.5, 13), (13.5, 19),
       (10.5, 21), (10.5, 13)], w=1.9)


def i_refresh(d, c, k):
    L, R, O, P = _pen(d, c, k)
    d.arc([3 * k, 3 * k, 21 * k, 21 * k], start=30, end=330,
          fill=c, width=max(1, int(1.9 * k)))
    L([(18, 2.6), (21.4, 6.4), (16.4, 7.6)], 1.9)


def i_layers(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(12, 2.8), (21, 7.4), (12, 12), (3, 7.4)], w=1.9)
    L([(3, 12), (12, 16.6), (21, 12)], 1.8)
    L([(3, 16.6), (12, 21.2), (21, 16.6)], 1.8)


def i_server(d, c, k):
    L, R, O, P = _pen(d, c, k)
    R((2.8, 3.5, 21.2, 10.5), 1.9, radius=2)
    R((2.8, 13.5, 21.2, 20.5), 1.9, radius=2)
    L([(6.5, 7), (6.6, 7)], 2.2)
    L([(6.5, 17), (6.6, 17)], 2.2)


def i_database(d, c, k):
    L, R, O, P = _pen(d, c, k)
    O((3, 4, 21, 9.5), 1.9)
    L([(3, 12), (3, 19.5)], 1.9)
    L([(21, 12), (21, 19.5)], 1.9)
    O((3, 14.5, 21, 20), 1.9)


def i_zap(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(13.5, 2.5), (5, 13.5), (11, 13.5), (10, 21.5),
       (19, 10.5), (13, 10.5)], w=1.9)


def i_check(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(4, 12.5), (9.5, 18), (20, 6)], 2.2)


def i_x(d, c, k):
    L, R, O, P = _pen(d, c, k)
    L([(6, 6), (18, 18)], 2.0)
    L([(18, 6), (6, 18)], 2.0)


def i_alert(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(12, 2.8), (2, 20), (22, 20)], w=1.9)
    L([(12, 9), (12, 15)], 2.0)
    O((10.9, 16.6, 13.1, 18.8), 1.2)


def i_external(d, c, k):
    L, R, O, P = _pen(d, c, k)
    P([(13, 3.5), (20.5, 3.5), (20.5, 11)], w=1.9)
    L([(20.5, 3.5), (11, 13)], 1.9)
    L([(17, 14), (17, 20.5), (3.5, 20.5), (3.5, 7), (10, 7)], 1.9)


_DRAWERS = dict(
    (n[2:], fn) for n, fn in list(globals().items())
    if n.startswith("i_") and callable(fn)
)


def names():
    """可用图标名（调试用）。"""
    return sorted(_DRAWERS)


def get(name, color, size=18):
    """取一个 PhotoImage；同名同色同尺寸复用缓存。

    ImageTk 延迟到真正要出图时才导入：模块本身（names/绘图定义）在没有
    tkinter 的解释器里也能被静态检查导入，不拖累 --list-tools / --selftest。
    """
    from PIL import ImageTk
    key = (str(name), str(color), int(size))
    if key in _CACHE:
        return _CACHE[key]
    k = _SUPERSAMPLE
    img = Image.new("RGBA", (24 * k, 24 * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fn = _DRAWERS.get(str(name))
    if fn is not None:
        try:
            fn(draw, str(color), k)
        except Exception:
            pass
    img = img.resize((int(size), int(size)), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _CACHE[key] = photo
    return photo


def clear_cache():
    """主题切换后清缓存（颜色变了，旧位图不能复用）。"""
    _CACHE.clear()
