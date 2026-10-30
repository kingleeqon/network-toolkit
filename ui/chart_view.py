# -*- coding: utf-8 -*-
"""图表预览组件（内嵌 matplotlib，网格卡片 + 单图放大 + 悬浮取值）。

布局：一行固定 3 个卡片，等分铺满整行（宽度变化时高度同步按比例变，图不变形），
      纵向滚动。只有 1 张图时也占 1/3 宽，不拉满整行。
放大：点卡片右上角 ⤢，该卡片宽、高同时放大 1.9 倍（等比，长宽比不变），
      在自己那一行居中显示；点 ⤡ 还原。
悬浮：鼠标移到图上显示该点/该条的数值（tooltip），数据由工具通过
      figure._ntk_hover 提供，缺省则该图无悬浮提示。

线程安全：Figure 在 Worker 线程创建、跨线程传入，仅在本组件（主线程）绑定 Canvas。

性能：切换工具时 canvas 常驻、只做 grid/remove 显隐，不重建不重绘。
"""
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog
import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.tooltip import ToolTip

GRID_COLS = 3            # 一行固定 3 个，等分铺满整行（不随窗口宽度变列数）
CARD_PAD = 4             # 卡片间距
# 卡片高度按用户给的两个基准点线性插值（含 50px 标题行）：
#   默认窗口 416 × 588、全屏 904 × 1158  →  画布高 = 宽 × 1.168 + 56
# 用"高 = 宽 × 固定比例"去套的话，这两点一个都落不准（588/416=1.41、1158/904=1.28，
# 不是同一个比例），所以按两点连线算，宽度落在这两处时尺寸分毫不差。
CARD_SLOPE = 1.168       # 画布高 = 宽 × SLOPE + BASE
CARD_BASE = 56
CARD_H_MIN = 300         # 卡片高度下限（窗口很窄时别压成一条）
CARD_H_MAX = 1600        # 绝对上限（防极端宽高比窗口把单卡拉到离谱）
CARD_HEIGHT = 410        # 画布尚未拿到真实宽度时的兜底高度（不随 DPI 推算，见 _create）
REF_WIDTH = 270.0        # 卡片设计基准宽：图内字号按「实际宽 / 基准宽」等比缩放
EXPORT_DPI = 150         # 导出清晰度下限
EXPORT_TARGET_PX = 1600  # 导出图目标像素宽（按实际英寸反推 dpi）
EXPAND_SCALE = 1.9       # 放大：宽、高同乘这个倍数（等比，长宽比保持不变）
HOVER_FONT_SIZE = 6      # 悬浮提示基础字号（比图内 7pt 文字小一号）
HOVER_WIDTH_RATIO = 0.5  # 悬浮提示框宽度 = 卡片宽度的这个比例

# 断行机会：接口名/设备名里的这些字符后面断开不会产生歧义
_BREAK_CHARS = "/-_ ()"


def _wrap_text(s, n):
    """按分隔符优先断行，实在没有分隔符才硬切。

    textwrap 对 "Ten-GigabitEthernet1/0/8" 这种不含空格的串只会在第一个
    能放下的位置硬切，结果切成 "Ten-GigabitEther / net1/0/8"，读起来像
    两个不同的东西。这里改成：先记下最近的分隔符位置，超长时从那里断。
    """
    s = str(s or "")
    if len(s) <= n:
        return [s]
    out, cur, brk = [], "", -1
    for ch in s:
        cur += ch
        if ch in _BREAK_CHARS:
            brk = len(cur)
        if len(cur) >= n:
            cut = brk if 0 < brk <= len(cur) else len(cur)
            out.append(cur[:cut])
            cur = cur[cut:]
            brk = -1
    if cur:
        out.append(cur)
    return out or [""]


class ChartView(object):
    """内嵌 matplotlib 图表的网格卡片预览区。"""

    def __init__(self, master):
        self.frame = ttk.Frame(master)

        # 工具条
        bar = ttk.Frame(self.frame)
        bar.pack(side="top", fill="x", padx=6, pady=(6, 2))
        self.count_label = ttk.Label(bar, text="暂无图表", bootstyle="secondary")
        self.count_label.pack(side="left")
        ttk.Button(bar, text="清空", width=6, bootstyle="secondary-outline",
                   command=self.clear).pack(side="right")
        ttk.Button(bar, text="导出全部", width=9, bootstyle="secondary-outline",
                   command=self.save_all).pack(side="right", padx=(0, 4))

        # 滚动容器
        self._canvas = tk.Canvas(self.frame, highlightthickness=0)
        vs = ttk.Scrollbar(self.frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner = ttk.Frame(self._canvas)
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        # 固定 3 列等宽（uniform 保证三列宽度完全一致，卡片才是"铺满整行"）
        for c in range(GRID_COLS):
            self._inner.columnconfigure(c, weight=1, uniform="chartcol")

        self._inner.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # 滚轮只在本组件内生效，不抢占全局
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)
        # 每项：{figure,title,card,holder,canvas,btn,visible,expanded,nat_w,nat_h}
        self._items = []
        # 全局字号比例（1.0 = 图表原始字号）
        self._font_scale = 1.0
        # 宽度变化后重算卡片尺寸的防抖任务
        self._size_job = None
        self._title_job = None

    # —— 滚轮 ——
    def _on_canvas_configure(self, event):
        """滚动区宽度变化：内层跟着变宽，并重新按新宽度折算卡片高度。

        宽度变了如果不改高度，卡片就一直往两边拉扁——这正是"放大/最大化只往
        两边拉长"的观感来源。这里防抖 160ms，避免拖动窗口时反复重排。
        """
        try:
            self._canvas.itemconfigure(self._win, width=event.width)
        except Exception:
            pass
        if self._size_job is not None:
            try:
                self._canvas.after_cancel(self._size_job)
            except Exception:
                pass
        try:
            self._size_job = self._canvas.after(160, self._resize_all)
        except Exception:
            self._size_job = None

    def _resize_all(self):
        """按当前可视宽高重算所有卡片尺寸（高度随宽度变，长宽比保持稳定）。"""
        self._size_job = None
        for rec in self._items:
            before = rec.get("_ks")
            self._apply_size(rec)
            # 基准宽变了，字号要跟着重算（_ks 是上一次实际用的总倍数）
            bw = float(self._base_size(rec)[0])
            want = float(rec.get("_k") or 1.0) * self._font_scale * (bw / REF_WIDTH)
            if before is None or abs(before - want) > 0.005:
                self._apply_scale(rec)

    def _bind_wheel(self, _e=None):
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e=None):
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        try:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # —— 卡片创建 ——
    _DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}\s*")
    # 卡片标题里可以省掉的通用词：每张图都带着它，占掉一半宽度却不提供区分度
    _TITLE_NOISE = ("接口 CRC 差值排行 ", "接口 CRC 差值排行", "CRC 差值排行 ",
                    "CRC 差值", "接口 CRC")

    @classmethod
    def _display_title(cls, title, max_chars=18):
        """压缩卡片标题：去掉日期前缀和通用词，仍超长才截断加省略号。

        原始标题如 "2026-10-28 厂商 中兴 接口 CRC 差值排行 Top20"（33 字符），
        卡片可用宽度只放得下约 16 个汉字，不处理就被硬切成
        "2026-10-28 全局接口 C"，几张图的标题看起来完全一样。
        """
        s = str(title or "").strip()
        short = cls._DATE_PREFIX.sub("", s)
        for noise in cls._TITLE_NOISE:
            short = short.replace(noise, "")
        short = short.strip()
        short = re.sub(r"\s{2,}", " ", short)
        if len(short) > max_chars:
            # "Top20" 这类后缀是最有信息量的部分，截断时不能把它切掉
            m = re.match(r"(.*?)\s*(Top\d+)$", short)
            if m:
                head, tail = m.group(1), m.group(2)
                keep = max_chars - len(tail) - 1
                if 0 < keep < len(head):
                    short = head[:keep] + "… " + tail
                elif keep >= len(head):
                    short = head + " " + tail
            else:
                short = short[:max_chars - 1] + "…"
        return short or s[:max_chars]

    def _find(self, figure):
        for rec in self._items:
            if rec["figure"] is figure:
                return rec
        return None

    def _create(self, figure, title):
        rec = self._find(figure)
        if rec is not None:
            return rec
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as exc:
            self.count_label.configure(text="图表渲染失败：%s" % exc)
            return None

        # 卡片尺寸交给 _apply_size 统一算（宽 = 格子宽，高 = 宽 × CARD_SLOPE + CARD_BASE），
        # **不拿屏幕 DPI 推算**：winfo_fpixels("1i") 在多屏/缩放下会返回 51 这类怪值。
        # 这里只给个兜底尺寸，真正尺寸在 _apply_size 里按画布实际宽度定。
        card = ttk.Frame(self._inner, bootstyle="secondary")

        head = ttk.Frame(card)
        head.pack(side="top", fill="x", padx=4, pady=(4, 0))
        # width=1：图标本身只有 1 个字符宽，写 2 会白占 48px；
        # 卡片才 270px 宽，两个按钮吃掉 96px 后标题只剩 166px，
        # 连 "持续恶化接口 Top11" 都要被切掉最后一位（显示成 Top1）。
        btn = ttk.Button(head, text="⤢", width=1, bootstyle="secondary-outline")
        btn.pack(side="right")
        save_btn = ttk.Button(head, text="存", width=1, bootstyle="secondary-outline")
        save_btn.pack(side="right", padx=(0, 3))
        title_lbl = ttk.Label(head, text=self._display_title(title),
                              font=("Microsoft YaHei UI", 9, "bold"),
                              bootstyle="primary")
        title_lbl.pack(side="left", fill="x", expand=True)
        # 卡片只有 270~310px 宽，标题必然要压缩；完整标题放到悬浮提示里，
        # 否则用户看到的永远是 "2026-10-28 全局接口 C…"，分不清是哪张图
        try:
            if title:
                ToolTip(title_lbl, text=str(title), delay=400)
        except Exception:
            pass

        holder = ttk.Frame(card)
        # 固定尺寸的容器：pack_propagate(False) 让容器只认我们设的宽高，
        # 不受 matplotlib 画布自身请求尺寸影响（否则回读尺寸会形成
        # 427→640→960 的逐次翻倍连锁）。
        holder.pack(side="top", fill="x", padx=3, pady=3)
        holder.pack_propagate(False)
        holder.configure(width=200, height=CARD_HEIGHT)
        canvas = FigureCanvasTkAgg(figure, master=holder)
        canvas.draw()
        tk_widget = canvas.get_tk_widget()
        tk_widget.pack(fill="both", expand=True)

        rec = {"figure": figure, "title": title, "card": card, "holder": holder,
               "canvas": canvas, "tk": tk_widget, "btn": btn, "label": title_lbl,
               "visible": False, "expanded": False,
               "nat_w": 0, "nat_h": 0,     # 放大前的基准宽/高（等比放大用）
               "hover_cb": None}
        btn.configure(command=lambda r=rec: self._toggle_expand(r))
        save_btn.configure(command=lambda r=rec: self.save_one(r))
        rec["hover_cb"] = self._bind_hover(canvas, figure)
        self._items.append(rec)
        # 先按卡片实际宽度定尺，再按同一比例缩放图内文字（含悬浮提示框：
        # 它在 _bind_hover 里已建好，会一起进 _scale_figure 的快照）
        self._apply_size(rec)
        try:
            self._apply_scale(rec)
        except Exception:
            pass
        return rec

    # —— 导出图片 ——
    @staticmethod
    def _safe_name(title, fallback="chart"):
        name = (title or fallback).strip()
        for ch_ in ('/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n'):
            name = name.replace(ch_, "_")
        return name[:80] or fallback

    def save_one(self, rec):
        """保存单个图表为图片（白底，便于放进汇报材料）。"""
        fig = rec.get("figure")
        if fig is None:
            return
        default = self._safe_name(rec.get("title"))
        try:
            path = filedialog.asksaveasfilename(
                defaultextension=".png", initialfile=default + ".png",
                filetypes=[("PNG 图片", "*.png"), ("PDF 文档", "*.pdf"),
                           ("SVG 矢量图", "*.svg")])
        except Exception:
            return
        if not path:
            return
        try:
            self._export(fig, path)
            Messagebox.show_info("已保存到：\n%s" % path, title="保存成功")
        except Exception as exc:
            Messagebox.show_error("保存失败：%s" % exc, title="保存失败")

    @staticmethod
    def _export(fig, path):
        """导出图片：按目标像素宽反推 dpi，并先把悬浮提示框藏掉。

        原来固定 dpi=150 + bbox_inches="tight"，卡片只有 1.8 英寸宽，
        出来才 284×427 像素，插进汇报材料看着糊；而且鼠标刚悬停过的话，
        提示框会被一起画进图片里。
        """
        hidden = []
        try:
            for ax in fig.axes:
                for t in ax.texts:
                    if t.get_zorder() >= 1000 and t.get_visible():
                        t.set_visible(False)
                        hidden.append(t)
            fw = max(fig.get_size_inches()[0], 0.1)
            # PNG 按像素宽定 dpi；矢量格式 dpi 无所谓，给个适中值即可
            dpi = int(max(EXPORT_DPI, min(1200, EXPORT_TARGET_PX / fw)))
            if path.lower().endswith((".pdf", ".svg")):
                dpi = EXPORT_DPI
            # 不再用 bbox_inches="tight"：constrained_layout 已算好边距，
            # 再 tight 一次会和布局引擎抢边距，且输出宽度不可控
            fig.savefig(path, dpi=dpi, facecolor="white")
        finally:
            for t in hidden:
                try:
                    t.set_visible(True)
                except Exception:
                    pass

    def save_all(self):
        """把当前可见的图表批量导出到指定目录。"""
        vis = [r for r in self._items if r["visible"]]
        if not vis:
            Messagebox.show_info("当前没有可导出的图表", title="提示")
            return
        try:
            target = filedialog.askdirectory(title="选择保存目录")
        except Exception:
            return
        if not target:
            return
        ok, failed = 0, 0
        for i, rec in enumerate(vis, 1):
            name = self._safe_name(rec.get("title"), "chart_%02d" % i)
            path = os.path.join(target, "%02d_%s.png" % (i, name))
            try:
                self._export(rec["figure"], path)
                ok += 1
            except Exception:
                failed += 1
        msg = "已导出 %d 张到：\n%s" % (ok, target)
        if failed:
            msg += "\n失败 %d 张" % failed
        Messagebox.show_info(msg, title="导出完成")

    # —— 悬浮取值 ——
    def _bind_hover(self, canvas, figure):
        """绑定 motion 事件，鼠标移到数据点/条上显示数值提示。

        返回 on_move 回调（便于测试/调试），未提供悬浮数据时返回 None。
        """
        hover = getattr(figure, "_ntk_hover", None)
        if not hover or not figure.axes:
            return None
        ax = figure.axes[0]
        items = hover.get("items") or []
        if not items:
            return None
        horizontal = bool(hover.get("horizontal"))

        # 字号比图内文字小一号：直接读坐标轴刻度字号（各工具不一定都是 7pt），
        # 减 1pt 作为提示框基准。它会随 _scale_figure 一起按卡片比例缩放，
        # 所以放大/全屏后依然保持"比图内文字小一号"。
        base_fs = HOVER_FONT_SIZE
        try:
            lbls = ax.get_yticklabels() or ax.get_xticklabels()
            if lbls:
                base_fs = max(4.0, float(lbls[0].get_fontsize()) - 1.0)
        except Exception:
            pass

        annot = ax.annotate("", xy=(0, 0), xytext=(14, 14), textcoords="offset points",
                            bbox=dict(boxstyle="round,pad=0.45", fc="#ffffff",
                                      ec="#888888", alpha=0.96),
                            fontsize=base_fs, color="#222222", zorder=1000,
                            annotation_clip=False)
        # 关键：不参与布局计算。否则 constrained_layout 会把提示框算进画布范围，
        # 每次显示/隐藏提示都会触发重新排版，导致图表内容被挤压跳动。
        annot.set_in_layout(False)
        annot.set_clip_on(False)   # 允许画到坐标区以外（靠近边缘时会翻到另一侧）
        annot.set_visible(False)
        state = {"idx": -1}

        def hide():
            if state["idx"] != -1:
                state["idx"] = -1
                annot.set_visible(False)
                canvas.draw_idle()

        def place_near(event):
            """提示框贴着光标，并保证整个框落在画布内。

            卡片只有 267px 宽左右，提示框本身就可能比卡片宽，
            单纯左右翻转不够：这里先按默认偏移摆一次，量出溢出量再整体拉回来，
            否则悬停最上面/最右边那条时提示框会跑到画布外，看起来像「不见了」。

            单位：`get_window_extent` 给的是**像素**，`set_position` 收的是**点**。
            两者在 dpi=150 下差一倍，混着算就会出现「右边推回来、左边又顶出去」，
            所以底下全部换算成像素算偏移，最后一步才转回点。
            """
            base = 10                # 点
            margin = 6               # 像素：框边留白，贴到 1~2px 时视觉上像被裁掉
            annot.set_ha("left")
            annot.set_va("bottom")
            annot.set_position((base, base))
            try:
                px2pt = 72.0 / max(figure.dpi, 1.0)
                ext = annot.get_window_extent(canvas.get_renderer())
                pw = figure.bbox.width
                ph = figure.bbox.height
                w = ext.width
                h = ext.height
                # 默认「右上偏移」后，框左上角的像素坐标
                x0 = event.x + base / px2pt
                y0 = event.y + base / px2pt
                dx = dy = 0.0
                if x0 + w > pw - margin:
                    dx = (pw - margin) - (x0 + w)      # 负数 = 往左推
                if y0 + h > ph - margin:
                    dy = (ph - margin) - (y0 + h)
                if x0 + dx < margin:
                    dx = margin - x0                   # 往左推过头了，再兜回右边界
                if y0 + dy < margin:
                    dy = margin - y0
                if dx or dy:
                    annot.set_position((base + dx * px2pt, base + dy * px2pt))
            except Exception:
                pass

        def on_move(event):
            if event.inaxes is not ax or event.xdata is None or event.ydata is None:
                hide()
                return
            coord = event.ydata if horizontal else event.xdata
            idx = int(round(coord))
            if idx < 0 or idx >= len(items):
                # 坐标区上下各有一圈留白（实测 ylim 为 -1.35~n+1.35，柱子只占 0~n-1），
                # 光标扫过这圈留白时 round() 会算出 -1 或 n。原先这里直接 hide()，
                # 表现就是「从上方移到最上面那条时提示框突然不见」。
                # 留白只有 1 个单位多，夹到最近的一条比直接消失更符合直觉。
                if not items:
                    hide()
                    return
                idx = 0 if idx < 0 else len(items) - 1
            if idx == state["idx"]:
                return          # 同一条内移动不重绘，避免闪烁/掉帧
            state["idx"] = idx
            it = items[idx]
            title = it.get("title") or it.get("label") or ""
            sub = it.get("sub") or ""
            tail = "%s: %s" % (hover.get("value_label", "值"), it.get("value"))

            def build(n_title, n_sub):
                lines = _wrap_text(title, n_title)
                if sub:
                    lines += _wrap_text(sub, n_sub)
                lines.append(tail)
                return "\n".join(lines)

            annot.xy = (event.xdata, event.ydata)
            # 提示框宽度 = 卡片宽度的一半（用户要求）。做法是按这个目标宽度
            # 二分找折行字数：字多了框变宽、字少了框变窄，几次就能贴住目标。
            # 不写死字数——中英文宽度差一倍，写死在任何一种字号下都会翻车。
            n1 = n2 = 40
            annot.set_text(build(n1, n2))
            # 先显示再定位：不可见时 get_window_extent 量不出真实尺寸，钳制会失效
            annot.set_visible(True)
            try:
                renderer = canvas.get_renderer()
                target = figure.bbox.width * HOVER_WIDTH_RATIO
                longest = max([len(title), len(sub), 8])
                lo, hi = 4, longest + 6
                best_n, best_err = None, 1e18
                for _ in range(8):
                    if lo > hi:
                        break
                    mid = (lo + hi) // 2
                    annot.set_text(build(mid, mid))
                    w = annot.get_window_extent(renderer).width
                    err = abs(w - target)
                    if err < best_err:
                        best_n, best_err = mid, err
                    if w > target:
                        hi = mid - 1
                    else:
                        lo = mid + 1
                if best_n is not None:
                    annot.set_text(build(best_n, best_n))
            except Exception:
                pass
            place_near(event)
            canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", on_move)
        canvas.mpl_connect("figure_leave_event", lambda e: hide())
        return on_move

    # —— 放大 / 缩小 ——
    def _toggle_expand(self, rec):
        """原地等比放大/还原：宽和高同乘 EXPAND_SCALE，长宽比一点不变。

        之前只让卡片跨满整行（宽变约 3 倍）而高度只长 1.9 倍，
        x 轴被横向拉长、内容变形——用户看到的就是"只往两边拉长"。
        现在：放大后宽度 = 放大前宽度 × 1.9，高度 = 放大前高度 × 1.9，
        图像内容是同一张图的等比放大版。
        """
        if not rec["expanded"]:
            # 记下放大前的基准尺寸（此时卡片还在正常的 1/3 格子里）
            w, h = self._base_size(rec)
            rec["nat_w"], rec["nat_h"] = w, h
        rec["expanded"] = not rec["expanded"]
        rec["btn"].configure(text="⤡" if rec["expanded"] else "⤢")
        # 先让参数区让位（放大后的图要占满高度，不然多出来的部分被裁掉）
        cb = getattr(self, "on_expand", None)
        if cb:
            try:
                cb(rec["expanded"])
            except Exception:
                pass
        self._apply_size(rec)
        self._apply_scale(rec)
        self._relayout()
        if rec["expanded"]:
            # 放大后的卡片在自己那一行，可能落在可视区之外——滚过去，
            # 否则点了放大"什么都没发生"，用户只能自己再找
            self._scroll_into_view(rec["card"])
        # 布局后画布会被重设尺寸，matplotlib 需要一次重绘才按新尺寸排版
        try:
            rec["canvas"].draw_idle()
        except Exception:
            pass

    def _scroll_into_view(self, card):
        """把指定卡片滚到可视区内（比可视区还高时顶部对齐）。"""
        def _go():
            try:
                self._canvas.update_idletasks()
                y = card.winfo_y()
                ch = card.winfo_height()
                total = max(1, self._inner.winfo_height())
                view = max(1, self._canvas.winfo_height())
                top = self._canvas.canvasy(0)
                if y < top or y + min(ch, view) > top + view:
                    frac = max(0.0, min(1.0, y / float(total)))
                    self._canvas.yview_moveto(frac)
            except Exception:
                pass
        try:
            self._canvas.after_idle(_go)
        except Exception:
            pass

    def _cell_width(self):
        """一行三格里单格的可视宽度（画布还没布局时退回一个保守值）。"""
        w = 0
        try:
            w = self._canvas.winfo_width()
        except Exception:
            w = 0
        if w <= 1:
            try:
                w = self.frame.winfo_width()
            except Exception:
                w = 0
        if w <= 1:
            w = GRID_COLS * CARD_HEIGHT        # 完全没布局：按兜底比例给个合理值
        usable = w - 2 * GRID_COLS * CARD_PAD - 8
        return max(120, int(usable / GRID_COLS))

    def _height_for(self, width):
        """由宽度折算卡片画布高度（不含 50px 标题行）。

        按用户给的基准点连线：整卡宽 416 → 画布高 538（整卡 588）、
        整卡宽 904 → 画布高 1108（整卡 1158）。**不掺任何视口判断**——
        只要高度不纯粹由宽度决定，窗口一变长宽比就漂，图就会被拉扁。
        """
        h = int(round(width * CARD_SLOPE + CARD_BASE))
        return int(max(CARD_H_MIN, min(CARD_H_MAX, h)))


    def _base_size(self, rec):
        """返回卡片在"未放大"状态下的基准 (宽, 高)。

        放大态用进入放大那一刻的快照，不要去量当前实际宽度——量到的是
        放大后的宽度，再乘倍数就成了 1.9×1.9 的滚雪球。
        """
        if rec["expanded"] and rec.get("nat_w"):
            return int(rec["nat_w"]), int(rec["nat_h"])
        w = self._cell_width()
        return w, self._height_for(w)

    def _apply_size(self, rec):
        """按基准宽高 × 放大倍数 × 字号比例，定卡片容器与画布的像素尺寸。

        容器 holder 关掉了 pack_propagate，宽高只认这里设的值，
        所以**宽和高必须在同一个地方一起算**；只改其中一个，
        表现出来就是"只往两边拉长"或"只往上下拉长"。
        """
        try:
            bw, bh = self._base_size(rec)
            fscale = max(1.0, float(self._font_scale))   # 字号变大要给字留出高度
            # 放大倍数：宽、高用**同一个** k，长宽比才不变。
            # 不要把 k 压到"刚好塞进可视区"——那会只剩 1.1 倍，用户会以为没放大；
            # 超出视口的部分靠滚动看，参数区也会自动让位（见 on_expand）。
            k = EXPAND_SCALE if rec["expanded"] else 1.0
            w = max(140, int(round(bw * k)))
            h = max(120, int(round(bh * fscale * k)))
            # 放大后可能比可视区还宽（窗口被缩小时），按同一比例缩回去，
            # 比例不变所以图不变形，只是没有铺出去那么宽
            try:
                vw = int(self._canvas.winfo_width())
            except Exception:
                vw = 0
            if vw > 60 and w > vw - 10:
                s = (vw - 10) / float(w)
                w = int(w * s)
                h = max(120, int(h * s))
            inner_w = max(120, w - 8)      # 减掉卡片左右内边距
            rec["_k"] = k                  # 字号/线宽按同一个倍数走，否则字会撑出卡片
            rec["holder"].configure(width=inner_w, height=h)
            rec["tk"].configure(width=inner_w, height=h)
            self._schedule_title_fit()
        except Exception:
            pass

    # —— 标题按可用像素裁剪 ——
    def _schedule_title_fit(self):
        """尺寸变化后重算标题可显示的字数（防抖，避免每次 configure 都量字宽）。"""
        if self._title_job is not None:
            return
        try:
            self._title_job = self._canvas.after(80, self._fit_titles)
        except Exception:
            self._title_job = None

    def _fit_titles(self):
        self._title_job = None
        for rec in self._items:
            self._fit_title(rec)

    def _fit_title(self, rec):
        """把标题压到 Label 现有宽度内，且不切坏 "Top20" 这类后缀。

        写死字数（原来是 18）在不同字号/字体下必然翻车：
        实测 9pt 粗体下 "持续恶化接口 Top11" 要 174px，而按钮占掉 96px 后
        Label 只剩 166px，最后一个 "1" 被硬切，显示成 "Top1"。
        """
        lbl = rec.get("label")
        if lbl is None:
            return
        full = rec.get("title") or ""
        try:
            avail = int(lbl.winfo_width())
        except Exception:
            avail = 0
        if avail <= 1:
            avail = 150
        try:
            font = tkfont.Font(font=lbl.cget("font"))
        except Exception:
            return
        text = self._display_title(full, 60)   # 先只去噪不截断，再按实际宽度收
        for _ in range(3):
            need = font.measure(text)
            if need <= avail or len(text) <= 4:
                break
            # 按实测宽度折算还能放几个字，再走 _display_title 的截断逻辑
            n = max(4, int(len(text) * (avail / float(need))) - 1)
            text = self._display_title(full, n)
        try:
            if str(lbl.cget("text")) != text:
                lbl.configure(text=text)
        except Exception:
            pass

    def _apply_scale(self, rec):
        """按「卡片实际宽度 / 基准宽」缩放图内文字、线宽、标记。

        matplotlib 的字号是**绝对点值**，画布变大变小它自己不跟着变：
        卡片缩到 206px 时 6pt 标签仍有 12px 高，20 行叠在一起糊成一片；
        铺到 900px 时同一行字又小得像脚注。所以字号必须跟着卡片宽度走，
        这样不管窗口多大、放大几倍，看到的都是"同一张图等比缩放"。
        """
        try:
            bw = float(self._base_size(rec)[0])
            k = float(rec.get("_k") or (EXPAND_SCALE if rec["expanded"] else 1.0))
            k = k * self._font_scale * (bw / REF_WIDTH)
        except Exception:
            k = self._font_scale
        rec["_ks"] = k
        self._scale_figure(rec["figure"], k)
        try:
            rec["canvas"].draw_idle()
        except Exception:
            pass

    @staticmethod
    def _scale_figure(fig, k):
        """把图内所有文字/线宽/标记按 k 等比缩放（基于首次快照，可反复调用）。"""
        try:
            from matplotlib.text import Text
            from matplotlib.lines import Line2D
        except Exception:
            return
        origin = getattr(fig, "_ntk_scale_origin", None)
        if origin is None:
            texts, lines, marks = [], [], []
            try:
                for t in fig.findobj(Text):
                    try:
                        texts.append((t, float(t.get_fontsize())))
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                for ln in fig.findobj(Line2D):
                    try:
                        lines.append((ln, float(ln.get_linewidth())))
                        marks.append((ln, float(ln.get_markersize())))
                    except Exception:
                        pass
            except Exception:
                pass
            origin = {"texts": texts, "lines": lines, "marks": marks}
            fig._ntk_scale_origin = origin

        for t, fs in origin["texts"]:
            try:
                t.set_fontsize(fs * k)
            except Exception:
                pass
        for ln, lw in origin["lines"]:
            try:
                ln.set_linewidth(lw * k)
            except Exception:
                pass
        for ln, ms in origin["marks"]:
            try:
                ln.set_markersize(ms * k)
            except Exception:
                pass

    def set_font_scale(self, scale):
        """全局字号比例（所有已渲染图表立即生效，新建图表也沿用）。"""
        try:
            self._font_scale = max(0.6, min(2.5, float(scale)))
        except (TypeError, ValueError):
            return
        for rec in self._items:
            self._apply_size(rec)
            self._apply_scale(rec)

    # —— 布局 ——
    def _relayout(self, reset_view=False):
        """排布可见卡片：**一行固定 3 个、等分铺满整行**；放大的卡片独占一行居中。

        规则（用户明确要求，别再改）：
        * 永远 3 列，不按窗口宽度增减列数；
        * 只有 1 张图时也占 1/3 宽，不拉满整行；
        * 放大 = 宽高同乘 EXPAND_SCALE，卡片用 sticky="ns" 居中，
          不再跨满整行（跨满整行会把宽拉到约 3 倍、而高度只有 1.9 倍，
          图像就被横向拉长了）。

        reset_view：把滚动位置拉回顶部。行数变化后滚动偏移会失配，
        表现为"显示共 13 张图但一片空白"。
        """
        for c in range(GRID_COLS):
            try:
                self._inner.columnconfigure(c, weight=1, uniform="chartcol")
            except Exception:
                pass
        row = 0
        col = 0
        for rec in self._items:
            if not rec["visible"]:
                try:
                    rec["card"].grid_remove()
                except Exception:
                    pass
                continue
            if rec["expanded"]:
                if col != 0:
                    row += 1
                    col = 0
                # columnspan 占满一行只为居中；sticky 不带 e/w，卡片保持自己的宽度
                rec["card"].grid(row=row, column=0, columnspan=GRID_COLS,
                                 sticky="ns", padx=CARD_PAD, pady=CARD_PAD)
                row += 1
                col = 0
            else:
                rec["card"].grid(row=row, column=col, columnspan=1,
                                 sticky="nsew", padx=CARD_PAD, pady=CARD_PAD)
                col += 1
                if col >= GRID_COLS:
                    col = 0
                    row += 1

        if reset_view:
            self.scroll_top()

    def scroll_top(self):
        """把图表区滚回顶部（布局变化后立即生效 + 下一帧再兜一次）。

        `yview_moveto` 依赖 scrollregion 已经更新；刚改完布局时它可能还是旧值，
        所以补一个 after_idle，否则会停在错误的偏移量上。
        """
        def _go():
            try:
                self._canvas.yview_moveto(0)
            except Exception:
                pass
        _go()
        try:
            self._canvas.after_idle(_go)
        except Exception:
            pass

    # —— 对外接口 ——
    def add_figure(self, figure, title=""):
        """主线程调用：追加一张图并显示（供任务实时插图）。"""
        if figure is None:
            return
        rec = self._create(figure, title)
        if rec is None:
            return
        rec["visible"] = True
        # 新增图后停在顶部：原来每次加图都 yview_moveto(1.0) 追最新，
        # 13 张图跑完视图就停在最底部，用户看到的是最后几张的边角。
        self._relayout(reset_view=True)
        self._update_count()

    def _update_count(self):
        n = sum(1 for r in self._items if r["visible"])
        self.count_label.configure(text=("共 %d 张图" % n) if n else "暂无图表")

    def get_state(self):
        """返回当前可见的 [(figure, title), ...]（含放大状态由 title 外单独维护）。"""
        return [(r["figure"], r["title"]) for r in self._items if r["visible"]]

    def set_state(self, items):
        """按 items 顺序切换可见图表；已渲染过的 canvas 直接复用，不重绘。"""
        items = [it for it in (items or []) if it and it[0] is not None]
        wanted_ids = set(id(f) for f, _ in items)

        # 全部隐藏
        for rec in self._items:
            rec["visible"] = False

        # 按新顺序显示
        for figure, title in items:
            rec = self._create(figure, title)
            if rec is None:
                continue
            rec["visible"] = True

        # 未被任何工具引用的卡片保持隐藏（保留 canvas 供切回复用）
        for rec in self._items:
            if id(rec["figure"]) not in wanted_ids:
                rec["visible"] = False

        self._relayout(reset_view=True)
        self._update_count()

    def clear(self):
        """销毁当前**可见**的图表（同一工具重新运行前清空自己的旧图）。

        隐藏（属于其他工具）的 canvas 保留，切回该工具时可秒开、不重绘。
        """
        kept = []
        for rec in self._items:
            if rec["visible"]:
                try:
                    rec["card"].destroy()
                except Exception:
                    pass
            else:
                kept.append(rec)
        self._items = kept
        self._relayout(reset_view=True)
        self._update_count()

    def purge(self):
        """彻底销毁所有图表（含隐藏缓存）。"""
        for rec in self._items:
            try:
                rec["card"].destroy()
            except Exception:
                pass
        self._items = []
        self._update_count()
