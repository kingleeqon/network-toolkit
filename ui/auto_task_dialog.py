# -*- coding: utf-8 -*-
"""「定时任务」设置窗口。

对应 `scripts/daily_crc.py` 的配置：设备清单、日志归档根目录、并发/超时、每天几点跑。

这个窗口管的是**应用内定时执行**：程序开着，到点自己跑一遍
「采集接口 → 备份配置 → CRC 对比」。不用去系统里注册计划任务。

    启用自动执行   → 写配置 + 立刻生效（主窗口的应用内调度器接管）
    立即执行一次   → 后台跑一次，输出实时显示在下方（不卡界面）
    保存配置       → 只保存路径等参数
"""
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = BASE_DIR / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import daily_crc as daily
except Exception:            # 极端情况下（脚本缺失）窗口仍可打开并提示
    daily = None


class AutoTaskDialog(object):
    """非模态设置窗口（同一时间只允许开一个）。"""

    def __init__(self, master, scheduler=None):
        self.master = master
        self.scheduler = scheduler
        # master 可能是主窗口对象，也可能就是 tk 根；Toplevel 需要真正的 tk 父窗口
        parent = getattr(master, "root", master)
        self.win = ttk.Toplevel(parent)
        self.win.title("每日自动任务：采集接口 + 备份配置 + CRC 对比")
        self.win.geometry("780x660")
        self.win.minsize(700, 560)
        try:
            self.win.transient(master)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._q = queue.Queue()
        self._proc = None
        self._running = False

        cfg = self._load()
        self.var_devices = tk.StringVar(value=cfg.get("devices", ""))
        self.var_archive = tk.StringVar(value=cfg.get("archive_root", ""))
        self.var_backup_on = tk.BooleanVar(value=bool(cfg.get("backup_enabled", True)))
        self.var_backup = tk.StringVar(value=cfg.get("backup_root", ""))
        self.var_conc = tk.StringVar(value=str(cfg.get("concurrency", 5)))
        self.var_timeout = tk.StringVar(value=str(cfg.get("timeout", 40)))
        self.var_at = tk.StringVar(value=cfg.get("daily_time", "08:30"))
        self.var_auto = tk.BooleanVar(value=bool(cfg.get("auto_enabled", False)))
        self.var_status = tk.StringVar(value="")
        self.var_sched = tk.StringVar(value="")

        self._build()
        self.refresh_status()

    # ---------- 配置 ----------
    def _load(self):
        if daily is None:
            return {}
        try:
            return daily.load_config(daily.DEFAULT_CONFIG)
        except Exception:
            return {}

    def _gather(self):
        """读取界面值并校验，返回 (cfg, error)。"""
        devices = self.var_devices.get().strip()
        archive = self.var_archive.get().strip()
        if not devices:
            return None, "请先选择「设备清单」（xlsx / csv / txt）"
        if not Path(devices).is_file():
            return None, "设备清单不存在：\n%s" % devices
        if not archive:
            return None, "请先选择「日志归档根目录」"
        try:
            conc = max(1, min(50, int(self.var_conc.get())))
            tmo = max(5, min(300, int(self.var_timeout.get())))
        except (TypeError, ValueError):
            return None, "并发/超时必须是数字"
        at = daily.normalize_time(self.var_at.get()) if daily else None
        if not at:
            return None, "执行时间格式应为 HH:MM（00:00 ~ 23:59），例如 08:30"
        backup_on = bool(self.var_backup_on.get())
        backup_root = self.var_backup.get().strip()
        if backup_on and not backup_root:
            return None, "勾选了「同时备份设备配置」，请再选一个「配置备份根目录」"
        return {"devices": devices, "archive_root": archive,
                "concurrency": conc, "timeout": tmo,
                "skip_collect": False,
                "backup_enabled": backup_on,
                "backup_root": backup_root,
                "daily_time": at}, None

    # ---------- 界面 ----------
    def _build(self):
        root = ttk.Frame(self.win, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="每天定时：登录设备采集接口数据 → 备份设备配置 → 自动做 CRC 对比（当天 vs 前一天）",
                  bootstyle="secondary").grid(row=0, column=0, columnspan=3,
                                              sticky="w", pady=(0, 10))

        row = [1]

        def path_row(label, var, kind, tip):
            r = row[0]
            ttk.Label(root, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ent = ttk.Entry(root, textvariable=var)
            ent.grid(row=r, column=1, sticky="ew", padx=(6, 6), pady=4)
            ttk.Button(root, text="浏览…", width=8, bootstyle="secondary-outline",
                       command=lambda v=var, k=kind: self._browse(v, k)).grid(
                row=r, column=2, pady=4)
            ttk.Label(root, text=tip, bootstyle="secondary",
                      font=("Microsoft YaHei UI", 8)).grid(row=r + 1, column=1,
                                                           columnspan=2, sticky="w")
            row[0] = r + 2

        path_row("设备清单", self.var_devices, "file",
                 "xlsx / csv / txt，需含 IP、设备名、厂商、凭证 列")
        path_row("日志根目录", self.var_archive, "dir",
                 "自动建 <日期> 子目录，每台设备一个 <设备名>.log")

        r = row[0]
        self.chk_backup = ttk.Checkbutton(
            root, text="同时备份设备配置（同一次登录取两份，每天一份 .cfg）",
            variable=self.var_backup_on, bootstyle="round-toggle",
            command=self._sync_backup_state)
        self.chk_backup.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))

        ttk.Label(root, text="配置备份目录").grid(row=r + 1, column=0, sticky="w", pady=4)
        self.ent_backup = ttk.Entry(root, textvariable=self.var_backup)
        self.ent_backup.grid(row=r + 1, column=1, sticky="ew", padx=(6, 6), pady=4)
        self.btn_backup = ttk.Button(root, text="浏览…", width=8,
                                     bootstyle="secondary-outline",
                                     command=lambda: self._browse(self.var_backup, "dir"))
        self.btn_backup.grid(row=r + 1, column=2, pady=4)
        ttk.Label(root, text="写 <日期>\\<设备名>.cfg；与接口采集同一次登录完成，"
                             "备份失败不影响 CRC 对比",
                  bootstyle="secondary", font=("Microsoft YaHei UI", 8)).grid(
            row=r + 2, column=1, columnspan=2, sticky="w")
        self._sync_backup_state()
        row[0] = r + 3

        r = row[0]
        box = ttk.Frame(root)
        box.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 4))
        ttk.Label(box, text="并发").pack(side="left")
        ttk.Spinbox(box, from_=1, to=50, width=5,
                    textvariable=self.var_conc).pack(side="left", padx=(4, 14))
        ttk.Label(box, text="超时(秒)").pack(side="left")
        ttk.Spinbox(box, from_=5, to=300, width=6,
                    textvariable=self.var_timeout).pack(side="left", padx=(4, 14))
        row[0] = r + 1

        r = row[0]
        self.chk_auto = ttk.Checkbutton(
            root, text="本程序开着时，每天到点自动执行",
            variable=self.var_auto, bootstyle="round-toggle",
            command=self._sync_auto_state)
        self.chk_auto.grid(row=r, column=0, columnspan=2, sticky="w", pady=(12, 2))
        ttk.Label(root, text="每天").grid(row=r, column=2, sticky="e", padx=(0, 34))
        self.ent_at = ttk.Entry(root, width=7, textvariable=self.var_at)
        self.ent_at.grid(row=r, column=2, sticky="e", padx=(0, 6))
        row[0] = r + 1

        r = row[0]
        ttk.Label(root, text="（应用关闭就不会执行；想让它一直跑，"
                             "可用命令行 scripts\\install_task.bat 注册成系统计划任务）",
                  bootstyle="secondary", font=("Microsoft YaHei UI", 8)).grid(
            row=r, column=0, columnspan=3, sticky="w")
        row[0] = r + 1

        r = row[0]
        self.lbl_sched = ttk.Label(root, textvariable=self.var_sched, bootstyle="info")
        self.lbl_sched.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 8))
        row[0] = r + 1

        r = row[0]
        btns = ttk.Frame(root)
        btns.grid(row=r, column=0, columnspan=3, sticky="ew")
        self.btn_run = ttk.Button(btns, text="立即执行一次", width=12,
                                  bootstyle="success", command=self.run_once)
        self.btn_run.pack(side="left")
        ttk.Button(btns, text="保存配置", width=10, bootstyle="secondary-outline",
                   command=self.save).pack(side="left", padx=6)
        ttk.Button(btns, text="保存并启用自动执行", width=16, bootstyle="primary",
                   command=self.save_and_enable).pack(side="left")
        ttk.Button(btns, text="关闭", width=8, bootstyle="secondary",
                   command=self.close).pack(side="right")
        row[0] = r + 1

        r = row[0]
        ttk.Label(root, text="运行输出", bootstyle="secondary").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(10, 2))
        self.txt = ttk.ScrolledText(root, height=10, wrap="word")
        self.txt.grid(row=r + 1, column=0, columnspan=3, sticky="nsew")
        self.txt.configure(state="disabled")
        root.rowconfigure(r + 1, weight=1)
        self._sync_auto_state()

    def _sync_backup_state(self):
        """没勾选时把备份目录置灰，避免误以为没用上。"""
        on = bool(self.var_backup_on.get())
        state = "normal" if on else "disabled"
        try:
            self.ent_backup.configure(state=state)
            self.btn_backup.configure(state=state)
        except Exception:
            pass

    def _sync_auto_state(self):
        """没勾选时把时间输入框置灰。"""
        state = "normal" if bool(self.var_auto.get()) else "disabled"
        try:
            self.ent_at.configure(state=state)
        except Exception:
            pass

    def _browse(self, var, kind):
        if kind == "file":
            path = filedialog.askopenfilename(
                title="选择设备清单", parent=self.win,
                filetypes=[("设备清单", "*.xlsx *.xls *.csv *.txt"),
                           ("Excel", "*.xlsx *.xls"), ("CSV", "*.csv"),
                           ("文本", "*.txt"), ("全部文件", "*.*")])
        else:
            path = filedialog.askdirectory(title="选择归档根目录", parent=self.win)
        if path:
            var.set(path)

    # ---------- 日志输出 ----------
    def _append(self, text):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__done__":
                    self._finish(item[1])
                    return
                self._append(str(item))
        except queue.Empty:
            pass
        if self._running:
            self.win.after(120, self._poll)

    def _finish(self, code):
        self._running = False
        self.btn_run.configure(state="normal", text="立即执行一次")
        self._append("")
        self._append("—— 结束，退出码 %d ——" % code)
        if code == 0:
            Messagebox.show_info("执行完成，\n结果在项目 output 目录下。", title="完成",
                                 parent=self.win)
        else:
            Messagebox.show_warning("执行结束但有问题，退出码 %d。\n请看下方输出。" % code,
                                    title="未成功", parent=self.win)
        self.refresh_status()

    # ---------- 动作 ----------
    def save(self, silent=False):
        cfg, err = self._gather()
        if err:
            Messagebox.show_warning(err, title="配置不完整", parent=self.win)
            return None
        try:
            path = daily.save_config(cfg)
        except Exception as exc:
            Messagebox.show_error("保存失败：%s" % exc, title="保存失败", parent=self.win)
            return None
        if not silent:
            Messagebox.show_info("已保存到：\n%s" % path, title="已保存", parent=self.win)
        return path

    def save_and_enable(self):
        """保存配置 + 打开「本程序开着时自动执行」。"""
        cfg, err = self._gather()
        if err:
            Messagebox.show_warning(err, title="配置不完整", parent=self.win)
            return
        path = self.save(silent=True)
        if path is None:
            return
        self.var_auto.set(True)
        self._sync_auto_state()
        if self.scheduler is None:
            Messagebox.show_info("配置已保存。\n（当前版本没有内置调度器，请用命令行）",
                                 title="已保存", parent=self.win)
            return
        try:
            self.scheduler.save_settings(True, cfg.get("daily_time", "08:30"))
            self.refresh_status()
            nxt = self.scheduler.next_run_at()
            Messagebox.show_info(
                "已启用自动执行。\n\n下次执行：%s\n（程序保持打开即可，关闭后不会执行）"
                % (nxt.strftime("%m-%d %H:%M") if nxt else "-"),
                title="已启用", parent=self.win)
        except Exception as exc:
            Messagebox.show_error("启用失败：%s" % exc, title="启用失败", parent=self.win)

    def run_once(self):
        """立刻跑一次：优先用应用内调度器（和定时执行走同一条路径）。"""
        if self._running:
            return
        path = self.save(silent=True)
        if path is None:
            return
        self._running = True
        self.btn_run.configure(state="disabled", text="执行中…")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        self._append("开始执行：%s" % path)

        if self.scheduler is not None:
            # 调度器的输出会转到本窗口（主窗口把它加进了 _sched_log_targets）
            self._append("（由应用内调度器执行，日志实时显示在这里）")
            try:
                if not self.scheduler.run_now(force=True):
                    self._running = False
                    self.btn_run.configure(state="normal", text="立即执行一次")
                    self._append("上一次执行还没结束")
                return
            except Exception as exc:
                self._append("调度器启动失败：%s" % exc)
                self._running = False
                self.btn_run.configure(state="normal", text="立即执行一次")
                return

        self._run_subprocess(path)

    def _run_subprocess(self, path):
        """没有内置调度器时，退回直接起子进程。"""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, "-u", str(SCRIPTS / "daily_crc.py"),
               "--config", str(path), "--force"]

        def worker():
            try:
                self._proc = subprocess.Popen(
                    cmd, cwd=str(BASE_DIR), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=0x08000000)          # CREATE_NO_WINDOW
                for line in self._proc.stdout:
                    self._q.put(line.rstrip())
                self._proc.wait()
                self._q.put(("__done__", self._proc.returncode))
            except Exception as exc:
                self._q.put("启动失败：%s" % exc)
                self._q.put(("__done__", -1))

        threading.Thread(target=worker, daemon=True).start()
        self.win.after(150, self._poll)

    def refresh_status(self):
        """显示自动执行状态（下次时间 / 上次结果）。"""
        if self.scheduler is None:
            self.var_sched.set("（当前版本没有内置调度器）")
            return
        try:
            nxt = self.scheduler.next_run_at()
            when = nxt.strftime("%m-%d %H:%M") if nxt else "-"
            last = self.scheduler.last or {}
            if last:
                flag = "成功" if last.get("code") == 0 else "失败(退出码 %s)" % last.get("code")
                txt = "下次执行：%s　　上次执行：%s %s" % (
                    when, last.get("at", "-"), flag)
            else:
                txt = "下次执行：%s　　（还没有执行记录）" % when
            if not self.scheduler.enabled:
                txt = "未启用自动执行　｜　" + txt
            self.var_sched.set(txt)
        except Exception as exc:
            self.var_sched.set("状态读取失败：%s" % exc)

    def close(self):
        if self._running and self._proc is not None:
            if not Messagebox.yesno("任务还在跑，确定关闭窗口吗？（任务会继续）",
                                    title="确认关闭", parent=self.win):
                return
        try:
            targets = getattr(self.master, "_sched_log_targets", None)
            if targets is not None and self.txt in targets:
                targets.remove(self.txt)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


def open_dialog(master, scheduler=None):
    """打开（或聚焦已有的）设置窗口。"""
    old = getattr(master, "_auto_task_dlg", None)
    if old is not None:
        try:
            old.win.deiconify()
            old.win.lift()
            old.win.focus_force()
            return old
        except Exception:
            pass
    dlg = AutoTaskDialog(master, scheduler=scheduler)
    try:
        master._auto_task_dlg = dlg
        # 把输出框登记到主窗口：调度器的日志会实时转过来
        targets = getattr(master, "_sched_log_targets", None)
        if targets is not None and dlg.txt not in targets:
            targets.append(dlg.txt)
    except Exception:
        pass
    return dlg
