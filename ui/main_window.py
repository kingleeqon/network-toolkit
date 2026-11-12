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
import threading
import time
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
from ui import theme as ui_theme
from ui import icons
from ui import widgets as ui_widgets

POLL_INTERVAL_MS = 100
FONT_FAMILY = ui_theme.FONT_SANS

# 主题清单由 ui/theme.py 统一提供：9 套收敛为深浅两套语义主题。
# 变量名保留，其余代码（load_ui_pref / _launch_gui）不用改。
THEMES = ui_theme.THEMES
DEFAULT_THEME = ui_theme.DEFAULT_THEME

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
MAX_FONT_SIZE = 32
OUTPUT_MIN_H = 150           # 自动贴合时输出区（图表/结果表）保底高度
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
    # 旧版本偏好里可能存着 journal / cyborg 等 9 套主题之一：按深浅归一，
    # 不要因为白名单不认识就把用户主题悄悄打回默认值。
    pref["theme"] = ui_theme.migrate_theme(pref["theme"])
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
        # —— 视觉层状态（只服务界面着色，不参与任何业务判断）——
        # 以实际生效的 ttk 主题为准：main.py 用偏好创建 Window，两者一致；
        # 但外部嵌入/测试可能传别的主题，视觉层必须跟着真实主题走，
        # 否则 tk 原生控件会套错色板（深底配浅色文字）。
        try:
            active_theme = ttk.Style().theme.name
        except Exception:
            active_theme = ""
        self._theme_mode = ui_theme.mode_of(active_theme or pref["theme"])
        self._nav_items = {}          # tool_id -> (row, color_bar, name_lbl, mark_lbl)
        self._nav_selected = None
        self._kpi_cards = {}          # key -> (value_lbl, sub_lbl, accent_bar)
        self._run_started_at = None
        self._icon_bindings = {}      # tk 路径 -> (widget, icon名, 尺寸, 语义色键)
        self._tab_icons = {}          # tab key -> PhotoImage（必须持有引用）
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
        self._start_crc_history_preload()
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
        p = ui_theme.palette(self._theme_mode)

        # —— 应用栏 AppBar：品牌区 / 全局调度状态 / 图标按钮组 / 操作者 ——
        appbar = tk.Frame(self.root, bg=p["panel"], height=58)
        appbar.pack(side="top", fill="x")
        appbar.pack_propagate(False)
        self.appbar = appbar

        logo = tk.Canvas(appbar, width=34, height=34, bg=p["panel"],
                         highlightthickness=0)
        logo.pack(side="left", padx=(14, 10))
        ui_theme.draw_logo(logo, p["brand"], 34)
        self.logo_canvas = logo

        brand = tk.Frame(appbar, bg=p["panel"])
        brand.pack(side="left")
        # 品牌容器必须登记下来参与重涂：它的宽度取两个 Label 的较大值，
        # 「NETOPS TOOLKIT」右侧会留出一条自身底色的空档。创建时的 bg= 会被
        # 根节点的 option_add("*Background", …) 覆盖成 #222222，深色主题下
        # 就是品牌名右下角那块突兀的黑方块。
        self.brand_box = brand
        self.brand_cn = tk.Label(brand, text="网络运维工具箱", bg=p["panel"],
                                 fg=p["text"], font=(FONT_FAMILY, 11, "bold"))
        self.brand_cn.pack(anchor="w")
        self.brand_en = tk.Label(brand, text="NETOPS TOOLKIT", bg=p["panel"],
                                 fg=p["text_muted"], font=(ui_theme.FONT_MONO, 8))
        self.brand_en.pack(anchor="w")

        # 品牌区到此为止：原先这里有一段分隔线 + 「UI v0.2」概念胶囊，
        # 属于设计稿标注，正式界面不展示，已连同样式重涂一起删除。

        # 操作者头像（Canvas 画圆，避免引入图片资源）
        avatar = tk.Canvas(appbar, width=30, height=30, bg=p["panel"],
                           highlightthickness=0)
        avatar.pack(side="right", padx=(10, 14))
        self.avatar = avatar
        self._avatar_text = (operator or "OP")[:2].upper()

        # 两主题切换按钮（替代原来的 9 项下拉）
        # 不设 width：tk.Button 的 width 是字符单位，同时带 image+text 时
        # 会把「图标+文字」一起挤进这个宽度，文字容易被截成单字。
        self.theme_button = tk.Button(appbar, text="", padx=7,
                                      command=self.on_change_theme,
                                      relief="flat", bd=0, cursor="hand2",
                                      font=(FONT_FAMILY, 9))
        self.theme_button.pack(side="right", padx=3)
        self._bind_icon(self.theme_button, "contrast", 16, "brand")
        self._sync_theme_button()

        self._icon_buttons = []
        for text, icon_name, cmd, tip in (
            ("关于", "info", self.show_about, "版本 / 依赖 / DPI"),
            ("定时", "clock", self.show_auto_task, "定时任务总览"),
            ("凭证", "key", self.show_credentials, "凭证管理"),
        ):
            b = tk.Button(appbar, text=text, command=cmd,
                          relief="flat", bd=0, cursor="hand2",
                          font=(FONT_FAMILY, 9), padx=6)
            b.pack(side="right", padx=2)
            self._bind_icon(b, icon_name, 16)
            try:
                ToolTip(b, text=tip, delay=400)
            except Exception:
                pass
            self._icon_buttons.append(b)

        self.global_status = tk.Label(appbar, text="", bg=p["panel"],
                                      fg=p["text_secondary"],
                                      font=(FONT_FAMILY, 9))
        self.global_status.pack(side="right", padx=(0, 12))

        # 状态栏必须先于 body 声明，否则 body 的 expand=True 会吃掉全部剩余空间，
        # 把它挤到可视区外
        self._build_statusbar()

        # 主体
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, minsize=268)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=p["panel"], width=268)
        left.grid(row=0, column=0, sticky="ns")
        # 侧栏自身用 grid 放进 body，但它的子控件全部用 pack —— 必须关掉
        # pack_propagate 才能把宽度锁在 268。只关 grid_propagate 的话，
        # 导航项的请求宽度会把侧栏撑到 580+，右侧工作区被挤掉一半。
        left.pack_propagate(False)
        self.sidebar = left
        self._build_sidebar(left)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 4))
        right.columnconfigure(0, weight=1)
        # 剩余高度全部给「参数/输出」分栏（第 3 行），标题 / 描述 / KPI 只占自然高度
        right.rowconfigure(3, weight=1)
        self._build_workspace(right)
        # tk 原生控件不跟 ttk 主题走，统一在这里刷一次色
        self._refresh_theme_colors()

    def _build_sidebar(self, left):
        """导航区：搜索 + 分类分组自定义列表 + 页脚状态。

        ttk.Treeview 只能靠缩进区分 12 个工具，无法给分类上色/加图标/显示
        版本，也没有选中色条。改成 Canvas + tk.Frame 列表后，仍保留
        self.search_var / empty_label / fail_badge 三个既有属性，_load_tools
        与搜索回调一行都不用改。
        """
        p = ui_theme.palette(self._theme_mode)

        head = tk.Frame(left, bg=p["panel"])
        head.pack(side="top", fill="x", padx=12, pady=(12, 8))
        # 记录容器本身：`option_add("*Background", …)` 会让创建时的 bg= 失效，
        # 不显式重涂的话搜索框那一行的空白处会留成 #222222 深灰块
        self.sidebar_head = head
        self.search_caption = tk.Label(head, text="搜索工具", bg=p["panel"],
                                       fg=p["text_muted"],
                                       font=(FONT_FAMILY, 9))
        self.search_caption.pack(anchor="w", pady=(0, 4))
        self._bind_icon(self.search_caption, "search", 15)
        self.search_var = tk.StringVar()
        search = ttk.Entry(head, textvariable=self.search_var, bootstyle="secondary")
        search.pack(fill="x", ipady=3)
        self.search_entry = search
        try:
            ToolTip(search, text="按工具名或 id 过滤")
        except Exception:
            pass
        search.bind("<KeyRelease>", lambda e: self._render_tool_list())

        wrap = tk.Frame(left, bg=p["panel"])
        wrap.pack(side="top", fill="both", expand=True)
        self.nav_canvas = tk.Canvas(wrap, bg=p["panel"], highlightthickness=0,
                                    borderwidth=0)
        self.nav_canvas.pack(side="left", fill="both", expand=True)
        self.nav_sbar = ttk.Scrollbar(wrap, orient="vertical",
                                      command=self.nav_canvas.yview,
                                      bootstyle="secondary-round")
        self.nav_canvas.configure(yscrollcommand=self._on_nav_scroll)
        self.nav_inner = tk.Frame(self.nav_canvas, bg=p["panel"])
        self._nav_win = self.nav_canvas.create_window((0, 0), window=self.nav_inner,
                                                      anchor="nw")
        self.nav_inner.bind("<Configure>", self._on_nav_inner_configure)
        self.nav_canvas.bind("<Configure>", self._on_nav_canvas_configure)
        self.nav_canvas.bind("<Enter>", self._bind_nav_wheel)
        self.nav_canvas.bind("<Leave>", self._unbind_nav_wheel)

        foot = tk.Frame(left, bg=p["panel"])
        foot.pack(side="bottom", fill="x", padx=12, pady=(6, 10))
        self.sidebar_foot = foot
        self.nav_count = tk.Label(foot, text="", bg=p["panel"], fg=p["success"],
                                  font=(FONT_FAMILY, 9), wraplength=238,
                                  justify="left")
        self.nav_count.pack(anchor="w")
        self.nav_foot_hint = tk.Label(foot, text="插件式注册 · tools/ 丢文件即生效",
                                      bg=p["panel"], fg=p["text_muted"],
                                      font=(FONT_FAMILY, 9), wraplength=238,
                                      justify="left")
        self.nav_foot_hint.pack(anchor="w", pady=(2, 0))
        self.nav_foot_key = tk.Label(foot, text="F11 全屏 · Ctrl+滚轮 字号",
                                     bg=p["panel"], fg=p["text_muted"],
                                     font=(ui_theme.FONT_MONO, 8), wraplength=238,
                                     justify="left")
        self.nav_foot_key.pack(anchor="w", pady=(2, 0))
        self.empty_label = tk.Label(foot, text="", bg=p["panel"], fg=p["danger"],
                                    font=(FONT_FAMILY, 9), wraplength=230,
                                    justify="left")
        self.fail_badge = tk.Label(foot, text="", bg=p["panel"], fg=p["warning"],
                                   font=(FONT_FAMILY, 9), wraplength=230,
                                   justify="left")

    # —— 导航列表滚动（与参数区滚动同一套写法，互不干扰）——
    def _on_nav_inner_configure(self, _event=None):
        try:
            self.nav_canvas.configure(scrollregion=self.nav_canvas.bbox("all"))
        except Exception:
            pass

    def _on_nav_canvas_configure(self, event):
        try:
            self.nav_canvas.itemconfigure(self._nav_win, width=event.width)
        except Exception:
            pass

    def _on_nav_scroll(self, first, last):
        try:
            self.nav_sbar.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.nav_sbar.pack_forget()
            elif not self.nav_sbar.winfo_ismapped():
                self.nav_sbar.pack(side="right", fill="y")
        except Exception:
            pass

    def _bind_nav_wheel(self, _e=None):
        self.nav_canvas.bind_all("<MouseWheel>", self._on_nav_wheel)

    def _unbind_nav_wheel(self, _e=None):
        self.nav_canvas.unbind_all("<MouseWheel>")

    def _on_nav_wheel(self, event):
        try:
            self.nav_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _highlight_nav(self):
        """刷新选中态：品牌色左色条 + soft 底 + 名称加粗。"""
        p = ui_theme.palette(self._theme_mode)
        for tid, (row, bar, name, mark) in self._nav_items.items():
            active = (tid == self._nav_selected)
            bg = p["brand_soft"] if active else p["panel"]
            for w in (row, mark, name):
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass
            try:
                bar.configure(bg=p["brand"] if active else bg)
                name.configure(fg=p["text"] if active else p["text_secondary"],
                               font=(FONT_FAMILY, 10, "bold" if active else "normal"))
            except Exception:
                pass

    # —— KPI 指标条（纯展示，只订阅已有消息，不改任务引擎）——
    def _build_kpi_strip(self, right):
        p = ui_theme.palette(self._theme_mode)
        strip = tk.Frame(right, bg=p["app"])
        strip.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 8))
        for c in range(4):
            strip.columnconfigure(c, weight=1, uniform="kpi")
        specs = [
            ("total", "设备总数", "brand", "server"),
            ("success", "成功", "success", "check"),
            ("fail", "失败 / 部分", "danger", "alert"),
            ("elapsed", "耗时", "accent", "clock"),
        ]
        self._kpi_cards = {}
        for i, (key, label, tone, icon_name) in enumerate(specs):
            # 卡片 = surface 底 + 8px 圆角；Tk 没有圆角容器，用四角扇形补
            card = tk.Frame(strip, bg=p["surface"], bd=0, highlightthickness=0)
            card.grid(row=0, column=i, sticky="ew",
                      padx=(0 if i == 0 else 8, 0))
            corners = ui_widgets.round_corners(card, 8, p["surface"], p["app"])
            box = tk.Frame(card, bg=p["surface"])
            box.pack(fill="both", expand=True, padx=14, pady=9)
            top = tk.Frame(box, bg=p["surface"])
            top.pack(anchor="w", fill="x")
            ic = tk.Label(top, bg=p["surface"])
            ic.pack(side="left")
            lbl = tk.Label(top, text=label, bg=p["surface"], fg=p["text_muted"],
                           font=(FONT_FAMILY, 9))
            lbl.pack(side="left", padx=(6, 0))
            val = tk.Label(box, text="—", bg=p["surface"], fg=p[tone],
                           font=(ui_theme.FONT_MONO, 17, "bold"), anchor="w")
            val.pack(anchor="w", pady=(2, 0))
            sub = tk.Label(box, text="尚未运行", bg=p["surface"],
                           fg=p["text_muted"], font=(FONT_FAMILY, 8), anchor="w")
            sub.pack(anchor="w")
            self._bind_icon(ic, icon_name, 14, color=p[tone], track=False)
            ui_widgets.lift_corners(corners)
            # top 必须由主题刷新显式重刷：根节点上的 option_add("*Background", …)
            # 会让「创建时传入的 bg=」被忽略（只有事后 configure 才生效），
            # 不重刷的话这一行会留成 Tk 默认的 #222222 深灰条。
            self._kpi_cards[key] = (val, sub, ic, card, box, lbl, tone,
                                    corners, icon_name, top)
        self.kpi_strip = strip

    def set_kpi(self, key, value, sub=None):
        rec = self._kpi_cards.get(key)
        if not rec:
            return
        try:
            rec[0].configure(text=str(value))
            if sub is not None:
                rec[1].configure(text=str(sub))
        except Exception:
            pass

    def _reset_kpi(self):
        self._run_started_at = None
        for key in ("total", "success", "fail", "elapsed"):
            self.set_kpi(key, "—", "尚未运行")

    def _update_kpi_from_engine(self, done=False):
        """从 engine.stats 读数（只用于显示，不参与状态判断）。"""
        try:
            stats = dict(self.engine.stats or {})
        except Exception:
            stats = {}
        total = stats.get("total", 0)
        success = stats.get("success", 0)
        partial = stats.get("partial", 0)
        fail = stats.get("fail", 0) + partial
        self.set_kpi("total", total, "本次已处理" if done else "处理中…")
        self.set_kpi("success", success,
                     ("成功率 %.1f%%" % (success * 100.0 / total)) if total else "—")
        self.set_kpi("fail", fail,
                     ("含 %d 台部分成功" % partial) if partial
                     else ("全部成功" if done else "—"))
        if self._run_started_at:
            secs = int(time.time() - self._run_started_at)
            self.set_kpi("elapsed", "%02d:%02d" % (secs // 60, secs % 60),
                         "已完成" if done else "运行中")

    def _update_head_chips(self, tool):
        p = ui_theme.palette(self._theme_mode)
        color = ui_theme.category_color(tool.category, self._theme_mode)
        try:
            self.crumb.configure(text="工具箱  ›  %s  ›  %s"
                                      % (tool.category, tool.name))
            self.cat_chip.configure(text=tool.category, bg=p["elevated"], fg=color)
        except Exception:
            pass

    def _build_workspace(self, right):
        p = ui_theme.palette(self._theme_mode)
        # —— 工具头：面包屑 + 名称 + 分类徽章（版本 / id 不展示，见设计评审）——
        head = ttk.Frame(right)
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(8, 2))
        # darkly 主题下 secondary.TLabel 前景是 #444444，压在深底上几乎看不见；
        # 改用自定义 Desc.TLabel，只覆盖前景色，背景仍随主题透明。
        self.crumb = ttk.Label(head, text="工具箱", style="Desc.TLabel")
        self.crumb.grid(row=0, column=0, sticky="w")
        line = ttk.Frame(head)
        line.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.tool_title = ttk.Label(line, text="未选择工具",
                                    font=(FONT_FAMILY, 15, "bold"))
        self.tool_title.pack(side="left")
        self.cat_chip = tk.Label(line, text="", font=(FONT_FAMILY, 9),
                                 padx=7, pady=1)
        self.cat_chip.pack(side="left", padx=(10, 4))

        self.tool_desc = ttk.Label(right, text="", style="Desc.TLabel",
                                   wraplength=780)
        self.tool_desc.grid(row=1, column=0, sticky="w", padx=4, pady=(0, 6))
        # 换行宽度跟着窗口走：写死 780 在窄窗口会折成第二行只剩一个"）"
        right.bind("<Configure>", self._on_right_configure)

        # KPI 指标条占第 2 行；参数/输出分栏下移到第 3 行
        self._build_kpi_strip(right)

        # 参数区 / 输出区 放进可上下拖拽的分栏：
        # 参数多的工具（如 CRC趋势分析 11 项，每行约 43px）按自然高度会挤掉输出区，
        # 用分栏后默认把剩余空间全给输出区，用户还能自己拖动分配。
        self.vsplit = ttk.Panedwindow(right, orient="vertical", bootstyle="secondary")
        self.vsplit.grid(row=3, column=0, sticky="nsew", padx=4, pady=(6, 2))
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

        # 参数卡片：卡片头（图标 + 标题 + 项数徽章 + 分隔线）+ 表单体。
        # 用 tk.Frame 而不是 ttk.Labelframe —— 后者只会画浮雕方角边框、
        # 标题还压在边框线上，和设计稿的卡片头差得最远。
        self.form_card = tk.Frame(self.form_canvas, bg=p["app"], bd=0,
                                  highlightthickness=1,
                                  highlightbackground=p["border"])
        self.form_head = tk.Frame(self.form_card, bg=p["app"])
        self.form_head.pack(fill="x", padx=(12, 10), pady=(8, 6))
        self.form_head_icon = tk.Label(self.form_head, bg=p["app"])
        self.form_head_icon.pack(side="left")
        self._bind_icon(self.form_head_icon, "sliders", 15, "brand")
        self.form_head_title = tk.Label(self.form_head, text="运行参数", bg=p["app"],
                                        fg=p["text"], font=(FONT_FAMILY, 10, "bold"))
        self.form_head_title.pack(side="left", padx=(7, 0))
        self.form_head_chip = tk.Label(self.form_head, text="", bg=p["elevated"],
                                       fg=p["text_muted"], font=(FONT_FAMILY, 8),
                                       padx=7, pady=1)
        self.form_head_chip.pack(side="left", padx=(8, 0))
        self.form_head_rule = tk.Frame(self.form_card, bg=p["border"], height=1)
        self.form_head_rule.pack(fill="x")

        self.form_frame = ttk.Frame(self.form_card, padding=(12, 8, 12, 8))
        self.form_frame.pack(fill="both", expand=True)
        self.form_hint = ttk.Label(self.form_frame, text="请先在左侧选择工具",
                                   bootstyle="secondary")
        self.form_hint.grid(row=0, column=0, sticky="w", pady=6)
        self._form_win = self.form_canvas.create_window((0, 0), window=self.form_card,
                                                        anchor="nw")
        self.form_card.bind("<Configure>", self._on_form_inner_configure)
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
        self.btn_start = ttk.Button(prog, text="开始执行", width=10,
                                    bootstyle="primary", command=self.on_start)
        self.btn_start.pack(side="left")
        self._bind_icon(self.btn_start, "play", 16, "on_primary")

        self.btn_cancel = ttk.Button(prog, text="中止", width=7,
                                     bootstyle="danger-outline",
                                     command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self._bind_icon(self.btn_cancel, "square", 16, "danger")

        self.btn_open_dir = ttk.Button(prog, text="打开结果目录",
                                       bootstyle="secondary-outline",
                                       command=self.open_output_dir)
        self.btn_open_dir.pack(side="left", padx=6)
        self._bind_icon(self.btn_open_dir, "folder", 16)

        # 一键收起参数区，把整块高度让给结果/图表
        self.btn_fold = ttk.Button(prog, text="收起参数", width=9,
                                   bootstyle="secondary-outline",
                                   command=self.toggle_form_pane)
        self.btn_fold.pack(side="left", padx=(0, 6))
        self._bind_icon(self.btn_fold, "chevron_up", 16)

        # 右侧（从右往左）：百分比 / 细进度条 / 进度计数 / 当前设备
        # 进度条用自绘 Meter 而不是 Floodgauge：Floodgauge 只能用主题自带的
        # primary 蓝（#375a7f），缩到 33px 高之后就是一块和品牌青撞色的蓝方块。
        self.progress_value = ttk.Label(prog, text="0%", width=5,
                                        style="Mono.TLabel", anchor="e")
        self.progress_value.pack(side="right", padx=(8, 2))
        self.progress = ui_widgets.Meter(prog, width=150, height=22, p=p)
        self.progress.pack(side="right", padx=(10, 0))
        self.progress_label = ttk.Label(prog, text="尚未运行", width=9,
                                        bootstyle="secondary", anchor="e")
        self.progress_label.pack(side="right", padx=(0, 6))
        # 采集类任务动辄几百台，进度条上的"24/543"看不出采到哪台了，
        # 这里单独显示当前正在处理的设备（IP + 设备名）
        self.current_label = ttk.Label(prog, text="", width=26,
                                       bootstyle="secondary", anchor="e")
        self.current_label.pack(side="right", padx=(0, 10))

        # 结果输出区（页签按工具的 output_tabs 声明动态重建）
        self.tabs = ui_widgets.TabView(lower, p, font_family=FONT_FAMILY)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # 输出组件统一在此创建一次，按需挂到标签页；tab key -> (widget, text)
        body = self.tabs.body
        self.result_view = ResultView(body)
        self.chart_view = ChartView(body)
        self.log_view = LogView(body, height=9)
        self.summary_text = ttk.ScrolledText(body, wrap="word", height=10)
        self.summary_text.insert("end", "尚未运行任务。\n")
        self.summary_text.configure(state="disabled")
        # 文本类控件不走 ttk 样式，主题刷新时单独着色
        self._text_widgets = (self.log_view.text, self.summary_text)

        self._tab_defs = {
            "result":  (self.result_view.frame, "结果表"),
            "chart":   (self.chart_view.frame, "图表"),
            "log":     (self.log_view.frame, "运行日志"),
            "summary": (self.summary_text, "任务摘要"),
        }
        # 图表放大时把参数区让出来（否则放大后的图被裁掉一半，看着像"没放大"）
        self.chart_view.on_expand = self._on_chart_expand
        # 结果表工具栏的 A+/A-/默认 与参数区「显示字号」保持同步
        self.result_view.on_font_size = self._on_font_size_from_table
        # Ctrl+滚轮直接缩放结果表 / 运行日志
        self.result_view.tree.bind("<Control-MouseWheel>", self._on_font_wheel)
        self.log_view.text.bind("<Control-MouseWheel>", self._on_font_wheel)
        # key -> 当前标签页的页签 id（用于 select）
        self._tab_ids = {}
        self._current_tab_keys = []

    def _rebuild_output_tabs(self, keys):
        """按工具的 output_tabs 声明重建标签页（移除旧的再按序加入）。

        标签页组合未变化时直接跳过，避免无谓重排。
        """
        keys = tuple(k for k in keys if k in self._tab_defs)
        if keys == tuple(self._current_tab_keys):
            return
        self._current_tab_keys = list(keys)

        for tab_id in self.tabs.tabs():
            self.tabs.forget(tab_id)
        self._tab_ids = {}
        p = ui_theme.palette(self._theme_mode)
        for key in keys:
            widget, text = self._tab_defs[key]
            photo = None
            try:
                photo = icons.get(ui_theme.tab_icon(key), p["text_secondary"], 16)
                self._tab_icons[key] = photo
            except Exception:
                photo = None
            self._tab_ids[key] = self.tabs.add(widget, text=text, image=photo)

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
        self.badge = ttk.Label(bar, text="● 空闲", bootstyle="secondary-inverse",
                               padding=(8, 1))
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
        try:
            self.global_status.configure(
                text=("● 定时运行中 · %s" % text) if text
                else "● 调度器就绪 · 每日流水线")
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

    def _start_crc_history_preload(self):
        """启动后后台预热 CRC 历史缓存，首次切到趋势工具不再卡主线程。

        worker 线程只做反序列化与聚合，完成后往 _sched_q 投递事件；
        工作线程禁止碰 Tk（见 _on_scheduler_log 注释）。
        """
        try:
            from tools import crc_trend
            crc_trend.start_preload(
                lambda: self._sched_q.put(("history_ready",)))
        except Exception:
            pass

    def _on_history_ready(self):
        """缓存预热完成后刷新当前表单的动态下拉（占位 → 真实选项）。"""
        tool = getattr(self, "current_tool", None)
        form = getattr(self, "form", None)
        if tool is None or form is None:
            return
        if not any(getattr(s, "options_provider", None)
                   for s in (tool.params or [])):
            return
        try:
            form.refresh_dynamic_options()
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
                    elif item[0] == "result_files":
                        self._apply_result_files(item[1], item[2])
                    elif item[0] == "history_ready":
                        self._on_history_ready()
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
        from core.result_writer import read_rows
        return read_rows(output_dir)

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
        """表单任一参数变化 → 防抖同步定时快照 + 防抖刷新历史结果列表。"""
        # 历史结果列表只在「结果输出目录」真的变了才扫盘（用户每敲一个字符
        # 都 glob 一次没必要，路径没输完时还会扫到不存在的目录）
        self._schedule_refresh_result_files()
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
        bar.grid(row=self.form.footer_row(), column=0,
                 columnspan=self.form.footer_span(),
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
    def _sync_theme_button(self):
        """按钮文字 = 点下去会切到的那一套。"""
        p = ui_theme.palette(self._theme_mode)
        try:
            dark = self._theme_mode == "dark"
            self.theme_button.configure(
                text="浅色" if dark else "深色",
                bg=p["brand_soft"], fg=p["brand"],
                activebackground=p["hover"], activeforeground=p["brand"])
        except Exception:
            pass

    def on_change_theme(self):
        """9 套下拉收敛为一键深浅切换；其余重置链路保持原样。"""
        mode = "light" if self._theme_mode == "dark" else "dark"
        name = ui_theme.theme_of(mode)
        try:
            ttk.Style().theme_use(name)
        except Exception as exc:
            self.log_view.append("ERROR", "切换主题失败：%s" % exc)
            return
        self._theme_mode = mode
        save_ui_pref({"theme": name})
        # tk 原生控件不跟 ttk 主题走，统一重新着色
        self._refresh_theme_colors()
        # 主题变了，重建所有依赖语义色的标签
        self.result_view.refresh_tag_colors()
        self.log_view.refresh_level_colors()
        # 主题切换会重置 ttk.Style，需重新套用字号
        self._apply_font_size()
        self.set_state(self.engine.state)

    # —— 运行时图标 ——
    def _bind_icon(self, widget, name, size=16, color_key="text_secondary",
                   track=True, compound=True, color=None):
        """给控件挂一个线性图标。

        PhotoImage 必须被控件自己持有引用（widget._ntk_icon），否则会被 GC
        导致图标悄悄消失；track=True 登记进主题刷新表，切主题时按新色板重画。
        color 用于「分类色」这类固定语义色（通常配合 track=False，由重建时重算）。
        """
        p = ui_theme.palette(self._theme_mode)
        tint = color if color is not None else p.get(color_key, p["text_secondary"])
        try:
            photo = icons.get(name, tint, size)
            if compound:
                widget.configure(image=photo, compound="left")
            else:
                widget.configure(image=photo)
            widget._ntk_icon = photo
        except Exception:
            pass
        if track and color is None:
            self._icon_bindings[str(widget)] = (
                widget, name, size, color_key, compound)

    def _refresh_icons(self):
        """主题切换后按新色板重画所有已登记图标（顺手清掉已销毁控件）。"""
        p = ui_theme.palette(self._theme_mode)
        stale = []
        for key, item in list(self._icon_bindings.items()):
            widget, name, size, color_key, compound = item
            try:
                if not widget.winfo_exists():
                    stale.append(key)
                    continue
                photo = icons.get(name, p.get(color_key, p["text_secondary"]), size)
                if compound:
                    widget.configure(image=photo, compound="left")
                else:
                    widget.configure(image=photo)
                widget._ntk_icon = photo
            except Exception:
                stale.append(key)
        for key in stale:
            self._icon_bindings.pop(key, None)

    def _refresh_tab_icons(self):
        """页签图标挂在页签项上而非控件上，单独刷新。"""
        p = ui_theme.palette(self._theme_mode)
        for key, tab_id in list(getattr(self, "_tab_ids", {}).items()):
            try:
                photo = icons.get(ui_theme.tab_icon(key), p["text_secondary"], 16)
                self.tabs.tab(tab_id, image=photo)
                self._tab_icons[key] = photo
            except Exception:
                pass

    def _configure_ttk_styles(self, p):
        """把 ttkbootstrap 默认的 #222222 / #2f2f2f 灰黑底统一到语义 token。

        新增的 tk 原生控件（顶栏 / 侧栏 / KPI）用的是深蓝色板，而原有 ttk
        控件还留在 darkly 主题的中性灰黑上，同一个窗口里两套色系——这就是
        截图里「有些地方是黑的」。这里把 ttk 容器统一到 app 色，输入框 /
        表格 / 页签用 elevated / inset / surface 拉开层次，全部落在蓝调内。

        theme_use() 会重置所有自定义样式，所以每次主题刷新都要重新配一遍。
        """
        try:
            st = ttk.Style()
            app, surf = p["app"], p["surface"]
            elev, inset, border = p["elevated"], p["inset"], p["border"]
            fg, fg2, brand = p["text"], p["text_secondary"], p["brand"]

            for name in ("TFrame", "secondary.TFrame", "TPanedwindow",
                         "secondary.Vertical.TPanedwindow",
                         "secondary.Horizontal.TPanedwindow"):
                st.configure(name, background=app)
            for name in ("TLabelframe", "secondary.TLabelframe"):
                st.configure(name, background=app, bordercolor=border,
                             lightcolor=app, darkcolor=app)
            st.configure("TLabelframe.Label", background=app, foreground=fg2)
            # 所有 ttk Label 与容器同底色，否则会出现一块块灰斑
            st.configure("TLabel", background=app, foreground=fg)
            st.configure("secondary.TLabel", background=app, foreground=fg2)
            st.configure("Desc.TLabel", background=app, foreground=fg2)
            # 进度百分比等需要等宽对齐的小标签
            st.configure("Mono.TLabel", background=app, foreground=fg2,
                         font=(ui_theme.FONT_MONO, 9))

            for name in ("TNotebook", "secondary.TNotebook"):
                st.configure(name, background=app, bordercolor=border,
                             tabmargins=(2, 4, 2, 0))
            st.configure("TNotebook.Tab", background=elev, foreground=fg2,
                         bordercolor=border, padding=(10, 5))
            st.map("TNotebook.Tab",
                   background=[("selected", surf)],
                   foreground=[("selected", brand)])

            for name in ("Treeview", "Result.Treeview", "primary.Treeview"):
                st.configure(name, background=inset, fieldbackground=inset,
                             foreground=fg, bordercolor=inset,
                             lightcolor=inset, darkcolor=inset, relief="flat")
                st.map(name, background=[("selected", p["brand_soft"])],
                       foreground=[("selected", fg)])
            # 表头不画浮雕边框、不上重底色：贴住卡片底，只用一条细线分隔，
            # 否则表头会变成一块深蓝板压在结果表上（设计稿里表头是弱化的）
            for name in ("Treeview.Heading", "Result.Treeview.Heading"):
                st.configure(name, background=app, foreground=p["text_muted"],
                             bordercolor=app, lightcolor=app, darkcolor=app,
                             relief="flat", padding=(4, 6))

            # 根级 + bootstyle 变体都要配：ttkbootstrap 的变体样式名是
            # <color>.<Type>.<Class>（如 secondary.Outline.TButton），
            # 只配 TButton 覆盖不到，工具栏按钮就会留成 #222222 灰黑块。
            for name in ("TEntry", "TCombobox", "TSpinbox"):
                st.configure(name, background=elev, fieldbackground=elev,
                             foreground=fg, bordercolor=border,
                             insertcolor=fg, arrowcolor=fg2)
            for name in ("TButton", "secondary.TButton"):
                st.configure(name, background=elev, foreground=fg,
                             bordercolor=border)
            # 主操作按钮（开始执行）用设计稿的实心蓝，而不是主题自带的
            # #375a7f / #2c3e50（后者在浅色下几乎是黑块，和品牌色不搭）
            pf = p.get("primary_fill", brand)
            st.configure("primary.TButton", background=pf,
                         foreground=p["on_primary"], bordercolor=pf)
            st.map("primary.TButton",
                   background=[("disabled", elev), ("pressed", pf),
                               ("active", pf)],
                   foreground=[("disabled", p["text_muted"])])
            for variant in ("primary", "secondary", "success", "info",
                            "warning", "danger", "light", "dark"):
                for base in ("TEntry", "TCombobox", "TSpinbox"):
                    st.configure("%s.%s" % (variant, base),
                                 background=elev, fieldbackground=elev,
                                 foreground=fg, bordercolor=border,
                                 insertcolor=fg, arrowcolor=fg2)
                st.configure("%s.Outline.TButton" % variant,
                             background=elev, foreground=fg2,
                             bordercolor=border)
                for kind in ("Round.Vertical.TScrollbar",
                             "Round.Horizontal.TScrollbar",
                             "Vertical.TScrollbar", "Horizontal.TScrollbar"):
                    st.configure("%s.%s" % (variant, kind),
                                 background=elev, troughcolor=app,
                                 bordercolor=border, arrowcolor=fg2)
            for name in ("TScrollbar", "Vertical.TScrollbar",
                         "Horizontal.TScrollbar"):
                st.configure(name, background=elev, troughcolor=app,
                             bordercolor=border, arrowcolor=fg2)
            st.configure("TCheckbutton", background=app, foreground=fg)
            st.configure("TRadiobutton", background=app, foreground=fg)
            st.map("danger.TLabel", background=[])
            st.configure("danger.TLabel", background=app,
                         foreground=p["danger"])
            # 表单校验提示（ui/form_builder.py）用的自定义样式
            st.configure("Error.TLabel", background=app,
                         foreground=p["danger"])
            st.configure("secondary.Inverse.TLabel", background=elev,
                         foreground=fg)
            for name in ("Round.Toggle", "secondary.Round.Toggle"):
                st.configure(name, background=app, foreground=fg)
            # tk 原生控件的默认底色也归到蓝调（显式 bg= 的仍然优先）
            try:
                self.root.option_add("*Background", app)
                self.root.option_add("*Canvas.Background", app)
                self.root.option_add("*Text.Background", inset)
                self.root.option_add("*Entry.Background", elev)
                self.root.option_add("*Listbox.Background", inset)
            except Exception:
                pass

            for widget in getattr(self, "_text_widgets", ()):
                try:
                    widget.configure(background=inset, foreground=fg,
                                     insertbackground=fg)
                except Exception:
                    pass
            try:
                self.form_canvas.configure(background=app)
            except Exception:
                pass
        except Exception:
            pass

    def _refresh_theme_colors(self):
        """把语义 token 重新刷到所有 tk 原生控件上。

        ttk 控件由 ttkbootstrap 主题自动管理；AppBar / Sidebar / KPI / chips
        是 tk 原生控件（为了精确控制背景色与色条），主题切换时必须手动重刷。
        """
        p = ui_theme.palette(self._theme_mode)
        # 先统一 ttk 控件配色（顺带修掉 secondary.TLabel 的 #444444 不可读问题）
        self._configure_ttk_styles(p)

        def paint(widget, bg, fg=None):
            try:
                kw = {"bg": bg}
                if fg is not None:
                    kw["fg"] = fg
                widget.configure(**kw)
            except Exception:
                pass

        paint(self.appbar, p["panel"])
        paint(self.brand_box, p["panel"])
        paint(self.brand_cn, p["panel"], p["text"])
        paint(self.brand_en, p["panel"], p["text_muted"])
        paint(self.global_status, p["panel"], p["text_secondary"])
        try:
            self.logo_canvas.configure(bg=p["panel"])
            ui_theme.draw_logo(self.logo_canvas, p["brand"], 34)
            self.avatar.configure(bg=p["panel"])
            self.avatar.delete("all")
            self.avatar.create_oval(1, 1, 29, 29, fill=p["brand"], outline="")
            self.avatar.create_text(15, 15, text=self._avatar_text,
                                    fill=p["brand_on"],
                                    font=(FONT_FAMILY, 9, "bold"))
        except Exception:
            pass
        for btn in getattr(self, "_icon_buttons", []):
            try:
                btn.configure(bg=p["panel"], fg=p["text_secondary"],
                              activebackground=p["hover"],
                              activeforeground=p["text"])
            except Exception:
                pass
        self._sync_theme_button()

        paint(self.sidebar, p["panel"])
        paint(self.sidebar_head, p["panel"])
        paint(self.sidebar_foot, p["panel"])
        paint(self.search_caption, p["panel"], p["text_muted"])
        paint(self.nav_canvas, p["panel"])
        paint(self.nav_inner, p["panel"])
        paint(self.nav_count, p["panel"], p["success"])
        paint(self.nav_foot_hint, p["panel"], p["text_muted"])
        paint(self.nav_foot_key, p["panel"], p["text_muted"])
        paint(self.empty_label, p["panel"], p["danger"])
        paint(self.fail_badge, p["panel"], p["warning"])

        paint(self.kpi_strip, p["app"])
        for _key, card_rec in self._kpi_cards.items():
            (val, sub, ic, card, box, lbl, tone, corners, icon_name, top) = card_rec
            paint(card, p["surface"])
            paint(box, p["surface"])
            paint(top, p["surface"])
            paint(lbl, p["surface"], p["text_muted"])
            paint(val, p["surface"], p[tone])
            paint(sub, p["surface"], p["text_muted"])
            paint(ic, p["surface"])
            ui_widgets.paint_corners(corners, p["surface"], p["app"])
            self._bind_icon(ic, icon_name, 14, color=p[tone], track=False)

        # 参数卡片头 + 边框
        paint(self.form_card, p["app"])
        try:
            self.form_card.configure(highlightbackground=p["border"])
        except Exception:
            pass
        paint(self.form_head, p["app"])
        paint(self.form_head_icon, p["app"])
        paint(self.form_head_title, p["app"], p["text"])
        paint(self.form_head_chip, p["elevated"], p["text_muted"])
        paint(self.form_head_rule, p["border"])

        # 页签 + 进度条（都是自绘控件，需要显式重绘）
        try:
            self.tabs.refresh(p)
            self.progress.refresh(p)
        except Exception:
            pass
        # 结果表工具栏里的分段筛选/字号标签/按钮图标
        try:
            self.result_view.refresh_theme(p)
        except Exception:
            pass

        paint(self.cat_chip, p["elevated"])
        if getattr(self, "current_tool", None):
            self._update_head_chips(self.current_tool)
        # 分类色随主题变，导航重建一次（保留当前选中项）
        try:
            self._render_tool_list()
        except Exception:
            pass
        # 图标线条色也要跟着语义色板走：清缓存后重画
        try:
            icons.clear_cache()
            self._refresh_icons()
            self._refresh_tab_icons()
        except Exception:
            pass

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
        """按分类重建导航：分类色条 + 分类名 + 右侧计数 + 选中态（不显示版本号）。"""
        p = ui_theme.palette(self._theme_mode)
        for child in self.nav_inner.winfo_children():
            child.destroy()
        self._nav_items = {}
        self._nav_groups = []      # (gh, bar, label, badge, 分类色)
        keyword = self.search_var.get().strip().lower()
        shown = 0
        for category, tools in self.registry.by_category().items():
            items = [t for t in tools
                     if not keyword or keyword in t.name.lower()
                     or keyword in t.id.lower()]
            if not items:
                continue
            color = ui_theme.category_color(category, self._theme_mode)

            gh = tk.Frame(self.nav_inner, bg=p["panel"])
            gh.pack(fill="x", pady=(12, 4))
            # 分类头不再放图标（评审：图标只留给工具项，分类靠色条+文字区分），
            # 计数徽章改为靠右对齐，和下方工具项形成清晰的左右边界。
            group_bar = tk.Frame(gh, bg=color, width=3, height=10)
            group_bar.pack(side="left", padx=(12, 7))
            group_label = tk.Label(gh, text=category, bg=p["panel"],
                                   fg=p["text_muted"],
                                   font=(FONT_FAMILY, 9, "bold"))
            group_label.pack(side="left")
            # 计数徽章：中性浅底 + 次要文字色（浅色主题下 elevated 贴白底几乎看不见，
            # 且 8pt 小字需要 ≥4.5:1 的对比度）
            group_badge = tk.Label(gh, text=str(len(items)),
                                   bg=p["neutral_soft"], fg=p["text_secondary"],
                                   width=2, font=(ui_theme.FONT_MONO, 8), padx=4)
            group_badge.pack(side="right", padx=(0, 8))
            self._nav_groups.append((gh, group_bar, group_label, group_badge, color))

            for t in items:
                row = tk.Frame(self.nav_inner, bg=p["panel"], cursor="hand2")
                row.pack(fill="x", padx=8, pady=1)
                bar = tk.Frame(row, bg=p["panel"], width=3)
                bar.pack(side="left", fill="y")
                mark = tk.Label(row, bg=p["panel"])
                mark.pack(side="left", padx=(2, 5))
                self._bind_icon(mark, ui_theme.category_icon(category), 15,
                                track=False, compound=False, color=color)
                # 侧栏不再显示版本号，整行宽度留给工具名；极端长名仍做
                # 省略号 + 悬浮完整名，绝不静默把字切掉。
                display = t.name if len(t.name) <= 14 else t.name[:13] + "…"
                name = tk.Label(row, text=display, bg=p["panel"],
                                fg=p["text_secondary"],
                                font=(FONT_FAMILY, 10), anchor="w")
                name.pack(side="left", fill="x", expand=True)
                if display != t.name:
                    try:
                        ToolTip(name, text=t.name, delay=400)
                    except Exception:
                        pass
                for w in (row, bar, mark, name):
                    w.bind("<Button-1>",
                           lambda e, tid=t.id: self.on_select_tool(tid))
                self._nav_items[t.id] = (row, bar, name, mark)
                shown += 1

        if not shown:
            tk.Label(self.nav_inner, text="没有匹配的工具", bg=p["panel"],
                     fg=p["text_muted"], font=(FONT_FAMILY, 10)).pack(pady=20)
        try:
            self.nav_count.configure(text="%d / %d 个工具已注册"
                                          % (shown, len(self.registry.tools)))
        except Exception:
            pass
        # tk 原生控件创建时背景可能被 option database 的 #222222 覆盖，
        # 按角色显式重涂一次（工具项由 _highlight_nav 负责）。
        for gh, bar, label, badge, c in self._nav_groups:
            try:
                gh.configure(bg=p["panel"])
                bar.configure(bg=c)
                label.configure(bg=p["panel"], fg=p["text_muted"])
                badge.configure(bg=p["neutral_soft"], fg=p["text_secondary"])
            except Exception:
                pass
        self._highlight_nav()
        try:
            self.nav_canvas.yview_moveto(0)
            self.root.after_idle(self._on_nav_inner_configure)
        except Exception:
            pass

    def on_select_tool(self, tool_id=None, _event=None):
        """选中工具（导航项点击时传入 tool_id 字符串）。

        只有「怎么拿到 tool_id」从 Treeview.selection() 换成了参数；
        下面的切换/缓存/分栏贴合逻辑一行未动。
        """
        if self.engine.is_running():
            Messagebox.show_info("任务执行中，请先中止或等待完成", title="提示")
            return
        tid = tool_id if isinstance(tool_id, str) else None
        if not tid:
            tid = getattr(self, "_nav_selected", None)
        tool = self.registry.get(tid) if tid else None
        if tool is None:
            return
        self._nav_selected = tid
        self._highlight_nav()

        # 切换前把当前工具的全部输出（结果表/图表/日志/摘要）存入缓存
        self._save_outputs()
        # 切换前把当前工具的参数同步进它的定时条目（已启用才同步）
        self._sync_sched_params()

        self.current_tool = tool
        self.tool_title.configure(text=tool.name)
        self.tool_desc.configure(text=getattr(tool, "description", "") or "")
        self._update_head_chips(tool)
        self._reset_kpi()

        # 按工具的 output_tabs 声明重建输出标签页
        tabs = getattr(tool, "output_tabs", None) or ["result", "log", "summary"]
        self._rebuild_output_tabs(tabs)

        for child in self.form_frame.winfo_children():
            child.destroy()
        try:
            n = len(tool.params or [])
            self.form_head_chip.configure(
                text="%d 项参数" % n if n else "无参数")
        except Exception:
            pass
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
            self.progress_value.configure(text="0%")
        except Exception:
            pass
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
        """已废弃：字号调节统一收敛到结果表工具栏的 A- / 12 / A+。

        参数区底部再放一个「显示字号」Spinbox 属于同一功能的第二入口，
        在 760px 高的窗口里白占 38px，还会把最后一行参数挤出可视区。
        方法保留为空实现，避免外部调用点报错。
        """
        return

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
        self._files_job = None
        self._files_folder_last = None
        self._result_files = []
        if tool is None or not getattr(tool, "result_files_param", ""):
            return
        if self._form_avail_width() < 820:
            return          # 窄窗口先不挤，宽度恢复后 _reflow_form 会重建
        try:
            rowspan = max(1, self.form.footer_row())
            panel = ttk.Frame(self.form_frame)
            panel.grid(row=0, column=self.form.cols, rowspan=rowspan, sticky="nsew",
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

    def _schedule_refresh_result_files(self):
        """目录参数变化后延迟 400ms 刷新历史结果列表（合并连续输入）。"""
        lb = getattr(self, "_files_list", None)
        if lb is None:
            return
        try:
            if self._result_files_folder() == getattr(self, "_files_folder_last", None):
                return
            job = getattr(self, "_files_job", None)
            if job:
                self.root.after_cancel(job)
            self._files_job = self.root.after(400, self._refresh_result_files_now)
        except Exception:
            pass

    def _refresh_result_files_now(self):
        """防抖到期后真正扫盘（记录本次目录，避免同样的值反复触发）。"""
        self._files_job = None
        try:
            self._files_folder_last = self._result_files_folder()
        except Exception:
            self._files_folder_last = None
        self._refresh_result_files()

    def _refresh_result_files(self):
        """重新扫描结果输出目录，刷新文件列表。

        扫盘放后台线程：结果输出目录很可能是共享盘/网络盘，glob 一卡就是
        几秒，主线程等它就是「切个工具就无响应」。扫完经 _sched_q 回主线程
        渲染（工作线程禁止碰 Tk，见 _on_scheduler_log 注释）。
        """
        lb = getattr(self, "_files_list", None)
        if lb is None:
            return
        self._result_files = []
        try:
            lb.delete(0, "end")
            lb.insert("end", "（正在扫描目录…）")
        except Exception:
            return
        folder = self._result_files_folder()
        self._files_folder_last = folder
        if not folder:
            try:
                lb.delete(0, "end")
                lb.insert("end", "（先在参数里填「结果输出目录」）")
            except Exception:
                pass
            return
        tool = self.current_tool          # 捕获引用，线程里不再碰界面状态
        self._files_scan_id = getattr(self, "_files_scan_id", 0) + 1
        scan_id = self._files_scan_id

        def _scan():
            try:
                files = tool.list_result_files(folder) or []
            except Exception:
                files = []
            try:
                self._sched_q.put(("result_files", scan_id, list(files)))
            except Exception:
                pass

        threading.Thread(target=_scan, daemon=True, name="scan-result-files").start()

    def _apply_result_files(self, scan_id, files):
        """后台扫描完成后回填历史结果列表（主线程）。"""
        if scan_id != getattr(self, "_files_scan_id", 0):
            return                          # 过期结果（目录又变了/已切换工具）
        lb = getattr(self, "_files_list", None)
        if lb is None:
            return
        try:
            lb.delete(0, "end")
        except Exception:
            return
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
                need = self.form_card.winfo_reqheight() + 6   # 卡片自然高度 + 上下留白
                # 参数区优先完整显示，但不能把输出区压到 OUTPUT_MIN_H 以下；
                # 参数实在放不下时由参数区自己的滚动条兜底
                need = min(need, max(160, total - OUTPUT_MIN_H))
            if abs(self.vsplit.sashpos(0) - need) > 2:
                self.vsplit.sashpos(0, need)
        except Exception:
            pass

    # —— 参数区滚动 ——
    def _on_form_inner_configure(self, _event=None):
        """参数内容变化 → 更新滚动范围；高度变了顺带重新贴合分栏。"""
        try:
            self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))
        except Exception:
            pass
        # 卡片高度会因为「列数重排 / 历史结果面板显隐」二次变化，
        # 只靠切换工具时那几次定时贴合，参数区会一直停在旧高度（例如按
        # 带右侧面板的高度留了 333px，面板隐藏后输出区就只剩 147px）。
        if getattr(self, "_form_autofit", True) and not getattr(self, "_form_folded", False):
            job = getattr(self, "_fit_job", None)
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
            try:
                self._fit_job = self.root.after(120, self._fit_form_pane)
            except Exception:
                self._fit_job = None

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
            from ui.form_builder import columns_for
            want = columns_for(len(self.form.params), width)
            if int(getattr(self.form, "cols", 1)) == want:
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
            self._bind_icon(self.btn_fold,
                            "chevron_down" if self._form_folded else "chevron_up", 16)
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
        self.badge.configure(text="● " + text, bootstyle="%s-inverse" % style)

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
        self._run_started_at = time.time()
        self._update_kpi_from_engine()
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
            try:
                self.progress_value.configure(text="%d%%" % int(percent))
            except Exception:
                pass
            self.stat_label.configure(text="已处理 %d / %d" % (current, total))
            item = payload.get("item") or ""
            if len(item) > 40:
                # 设备名可能很长（"路由器(BOSS-...)"），截断时保留前面的 IP
                item = item[:39] + "…"
            self.current_label.configure(text=("当前：%s" % item) if item else "")
            self._update_kpi_from_engine()
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
            self._update_kpi_from_engine(done=True)
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
