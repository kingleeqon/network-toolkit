# -*- coding: utf-8 -*-
"""自绘基础组件：圆角卡片 / 细进度条 / 分段控件 / 下划线标签页。

为什么需要这一层：
    * Tk 没有圆角容器，ttk.Labelframe 只能画方角 + 一圈浮雕边框，
      和设计稿里的卡片差得很远；
    * ttkbootstrap 的 Floodgauge 只能用主题自带的 primary 蓝（#375a7f），
      和品牌青 #22d3ee 撞色，缩到 33px 高之后就是一块突兀的蓝方块；
    * ttk.Notebook 的页签是方角浮雕，做不出设计稿的下划线页签。

这些组件一律只做绘制与布局，不含任何业务判断；配色由调用方按当前语义
色板（ui/theme.py 的 palette）传入，主题切换时调用各自的 refresh() 重绘。

Python 3.8 兼容：不用 list[str] / X | None，类型注解走 typing。
"""
import tkinter as tk

# 圆角卡片四角的叠放参数：(place 定位, 扇形外接矩形, 起始角度)
# 角度按 Tk 约定：0° 在 3 点钟方向，逆时针为正。
_CORNERS = (
    ({"x": 0, "y": 0},
     (0, 0, 2, 2), 90),                                     # 左上
    ({"relx": 1.0, "x": None, "y": 0},
     (-1, 0, 1, 2), 0),                                     # 右上
    ({"x": 0, "rely": 1.0, "y": None},
     (0, -1, 2, 1), 180),                                   # 左下
    ({"relx": 1.0, "x": None, "rely": 1.0, "y": None},
     (-1, -1, 1, 1), 270),                                  # 右下
)


def round_corners(frame, radius, fill, page):
    """给方形 Frame 造出圆角外观，返回 4 个角画布（供主题切换时重绘）。

    做法：四角各叠一个 radius×radius 的 Canvas，画布底色取「页面色」
    （把卡片原本的直角盖掉），再用「卡片色」画一个朝内的 90° 扇形。
    视觉上就是把直角削成了圆角，不依赖图片资源，也不影响内部布局。

    注意：Canvas 是在内容之后创建的，若之后再往卡片里放子控件，
    需要重新 lift 一次（见 lift_corners）。
    """
    canvases = []
    try:
        for pos, bbox, start in _CORNERS:
            kw = dict(pos)
            if kw.get("x") is None:
                kw["x"] = -radius
            if kw.get("y") is None:
                kw["y"] = -radius
            cv = tk.Canvas(frame, width=radius, height=radius,
                           highlightthickness=0, bd=0, bg=page)
            cv.place(width=radius, height=radius, **kw)
            cv.create_arc(bbox[0] * radius, bbox[1] * radius,
                          bbox[2] * radius, bbox[3] * radius,
                          start=start, extent=90,
                          fill=fill, outline=fill, tags="mask")
            canvases.append(cv)
    except Exception:
        pass
    return canvases


def lift_corners(canvases):
    """内容搭好之后把四角抬到最上层（否则会被后建的子控件压住）。"""
    for cv in canvases:
        try:
            cv.lift()
        except Exception:
            pass


def paint_corners(canvases, fill, page):
    """主题切换时重新着色四角（底色=页面色，扇形=卡片色）。"""
    for cv in canvases:
        try:
            cv.configure(bg=page)
            cv.itemconfigure("mask", fill=fill, outline=fill)
        except Exception:
            pass


def _is_light(hex_color):
    """判断色值是否为浅色（决定进度条 track 用哪个语义色）。"""
    try:
        c = str(hex_color or "").lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        return (0.299 * r + 0.587 * g + 0.114 * b) > 150
    except Exception:
        return False


class Meter(tk.Canvas):
    """细长圆角进度条：静默 track + 品牌色填充，条内不放文字。

    兼容 Floodgauge 的调用方式 —— progress.configure(value=64) 照旧可用，
    主窗口里原有的进度更新代码一行都不用改。
    """

    def __init__(self, master, width=150, height=20, p=None):
        p = p or {}
        tk.Canvas.__init__(self, master, width=width, height=height,
                           highlightthickness=0, bd=0,
                           bg=p.get("app", "#0a1120"))
        self._p = dict(p)
        self._value = 0.0
        self._thick = 6
        self.bind("<Configure>", lambda e: self._draw())

    # —— 与 Floodgauge 对齐的取值接口 ——
    def configure(self, cnf=None, **kw):
        if "value" in kw:
            try:
                self._value = max(0.0, min(100.0, float(kw.pop("value") or 0)))
            except (TypeError, ValueError):
                self._value = 0.0
            self._draw()
        return tk.Canvas.configure(self, cnf, **kw)

    config = configure

    def set_value(self, value):
        self.configure(value=value)

    def refresh(self, p):
        """主题切换后重绘（底色 + track/fill 都要换）。"""
        self._p = dict(p)
        try:
            self.configure(bg=p.get("app", "#0a1120"))
        except Exception:
            pass
        self._draw()

    def _draw(self):
        p = self._p
        try:
            self.delete("all")
            w = max(24, self.winfo_width())
            h = max(self._thick + 2, self.winfo_height())
            mid = h / 2.0
            t = self._thick
            pad = t / 2.0 + 1
            # 浅色主题下 elevated(#f7fafd) 贴在白底上几乎看不见，track 改用 border
            track = (p.get("border") if _is_light(p.get("app"))
                     else p.get("elevated", "#1a2b46"))
            brand = p.get("brand", "#22d3ee")
            # capstyle="round" 让线两端自带半圆，天然就是胶囊形
            self.create_line(pad, mid, w - pad, mid,
                             width=t, capstyle="round", fill=track)
            if self._value > 0:
                x = pad + (w - 2 * pad) * (self._value / 100.0)
                if x > pad + 1.5:
                    self.create_line(pad, mid, x, mid,
                                     width=t, capstyle="round", fill=brand)
        except Exception:
            pass


class Segmented(tk.Frame):
    """分段筛选控件（全部 / 成功 / 失败）：一排扁平胶囊，选中项用品牌浅底。"""

    def __init__(self, master, items, value=None, command=None, p=None,
                 font_family="Microsoft YaHei UI", font_size=9):
        p = p or {}
        tk.Frame.__init__(self, master, bg=p.get("app", "#0a1120"),
                          highlightthickness=0, bd=0)
        self._p = dict(p)
        self._font = (font_family, font_size)
        self._command = command
        self._items = list(items or [])
        self._value = value if value in self._items else (
            self._items[0] if self._items else "")
        self._labels = {}
        for text in self._items:
            lbl = tk.Label(self, text=text, font=self._font, padx=11, pady=3,
                           cursor="hand2", bg=self._p.get("app"),
                           fg=self._p.get("text_secondary"))
            lbl.pack(side="left", padx=(0, 3))
            lbl.bind("<Button-1>", lambda e, t=text: self.select(t))
            self._labels[text] = lbl
        self._paint()

    def get(self):
        return self._value

    def select(self, value, notify=True):
        if value not in self._labels:
            return
        self._value = value
        self._paint()
        if notify and self._command:
            try:
                self._command(value)
            except Exception:
                pass

    def refresh(self, p):
        self._p = dict(p)
        try:
            self.configure(bg=p.get("app", "#0a1120"))
        except Exception:
            pass
        self._paint()

    def _paint(self):
        p = self._p
        for text, lbl in self._labels.items():
            cur = (text == self._value)
            try:
                lbl.configure(
                    bg=p.get("brand_soft", "#123243") if cur
                    else p.get("elevated", "#1a2b46"),
                    fg=p.get("brand", "#22d3ee") if cur
                    else p.get("text_secondary", "#a8bad2"))
            except Exception:
                pass


class TabView(tk.Frame):
    """下划线式标签页容器（替代 ttk.Notebook 的方角浮雕页签）。

    只实现主窗口用到的那部分 Notebook 接口：
        add(widget, text, image) -> key / tabs() / forget(key)
        select(key) / tab(key, image=...) / current()
    子页面必须用本容器的 .body 当父控件创建。
    """

    def __init__(self, master, p, font_family="Microsoft YaHei UI"):
        p = p or {}
        tk.Frame.__init__(self, master, bg=p.get("app", "#0a1120"),
                          highlightthickness=0, bd=0)
        self._p = dict(p)
        self._font = font_family
        self.bar = tk.Frame(self, bg=p.get("app", "#0a1120"))
        self.bar.pack(side="top", fill="x", padx=2)
        self.rule = tk.Frame(self, bg=p.get("border", "#243753"), height=1)
        self.rule.pack(side="top", fill="x")
        self.body = tk.Frame(self, bg=p.get("app", "#0a1120"))
        self.body.pack(side="top", fill="both", expand=True)
        self._items = {}
        self._order = []
        self._current = None
        self._seq = 0

    # —— Notebook 兼容子集 ——
    def add(self, widget, text="", image=None):
        self._seq += 1
        key = "tab%d" % self._seq
        p = self._p
        item = tk.Frame(self.bar, bg=p.get("app", "#0a1120"), cursor="hand2")
        item.pack(side="left")
        lbl = tk.Label(item, text=text, image=image or "", compound="left",
                       bg=p.get("app", "#0a1120"),
                       fg=p.get("text_secondary", "#a8bad2"),
                       font=(self._font, 10), padx=11, pady=4)
        lbl.pack()
        line = tk.Frame(item, bg=p.get("app", "#0a1120"), height=2)
        line.pack(fill="x")
        self._items[key] = {"frame": item, "label": lbl, "line": line,
                            "widget": widget, "image": image}
        self._order.append(key)
        for w in (item, lbl, line):
            try:
                w.bind("<Button-1>", lambda e, k=key: self.select(k))
            except Exception:
                pass
        if self._current is None:
            self.select(key)
        else:
            # 后加的页签不会走 select()，必须自己上色：根节点的
            # option_add("*Background", …) 会盖掉创建时传入的 bg=（只有事后
            # configure 才生效），不上色的话未选中页签会留成主题灰底。
            self._paint()
        return key

    def tabs(self):
        return list(self._order)

    def forget(self, key):
        it = self._items.pop(key, None)
        if it is None:
            return
        try:
            it["widget"].pack_forget()
        except Exception:
            pass
        try:
            it["frame"].destroy()
        except Exception:
            pass
        if key in self._order:
            self._order.remove(key)
        if self._current == key:
            self._current = None
            if self._order:
                self.select(self._order[0])

    def select(self, key):
        if key not in self._items:
            return
        for k in self._order:
            try:
                self._items[k]["widget"].pack_forget()
            except Exception:
                pass
        self._current = key
        try:
            self._items[key]["widget"].pack(fill="both", expand=True)
        except Exception:
            pass
        self._paint()

    def tab(self, key, **kw):
        it = self._items.get(key)
        if it is None:
            return
        if "image" in kw:
            it["image"] = kw["image"]
            try:
                it["label"].configure(image=kw["image"] or "")
            except Exception:
                pass

    def current(self):
        return self._current

    # —— 主题 ——
    def refresh(self, p):
        self._p = dict(p)
        try:
            self.configure(bg=p.get("app", "#0a1120"))
            self.bar.configure(bg=p.get("app", "#0a1120"))
            self.rule.configure(bg=p.get("border", "#243753"))
            self.body.configure(bg=p.get("app", "#0a1120"))
        except Exception:
            pass
        self._paint()

    def _paint(self):
        p = self._p
        for k in self._order:
            it = self._items[k]
            cur = (k == self._current)
            try:
                it["label"].configure(
                    bg=p.get("app", "#0a1120"),
                    fg=p.get("brand", "#22d3ee") if cur
                    else p.get("text_secondary", "#a8bad2"),
                    font=(self._font, 10, "bold" if cur else "normal"))
                it["line"].configure(
                    bg=p.get("brand", "#22d3ee") if cur
                    else p.get("app", "#0a1120"))
            except Exception:
                pass
