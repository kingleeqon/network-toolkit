# -*- coding: utf-8 -*-
"""任务上下文 TaskContext（对应 PRD FR-115 / 15.6.2）。

工具通过它获取输出目录、取消信号、设备数据、凭证与连接能力。
run_parallel 内置并发、取消检查与异常隔离，工具无需自己写线程池。
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional

from core.errors import map_fail_reason, redact_text, status_text
from core.message import ItemStatus, MsgType


class TaskContext:
    def __init__(self, task_id, output_dir, emit, logger, cancel_event=None):
        self.task_id = task_id
        self.output_dir = Path(output_dir)
        self.raw_dir = self.output_dir / "raw"
        self.cancel_event = cancel_event or Event()
        self.logger = logger
        self._emit = emit
        self._tool = None          # 由引擎注入，用于取 result_columns
        self._devices_cache = {}   # path -> List[Device]
        # 运行日志落盘：run.log 全量、error.log 只有 WARN/ERROR
        self._run_log = None
        self._err_log = None
        self._total = 0
        self._done = 0

    # —— 消息投递 ——
    def emit(self, type_, payload):
        self._emit(type_, payload)

    def log(self, msg, level="INFO", ip=None):
        payload = {"level": level, "msg": msg}
        if ip:
            payload["ip"] = ip
        self.emit(MsgType.LOG, payload)
        self._write_log(level, msg, ip)

    def _write_log(self, level, msg, ip=None):
        """写运行日志文件。

        界面里的「运行日志」是内存态，刷新就没了；采集这类任务动辄几十分钟，
        必须留一份可回查的文本。run.log 记全部，error.log 只记 WARN/ERROR。
        """
        try:
            from datetime import datetime
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = "%s [%s]%s %s\n" % (stamp, level,
                                        (" [%s]" % ip) if ip else "", msg)
            if self._run_log is None:
                self._run_log = open(str(self.output_dir / "run.log"), "a",
                                     encoding="utf-8")
            self._run_log.write(line)
            self._run_log.flush()
            if level.upper() in ("WARN", "WARNING", "ERROR"):
                if self._err_log is None:
                    self._err_log = open(str(self.output_dir / "error.log"), "a",
                                         encoding="utf-8")
                self._err_log.write(line)
                self._err_log.flush()
        except Exception as exc:
            # 落盘失败不能吞：界面日志还在，这里降级记录到 stderr 便于排查
            import sys
            print("[WARN] 运行日志写入失败：%s" % exc, file=sys.stderr)

    def report_current(self, label):
        """上报"当前正在处理的条目"，界面进度区实时显示（不刷日志）。"""
        try:
            self.emit(MsgType.PROGRESS, {"current": self._done, "total": self._total,
                                         "item": label})
        except Exception:
            pass

    # —— 设备清单 ——
    def load_devices(self, param_value):
        """param_value 为文件路径（str）。同一路径在一次任务内只解析一次。"""
        from infra.device_loader import load_devices as _load
        key = str(param_value)
        if key not in self._devices_cache:
            devices, errors = _load(param_value)
            self._devices_cache[key] = devices
            for e in errors:
                self.log("清单异常：%s" % e, level="WARN")
            self.log("已加载设备 %d 台，异常 %d 行" % (len(devices), len(errors)))
        return self._devices_cache[key]

    # —— 凭证 ——
    def get_credential(self, ref):
        from infra.credential_store import get_credential
        return get_credential(ref)

    # —— 连接层（双通道，对工具屏蔽差异）——
    def open_session(self, device, timeout=30, paging_cmd=None):
        """建立会话。paging_cmd：登录后要发的"关闭分屏"命令（可为空）。"""
        from infra.connector import open_session
        return open_session(device, timeout=timeout, ctx=self, paging_cmd=paging_cmd)

    # —— 并发执行（内置取消与异常隔离）——
    def run_parallel(self, items, worker, result_columns=None, max_workers=10, item_label=lambda x: getattr(x, "ip", str(x))):
        """并发处理 items，逐项 emit result 与 progress。

        worker(item) -> row(list) 或 (row, status)；抛异常时由框架归类失败原因。
        """
        total = len(items)
        self._total = total
        self._done = 0
        if total == 0:
            self.emit(MsgType.PROGRESS, {"current": 0, "total": 0, "item": ""})
            return []

        columns = result_columns or (self._tool.result_columns if self._tool else None)
        done = 0
        results = []
        emitted = set()   # 已出结果的 future，收尾补账时跳过

        def _consume(fut, item):
            """消费一个 future 的结果（成功/失败都转成 result 行）。"""
            if fut.cancelled():
                self._emit_result(item, None, ItemStatus.SKIPPED, columns)
                results.append((item, ItemStatus.SKIPPED))
                return
            try:
                out = fut.result()
                status = ItemStatus.OK
                row = out
                if isinstance(out, tuple) and len(out) == 2:
                    row, status = out
                self._emit_result(item, row, status, columns)
                results.append((item, status))
            except Exception as exc:  # 设备级异常隔离
                status = map_fail_reason(exc)
                self.log("%s 执行失败：%s: %s" % (item_label(item), type(exc).__name__,
                                                 redact_text(str(exc))),
                         level="ERROR", ip=getattr(item, "ip", None))
                self._emit_result(item, None, status, columns)
                results.append((item, status))

        # 不用 with：退出时 shutdown(wait=True) 会把取消后的收尾拖到所有
        # 在途设备超时结束（最坏几百秒），界面一直卡「中止中」。
        ex = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {ex.submit(worker, it): it for it in items}
            pending = set(futures)
            try:
                while pending:
                    if self.cancel_event.is_set():
                        for f in pending:
                            f.cancel()
                        break        # 立即收尾，不再等剩余任务
                    # wait 带超时轮询：as_completed 会阻塞到第一个任务完成
                    # 才回到循环体，长超时任务在途时取消信号根本看不到
                    done_set, pending = wait(pending, timeout=0.5,
                                             return_when=FIRST_COMPLETED)
                    for fut in done_set:
                        item = futures[fut]
                        _consume(fut, item)
                        emitted.add(fut)
                        done += 1
                        self._done = done
                        self.emit(MsgType.PROGRESS, {"current": done, "total": total,
                                                     "item": item_label(item)})
            finally:
                # 补账：结果条数与 items 对齐——
                # 已跑完没消费的补发真实结果；没跑的补 SKIPPED
                for fut, item in futures.items():
                    if fut in emitted:
                        continue
                    if fut.done() and not fut.cancelled():
                        _consume(fut, item)
                    else:
                        self._emit_result(item, None, ItemStatus.SKIPPED, columns)
                        results.append((item, ItemStatus.SKIPPED))
        finally:
            # wait=False：在途设备由各自的超时自然结束，任务状态立即收尾；
            # 未启动的 future 上面已手动 cancel
            ex.shutdown(wait=False)

        return results

    def _emit_result(self, item, row, status, columns):
        payload = {"status": status.value if isinstance(status, ItemStatus) else str(status)}
        payload["fail_reason"] = status_text(status) if status != ItemStatus.OK else ""

        if row is None:
            row = self._fallback_row(item, columns)
        if columns and len(row) != len(columns):
            self.log("结果行列数不匹配（期望 %d，实际 %d），已丢弃该行" % (len(columns), len(row)),
                     level="ERROR", ip=getattr(item, "ip", None))
            return
        row = list(row)
        # 「状态」「失败原因」两列由框架统一填中文文案，工具无需关心
        for i, col in enumerate(columns or []):
            if i >= len(row):
                break
            if col == "状态":
                row[i] = status_text(status)
            elif col == "失败原因" and status not in (ItemStatus.OK, ItemStatus.PARTIAL_OK):
                row[i] = status_text(status)
        payload["row"] = row
        self.emit(MsgType.RESULT, payload)

    @staticmethod
    def _fallback_row(item, columns):
        """异常时构造基础行：ip/hostname 有值，其余填 '-'。"""
        base = [getattr(item, "ip", "-"), getattr(item, "hostname", "") or "-"]
        if not columns:
            return base
        row = list(base)
        while len(row) < len(columns):
            row.append("-")
        return row[:len(columns)] if columns else row
