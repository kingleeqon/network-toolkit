# -*- coding: utf-8 -*-
"""应用内定时执行器（**不是** Windows 计划任务）。

目标：应用开着的时候，到点自己跑一次每日流水线
（采集接口数据 → 备份设备配置 → CRC 对比），不用手工点，也不用去系统里注册。

设计要点：
- **每 30 秒轮询一次当前时间**：改系统时间、休眠唤醒都能纠正，不会漏跑也不会连着跑；
- **一天只跑一次**：用 `output/.last_sched_run.json` 记日期，失败也算"跑过了"，
  避免每 30 秒重试一次把设备刷爆；想重跑由用户手动触发；
- **执行放在子进程**（`scripts/daily_crc.py`）：流水线崩了/卡住都不影响界面；
- 输出通过回调抛给界面，状态栏能显示"下次执行 / 上次结果"。
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = BASE_DIR / "scripts"
DEFAULT_CONFIG = SCRIPTS / "daily_config.json"
RUN_MARKER = BASE_DIR / "output" / ".last_sched_run.json"
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


class DailyScheduler(object):
    """每天到点自动跑一次每日流水线。"""

    def __init__(self, config_path=None, on_log=None, on_status=None):
        self.config_path = Path(config_path or DEFAULT_CONFIG)
        self.on_log = on_log or (lambda line: None)
        self.on_status = on_status or (lambda info: None)
        self.enabled = False
        self.at = "08:30"
        self.running = False
        self.last = None            # {"at": str, "code": int, "ok": bool}
        self._proc = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.load_settings()

    # ---------- 配置 ----------
    def _read_cfg(self):
        try:
            with open(str(self.config_path), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def load_settings(self):
        cfg = self._read_cfg()
        self.enabled = bool(cfg.get("auto_enabled", False))
        at = parse_at(cfg.get("daily_time", "08:30"))
        self.at = format_at(*at) if at else "08:30"
        self.last = self._read_marker()
        return cfg

    def save_settings(self, enabled, at):
        """把「是否自动执行 + 几点执行」写回配置文件（保留其它键）。"""
        hhmm = parse_at(at)
        if hhmm is None:
            raise ValueError("时间格式应为 HH:MM（00:00 ~ 23:59）")
        cfg = self._read_cfg()
        cfg["auto_enabled"] = bool(enabled)
        cfg["daily_time"] = format_at(*hhmm)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.config_path), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.enabled = bool(enabled)
        self.at = format_at(*hhmm)
        self._emit_status()
        return self.config_path

    # ---------- 运行记录 ----------
    def _read_marker(self):
        try:
            with open(str(RUN_MARKER), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_marker(self, code):
        payload = {"date": datetime.now().strftime(DAY_KEY),
                   "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "code": int(code)}
        try:
            RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
            with open(str(RUN_MARKER), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.last = payload

    def ran_today(self):
        """今天是否已经尝试执行过（失败也算，避免 30 秒一次的重试风暴）。"""
        mark = self._read_marker()
        return bool(mark and mark.get("date") == datetime.now().strftime(DAY_KEY))

    # ---------- 时间 ----------
    def target_today(self):
        hh, mm = parse_at(self.at) or (8, 30)
        now = datetime.now()
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def next_run_at(self):
        """返回下次自动执行的时间；没启用返回 None。"""
        if not self.enabled:
            return None
        target = self.target_today()
        now = datetime.now()
        if self.ran_today():
            return target + timedelta(days=1)
        if now >= target:
            return target          # 已到点但还没跑，马上就会跑
        return target

    def status_text(self):
        """给状态栏/设置窗口用的一行状态。"""
        if not self.enabled:
            return "每日自动：未启用"
        nxt = self.next_run_at()
        when = nxt.strftime("%m-%d %H:%M") if nxt else "-"
        if self.running:
            return "每日自动：正在执行…（原定 %s）" % when
        last = self.last or {}
        if last and last.get("date") == datetime.now().strftime(DAY_KEY):
            flag = "成功" if last.get("code") == 0 else "失败(退出码 %s)" % last.get("code")
            return "每日自动：下次 %s　今天已执行：%s" % (when, flag)
        return "每日自动：下次 %s" % when

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._emit_status()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(CHECK_INTERVAL)
        # 退出时如果还有子进程在跑，不强杀（让它把活干完），只断开

    def _tick(self):
        if not self.enabled or self.running:
            return
        if self.ran_today():
            return
        if datetime.now() >= self.target_today():
            self.run_now()

    # ---------- 执行 ----------
    def run_now(self, force=True):
        """立刻跑一次流水线（不判断时间）。返回 True 表示已启动。"""
        with self._lock:
            if self.running:
                return False
            self.running = True
        cmd = [sys.executable, "-u", str(SCRIPTS / "daily_crc.py"),
               "--config", str(self.config_path)]
        if force:
            cmd.append("--force")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"

        def worker():
            code = -1
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(BASE_DIR), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=0x08000000)       # CREATE_NO_WINDOW
                self._proc = proc
                for line in proc.stdout:
                    self.on_log(line.rstrip())
                code = proc.wait()
            except Exception as exc:
                self.on_log("启动失败：%s" % exc)
            finally:
                self._proc = None
                with self._lock:
                    self.running = False
                self._write_marker(code)
                self._emit_status()

        self.on_log("开始执行每日流水线：%s" % " ".join(cmd[1:]))
        threading.Thread(target=worker, daemon=True).start()
        self._emit_status()
        return True

    def _emit_status(self):
        try:
            self.on_status(self.status_text())
        except Exception:
            pass
