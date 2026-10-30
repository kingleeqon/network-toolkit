# -*- coding: utf-8 -*-
"""「定时任务总览」窗口。

列出所有可定时的任务（每个工具一条 + 每日流水线），可以：
- 看到每个任务**下一次执行时间**、**当前是否在运行**、**上次执行结果**
- 改定时时间、启用/停用、删除
- 立即执行一次

单个工具的定时参数（设备清单、并发、目录等）取自**工具参数区里填好的值**——
在对应工具页面填好参数后勾上「定时执行」，这里就能看到并管理。
"""
import tkinter as tk
from pathlib import Path

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

from core.scheduler import PIPELINE_KEY, SchedulerManager, normalize_at

BASE_DIR = Path(__file__).resolve().parent.parent


class ScheduleOverview(object):
    """定时任务总览（非模态，重复打开只聚焦已开的那一个）。"""

    def __init__(self, master, manager=None):
        self.master = master
        self.manager = manager or SchedulerManager()
        self.win = ttk.Toplevel(master)
        self.win.title("定时任务总览")
        self.win.geometry("840x560")
        self.win.minsize(760, 460)
        try:
            self.win.transient(master)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._keys = []          # 表格行 → 任务 key
        self.var_tool = tk.StringVar()
        self.var_at = tk.StringVar(value="08:30")
        self.var_on = tk.BooleanVar(value=True)

        self._build()
        self.reload()

    # ---------- 工具清单 ----------
    def _tool_list(self):
        """(key, 显示名, 是否已配置) 列表。"""
        items = [(PIPELINE_KEY, "每日流水线（采集 + 备份 + 对比）", True)]
        try:
            from core.registry import Registry
            reg = Registry(BASE_DIR / "tools")
            reg.discover()
            for tool in reg.tools:
                items.append((tool.id, tool.name, False))
        except Exception:
            pass
        return items

    # ---------- 界面 ----------
    def _build(self):
        root = ttk.Frame(self.win, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        ttk.Label(root, text="每个工具可各设一个定时点；到点自动执行（程序需保持打开）",
                  bootstyle="secondary").grid(row=0, column=0, sticky="w", pady=(0, 8))

        # 新增/编辑条
        top = ttk.Frame(root)
        top.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="任务").pack(side="left")
        self.cmb_tool = ttk.Combobox(top, textvariable=self.var_tool, width=34,
                                     state="readonly")
        self.cmb_tool.pack(side="left", padx=(6, 10))
        self.cmb_tool.bind("<<ComboboxSelected>>", self._on_pick)
        ttk.Label(top, text="时间").pack(side="left")
        self.ent_at = ttk.Entry(top, textvariable=self.var_at, width=7)
        self.ent_at.pack(side="left", padx=(6, 10))
        ttk.Checkbutton(top, text="启用", variable=self.var_on,
                        bootstyle="round-toggle").pack(side="left")
        ttk.Button(top, text="添加 / 更新", width=11, bootstyle="primary",
                   command=self.save).pack(side="left", padx=(12, 0))

        # 表格
        cols = ("name", "on", "at", "state", "last")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=12)
        for key, title, width in (("name", "任务", 250), ("on", "启用", 50),
                                  ("at", "定时", 60), ("state", "状态", 200),
                                  ("last", "上次执行", 190)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=2, column=0, sticky="nsew")
        vs = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        vs.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 底部按钮
        btns = ttk.Frame(root)
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="立即执行", width=10, bootstyle="success",
                   command=self.run_now).pack(side="left")
        ttk.Button(btns, text="删除定时", width=10, bootstyle="danger-outline",
                   command=self.delete).pack(side="left", padx=6)
        ttk.Button(btns, text="刷新", width=8, bootstyle="secondary-outline",
                   command=self.reload).pack(side="left")
        ttk.Button(btns, text="每日流水线参数设置…", width=16,
                   bootstyle="secondary-outline",
                   command=self.open_pipeline_settings).pack(side="left", padx=6)
        ttk.Button(btns, text="关闭", width=8, bootstyle="secondary",
                   command=self.close).pack(side="right")

        self.lbl_hint = ttk.Label(root, text="", bootstyle="secondary")
        self.lbl_hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # ---------- 数据 ----------
    def reload(self):
        """重建表格（保留当前选择）。"""
        selected = self._current_key()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._keys = []
        entries = self.manager.entries()
        for key, name, _ in self._tool_list():
            item = entries.get(key) or {}
            enabled = bool(item.get("enabled"))
            at = item.get("at", "")
            last = item.get("last") or {}
            last_txt = "-"
            if last:
                ok = last.get("code") == 0
                last_txt = "%s %s" % (last.get("at", "")[-5:], "成功" if ok
                                       else "失败(%s)" % last.get("code"))
            state = self.manager.status_of(key)
            self._keys.append(key)
            self.tree.insert("", "end", iid=key, values=(
                name, "✔" if enabled else "", at or "-", state, last_txt))
        # 下拉框内容
        names = ["%s" % name for _, name, _ in self._tool_list()]
        self.cmb_tool.configure(values=names)
        if selected:
            try:
                self.tree.selection_set(selected)
                self.tree.see(selected)
            except Exception:
                pass
        self._sync_tool_names()
        self._refresh_hint()

    def _sync_tool_names(self):
        """下拉框显示名 → 实际 key 的映射。"""
        self._name_to_key = {name: key for key, name, _ in self._tool_list()}

    def _current_key(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _on_pick(self, _event=None):
        """下拉选任务 → 表格选中它，并把时间/启用回填。"""
        key = self._name_to_key.get(self.var_tool.get())
        if not key:
            return
        try:
            self.tree.selection_set(key)
            self.tree.see(key)
        except Exception:
            pass
        self._fill_editor(key)

    def _on_select(self, _event=None):
        key = self._current_key()
        if key:
            self._fill_editor(key)

    def _fill_editor(self, key):
        item = self.manager.get(key)
        self.var_at.set(item.get("at", "08:30"))
        self.var_on.set(bool(item.get("enabled")))
        self.var_tool.set(self.manager.title_of(key))
        self._refresh_hint()

    def _refresh_hint(self):
        key = self._current_key()
        if not key:
            self.lbl_hint.configure(text="")
            return
        try:
            params = self.manager.get(key).get("params") or {}
        except Exception:
            params = {}
        if params:
            show = []
            for k, v in list(params.items())[:4]:
                if v not in ("", None):
                    show.append("%s=%s" % (k, v))
            self.lbl_hint.configure(
                text="该定时任务使用的参数（来自工具页面填写的值）：%s"
                     % ("　".join(show) if show else "（无）"))
        else:
            self.lbl_hint.configure(text="该定时任务还没有参数快照，"
                                          "请到对应工具页面填好参数并勾选「定时执行」")

    # ---------- 动作 ----------
    def save(self):
        """添加/更新所选任务的定时。"""
        key = self._name_to_key.get(self.var_tool.get())
        if not key:
            Messagebox.show_warning("请先选择任务。", title="提示", parent=self.win)
            return
        at = normalize_at(self.var_at.get(), "")
        if not at:
            Messagebox.show_warning("时间格式应为 HH:MM（00:00 ~ 23:59）。",
                                    title="时间不对", parent=self.win)
            return
        params = {}
        try:
            params = self.manager.get(key).get("params") or {}
        except Exception:
            pass
        self.manager.set_schedule(key, at=at, enabled=bool(self.var_on.get()),
                                  params=params)
        self.reload()
        try:
            self.tree.selection_set(key)
        except Exception:
            pass

    def delete(self):
        key = self._current_key()
        if not key:
            Messagebox.show_info("请先在表格里选中一个任务。", title="提示", parent=self.win)
            return
        if not Messagebox.yesno("确定删除「%s」的定时设置？" % self.manager.title_of(key),
                                title="删除定时", parent=self.win):
            return
        self.manager.remove(key)
        self.reload()

    def run_now(self):
        key = self._current_key()
        if not key:
            Messagebox.show_info("请先在表格里选中一个任务。", title="提示", parent=self.win)
            return
        ok, msg = self.manager.run_now(key)
        (Messagebox.show_info if ok else Messagebox.show_warning)(
            msg, title="定时任务" if ok else "无法执行", parent=self.win)
        self.reload()

    def open_pipeline_settings(self):
        """打开每日流水线的参数设置（设备清单/日志目录/备份目录）。"""
        try:
            from ui.auto_task_dialog import open_dialog as open_auto_task
            open_auto_task(self.master, self.manager)
        except Exception as exc:
            Messagebox.show_error("打开失败：%s" % exc, title="出错", parent=self.win)

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def open_dialog(master, manager=None):
    """打开（或聚焦已有的）总览窗口。"""
    old = getattr(master, "_schedule_overview", None)
    if old is not None:
        try:
            old.win.deiconify()
            old.win.lift()
            old.win.focus_force()
            return old
        except Exception:
            pass
    dlg = ScheduleOverview(master, manager)
    try:
        master._schedule_overview = dlg
    except Exception:
        pass
    return dlg
