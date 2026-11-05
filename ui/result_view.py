# -*- coding: utf-8 -*-
"""结果表格（对应 PRD FR-110 / FR-111）。

支持排序、按状态筛选、导出 Excel/CSV；失败行高亮。

ttkbootstrap 升级点：
    1. 失败行配色改为读取当前主题的 danger 语义色，换主题不再"打架"
       （原先硬编码 #fadbd8，切到深色主题后几乎不可读）
    2. 表头显示排序方向 ▲/▼，原先点了没有任何反馈
    3. 最后一列 stretch 填充剩余宽度，不再在大屏上留白
    4. 空态内建，无需主窗口在外面额外挂一个 Label

对外接口保持不变：set_columns / clear / add_row / refresh / export / .rows

Python 3.8 兼容：类型注解一律使用 typing。
"""
import tkinter as tk
import ttkbootstrap as ttk
from tkinter import filedialog
from ttkbootstrap.dialogs import Messagebox
from typing import Any, Dict, List, Optional, Tuple

FILTERS = ["全部", "成功", "失败"]


def _shift_color(hex_color, pct=0.06):
    """把颜色按亮度微调：深底提亮、浅底加深，用来做斑马纹。"""
    try:
        c = str(hex_color or "").lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6:
            return hex_color
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        delta = int(255 * pct) * (1 if lum < 128 else -1)
        r, g, b = (max(0, min(255, v + delta)) for v in (r, g, b))
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return hex_color

# 表格字号（与主窗口的显示字号同步，两侧常量保持一致）
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 32
DEFAULT_FONT_SIZE = 12

# 单帧渲染上限：超过后转批量刷新，避免逐行 insert 拖慢主线程
BATCH_LIMIT = 2000


class ResultView(object):
    def __init__(self, master, columns=None):
        self.frame = ttk.Frame(master)
        self.columns = list(columns or [])
        self.rows = []
        self._filter_var = tk.StringVar(value="全部")
        self._sort_desc = {}
        self._sort_col = None

        self._build_toolbar()

        # 注意：ttkbootstrap 的 Treeview 带 bootstyle 时会用自己的样式名，
        # 把传进来的 style 覆盖掉 —— 必须在创建后再显式挂一次，
        # 否则 Result.Treeview 里的字号/行高永远不生效（「字调不大」的根因）。
        self.tree = ttk.Treeview(self.frame, columns=self.columns, show="headings",
                                 height=12, bootstyle="primary", style="Result.Treeview")
        self.tree.configure(style="Result.Treeview")
        self._font_size = DEFAULT_FONT_SIZE
        self._fit_w = 0
        self.tree.bind("<Configure>", self._on_tree_configure)
        self._apply_columns()

        vs = ttk.Scrollbar(self.frame, orient="vertical", command=self.tree.yview,
                           bootstyle="primary-round")
        hs = ttk.Scrollbar(self.frame, orient="horizontal", command=self.tree.xview,
                           bootstyle="primary-round")
        self._vs, self._hs = vs, hs
        # 滚动条按需出现：内容放得下时留着一条灰条纯粹是碍眼
        self.tree.configure(yscrollcommand=self._on_yscroll,
                            xscrollcommand=self._on_xscroll)

        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)

        self.empty_label = ttk.Label(self.frame, text="", bootstyle="secondary")
        self.refresh_tag_colors()

    # —— 构建 ——
    def _build_toolbar(self):
        top = ttk.Frame(self.frame)
        top.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(top, text="筛选：", bootstyle="secondary").pack(side="left")
        box = ttk.Combobox(top, textvariable=self._filter_var, values=FILTERS,
                           state="readonly", width=8, bootstyle="primary")
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.count_label = ttk.Label(top, text="", bootstyle="secondary")
        self.count_label.pack(side="left", padx=10)
        # 字号就放在表格工具栏上（原来只在参数区底部有个 Spinbox，不好找）
        ttk.Button(top, text="A-", width=3, bootstyle="secondary-outline",
                   command=lambda: self._bump_font(-1)).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="A+", width=3, bootstyle="secondary-outline",
                   command=lambda: self._bump_font(1)).pack(side="right")
        ttk.Button(top, text="默认", bootstyle="secondary-outline",
                   command=lambda: self._set_font_and_notify(DEFAULT_FONT_SIZE)
                   ).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="导出 Excel", bootstyle="secondary-outline",
                   command=lambda: self.export(".xlsx")).pack(side="right", padx=4)
        ttk.Button(top, text="导出 CSV", bootstyle="secondary-outline",
                   command=lambda: self.export(".csv")).pack(side="right")

    def _bump_font(self, step):
        self._set_font_and_notify(max(MIN_FONT_SIZE,
                                      min(MAX_FONT_SIZE, self._font_size + step)))

    def _set_font_and_notify(self, size):
        self.set_font_size(size)
        if getattr(self, "on_font_size", None):
            try:
                self.on_font_size(size)
            except Exception:
                pass

    def _apply_columns(self):
        for col in self.columns:
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_by(c))
            is_last = col == self.columns[-1]
            self.tree.column(col, width=120, anchor="w",
                             stretch=is_last, minwidth=60)

    def autofit_columns(self):
        """按内容实际宽度分配列宽，避免「窄列浪费、长列被截、还要横向拖」。

        原先是每列固定 120px：6 列 720px，1180 窗口的表格区只有 622px，
        一打开就有横向滚动条；而"IP"这种窄列白占了 120px。
        这里用字体实际测量（抽样前 150 行），夹在 60~360 之间，
        剩余宽度仍由最后一列 stretch 吸收。
        """
        if not self.columns or not self.rows:
            return
        try:
            import tkinter.font as tkfont
            family = "Microsoft YaHei UI"
            font = tkfont.Font(family=family, size=getattr(self, "_font_size", 10))
        except Exception:
            return
        pad = 26
        sample = self.rows[:150]
        widths = []
        for i, col in enumerate(self.columns):
            widest = font.measure(str(col)) + 18      # 表头 + 排序箭头
            for item in sample:
                # self.rows 里存的是 (行数据, 状态)，取行数据那一半
                vals = item[0] if (isinstance(item, tuple) and len(item) == 2
                                   and isinstance(item[0], (list, tuple))) else item
                if not isinstance(vals, (list, tuple)) or i >= len(vals):
                    continue
                w = font.measure(str(vals[i]))
                if w > widest:
                    widest = w
            widths.append(max(60, min(360, widest + pad)))
        # 超出可视宽度时按比例收一点，尽量不出现横向滚动条
        try:
            avail = self.tree.winfo_width()
        except Exception:
            avail = 0
        total = sum(widths)
        if avail > 200 and total > avail:
            k = avail / float(total)
            widths = [max(60, int(w * k)) for w in widths]
        for col, w in zip(self.columns, widths):
            try:
                self.tree.column(col, width=w)
            except Exception:
                pass

    def _on_tree_configure(self, event):
        """表格宽度变化（窗口缩放/全屏）时重算列宽，别让内容挤在左边一条。"""
        if abs(event.width - self._fit_w) <= 40:
            return
        self._fit_w = event.width
        self.autofit_columns()

    def _on_yscroll(self, first, last):
        try:
            self._vs.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                self._vs.pack_forget()
            elif not self._vs.winfo_ismapped():
                self._vs.pack(side="right", fill="y")
        except Exception:
            pass

    def _on_xscroll(self, first, last):
        try:
            self._hs.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                self._hs.pack_forget()
            elif not self._hs.winfo_ismapped():
                self._hs.pack(side="bottom", fill="x")
        except Exception:
            pass

    def refresh_tag_colors(self):
        """按当前主题重建行标签配色。切换主题后调用此方法即可。"""
        try:
            colors = ttk.Style().colors
            fail_fg = colors.danger
        except Exception:
            fail_fg = "#c0392b"
        self.tree.tag_configure("fail", foreground=fail_fg)
        # 斑马纹：隔行浅色底，长表格不容易看错行
        try:
            style = ttk.Style()
            base = (style.lookup("Result.Treeview", "background")
                    or style.lookup("Treeview", "background")
                    or style.colors.bg)
            self.tree.tag_configure("zebra", background=_shift_color(base, 0.07))
        except Exception:
            pass

    def set_font_size(self, size):
        """调整表格字号（同时放大行高，避免文字被裁切）。

        使用独立样式名 Result.Treeview，避免影响左侧工具树。
        """
        self._font_size = size
        try:
            family = "Microsoft YaHei UI"
            style = ttk.Style()
            style.configure("Result.Treeview", font=(family, size),
                            rowheight=int(size * 2.2))
            style.configure("Result.Treeview.Heading", font=(family, size, "bold"))
        except Exception:
            pass
        # 再挂一次样式：主题切换/控件重建后 style 可能被重置回去
        try:
            self.tree.configure(style="Result.Treeview")
        except Exception:
            pass
        self.refresh_tag_colors()      # 背景色随主题/样式变化，斑马纹跟着重算
        # 字号变了文字宽度也变，列宽得重算
        self.autofit_columns()

    # —— 数据 ——
    def set_columns(self, columns):
        self.columns = list(columns or [])
        self.tree.configure(columns=self.columns)
        self._apply_columns()

    def clear(self):
        self.rows = []
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.count_label.configure(text="")
        self.empty_label.pack_forget()

    def add_row(self, row, status):
        self.rows.append((list(row), status))
        if len(self.rows) > BATCH_LIMIT:
            self.refresh()
            return
        self._insert(list(row), status, len(self.rows) - 1)

    def _insert(self, row, status, idx=0):
        # 失败行优先用红字；其余隔行加底色
        if status not in ("OK", "PARTIAL_OK"):
            tags = ("fail",)
        elif idx % 2 == 1:
            tags = ("zebra",)
        else:
            tags = ()
        try:
            self.tree.insert("", "end", values=row, tags=tags)
        except Exception:
            # 行长度与列数不一致时 Treeview 会抛错，兜底补齐后再插
            padded = list(row) + [""] * (len(self.columns) - len(row))
            self.tree.insert("", "end", values=padded[:len(self.columns)], tags=tags)

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        shown = self._filtered()
        for i, (row, status) in enumerate(shown):
            self._insert(row, status, i)

        if self.rows:
            self.count_label.configure(text="共 %d 行（显示 %d 行）" % (len(self.rows), len(shown)))
            self.empty_label.pack_forget()
            # 数据变了列宽也重算：一列都固定 120px 时，窄列（IP）白占空间、
            # 宽列（失败原因）被截，还会莫名多出横向滚动条
            self.autofit_columns()
        else:
            self.count_label.configure(text="")
            self.empty_label.configure(text="本次未产生结果数据")
            self.empty_label.pack(pady=12)

    def _filtered(self):
        f = self._filter_var.get()
        if f == "成功":
            return [r for r in self.rows if r[1] == "OK"]
        if f == "失败":
            return [r for r in self.rows if r[1] not in ("OK", "PARTIAL_OK")]
        return self.rows

    def sort_by(self, col):
        if col not in self.columns:
            return
        desc = not self._sort_desc.get(col, False)
        self._sort_desc[col] = desc
        self._sort_col = col
        idx = self.columns.index(col)
        self.rows.sort(key=lambda r: self._sort_key(r[0][idx] if idx < len(r[0]) else ""),
                       reverse=desc)
        self.update_headings()
        self.refresh()

    @staticmethod
    def _sort_key(value):
        """排序键：纯数字按数值排序，否则按字符串（大小写不敏感）。"""
        if value is None:
            return (1, "")
        text = str(value).strip()
        # 去掉千分位等可能干扰的字符，尝试作为数值比较
        try:
            return (0, float(text))
        except (ValueError, TypeError):
            return (1, text.lower())

    def update_headings(self):
        """刷新表头：当前排序列带上方向箭头。"""
        for col in self.columns:
            if col == self._sort_col:
                mark = " ▼" if self._sort_desc.get(col) else " ▲"
            else:
                mark = ""
            self.tree.heading(col, text=col + mark, command=lambda c=col: self.sort_by(c))

    # —— 导出 ——
    def export(self, suffix):
        shown = self._filtered()
        if not shown:
            Messagebox.show_info("暂无结果可导出", title="提示")
            return
        path = filedialog.asksaveasfilename(defaultextension=suffix,
                                            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not path:
            return
        try:
            from core.result_writer import export_rows
            target = export_rows([r[0] for r in shown], self.columns, path)
            Messagebox.show_info("已导出到：\n%s" % target, title="导出完成")
        except Exception as exc:
            Messagebox.show_error("文件可能被占用：%s" % exc, title="导出失败")
