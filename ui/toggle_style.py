# -*- coding: utf-8 -*-
"""拨钮（Checkbutton switch）配色：开=青色 #00d4ff，关=中性灰。

背景：ttkbootstrap 把 round-toggle 的 on/off/disabled 四态画进 4 张
PhotoImage 存进 builder.theme_images，element 持有 Tcl 图片名。所以
style.configure(background=) 改不到它；Tk 8.6 的 element 不支持 configure，
同名 create 会抛 Duplicate element。

做法：找出 theme_images 里所有「拨钮图」并按 (off, on, disabled, on_disabled)
四张一组重画像素。识别依据：宽高比约 1.4~2.0、宽 15~48、高 10~30 —— 在
本项目实际主题下只命中拨钮（其他图是滚动条箭头 13x11 / 28x9 / 9x28）。
图片对象和 Tcl 名字都不变，所有引用它的拨钮立即换色。

关键坑：不能调用 create_round_toggle_assets 拿名字——它会**新建**一批图，
element 引用的仍是旧图，改了白改。必须直接在 theme_images 里找现存对象。

主题切换后 ttkbootstrap 重建图片，调用方每次主题刷新再调一次 install。

Python 3.8 兼容：不用 walrus / X | None。
"""
from PIL import Image, ImageDraw

from ui import theme as ui_theme

# 拨钮开启态颜色：RGB(0, 212, 255) = #00d4ff（用户指定，深浅主题一致）
_ON_COLOR = "#00d4ff"


def _rgb(c):
    return c if isinstance(c, str) else "#%02x%02x%02x" % c[:3]


def _make_pil(bg, knob, knob_right, w, h):
    """画一张拨钮：圆角胶囊底 + 白色圆形滑块，on 滑块在右、off 在左。"""
    s = 4                      # 超采样，缩小后边缘平滑
    W, H = w * s, h * s
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = H // 2
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=_rgb(bg))
    k = max(3, int(H * 0.66))
    pad = max(2, int(H * 0.14))
    x1 = (W - k - pad) if knob_right else pad
    d.ellipse([x1, (H - k) // 2, x1 + k, (H + k) // 2], fill=_rgb(knob))
    return im.resize((w, h), Image.LANCZOS)


def _toggle_images(theme_images):
    """挑出 theme_images 里的拨钮图，按 (off, on, dis_off, dis_on) 四张一组。"""
    found = []
    for img in theme_images.values():
        if not hasattr(img, "paste") or not hasattr(img, "width"):
            continue
        w, h = img.width(), img.height()
        if 15 <= w <= 48 and 10 <= h <= 30 and 1.4 <= (w / float(h)) <= 2.0:
            found.append(img)
    # 每组 4 张，按 ttkbootstrap 的创建顺序排列；不足一组就跳过
    return [found[i:i + 4] for i in range(0, len(found) - 3, 4)]


def install(style, mode):
    """把当前主题的拨钮图片重画成青色/灰；失败时静默保留原配色。"""
    try:
        builder = style._get_builder()
        if builder is None:
            return
        groups = _toggle_images(getattr(builder, "theme_images", {}) or {})
        if not groups:
            return

        p = ui_theme.palette(mode)
        on_fill, off_fill = _ON_COLOR, p["neutral"]
        knob = "#ffffff" if mode == "dark" else p["panel"]
        dis_bg, dis_knob = p["border"], p["text_muted"]

        for group in groups:
            for idx, img in enumerate(group):
                w, h = img.width(), img.height()
                if idx == 0:      # off：灰底 + 滑块在左
                    pil = _make_pil(off_fill, knob, False, w, h)
                elif idx == 1:    # on：绿底 + 滑块在右
                    pil = _make_pil(on_fill, knob, True, w, h)
                elif idx == 2:    # disabled off
                    pil = _make_pil(dis_bg, dis_knob, False, w, h)
                else:             # disabled on
                    pil = _make_pil(dis_bg, dis_knob, True, w, h)
                try:
                    img.paste(pil)
                except Exception:
                    pass
    except Exception:
        pass
