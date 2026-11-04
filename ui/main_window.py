# -*- coding: utf-8 -*-
"""主界面（ttkbootstrap 主题层 + 原生 ttk 控件）。

线程模型不变：工具运行在 Worker 线程，通过 core.task_engine 的 Queue 把
消息送到主线程；主线程用 after(POLL_INTERVAL_MS) 轮询消费。
Worker 线程内禁止操作任何控件。

布局四层：
    应用栏 AppBar    标题 / 版本 / 操作者 / 主题切换 / 关于
    导航区 Sidebar   工具搜索 + 按分类分组的工具树 + 加载失败徽章
    工作区 Workspace 参数输入卡片 → 控制条与进度 → 结果表 / 运行日志 / 任务摘要
    状态栏 Statusbar 任务状态徽章 / 处理进度 / 结果目录

Python 3.8 兼容：类型注解一律使用 typing。
"""
import json
import os
import queue
import re
import tkinter as tk
import ttkbootstrap as ttk
from pathlib import Path
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.tooltip import ToolTip
from typing import Any, Dict, List, Optional, Tuple

from core.registry import Registry
from core.scheduler import PIPELINE_KEY, SchedulerManager, exit_code_text
from core.task_engine import (CANCELLED, CANCELLING, FAILED, IDLE, PARTIAL,
                              RUNNING, SUCCEEDED, TaskEngine)
from ui.form_builder import FormBuilder
from ui.log_view import LogView
from ui.result_view import ResultView
from ui.chart_view import ChartView
from ui.auto_task_dialog import open_dialog as open_auto_task
from ui.schedule_overview import open_dialog as open_schedule_overview
from ui.credential_dialog import open_dialog as open_credential

POLL_INTERVAL_MS = 100
FONT_FAMILY = "Microsoft YaHei UI"

# 界面主题清单：前段浅色，后段深色
THEMES = [
    ("flatly", "Flatly（浅色）"),
    ("litera", "Litera（浅色）"),
    ("journal", "Journal（浅色）"),
    ("minty", "Minty（浅色）"),
    ("cosmo", "Cosmo（浅色）"),
    ("darkly", "Darkly（深色）"),
    ("cyborg", "Cyborg（深色）"),
    ("solar", "Solar（深色）"),
    ("superhero", "Superhero（深色）"),
]
DEFAULT_THEME = "flatly"

# 任务状态 -> (bootstyle, 中文文案)
STATE_STYLE = {
    IDLE: ("secondary", "空闲"),
    RUNNING: ("primary", "运行中"),
    CANCELLING: ("warning", "中止中"),
    SUCCEEDED: ("success", "已完成"),
    PARTIAL: ("warning", "部分失败"),
    FAILED: ("danger", "失败"),
    CANCELLED: ("secondary", "已中止"),
}

DEPS = ("paramiko", "netmiko", "openpyxl", "win32crypt", "cryptography")
PREF_PATH = os.path.join(os.path.expanduser("~"), ".network_toolkit_ui.json")

BASE_FONT_SIZE = 12          # 图表/表格的基准字号（字号设置以此为 1.0 比例）；中文 12 号起看得清
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 22
OUTPUT_MIN_H = 340           # 自动贴合时输出区（图表/结果表）保底高度
OUTPUT_MIN_H_USER = 200      # 用户手动拖过分隔条后，输出区只需保底这么多


def load_ui_pref():
    """读取界面偏好（主题/字号/参数区高度）。文件不存在或损坏时回退默认值。"""
    pref = {"theme": DEFAULT_THEME, "font_size": BASE_FONT_SIZE, "sash": 0}
    try:
        with open(PREF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pref["theme"] = data.get("theme", DEFAULT_THEME)
        try:
            size = int(data.get("font_size", BASE_FONT_SIZE))
            # 老版本默认是 10，用户没动过的话这次跟着新默认 12 走
            # （手动调成 10 的场景极少，且表格工具栏一键可调回来）
            if size == 10 and BASE_FONT_SIZE != 10:
                size = BASE_FONT_SIZE
            pref["font_size"] = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        except (TypeError, ValueError):
            pass
        try:
            pref["sash"] = max(0, int(data.get("sash", 0)))
        except (TypeError, ValueError):
            pass
    except Exception:
        pass
    valid = [t[0] for t in THEMES]
    if pref["theme"] not in valid:
        pref["theme"] = DEFAULT_THEME
    return pref


def save_ui_pref(pref):
    """合并写入界面偏好，避免只保存部分字段时冲掉其余设置。"""
    try:
        data = load_ui_pref()
        data.update(pref or {})
        with open(PREF_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class MainWindow(object):
    def __init__(self, root, tools_dir, output_base="output", app_version="0.1.0"):
        self.root = root
        self.app_version = app_version
        self.registry = Registry(tools_dir)
        self.engine = TaskEngine(output_base=output_base, app_version=app_version,
                                 operator=os.environ.get("USERNAME", ""))
        self.current_tool = None
        self.form = None
        self._form_folded = False
        # 每个工具的输出缓存：tool_id -> {columns, rows, charts, logs, summary}
        # 切换工具时保留各自输出（结果表/图表/日志/摘要），切回即恢复，互不串台
        self._output_cache = {}
        self._form_cache = {}        # 工具参数值缓存（按工具 id）

        # 界面字号（结果表/日志/摘要/图表统一生效，按工具可调并记忆）
        pref = load_ui_pref()
        self.ui_font_size = pref["font_size"]
        self.font_size_var = tk.IntVar(value=self.ui_font_size)
        # 用户上次手工拖出来的参数区高度（0 = 没拖过，按内容自动贴合）
        self._user_sash = int(pref.get("sash", 0) or 0)
        self._sash_at_press = None
        self._reflow_job = None
        # 参数区是被"图表放大"自动收起的吗（还原时只恢复我们自己收的）
        self._auto_folded = False

        self.root.title("网络运维工具箱 v%s" % app_version)
        # 再小的话左侧 260px + 参数区会把输出区挤成一条缝
        try:
            self.root.minsize(960, 620)
        except Exception:
            pass
        self._build_layout()
        self._load_tools()
        # 应用内定时器：到点自动跑「采集 + 备份 + 对比」（应用开着才有效）
        self._sched_log_targets = []      # 打开着的「定时任务」窗口的输出框
        self.scheduler = SchedulerManager(on_log=self._on_scheduler_log,
                                          on_status=self._on_scheduler_status,
                                          on_run_done=self._on_scheduler_run_done)
        # 调度器事件（日志/状态/运行结束）从工作线程入队，主线程统一排空；
        # 工作线程不能碰 Tk —— root.after 跨线程调用会死锁
        self._sched_q = queue.Queue()
        self.root.after(200, self._drain_sched_events)
        self.scheduler.start()
        self.root.after(600, self._on_scheduler_status)
        self.root.after(2000, self._tick_sched_status)
        self._apply_font_size()
        self._poll()
        # F11 全屏 / Esc 退出全屏：全屏时布局全靠 grid 权重自适应
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", lambda e: self.toggle_fullscreen(False))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def toggle_fullscreen(self, force=None):
        """F11 全屏开关（force=False 表示退出）。"""
        try:
            cur = bool(self.root.attributes("-fullscreen"))
            want = (not cur) if force is None else bool(force)
            self.root.attributes("-fullscreen", want)
        except Exception:
            return

    # —— 布局 ——
    def _build_layout(self):
        operator = os.environ.get("USERNAME", "")

        # 应用栏
        appbar = ttk.Frame(self.root, bootstyle="secondary", padding=(10, 6))
        appbar.pack(side="top", fill="x")
        ttk.Label(appbar, text="网络运维工具箱",
                  font=(FONT_FAMILY, 12, "bold")).pack(side="left")
        ttk.Label(appbar, text="v%s · 操作者 %s" % (self.app_version, operator),
                  bootstyle="secondary").pack(side="left", padx=8)

        theme_names = [t[1] for t in THEMES]
        self.theme_var = tk.StringVar(value=THEMES[0][1])
        self._sync_theme_var()
        theme_box = ttk.Combobox(appbar, textvariable=self.theme_var, values=theme_names,
                                 state="readonly", width=14, bootstyle="secondary")
        theme_box.pack(side="right")
        theme_box.bind("<<ComboboxSelected>>", lambda e: self.on_change_theme())
        ToolTip(theme_box, text="切换界面主题，选择会被记住")

        ttk.Button(appbar, text="关于", width=6, bootstyle="secondary-outline",
                   command=self.show_about).pack(side="right", padx=(0, 10))
        ttk.Button(appbar, text="定时任务", width=9, bootstyle="primary-outline",
                   command=self.show_auto_task).pack(side="right", padx=(0, 10))
        ttk.Button(appbar, text="凭证", width=6, bootstyle="secondary-outline",
                   command=self.show_credentials).pack(side="right", padx=(0, 10))

        # 状态栏必须先于 body 声明，否则 body 的 expand=True 会吃掉全部剩余空间，
        # 把它挤到可视区外
        self._build_statusbar()

        # 主体
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, minsize=260)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="ns", padx=(4, 0))
        self._build_sidebar(left)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 4))
        right.columnconfigure(0, weight=1)
        # 剩余高度全部给「参数/输出」分栏（第 2 行），标题与描述只占自然高度
        right.rowconfigure(2, weight=1)
        self._build_workspace(right)

    def _build_sidebar(self, left):
        ttk.Label(left, text="工具", bootstyle="secondary").pack(anchor="w", padx=8, pady=(8, 2))
        self.search_var = tk.StringVar()
        search = ttk.Entry(left, textvariable=self.search_var, bootstyle="secondary")
        search.pack(fill="x", padx=8, pady=2)
        search.insert(0, "")
        ToolTip(search, text="按工具名或 id 过滤")
        search.bind("<KeyRelease>", lambda e: self._render_tool_list())

        frame = ttk.Frame(left)
        frame.pack(fill="both", expand=True, padx=8, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tool_tree = ttk.Treeview(frame, show="tree", bootstyle="secondary")
        self.tool_tree.column("#0", width=250, stretch=True, minwidth=140)
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.tool_tree.yview,
                           bootstyle="secondary-round")
        hs = ttk.Scrollbar(frame, orient="horizontal", command=self.tool_tree.xview,
                           bootstyle="secondary-round")
        self.tool_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tool_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.tool_tree.bind("<<TreeviewSelect>>", self.on_select_tool)

        self.empty_label = ttk.Label(left, text="", bootstyle="secondary", wraplength=180)
        self.fail_badge = ttk.Label(left, text="", bootstyle="warning", wraplength=180)

    def _build_workspace(self, right):
        self.tool_title = ttk.Label(right, text="未选择工具",
                                    font=(FONT_FAMILY, 11, "bold"))
        self.tool_title.grid(row=0, column=0, sticky="w", padx=4, pady=(6, 0))
        self.tool_desc = ttk.Label(right, text="", bootstyle="secondary",
                                   wraplength=780)
        self.tool_desc.grid(row=1, column=0, sticky="w", padx=4)
        # 换行宽度跟着窗口走：写死 780 在窄窗口会折成第二行只剩一个"）"
        right.bind("<Configure>", self._on_right_configure)

        # 参数区 / 输出区 放进可上下拖拽的分栏：
        # 参数多的工具（如 CRC趋势分析 11 项，每行约 43px）按自然高度会挤掉输出区，
        # 用分栏后默认把剩余空间全给输出区，用户还能自己拖动分配。
        self.vsplit = ttk.Panedwindow(right, orient="vertical", bootstyle="secondary")
        self.vsplit.grid(row=2, column=0, sticky="nsew", padx=4, pady=(6, 2))
        # 窗口尺寸/工具变化时自动贴合参数区；用户拖过分隔条后不再自动调
        self.vsplit.bind("<Configure>", self._on_split_configure)
        self.vsplit.bind("<ButtonPress-1>", self._on_split_press)
        self.vsplit.bind("<ButtonRelease-1>", self._on_split_dragged)

        # 参数输入区：外面包一层可滚动容器。
        # 参数多的工具（CRC趋势分析 12 项）在 1024×680 这种小窗口下自然高度 329px，
        # 分栏只肯给 296px，底部的「显示字号」行会被直接裁掉且没有任何提示；
        # 包成滚动区后，放不下就出现滚动条，参数一个都不会丢。
        self.form_holder = ttk.Frame(self.vsplit)
        self.form_holder.columnconfigure(0, weight=1)
        self.form_holder.rowconfigure(0, weight=1)
        self.form_canvas = tk.Canvas(self.form_holder, highlightthickness=0,
                                     borderwidth=0)
        self.form_canvas.grid(row=0, column=0, sticky="nsew")
        self.form_sbar = ttk.Scrollbar(self.form_holder, orient="vertical",
                                       command=self.form_canvas.yview,
                                       bootstyle="secondary-round")
        self.form_hbar = ttk.Scrollbar(self.form_holder, orient="horizontal",
                                       command=self.form_canvas.xview,
                                       bootstyle="secondary-round")
        self.form_canvas.configure(yscrollcommand=self._on_form_scroll,
                                   xscrollcommand=self._on_form_xscroll)

        self.form_frame = ttk.Labelframe(self.form_canvas, text="参数", padding=6,
                                         bootstyle="secondary")
        self.form_frame.columnconfigure(1, weight=1)
        self.form_hint = ttk.Label(self.form_frame, text="请先在左侧选择工具",
                                   bootstyle="secondary")
        self.form_hint.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self._form_win = self.form_canvas.create_window((0, 0), window=self.form_frame,
                                                        anchor="nw")
        self.form_frame.bind("<Configure>", self._on_form_inner_configure)
        self.form_canvas.bind("<Configure>", self._on_form_canvas_configure)
        self.form_canvas.bind("<Enter>", self._bind_form_wheel)
        self.form_canvas.bind("<Leave>", self._unbind_form_wheel)
        self.vsplit.add(self.form_holder, weight=0)

        lower = ttk.Frame(self.vsplit)
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(1, weight=1)
        self.vsplit.add(lower, weight=1)

        # 控制条 + 进度
        prog = ttk.Frame(lower)
        prog.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        self.btn_start = ttk.Button(prog, text="开始", width=8, bootstyle="success",
                                    command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(prog, text="中止", width=8, bootstyle="danger-outline",
                                     command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        ttk.Button(prog, text="打开结果目录", bootstyle="secondary-outline",
                   command=self.open_output_dir).pack(side="left", padx=6)
        # 一键收起参数区，把整块高度让给结果/图表
        self.btn_fold = ttk.Button(prog, text="收起参数", width=9,
                                   bootstyle="secondary-outline",
                                   command=self.toggle_form_pane)
        self.btn_fold.pack(side="left", padx=(0, 6))

        self.progress = ttk.Floodgauge(prog, orient="horizontal", mask="{}%",
                                       maximum=100, bootstyle="success", length=220)
        self.progress.pack(side="right", fill="x", expand=True, padx=(12, 0))
        self.progress_label = ttk.Label(prog, text="尚未运行", width=16,
                                        bootstyle="secondary", anchor="e")
        self.progress_label.pack(side="right")
        # 采集类任务动辄几百台，进度条上的"24/543"看不出采到哪台了，
        # 这里单独显示当前正在处理的设备（IP + 设备名）
        self.current_label = ttk.Label(prog, text="", width=42,
                                       bootstyle="secondary", anchor="e")
        self.current_label.pack(side="right", padx=(0, 10))

        # 结果输出区（标签页按工具的 output_tabs 声明动态重建）
        self.tabs = ttk.Notebook(lower, bootstyle="secondary")
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # 输出组件统一在此创建一次，按需挂到 Notebook；tab key -> (widget, text)
        self.result_view = ResultView(self.tabs)
        self.chart_view = ChartView(self.tabs)
        self.log_view = LogView(self.tabs, height=9)
        self.summary_text = ttk.ScrolledText(self.tabs, wrap="word", height=10)
        self.summary_text.insert("end", "尚未运行任务。\n")
        self.summary_text.configure(state="disabled")

        self._tab_defs = {
            "result":  (self.result_view.frame, " 结果表 "),
            "chart":   (self.chart_view.frame, " 图表 "),
            "log":     (self.log_view.frame, " 运行日志 "),
            "summary": (self.summary_text, " 任务摘要 "),
        }
        # 图表放大时把参数区让出来（否则放大后的图被裁掉一半，看着像"没放大"）
        self.chart_view.on_expand = self._on_chart_expand
        # 结果表工具栏的 A+/A-/默认 与参数区「显示字号」保持同步
        self.result_view.on_font_size = self._on_font_size_from_table
        # Ctrl+滚轮直接缩放结果表 / 运行日志
        self.result_view.tree.bind("<Control-MouseWheel>", self._on_font_wheel)
        self.log_view.text.bind("<Control-MouseWheel>", self._on_font_wheel)
        # key -> 当前 Notebook 内的 tab widget 名（用于 select）
        self._tab_ids = {}
        self._current_tab_keys = []

    def _rebuild_output_tabs(self, keys):
        """按工具的 output_tabs 声明重建标签页（移除旧的再按序加入）。

        标签页组合未变化时直接跳过，避免无谓的 Notebook 重排。
        """
        keys = tuple(k for k in keys if k in self._tab_defs)
        if keys == tuple(self._current_tab_keys):
            return
        self._current_tab_keys = list(keys)

        for tab_id in self.tabs.tabs():
            self.tabs.forget(tab_id)
        self._tab_ids = {}
        for key in keys:
            widget, text = self._tab_defs[key]
            self.tabs.add(widget, text=text)
            self._tab_ids[key] = self.tabs.tabs()[-1]

    def _select_tab(self, key):
        """按 key 选中标签页（不存在则忽略）。"""
        tab_id = self._tab_ids.get(key)
        if tab_id:
            try:
                self.tabs.select(tab_id)
            except Exception:
                pass

    def _select_best_tab(self):
        """任务结束后切到最有内容的页：图表 > 结果表 > 运行日志。

        之前固定切日志页，会把刚生成好的图表盖住，用户还得手动点回来。
        """
        try:
            has_chart = any(r["visible"] for r in self.chart_view._items)
        except Exception:
            has_chart = False
        try:
            has_result = bool(self.result_view.rows)
        except Exception:
            has_result = False
        if has_chart and "chart" in self._tab_ids:
            self._select_tab("chart")
        elif has_result and "result" in self._tab_ids:
            self._select_tab("result")
        else:
            self._select_tab("log")

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(side="bottom", fill="x")
        self.badge = ttk.Label(bar, text="空闲", bootstyle="secondary-inverse", padding=(8, 1))
        self.badge.pack(side="left")
        self.stat_label = ttk.Label(bar, text="", bootstyle="secondary")
        self.stat_label.pack(side="left", padx=10)
        self.path_label = ttk.Label(bar, text="", bootstyle="secondary")
        self.path_label.pack(side="right")
        # 每日自动执行状态（应用开着时到点自己跑）
        self.sched_label = ttk.Label(bar, text="", bootstyle="secondary")
        self.sched_label.pack(side="right", padx=(10, 0))
        # 结果目录路径通常比状态栏宽，会被直接切掉半截；悬浮显示完整路径
        self._path_tip = None
        try:
            self._path_tip = ToolTip(self.path_label, text="结果目录", delay=400)
        except Exception:
            pass

    def _tick_sched_status(self):
        """定时状态每 2 秒刷新一次（运行中的任务要显示已用时）。"""
        try:
            self._refresh_sched_bar()
            self._on_scheduler_status()
        except Exception:
            pass
        try:
            self.root.after(2000, self._tick_sched_status)
        except Exception:
            pass

    def _on_scheduler_log(self, line):
        """定时任务子进程的输出：入队，主线程排空后显示。

        工作线程禁止碰 Tk —— root.after 跨线程调用会死锁（实测卡死 worker，
        后续回调全部到不了）。
        """
        try:
            self._sched_q.put(("log", line))
        except Exception:
            pass

    def _append_scheduler_log(self, line):
        """把定时任务的输出转给打开着的「定时任务」窗口显示。

        没开窗口也不丢：daily_crc.py 自己会写 logs/daily_crc_YYYYMMDD.log。
        """
        for target in list(getattr(self, "_sched_log_targets", [])):
            try:
                target.append(line + "\n")
                target.see("end")
            except Exception:
                pass

    def _on_scheduler_status(self, _text=None):
        """状态变化信号：入队，主线程排空后刷新（同一线程安全约束）。"""
        try:
            self._sched_q.put(("status",))
        except Exception:
            pass

    def _apply_sched_status(self):
        """状态栏显示"正在运行的定时任务"（仅主线程调用）。"""
        try:
            text = self.scheduler.running_text()
        except Exception:
            text = ""
        try:
            self.sched_label.configure(text=("定时运行中：%s" % text) if text else "")
        except Exception:
            pass
        self._refresh_sched_bar()

    # —— 定时运行结束：回填工具页的运行日志/任务摘要（定时是子进程跑的，
    #    不走界面 TaskEngine，标签页不回填的话用户看不到这次干了什么）——
    def _on_scheduler_run_done(self, key, lines, code, output_dir, run_at):
        try:
            self._sched_q.put(("done", key, list(lines), code, output_dir, run_at))
        except Exception:
            pass

    def _drain_sched_events(self):
        """主线程统一排空调度器事件（日志/状态/运行结束），Tk 只在主线程碰。"""
        try:
            while True:
                item = self._sched_q.get_nowait()
                try:
                    if item[0] == "log":
                        self._append_scheduler_log(item[1])
                    elif item[0] == "status":
                        self._apply_sched_status()
                    elif item[0] == "done":
                        self._apply_sched_run(*item[1:])
                except Exception:
                    import traceback
                    traceback.print_exc()   # 回填异常不能静默，否则又查不到
        except queue.Empty:
            pass
        try:
            self.root.after(200, self._drain_sched_events)
        except Exception:
            pass

    def _apply_sched_run(self, key, lines, code, output_dir, run_at):
        if key == PIPELINE_KEY:
            return   # 流水线没有工具页，输出在「定时任务总览」窗口里看
        flag = exit_code_text(code, is_pipeline=False)
        entries = [("INFO", "───── 定时任务 %s（%s）─────" % (run_at, flag))]
        for ln in lines:
            level = "INFO"
            m = re.search(r"\[(INFO|WARN|WARNING|ERROR|DEBUG)\]", ln[:24])
            if m:
                level = m.group(1)
            entries.append((level, ln))
        summary = self._build_sched_summary(key, flag, code, output_dir, run_at)
        columns, rows = self._load_sched_rows(output_dir)

        if self.current_tool is not None and self.current_tool.id == key:
            for level, text in entries:
                self.log_view.append(level, text)
            self._set_summary_text(summary)
            if rows:
                # 结果表也要回填：定时是子进程跑的，结果行不会走界面消息
                self.result_view.set_columns(columns or self.result_view.columns)
                self.result_view.rows = rows
                self.result_view.refresh()
            self._save_outputs()
        else:
            # 用户在看别的工具：进缓存，切过去时 _restore_outputs 会带出来
            cached = self._output_cache.get(key)
            if cached is None:
                tool = self.registry.get(key)
                cached = {"columns": list(getattr(tool, "result_columns", []) or []),
                          "rows": [], "charts": [], "logs": [], "summary": ""}
                self._output_cache[key] = cached
            cached["logs"] = list(cached.get("logs") or []) + entries
            cached["summary"] = summary
            if rows:
                cached["columns"] = columns or cached.get("columns") or []
                cached["rows"] = rows
        self._refresh_result_files()

    def _load_sched_rows(self, output_dir):
        """读定时运行落盘的 result.json → (columns, [(row, status), ...])。

        界面里跑任务时结果行走内存消息；定时任务是子进程，行只落在自己的
        结果目录里，不读回来结果表就是空的。
        """
        if not output_dir:
            return [], []
        try:
            data = json.loads((Path(output_dir) / "result.json").read_text(encoding="utf-8"))
        except Exception:
            return [], []
        columns = list(data.get("columns") or [])
        rows = []
        for item in data.get("items") or []:
            rows.append((list(item.get("row") or []), item.get("status") or "OK"))
        return columns, rows

    def _build_sched_summary(self, key, flag, code, output_dir, run_at):
        title = self.scheduler.title_of(key) if self.scheduler else key
        lines = ["任务：%s【定时 %s】" % (title, run_at),
                 "状态：%s" % flag,
                 "退出码：%s" % code,
                 ""]
        if output_dir:
            p = Path(output_dir) / "summary.json"
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                stats = data.get("stats") or {}
                for k, v in stats.items():
                    lines.append("  %s：%s" % (k, v))
                reasons = data.get("fail_reason_count") or {}
                lines.append("")
                if reasons:
                    lines.append("失败原因分布：")
                    for reason, count in reasons.items():
                        lines.append("  %s：%d" % (reason, count))
                else:
                    lines.append("失败原因分布：无")
            except Exception:
                pass
            lines.append("")
            lines.append("结果目录：%s" % output_dir)
            lines.append("（完整日志见该目录 run.log / error.log）")
        return "\n".join(lines)

    # —— 每个工具的定时设置（时间可见、可改、可立即执行）——
    def _current_params(self):
        """当前工具参数区的值（定时任务要用的参数快照）。"""
        out = {}
        try:
            for key, var in self.form.vars.items():
                value = var.get()
                if isinstance(value, bool):
                    value = "true" if value else "false"
                if value is not None:
                    out[key] = value
        except Exception:
            pass
        return out

    def _sync_sched_params(self):
        """把当前表单参数同步进「已启用」的定时条目。

        定时用的是保存那一刻的参数快照：先启用定时、后填归档目录时，
        到点跑的仍是旧值（不归档）——这是最容易踩的坑。表单一改就同步，
        切换工具/任务结束时再兜一次底。
        """
        if not self.scheduler or self.current_tool is None or self.form is None:
            return
        try:
            key = self.current_tool.id
            item = self.scheduler.get(key) or {}
            if not item.get("enabled"):
                return
            self.scheduler.set_schedule(key, params=self._current_params())
        except Exception:
            pass

    def _on_form_changed(self, *_args):
        """表单任一参数变化 → 防抖同步定时快照 + 刷新历史结果列表。"""
        try:
            self._refresh_result_files()
        except Exception:
            pass
        try:
            job = getattr(self, "_sched_sync_job", None)
            if job:
                self.root.after_cancel(job)
            self._sched_sync_job = self.root.after(600, self._sync_sched_params)
        except Exception:
            pass

    def _watch_form_vars(self):
        """监听当前表单的所有变量，变化时同步定时参数快照。"""
        self._sched_sync_job = None
        if not self.form:
            return
        for var in list(getattr(self.form, "vars", {}).values()):
            try:
                var.trace_add("write", self._on_form_changed)
            except Exception:
                pass

    def _build_sched_bar(self, tool):
        """在参数区下方加一行定时设置。"""
        self._sched_bar = None
        self._sched_key = tool.id
        item = self.scheduler.get(tool.id) if self.scheduler else {}
        bar = ttk.Frame(self.form_frame)
        # 必须用 grid：form_frame 里的参数控件都由 FormBuilder 用 grid 管理，
        # 混用 pack 会抛 "cannot use geometry manager pack inside ..."。
        bar.grid(row=self.form.footer_row(), column=0, columnspan=4,
                 sticky="ew", pady=(10, 0))
        ttk.Label(bar, text="定时执行", bootstyle="secondary").pack(side="left")
        self.var_sched_at = tk.StringVar(value=item.get("at", "08:30"))
        ent = ttk.Entry(bar, textvariable=self.var_sched_at, width=7)
        ent.pack(side="left", padx=(6, 4))
        ent.bind("<Return>", lambda e: self._save_sched())
        ent.bind("<FocusOut>", lambda e: self._save_sched())
        self.var_sched_on = tk.BooleanVar(value=bool(item.get("enabled")))
        self.chk_sched = ttk.Checkbutton(bar, text="启用", variable=self.var_sched_on,
                                        bootstyle="round-toggle",
                                        command=self._save_sched)
        self.chk_sched.pack(side="left")
        self.lbl_sched = ttk.Label(bar, text="", bootstyle="secondary")
        self.lbl_sched.pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="立即执行", width=9, bootstyle="secondary-outline",
                   command=self._run_sched_now).pack(side="right", padx=(6, 0))
        # 打开最近一次定时运行的结果目录（run.log/error.log/summary.json 在里面）
        self.btn_sched_result = ttk.Button(bar, text="查看结果", width=9,
                                           bootstyle="secondary-outline",
                                           command=self._open_sched_result)
        self.btn_sched_result.pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="定时任务总览…", width=12, bootstyle="secondary-outline",
                   command=self.show_auto_task).pack(side="right")
        self._refresh_sched_bar()

    def _refresh_sched_bar(self):
        """刷新定时条上的状态文字（有任务在跑时显示已用时）。"""
        if not getattr(self, "lbl_sched", None) or not self.scheduler:
            return
        try:
            key = self._sched_key
            self.lbl_sched.configure(text=self.scheduler.status_of(key))
            last = self.scheduler.get(key).get("last") or {}
            has_dir = bool(last.get("output_dir"))
            self.btn_sched_result.configure(state="normal" if has_dir else "disabled")
        except Exception:
            pass

    def _open_sched_result(self):
        """打开最近一次定时运行的结果目录。"""
        if not self.scheduler or not getattr(self, "_sched_key", None):
            return
        last = self.scheduler.get(self._sched_key).get("last") or {}
        path = last.get("output_dir") or ""
        if not path:
            Messagebox.show_info("还没有定时运行的结果目录（先执行一次定时任务）",
                                 title="提示", parent=self.root)
            return
        if not Path(path).exists():
            Messagebox.show_info("结果目录已不存在：%s" % path,
                                 title="提示", parent=self.root)
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            Messagebox.show_error(str(exc), title="打开失败", parent=self.root)

    def _save_sched(self):
        """保存当前工具的定时设置（时间 + 启用 + 当前参数快照）。"""
        if not self.scheduler or not getattr(self, "_sched_key", None):
            return
        enabled = bool(self.var_sched_on.get())
        if enabled:
            # 启用前先做工具级校验：参数不全时到点必然失败（如 CRC 对比没选目录）
            err = self._validate_sched_params()
            if err:
                Messagebox.show_warning(err, title="定时任务参数不完整",
                                        parent=self.root)
                self.var_sched_on.set(False)
                return
        try:
            self.scheduler.set_schedule(
                self._sched_key,
                at=self.var_sched_at.get(),
                enabled=enabled,
                params=self._current_params())
        except Exception:
            pass
        self._refresh_sched_bar()

    def _validate_sched_params(self):
        """调工具的 validate_params；没有该钩子或校验通过时返回空串。"""
        try:
            tool = self.current_tool
            check = getattr(tool, "validate_params", None)
            if not callable(check):
                return ""
            return check(self._current_params()) or ""
        except Exception:
            return ""

    def _run_sched_now(self):
        """立即执行一次当前工具（走定时任务通道，日志进「运行日志」）。"""
        if not self.scheduler or not getattr(self, "_sched_key", None):
            return
        self._save_sched()
        ok, msg = self.scheduler.run_now(self._sched_key)
        Messagebox.show_info(msg, title="定时任务" if ok else "无法执行", parent=self.root)
        self._refresh_sched_bar()

    def _last_device_list(self):
        """最近一次用的设备清单路径（存在每日流水线配置里）。"""
        try:
            cfg = Path(__file__).resolve().parent.parent / "scripts" / "daily_config.json"
            with open(str(cfg), "r", encoding="utf-8") as f:
                return (json.load(f) or {}).get("devices", "") or ""
        except Exception:
            return ""

    def _fill_recent_device_list(self):
        """设备清单参数自动回填上次用的路径。

        每天都是同一张设备表，每次点进工具都要重新选一遍很烦；
        这里只填空值，不覆盖用户已经填好的。
        """
        try:
            last = self._last_device_list()
            if not last:
                return
            for spec in (self.current_tool.params or []):
                if getattr(spec, "type", "") != "device_list":
                    continue
                var = self.form.vars.get(spec.key)
                if var is not None and not var.get().strip():
                    var.set(last)
        except Exception:
            pass

    def _set_path_label(self, path):
        """更新状态栏结果目录（文本会被截断，完整值放进悬浮提示）。"""
        try:
            self.path_label.configure(text=path or "")
        except Exception:
            pass
        try:
            if self._path_tip is not None:
                self._path_tip.text = path or "结果目录"
        except Exception:
            pass

    # —— 主题 ——
    def _sync_theme_var(self):
        current = ttk.Style().theme.name
        for key, label in THEMES:
            if key == current:
                self.theme_var.set(label)
                return
        self.theme_var.set(THEMES[0][1])

    def on_change_theme(self):
        label = self.theme_var.get()
        theme = DEFAULT_THEME
        for key, lab in THEMES:
            if lab == label:
                theme = key
                break
        try:
            ttk.Style().theme_use(theme)
        except Exception as exc:
            self.log_view.append("ERROR", "切换主题失败：%s" % exc)
            return
        save_ui_pref({"theme": theme})
        # 主题变了，重建所有依赖语义色的标签
        self.result_view.refresh_tag_colors()
        self.log_view.refresh_level_colors()
        # 主题切换会重置 ttk.Style，需重新套用字号
        self._apply_font_size()
        self.set_state(self.engine.state)

    # —— 工具列表 ——
    def _load_tools(self):
        self.registry.discover()
        if not self.registry.tools:
            self.empty_label.configure(text="未找到可用工具，请确认 tools 目录")
            self.empty_label.pack(padx=8, pady=8)
        else:
            self.empty_label.pack_forget()

        n = len(self.registry.load_errors)
        if n:
            self.fail_badge.configure(text="%d 个工具加载失败" % n)
            self.fail_badge.pack(padx=8, pady=(0, 8), anchor="w")
            for name, err in self.registry.load_errors:
                self.log_view.append("ERROR", "%s：%s" % (name, err))
        else:
            self.fail_badge.pack_forget()
        self._render_tool_list()

    def _render_tool_list(self):
        for item in self.tool_tree.get_children():
            self.tool_tree.delete(item)
        keyword = self.search_var.get().strip().lower()
        for category, tools in self.registry.by_category().items():
            items = [t for t in tools
                     if not keyword or keyword in t.name.lower() or keyword in t.id.lower()]
            if not items:
                continue
            node = self.tool_tree.insert("", "end", text=category, open=True)
            for t in items:
                # 树里只放名字：加上 " (v1.0.0)" 后 260px 的树宽必然被截成
                # "设备配置备份 (v1.0." 这种，版本号在右侧标题栏已有显示
                self.tool_tree.insert(node, "end", iid=t.id, text=t.name)

    def on_select_tool(self, _event=None):
        if self.engine.is_running():
            Messagebox.show_info("任务执行中，请先中止或等待完成", title="提示")
            return
        sel = self.tool_tree.selection()
        if not sel:
            return
        tool = self.registry.get(sel[0])
        if tool is None:
            return

        # 切换前把当前工具的全部输出（结果表/图表/日志/摘要）存入缓存
        self._save_outputs()
        # 切换前把当前工具的参数同步进它的定时条目（已启用才同步）
        self._sync_sched_params()

        self.current_tool = tool
        self.tool_title.configure(text="%s  v%s" % (tool.name, tool.version))
        self.tool_desc.configure(text=getattr(tool, "description", "") or "")

        # 按工具的 output_tabs 声明重建输出标签页
        tabs = getattr(tool, "output_tabs", None) or ["result", "log", "summary"]
        self._rebuild_output_tabs(tabs)

        for child in self.form_frame.winfo_children():
            child.destroy()
        # 参数区宽度决定排两列还是一列（窄窗口排两列会把第二列切在右边缘）
        self.form = FormBuilder(self.form_frame, tool.params,
                                avail_width=self._form_avail_width())
        self._restore_form_values(tool)
        # 监听表单变化：改归档目录/清单等立即同步到定时参数快照
        self._watch_form_vars()
        self._fill_recent_device_list()
        self._build_sched_bar(tool)
        self._add_font_row()
        # 参数区右侧的「历史结果」面板（工具声明了 result_files_param 才有）
        self._build_result_files_panel(tool)
        # 换工具后参数区回到顶部并刷新滚动范围（否则新表单会停在上一份的滚动位置）
        try:
            self.form_canvas.yview_moveto(0)
            self.root.after_idle(self._on_form_inner_configure)
        except Exception:
            pass
        # 表单重建后请求高度变了，恢复自动贴合。
        # 首次测量可能早于 Tk 完成布局（reqheight 还是旧值），所以补两次延迟贴合；
        # 移动分隔条不会触发分栏 Configure，只靠事件回调会停在旧位置。
        self._form_folded = False
        self._form_autofit = True
        self._sync_fold_button()
        self.root.after_idle(self._fit_form_pane)
        self.root.after(80, self._fit_form_pane)
        self.root.after(220, self._fit_form_pane)
        # 换工具后 Panedwindow 会按新表单的请求高度重排一次，可能把用户设定的
        # 分隔条位置顶掉，所以再多补一次
        self.root.after(420, self._fit_form_pane)

        # 恢复该工具上次的输出（若有），否则全部清空
        self._restore_outputs(tool)

        self.progress_label.configure(text="尚未运行")
        self.progress.configure(value=0)
        try:
            self.current_label.configure(text="")
        except Exception:
            pass
        self.set_state(IDLE)

    # —— 显示字号（每个工具的参数区底部都有，作用于结果表/日志/摘要/图表）——
    def _form_avail_width(self):
        """估算参数区可用宽度。

        窗口还没映射时 form_canvas.winfo_width() 返回 1，这时**不能**当成窄窗口：
        默认按「宽」处理（两列），窗口真正显示后 _reflow_form 会在 250ms 内
        按实际宽度纠正。反过来默认单列的话，每次启动都会先闪一下单列再跳成两列。
        """
        try:
            w = self.form_canvas.winfo_width()
            if w > 60:
                return w
            gw = self.root.winfo_width()
            return max(800, gw - 300) if gw > 60 else 800
        except Exception:
            return 800

    def _add_font_row(self):
        """在参数区末尾追加一行字号设置；切换工具后依然存在并保留当前值。"""
        row = 100          # 固定放在参数行之后
        box = ttk.Frame(self.form_frame)
        box.grid(row=row, column=0, columnspan=4, sticky="w", padx=10, pady=(2, 3))
        ttk.Label(box, text="显示字号", bootstyle="secondary").pack(side="left")
        spin = ttk.Spinbox(box, from_=MIN_FONT_SIZE, to=MAX_FONT_SIZE, width=5,
                           textvariable=self.font_size_var, bootstyle="primary",
                           command=self._on_font_size_change)
        spin.pack(side="left", padx=(6, 4))
        spin.bind("<Return>", lambda e: self._on_font_size_change())
        spin.bind("<FocusOut>", lambda e: self._on_font_size_change())
        ttk.Label(box, text="（作用于结果表/日志/摘要/图表，自动记忆）",
                  bootstyle="secondary").pack(side="left", padx=(4, 0))

    # —— 参数区右侧：历史结果（工具声明 result_files_param 时显示）——
    def _result_files_folder(self):
        """当前工具「结果输出目录」参数的值。"""
        try:
            key = getattr(self.current_tool, "result_files_param", "")
            if not key:
                return ""
            return str(self.form.vars[key].get() or "").strip()
        except Exception:
            return ""

    def _build_result_files_panel(self, tool):
        """在参数区右侧列出「结果输出目录」里的历史结果，双击载入结果表。"""
        self._files_panel = None
        self._files_list = None
        self._result_files = []
        if tool is None or not getattr(tool, "result_files_param", ""):
            return
        if self._form_avail_width() < 820:
            return          # 窄窗口先不挤，宽度恢复后 _reflow_form 会重建
        try:
            rowspan = max(1, self.form.footer_row())
            panel = ttk.Frame(self.form_frame)
            panel.grid(row=0, column=4, rowspan=rowspan, sticky="nsew",
                       padx=(6, 8), pady=(4, 4))
            self.form_frame.columnconfigure(4, weight=2, minsize=200)

            ttk.Label(panel, text="历史结果（结果输出目录）",
                      bootstyle="secondary").pack(anchor="w")
            box = ttk.Frame(panel)
            box.pack(fill="both", expand=True, pady=(4, 2))
            sb = ttk.Scrollbar(box, orient="vertical")
            lb = tk.Listbox(box, yscrollcommand=sb.set, activestyle="none",
                            exportselection=False, height=6)
            sb.configure(command=lb.yview)
            sb.pack(side="right", fill="y")
            lb.pack(side="left", fill="both", expand=True)
            lb.bind("<Double-Button-1>", lambda e: self._open_selected_result())

            ttk.Label(panel, text="双击载入结果表", bootstyle="secondary").pack(anchor="w")
            bar = ttk.Frame(panel)
            bar.pack(fill="x", pady=(4, 0))
            ttk.Button(bar, text="打开目录", bootstyle="secondary-outline",
                       command=self._open_result_files_dir).pack(side="left")
            ttk.Button(bar, text="刷新", bootstyle="secondary-outline",
                       command=self._refresh_result_files).pack(side="left", padx=6)

            self._files_panel = panel
            self._files_list = lb
            self._refresh_result_files()
        except Exception:
            self._files_panel = None
            self._files_list = None

    def _sync_result_panel(self, width):
        """宽度够就显示历史结果面板，不够就收起（不销毁，避免反复重建）。"""
        try:
            if self.current_tool is None:
                return
            if not getattr(self.current_tool, "result_files_param", ""):
                return
            wide = float(width) >= 820
            panel = getattr(self, "_files_panel", None)
            if panel is None:
                if wide:
                    self._build_result_files_panel(self.current_tool)
                return
            if wide:
                if not panel.winfo_ismapped():
                    panel.grid()
            else:
                panel.grid_remove()
        except Exception:
            pass

    def _refresh_result_files(self):
        """重新扫描结果输出目录，刷新文件列表。"""
        lb = getattr(self, "_files_list", None)
        if lb is None:
            return
        self._result_files = []
        try:
            lb.delete(0, "end")
        except Exception:
            return
        folder = self._result_files_folder()
        if not folder:
            lb.insert("end", "（先在参数里填「结果输出目录」）")
            return
        try:
            files = self.current_tool.list_result_files(folder) or []
        except Exception:
            files = []
        if not files:
            lb.insert("end", "（目录里还没有结果文件）")
            return
        self._result_files = files
        for name, _path in files:
            lb.insert("end", name)

    def _open_selected_result(self):
        """双击历史结果 → 读成结果表显示。"""
        lb = getattr(self, "_files_list", None)
        if lb is None:
            return
        sel = lb.curselection()
        if not sel or not getattr(self, "_result_files", None):
            return
        idx = sel[0]
        if idx >= len(self._result_files):
            return                      # 点到占位提示行
        name, path = self._result_files[idx]
        try:
            loaded = self.current_tool.load_result_file(path)
        except Exception as exc:
            Messagebox.show_error("读取失败：%s" % exc, title="载入历史结果",
                                  parent=self.root)
            return
        if not loaded:
            return
        columns, rows = loaded
        try:
            self.result_view.set_columns(columns)
            self.result_view.rows = [(list(r), "OK") for r in rows]
            self.result_view.refresh()
            self._select_tab("result")
            self.log_view.append("INFO", "已载入历史结果：%s（%d 行）" % (name, len(rows)))
            self._save_outputs()
        except Exception as exc:
            Messagebox.show_error(str(exc), title="载入失败", parent=self.root)

    def _open_result_files_dir(self):
        """打开「结果输出目录」（不存在就先建）。"""
        folder = self._result_files_folder()
        if not folder:
            Messagebox.show_info("请先在参数里填「结果输出目录」", title="提示",
                                 parent=self.root)
            return
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            os.startfile(str(Path(folder)))
        except Exception as exc:
            Messagebox.show_error(str(exc), title="打开失败", parent=self.root)

    # —— 参数区 / 输出区 的上下分配 ——
    def _on_right_configure(self, event):
        """工具描述按当前宽度重排（否则宽窗口下仍按 780px 折行，留下半行空字）。"""
        try:
            w = max(240, event.width - 16)
            if abs(int(self.tool_desc.cget("wraplength")) - w) > 8:
                self.tool_desc.configure(wraplength=w)
        except Exception:
            pass

    def _on_split_configure(self, _event=None):
        """分栏尺寸变化时自动把参数区贴合到自然高度（用户拖过之后不再干预）。"""
        if getattr(self, "_form_autofit", True):
            self._fit_form_pane()

    def _fit_form_pane(self):
        """把分隔条放到「参数区自然高度」处，剩余高度全给输出区。

        参数多的工具（如 CRC趋势分析 12 项 ≈ 337px）如果不设初始位置，
        ttk.Panedwindow 会按权重把参数区压到几乎看不见。

        但也不能反过来把输出区挤死：1180×760 下如果参数区拿满 337px，
        输出区只剩 146px、图表可视高度只有 57px，等于没法看图。
        所以给输出区留保底高度（OUTPUT_MIN_H），参数区放不下就走滚动条。
        """
        if getattr(self, "_form_folded", False):
            return
        try:
            total = self.vsplit.winfo_height()
            if total <= 1:
                return
            user = int(getattr(self, "_user_sash", 0) or 0)
            if user > 0:
                # 用户亲手拖过：优先尊重他的高度，只保证输出区还剩 OUTPUT_MIN_H_USER
                need = min(user, max(120, total - OUTPUT_MIN_H_USER))
            else:
                need = self.form_frame.winfo_reqheight() + 8   # 内容高度 + 边框
                # 参数区优先完整显示，但不能把输出区压到 OUTPUT_MIN_H 以下；
                # 参数实在放不下时由参数区自己的滚动条兜底
                need = min(need, max(160, total - OUTPUT_MIN_H))
            if abs(self.vsplit.sashpos(0) - need) > 2:
                self.vsplit.sashpos(0, need)
        except Exception:
            pass

    # —— 参数区滚动 ——
    def _on_form_inner_configure(self, _event=None):
        """参数内容变化 → 更新滚动范围。"""
        try:
            self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))
        except Exception:
            pass

    def _on_form_canvas_configure(self, event):
        """参数区宽度变化 → 内容跟着变宽；比最小宽度还窄时保持最小宽度（可横向滚动）。

        这里**不能**用 form_frame.winfo_reqwidth() 当基准：控件实际请求宽度会随
        主题字体变化，1180×760 下量出来是 1178px，比参数区本身还宽，
        于是正常窗口也莫名其妙冒出一条横向滚动条。改用 form_builder 按列算出的
        min_width，只有真的放不下才出现滚动。
        """
        try:
            need = int(getattr(self.form, "min_width", 0) or 0)
            self.form_canvas.itemconfigure(self._form_win,
                                           width=max(event.width, need))
        except Exception:
            pass
        self._schedule_form_reflow(event.width)

    def _schedule_form_reflow(self, width):
        """窗口宽度跨越「单列/两列」阈值时，防抖后重排表单。

        只在宽度变化过程中重建会闪；等 350ms 稳定了再重排一次，
        并且只在列数真的要变时才动，避免每次 Configure 都重建控件。
        """
        if not self.current_tool or not self.form:
            return
        try:
            if getattr(self, "_reflow_job", None):
                self.root.after_cancel(self._reflow_job)
        except Exception:
            pass
        try:
            self._reflow_job = self.root.after(250, lambda w=width: self._reflow_form(w))
        except Exception:
            self._reflow_job = None

    def _reflow_form(self, width):
        self._reflow_job = None
        try:
            from ui.form_builder import TWO_COLUMN_MIN_W, TWO_COLUMN_THRESHOLD
            want = (len(self.form.params) >= TWO_COLUMN_THRESHOLD
                    and float(width) >= TWO_COLUMN_MIN_W)
            if bool(getattr(self.form, "two_col", True)) == want:
                # 表单不用重建，但历史结果面板要跟着宽度显示/隐藏
                self._sync_result_panel(width)
                return
            # 重建会丢控件状态，先把当前取值抓出来
            vals = {}
            for k, v in self.form.vars.items():
                try:
                    vals[k] = v.get()
                except Exception:
                    pass
            for child in self.form_frame.winfo_children():
                child.destroy()
            self.form = FormBuilder(self.form_frame, self.current_tool.params,
                                    avail_width=width)
            for k, v in vals.items():
                if k in self.form.vars:
                    try:
                        self.form.vars[k].set(v)
                    except Exception:
                        pass
            self._add_font_row()
            # 重建后定时条和历史结果面板都要重新挂上（否则窗口缩放一次就消失）
            if self.current_tool is not None:
                self._build_sched_bar(self.current_tool)
            self._build_result_files_panel(self.current_tool)
            self._sync_result_panel(width)
            self.root.after_idle(self._on_form_inner_configure)
            self.root.after_idle(self._fit_form_pane)
        except Exception:
            pass

    def _on_form_scroll(self, first, last):
        """竖直滚动条只在内容超出时出现，放得下就收起来（省一条边）。"""
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.form_sbar.grid_remove()
            else:
                self.form_sbar.grid(row=0, column=1, sticky="ns")
        except Exception:
            pass

    def _on_form_xscroll(self, first, last):
        """横向滚动条同理：参数列宽不够放时出现，参数一个都不会被裁掉。"""
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.form_hbar.grid_remove()
            else:
                self.form_hbar.grid(row=1, column=0, sticky="ew")
        except Exception:
            pass

    def _bind_form_wheel(self, _e=None):
        self.form_canvas.bind_all("<MouseWheel>", self._on_form_wheel)

    def _unbind_form_wheel(self, _e=None):
        self.form_canvas.unbind_all("<MouseWheel>")

    def _on_form_wheel(self, event):
        try:
            self.form_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _on_split_dragged(self, _event=None):
        """用户真的拖动过分隔条 → 停止自动贴合，并把位置记下来。

        不能只凭 ButtonRelease 判断：在参数区里点一下复选框也会触发它，
        那样点一次就被当成"拖过分隔条"，之后再也不自动贴合。
        这里比按下时的位置，位移超过 3px 才算拖动。
        """
        try:
            cur = self.vsplit.sashpos(0)
        except Exception:
            return
        at_press = getattr(self, "_sash_at_press", None)
        if at_press is None or abs(cur - at_press) < 3:
            return
        self._form_autofit = False
        self._user_sash = cur
        save_ui_pref({"sash": cur})

    def _on_split_press(self, _event=None):
        """记下按下时分隔条位置，用于判断这次是不是真拖动。"""
        try:
            self._sash_at_press = self.vsplit.sashpos(0)
        except Exception:
            self._sash_at_press = None

    def toggle_form_pane(self):
        """收起 / 展开参数区（看图表时一键腾出整块高度）。"""
        try:
            if self._form_folded:
                self.vsplit.insert(0, self.form_holder, weight=0)
                self._form_folded = False
                self._form_autofit = True
                self._fit_form_pane()
            else:
                self.vsplit.forget(self.form_holder)
                self._form_folded = True
        except Exception:
            return
        self._sync_fold_button()

    def _on_chart_expand(self, expanded):
        """图表放大/还原时自动收起/恢复参数区。

        图表区可视高度只有 350px 左右，放大后的图有 500+px——不腾出高度的话
        多出来的部分全被裁掉，用户看到的可视高度一点没变，就变成"只往两边拉长"。
        只恢复我们自己收起的那次，用户手动收起的参数区不动。
        """
        if expanded:
            if not self._form_folded:
                self._auto_folded = True
                self.toggle_form_pane()
        elif getattr(self, "_auto_folded", False):
            self._auto_folded = False
            if self._form_folded:
                self.toggle_form_pane()

    def _sync_fold_button(self):
        try:
            self.btn_fold.configure(text="展开参数" if self._form_folded else "收起参数")
        except Exception:
            pass

    def _on_font_size_change(self):
        try:
            size = int(self.font_size_var.get())
        except (TypeError, ValueError):
            return
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        if size == self.font_size_var.get() and size == self.ui_font_size:
            return
        self.font_size_var.set(size)
        if size == self.ui_font_size:
            return
        self.ui_font_size = size
        self._apply_font_size()
        save_ui_pref({"font_size": size})

    def _on_font_size_from_table(self, size):
        """结果表工具栏的 A+/A-/默认 按钮 → 同步「显示字号」并应用。"""
        self.font_size_var.set(size)
        self._on_font_size_change()

    def _on_font_wheel(self, event):
        """Ctrl+滚轮缩放字号（结果表 / 运行日志）。"""
        step = 1 if getattr(event, "delta", 0) > 0 else -1
        self.font_size_var.set(max(MIN_FONT_SIZE,
                                   min(MAX_FONT_SIZE, self.ui_font_size + step)))
        self._on_font_size_change()
        return "break"

    def _apply_font_size(self):
        """把当前字号应用到所有输出视图。"""
        size = self.ui_font_size
        try:
            self.result_view.set_font_size(size)
        except Exception:
            pass
        try:
            self.log_view.set_font_size(size)
        except Exception:
            pass
        try:
            self.summary_text.configure(font=(FONT_FAMILY, size))
        except Exception:
            pass
        try:
            self.chart_view.set_font_scale(size / float(BASE_FONT_SIZE))
        except Exception:
            pass

    # —— 参数值缓存（按工具隔离，切换工具不丢已填内容）——
    def _save_form_values(self):
        """把当前工具的参数值存起来。

        只缓存输出不缓存参数的话，来回切两次工具，刚选好的
        「昨日目录/今日目录」就没了，得重选一遍。
        """
        if self.current_tool is None or self.form is None:
            return
        try:
            self._form_cache[self.current_tool.id] = {
                k: v.get() for k, v in self.form.vars.items()
            }
        except Exception:
            pass

    def _restore_form_values(self, tool):
        """恢复该工具上次填过的参数（没填过的保持默认/自动回填）。"""
        vals = self._form_cache.get(tool.id) or {}
        for key, value in vals.items():
            var = self.form.vars.get(key)
            if var is None:
                continue
            try:
                var.set(value)
            except Exception:
                pass

    # —— 输出分组缓存（按工具隔离结果表/图表/日志/摘要，切换不串台）——
    def _save_outputs(self):
        if self.current_tool is None:
            return
        self._save_form_values()
        self._output_cache[self.current_tool.id] = {
            "columns": list(self.result_view.columns),
            "rows": list(self.result_view.rows),
            "charts": self.chart_view.get_state(),
            "logs": self.log_view.get_state(),
            "summary": self._get_summary_text(),
        }

    def _restore_outputs(self, tool):
        cached = self._output_cache.get(tool.id)

        # 结果表
        if cached is not None:
            self.result_view.set_columns(cached["columns"])
            self.result_view.rows = list(cached["rows"])
            self.result_view.refresh()
            self.chart_view.set_state(cached["charts"])
            self.log_view.set_state(cached["logs"])
            self._set_summary_text(cached["summary"])
        else:
            self.result_view.set_columns(tool.result_columns)
            self.result_view.clear()
            self.chart_view.clear()
            self.log_view.clear()
            self._set_summary_text("尚未运行任务。\n")

    # —— 摘要文本读写 ——
    def _get_summary_text(self):
        try:
            return self.summary_text.get("1.0", "end-1c")
        except Exception:
            return ""

    def _set_summary_text(self, text):
        try:
            self.summary_text.configure(state="normal")
            self.summary_text.delete("1.0", "end")
            self.summary_text.insert("end", text or "")
            self.summary_text.configure(state="disabled")
        except Exception:
            pass

    # —— 控件状态机 ——
    def set_state(self, state):
        running = state in (RUNNING, CANCELLING)
        terminal = state in (SUCCEEDED, PARTIAL, FAILED, CANCELLED)
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_cancel.configure(state="normal" if state == RUNNING else "disabled")
        if self.form:
            self.form.set_enabled(not running)
        self.btn_cancel.configure(text="中止中…" if state == CANCELLING else "中止")

        style, text = STATE_STYLE.get(state, ("secondary", state))
        self.badge.configure(text=text, bootstyle="%s-inverse" % style)

    # —— 执行 ——
    def on_start(self):
        if self.current_tool is None:
            Messagebox.show_info("请先在左侧选择工具", title="提示")
            return
        ok, errors = self.form.validate()
        if not ok:
            Messagebox.show_warning("\n".join(msg for _, msg in errors), title="参数有误")
            return
        self.result_view.clear()
        self.log_view.clear()
        self.chart_view.clear()
        self.result_view.set_columns(self.current_tool.result_columns)
        self._set_summary_text("正在运行…")
        # 清掉旧缓存，任务结束时会重新写入最新结果
        self._output_cache.pop(self.current_tool.id, None)
        try:
            self.engine.start(self.current_tool, self.form.get_values())
        except Exception as exc:
            Messagebox.show_error(str(exc), title="启动失败")
            return
        self.set_state(RUNNING)
        self.progress_label.configure(text="0/0")
        self._set_path_label(self.engine.output_dir or "")

    def on_cancel(self):
        if self.engine.cancel():
            self.set_state(CANCELLING)

    def _poll(self):
        self.engine.poll(self._handle_message)
        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _handle_message(self, msg):
        type_ = msg.get("type")
        value = getattr(type_, "value", type_)
        payload = msg.get("payload", {})

        if value == "log":
            self.log_view.append(payload.get("level", "INFO"), payload.get("msg", ""),
                                 payload.get("ip"))
        elif value == "progress":
            current = payload.get("current", 0)
            total = payload.get("total", 0) or 0
            percent = (current * 100.0 / total) if total else 0
            self.progress.configure(value=percent)
            self.progress_label.configure(text="%d/%d" % (current, total))
            self.stat_label.configure(text="已处理 %d / %d" % (current, total))
            item = payload.get("item") or ""
            if len(item) > 40:
                # 设备名可能很长（"路由器(BOSS-...)"），截断时保留前面的 IP
                item = item[:39] + "…"
            self.current_label.configure(text=("当前：%s" % item) if item else "")
        elif value == "result":
            self.result_view.add_row(payload.get("row", []), payload.get("status", ""))
        elif value == "chart":
            # 图表消息：渲染到「图表」标签页并自动切过去
            self.chart_view.add_figure(payload.get("figure"), payload.get("title", ""))
            self._select_tab("chart")
        elif value == "error":
            self.log_view.append("ERROR", payload.get("msg", ""))
            Messagebox.show_error(payload.get("msg", ""), title="任务失败")
        elif value == "done":
            state = self.engine.state
            self.set_state(state)
            self.result_view.refresh()
            self._set_path_label(self.engine.output_dir or "")
            self.log_view.append("INFO", "任务结束：%s，结果目录：%s"
                                 % (state, self.engine.output_dir))
            if state == PARTIAL:
                self.log_view.append("WARN", "存在失败项，可在结果区筛选查看")
            self._select_best_tab()
            self._update_summary()
            # 参数可能刚改过（如归档目录），同步一次定时快照
            self._sync_sched_params()
            # 本次可能往结果输出目录写了新文件，刷新历史列表
            self._refresh_result_files()
            # 任务结束统一落缓存（含图表/日志/摘要），保证切走再切回内容仍在
            self._save_outputs()

    def _update_summary(self):
        engine = self.engine
        lines = []
        lines.append("任务：%s" % (self.current_tool.name if self.current_tool else "-"))
        lines.append("状态：%s" % STATE_STYLE.get(engine.state, (None, engine.state))[1])
        lines.append("")
        stats = getattr(engine, "stats", {}) or {}
        for key, val in stats.items():
            lines.append("  %s：%s" % (key, val))
        lines.append("")
        reasons = getattr(engine, "fail_reason_count", {}) or {}
        if reasons:
            lines.append("失败原因分布：")
            for reason, count in reasons.items():
                lines.append("  %s：%d" % (reason, count))
        else:
            lines.append("失败原因分布：无")
        lines.append("")
        lines.append("结果目录：%s" % (engine.output_dir or ""))

        try:
            self.summary_text.configure(state="normal")
            self.summary_text.delete("1.0", "end")
            self.summary_text.insert("end", "\n".join(lines))
            self.summary_text.configure(state="disabled")
        except Exception:
            pass

    # —— 杂项 ——
    def open_output_dir(self):
        path = self.engine.output_dir
        if not path:
            Messagebox.show_info("尚未运行任务", title="提示")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            Messagebox.show_error(str(exc), title="打开失败")

    def show_credentials(self):
        """打开凭证管理窗口（设备登录用的账号密码）。"""
        try:
            open_credential(self)
        except Exception as exc:
            Messagebox.show_error("打开凭证窗口失败：%s" % exc, title="出错")

    def show_auto_task(self):
        """打开「定时任务总览」：所有工具的定时时间、启停、状态、立即执行。"""
        try:
            open_schedule_overview(self.root, getattr(self, "scheduler", None))
        except Exception as exc:
            Messagebox.show_error("打开定时任务窗口失败：%s" % exc, title="出错")

    def show_about(self):
        rows = ["网络运维工具箱 v%s" % self.app_version, ""]
        rows.append("依赖状态：")
        for name in DEPS:
            try:
                mod = __import__(name)
                rows.append("  %s %s" % (name, getattr(mod, "__version__", "已安装")))
            except Exception:
                rows.append("  %s 未安装" % name)
        rows.append("")
        try:
            import ui.dpi as dpi
            rows.append(dpi.describe(self.root))
        except Exception:
            pass
        Messagebox.show_info("\n".join(rows), title="关于")

    def on_close(self):
        if self.engine.is_running():
            if not Messagebox.yesno("有任务正在执行，确认中止并退出？", title="确认"):
                return
            self.engine.cancel()
        # 停掉应用内定时器（正在跑的子进程让它自己跑完）
        try:
            getattr(self, "scheduler", None) and self.scheduler.stop_all()
        except Exception:
            pass
        self.root.destroy()
