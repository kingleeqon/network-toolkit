# -*- coding: utf-8 -*-
"""多任务定时调度器：每个工具都能各设一个定时点，到点自动执行。

不是 Windows 计划任务——那是命令行备选（`scripts/install_task.bat`）。
这里做的是：**应用开着的时候**，到点自己跑。

设计要点：
- **一条配置一个工具**：`scripts/schedules.json` 里每个 tool_id 一条
  （{"enabled":bool, "at":"HH:MM", "params":{...}, "last":{...}}）；
  复合任务（每日流水线 采集+备份+对比）用特殊 key `daily_pipeline`；
- **执行走子进程**：单工具用 `main.py --cli <tool_id> key=value`，
  流水线用 `scripts/daily_crc.py --config ...`。子进程崩了不影响界面，
  stdout 直接灌进「运行日志」；
- **每 30 秒校一次时钟**：改系统时间、休眠唤醒都不会漏跑；
- **一天只跑一次**：靠 last.date 判断，失败也记，避免 30 秒一次的重试风暴；
- **状态可见**：running 里记着正在跑的任务与开始时间，界面能显示"运行中 xx（已用时 m:ss）"。
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
STORE_PATH = SCRIPTS_DIR / "schedules.json"
DAILY_CONFIG = SCRIPTS_DIR / "daily_config.json"
PIPELINE_KEY = "daily_pipeline"
CHECK_INTERVAL = 30          # 秒
DAY_KEY = "%Y-%m-%d"


def parse_at(text):
    """'8:30' / '08：30' → (8, 30)；非法返回 None。"""
    s = str(text or "").strip().replace("：", ":")
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def format_at(hh, mm):
    return "%02d:%02d" % (hh, mm)


def normalize_at(text, default="08:30"):
    at = parse_at(text)
    return format_at(*at) if at else default


def elapsed_text(seconds):
    """秒 → 'm:ss' 或 'h:mm:ss'。"""
    seconds = int(max(0, seconds))
    if seconds < 3600:
        return "%d:%02d" % (seconds // 60, seconds % 60)
    return "%d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def exit_code_text(code):
    """退出码 → 状态文案。

    与 main.py --cli 约定一致：0=全部成功 2=部分成功（跑完但有失败项）1=失败/异常。
    流水线（daily_crc.py）另有 3=无设备可采。
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "成功" if code == 0 else "失败(%s)" % code
    if code == 0:
        return "成功"
    if code == 2:
        return "部分失败"
    if code == 3:
        return "无设备可采"
    return "失败(退出码 %d)" % code


class SchedulerManager(object):
    """管理所有定时任务。"""

    def __init__(self, store_path=None, on_log=None, on_status=None):
        self.store_path = Path(store_path or STORE_PATH)
        self.on_log = on_log or (lambda line: None)
        self.on_status = on_status or (lambda info: None)
        self.running = {}        # key -> {"started": dt, "title": str}
        self._procs = {}
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # ---------- 存储 ----------
    def _read(self):
        try:
            with open(str(self.store_path), "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _write(self, data):
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self.store_path), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def entries(self):
        """全部定时条目 {key: {...}}。"""
        return self._read()

    def get(self, key):
        return self._read().get(key) or {}

    def set_schedule(self, key, at=None, enabled=None, params=None):
        """设置/更新一个定时任务。"""
        data = self._read()
        item = data.get(key) or {}
        if at is not None:
            item["at"] = normalize_at(at)
        if enabled is not None:
            item["enabled"] = bool(enabled)
        if params is not None:
            item["params"] = dict(params)
        item.setdefault("at", "08:30")
        item.setdefault("enabled", False)
        item.setdefault("params", {})
        data[key] = item
        self._write(data)
        self._emit_status()
        return item

    def remove(self, key):
        data = self._read()
        data.pop(key, None)
        self._write(data)
        self._emit_status()

    def enabled_entries(self):
        return {k: v for k, v in self._read().items() if v.get("enabled")}

    # ---------- 标题与命令 ----------
    def title_of(self, key):
        if key == PIPELINE_KEY:
            return "每日流水线（采集 + 备份 + 对比）"
        try:
            from core.registry import Registry
            reg = Registry(BASE_DIR / "tools")
            reg.discover()
            tool = reg.get(key)
            if tool is not None:
                return tool.name
        except Exception:
            pass
        return key

    def build_cmd(self, key):
        """返回 (cmd, cwd)；没有对应任务返回 (None, None)。"""
        item = self.get(key)
        if key == PIPELINE_KEY:
            return ([sys.executable, "-u", str(SCRIPTS_DIR / "daily_crc.py"),
                     "--config", str(DAILY_CONFIG)], str(BASE_DIR))
        try:
            from core.registry import Registry
            reg = Registry(BASE_DIR / "tools")
            reg.discover()
            tool = reg.get(key)
        except Exception:
            tool = None
        if tool is None:
            return None, None
        cmd = [sys.executable, "-u", str(BASE_DIR / "main.py"), "--cli", key]
        for name, value in (item.get("params") or {}).items():
            if value is None or value == "":
                continue
            cmd.append("%s=%s" % (name, value))
        return cmd, str(BASE_DIR)

    # ---------- 时间与状态 ----------
    def next_run_at(self, key):
        item = self.get(key)
        if not item.get("enabled"):
            return None
        at = parse_at(item.get("at")) or (8, 30)
        now = datetime.now()
        target = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
        if self.ran_today(key) or now >= target:
            return target + timedelta(days=1)
        return target

    def ran_today(self, key):
        last = self.get(key).get("last") or {}
        return last.get("date") == datetime.now().strftime(DAY_KEY)

    def is_running(self, key):
        with self._lock:
            return key in self.running

    def running_text(self):
        """正在运行的任务描述；没有则空串。"""
        with self._lock:
            if not self.running:
                return ""
            out = []
            now = datetime.now()
            for key, info in self.running.items():
                secs = (now - info["started"]).total_seconds()
                out.append("%s（已用时 %s）" % (info.get("title") or key,
                                             elapsed_text(secs)))
            return "　".join(out)

    def status_of(self, key):
        """一行状态文案。"""
        item = self.get(key)
        if self.is_running(key):
            with self._lock:
                info = self.running.get(key) or {}
                started = info.get("started", datetime.now())
            return "运行中　已用时 %s" % elapsed_text(
                (datetime.now() - started).total_seconds())
        if not item.get("enabled"):
            return "未启用"
        nxt = self.next_run_at(key)
        when = nxt.strftime("%m-%d %H:%M") if nxt else "-"
        last = item.get("last") or {}
        if last and last.get("date") == datetime.now().strftime(DAY_KEY):
            flag = exit_code_text(last.get("code"))
            return "下次 %s　今天已执行：%s" % (when, flag)
        return "下次 %s" % when

    # ---------- 执行 ----------
    def run_now(self, key):
        """立即执行一次。返回 (ok, message)。"""
        cmd, cwd = self.build_cmd(key)
        if not cmd:
            return False, "找不到工具「%s」" % key
        with self._lock:
            if key in self.running:
                return False, "「%s」正在执行中" % self.title_of(key)
            self.running[key] = {"started": datetime.now(),
                                 "title": self.title_of(key)}
        title = self.title_of(key)
        self.on_log("▶ 定时任务开始：%s" % title)

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"

        def worker():
            code = -1
            try:
                proc = subprocess.Popen(
                    cmd, cwd=cwd, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=0x08000000)      # CREATE_NO_WINDOW
                self._procs[key] = proc
                for line in proc.stdout:
                    self.on_log(line.rstrip())
                code = proc.wait()
            except Exception as exc:
                self.on_log("定时任务启动失败：%s" % exc)
            finally:
                self._procs.pop(key, None)
                # 先落结果再清 running：调用方看到「不在运行」时 last 必已写入
                self._record_result(key, code)
                with self._lock:
                    self.running.pop(key, None)
                self.on_log("■ 定时任务结束：%s（退出码 %d）" % (title, code))
                self._emit_status()

        threading.Thread(target=worker, daemon=True).start()
        self._emit_status()
        return True, "已开始执行：%s" % title

    def _record_result(self, key, code):
        data = self._read()
        item = data.get(key) or {}
        item["last"] = {"date": datetime.now().strftime(DAY_KEY),
                        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "code": int(code)}
        data[key] = item
        self._write(data)

    def stop_all(self):
        """停掉调度循环（不杀正在跑的子进程，让它跑完）。"""
        self._stop.set()

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._emit_status()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(CHECK_INTERVAL)

    def _tick(self):
        now = datetime.now()
        for key, item in self.enabled_entries().items():
            if self.is_running(key) or self.ran_today(key):
                continue
            at = parse_at(item.get("at")) or (8, 30)
            target = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
            if now >= target:
                self.run_now(key)

    def _emit_status(self):
        try:
            self.on_status(self.running_text())
        except Exception:
            pass
