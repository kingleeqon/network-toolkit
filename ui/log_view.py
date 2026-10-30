# -*- coding: utf-8 -*-
"""运行日志面板（对应 PRD FR-107）。

界面只保留最近 MAX_LINES 行，完整日志由框架写入 run.log。

ttkbootstrap 升级点：
    1. ScrolledText 取代手写的 Text + Scrollbar 组合（少 6 行、行为更稳）
    2. 日志等级配色读取当前主题的语义色，不再硬编码三个 hex
       （原先 #2c3e50/#b9770e/#c0392b 在深色主题下要么发灰要么刺眼）
    3. 新增清空按钮

对外接口保持不变：append(level, msg, ip) / clear() / export(path) / .frame

Python 3.8 兼容。
"""
import datetime
import tkinter as tk
import ttkbootstrap as ttk
from tkinter import filedialog
from ttkbootstrap.dialogs import Messagebox
from typing import Optional

MAX_LINES = 5000
LEVELS = ("INFO", "WARN", "ERROR")


class LogView(object):
    def __init__(self, master, height=10):
        self.frame = ttk.Frame(master)
        self.level_tags = {}

        bar = ttk.Frame(self.frame)
        bar.pack(side="top", fill="x", pady=(0, 4))
        ttk.Label(bar, text="运行日志", bootstyle="secondary").pack(side="left")
        self.count_label = ttk.Label(bar, text="", bootstyle="secondary")
        self.count_label.pack(side="left", padx=8)
        ttk.Button(bar, text="清空", width=6, bootstyle="secondary-outline",
                   command=self.clear).pack(side="right")

        # ScrolledText 本身就继承自 Text：直接对它 insert/get/tag_configure 即可，
        # 它的 pack/grid 方法被代理到内部的容器 Frame 上。
        self.text = ttk.ScrolledText(self.frame, wrap="none", height=height)
        self.text.pack(side="left", fill="both", expand=True)

        opt = ttk.Frame(self.frame)
        opt.pack(side="bottom", fill="x", pady=(4, 0))
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="自动滚动", variable=self.follow,
                        bootstyle="secondary-round-toggle").pack(side="left")
        ttk.Button(opt, text="导出日志", width=9, bootstyle="secondary-outline",
                   command=self.ask_export).pack(side="right")

        self.line_count = 0
        self.refresh_level_colors()

    def refresh_level_colors(self):
        """按当前主题重建日志等级配色。切换主题后调用。"""
        try:
            colors = ttk.Style().colors
            mapping = {"INFO": colors.fg, "WARN": colors.warning, "ERROR": colors.danger}
        except Exception:
            mapping = {"INFO": "#2c3e50", "WARN": "#b9770e", "ERROR": "#c0392b"}

        for level in LEVELS:
            tag = "lv-%s" % level
            self.text.tag_configure(tag, foreground=mapping[level])
            self.level_tags[level] = tag

    def set_font_size(self, size):
        """调整日志字号。"""
        try:
            self.text.configure(font=("Microsoft YaHei UI", size))
        except Exception:
            pass

    def append(self, level, msg, ip=None):
        level = level if level in LEVELS else "INFO"
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = "[%s]" % ip if ip else ""
        line = "%s %s %s %s" % (stamp, level, prefix, msg)
        self._write_line(level, line)

    def _write_line(self, level, line):
        """写入一行（line 不含换行符），并按级别着色。"""
        tag = self.level_tags.get(level)
        try:
            self.text.configure(state="normal")
            if tag:
                self.text.insert("end", line + "\n", tag)
            else:
                self.text.insert("end", line + "\n")
            self.line_count += 1
            if self.line_count > MAX_LINES:
                self.text.delete("1.0", "2.0")
                self.line_count -= 1
            if self.follow.get():
                self.text.see("end")
            self.text.configure(state="disabled")
            self.count_label.configure(text="%d 行" % self.line_count)
        except Exception:
            pass

    # —— 状态导入导出（供主窗口按工具切换时保存/恢复）——
    def get_state(self):
        """返回 [(level, line), ...]，line 已含时间戳，恢复时原样回填。"""
        try:
            self.text.configure(state="normal")
            content = self.text.get("1.0", "end-1c")
            self.text.configure(state="disabled")
        except Exception:
            return []
        entries = []
        for raw in content.split("\n"):
            if not raw:
                continue
            # 行格式：HH:MM:SS LEVEL [ip] msg，第二段即等级
            parts = raw.split(" ", 2)
            level = parts[1] if len(parts) > 1 and parts[1] in LEVELS else "INFO"
            entries.append((level, raw))
        return entries

    def set_state(self, entries):
        """用 get_state() 的结果整体恢复（内部先清空）。"""
        self.clear()
        for level, line in (entries or []):
            self._write_line(level if level in LEVELS else "INFO", line)

    def clear(self):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
            self.line_count = 0
            self.count_label.configure(text="")
        except Exception:
            pass

    def export(self, path):
        try:
            self.text.configure(state="normal")
            content = self.text.get("1.0", "end")
            self.text.configure(state="disabled")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return path
        except Exception as exc:
            Messagebox.show_error("导出失败：%s" % exc, title="导出日志")
            return None

    def ask_export(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("文本", "*.txt"), ("日志", "*.log")])
        if path:
            self.export(path)
            Messagebox.show_info("已导出到：\n%s" % path, title="导出日志")
