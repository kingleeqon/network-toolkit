# -*- coding: utf-8 -*-
"""任务上下文 TaskContext（对应 PRD FR-115 / 15.6.2）。

工具通过它获取输出目录、取消信号、设备数据、凭证与连接能力。
run_parallel 内置并发、取消检查与异常隔离，工具无需自己写线程池。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional

from core.errors import map_fail_reason, status_text
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

    # —— 消息投递 ——
    def emit(self, type_, payload):
        self._emit(type_, payload)

    def log(self, msg, level="INFO", ip=None):
        payload = {"level": level, "msg": msg}
        if ip:
            payload["ip"] = ip
        self.emit(MsgType.LOG, payload)

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
        if total == 0:
            self.emit(MsgType.PROGRESS, {"current": 0, "total": 0})
            return []

        columns = result_columns or (self._tool.result_columns if self._tool else None)
        done = 0
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {}
            for it in items:
                futures[ex.submit(worker, it)] = it

            for fut in as_completed(futures):
                item = futures[fut]
                if self.cancel_event.is_set():
                    for f in futures:
                        if not f.done():
                            f.cancel()

                if fut.cancelled():
                    self._emit_result(item, None, ItemStatus.SKIPPED, columns)
                    results.append((item, ItemStatus.SKIPPED))
                else:
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
                        self.log("%s 执行失败：%s: %s" % (item_label(item), type(exc).__name__, exc),
                                 level="ERROR", ip=getattr(item, "ip", None))
                        self._emit_result(item, None, status, columns)
                        results.append((item, status))

                done += 1
                self.emit(MsgType.PROGRESS, {"current": done, "total": total})

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
