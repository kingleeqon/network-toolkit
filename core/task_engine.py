# -*- coding: utf-8 -*-
"""任务执行引擎（对应 PRD FR-106 / FR-109）。

模型：工具跑在 Worker 线程 → emit 投递到 Queue → 主线程 poll() 消费后更新 UI。
红线：Worker 线程内禁止操作 tkinter 控件。
"""
import logging
import threading
import uuid
from datetime import datetime
from queue import Empty, Queue
from typing import Callable, Dict, List, Optional

from core.errors import is_sensitive_key, map_fail_reason, redact_text
from core.message import DRAIN_LIMIT_PER_TICK, ItemStatus, MsgType, STATUS_TEXT
from core.result_writer import create_task_dirs, write_rows, write_summary
from core.task_context import TaskContext

logger = logging.getLogger(__name__)

IDLE = "IDLE"
RUNNING = "RUNNING"
CANCELLING = "CANCELLING"
SUCCEEDED = "SUCCEEDED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

TERMINAL_STATES = (SUCCEEDED, PARTIAL, FAILED, CANCELLED)


class TaskEngine:
    def __init__(self, output_base="output", app_version="0.1.0", operator=""):
        self.queue = Queue()
        self.state = IDLE
        self.thread = None
        self.cancel_event = threading.Event()
        self.ctx = None
        self.tool = None
        self.params = {}
        self.task_id = ""
        self.output_dir = None
        self.columns = []
        self.rows = []
        self.row_statuses = []   # 与 rows 一一对应的状态（回填结果表要配色）
        self.stats = {"total": 0, "success": 0, "partial": 0, "fail": 0, "skipped": 0}
        self.fail_reason_count = {}
        self.output_base = output_base
        self.app_version = app_version
        self.operator = operator
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._started_at = None
        self._errored = False

    # —— 生命周期 ——
    def start(self, tool, params):
        if self.state == RUNNING:
            raise RuntimeError("已有任务在执行")
        self.tool = tool
        self.params = params
        self.task_id = uuid.uuid4().hex
        self.columns = list(getattr(tool, "result_columns", []))
        self.rows = []
        self.row_statuses = []   # 与 rows 一一对应的状态（回填结果表要配色）
        self.stats = {"total": 0, "success": 0, "partial": 0, "fail": 0, "skipped": 0}
        self.fail_reason_count = {}
        self.cancel_event = threading.Event()
        self._closed = threading.Event()
        self._started_at = datetime.now()
        self._errored = False

        self.output_dir = create_task_dirs(self.output_base, tool.id, self.task_id)
        self.ctx = TaskContext(self.task_id, self.output_dir, self._emit, logger, self.cancel_event)
        self.ctx._tool = tool

        self.state = RUNNING
        self.thread = threading.Thread(target=self._run, name="task-worker", daemon=True)
        self.thread.start()
        return self.task_id

    def cancel(self):
        if self.state in (RUNNING, CANCELLING):
            self.state = CANCELLING
            self.cancel_event.set()
            self._emit(MsgType.LOG, {"level": "WARN", "msg": "已下发中止指令，等待在途设备返回…"})
            return True
        return False

    def is_running(self):
        return self.state in (RUNNING, CANCELLING)

    # —— 消息 ——
    def _emit(self, type_, payload):
        """工具侧调用（可能来自多个工作线程），线程安全。未知类型丢弃并告警。"""
        try:
            t = MsgType(type_)
        except ValueError:
            logger.warning("未知消息类型，已丢弃：%s", type_)
            self.queue.put({"type": MsgType.LOG,
                            "payload": {"level": "WARN", "msg": "未知消息类型已丢弃：%s" % type_}})
            return

        if t == MsgType.RESULT:
            with self._lock:
                self.rows.append(list(payload.get("row", [])))
                status = payload.get("status")
                status_value = getattr(status, "value", status)
                self.row_statuses.append(status_value)
                self.stats["total"] += 1
                if status_value == ItemStatus.OK.value:
                    self.stats["success"] += 1
                elif status_value == ItemStatus.PARTIAL_OK.value:
                    # 部分成功：数据拿到了，但有附带问题（如配置备份失败）。
                    # 不算失败（设备数据本身是好的），但要单独计数，
                    # 否则整体状态会被误判成「有设备失败」
                    self.stats["partial"] += 1
                elif status_value == ItemStatus.SKIPPED.value:
                    self.stats["skipped"] += 1
                else:
                    self.stats["fail"] += 1
                    self.fail_reason_count[status_value] = self.fail_reason_count.get(status_value, 0) + 1
        if t == MsgType.DONE:
            self._closed.set()
        self.queue.put({"type": t, "payload": payload})

    def poll(self, handler, limit=DRAIN_LIMIT_PER_TICK):
        """主线程调用：drain 队列并交给 handler(msg_dict)。返回本轮处理条数。"""
        count = 0
        while count < limit:
            try:
                msg = self.queue.get_nowait()
            except Empty:
                break
            # 先终结状态，再回调 handler：保证 handler 处理 DONE 时能读到最终 state
            # （否则 UI 在 DONE 消息里读到的还是 RUNNING，导致表单/按钮无法恢复）
            if msg.get("type") == MsgType.DONE:
                self._on_done(msg.get("payload", {}))
            handler(msg)
            count += 1
        return count

    def _on_done(self, _payload):
        self._finalize()

    # —— 执行体（Worker 线程）——
    def _run(self):
        try:
            self._emit(MsgType.LOG, {"level": "INFO", "msg": "任务开始：%s" % self.tool.name})
            self.tool.run(self.params, self.ctx, self._emit)
        except Exception as exc:
            status = map_fail_reason(exc)
            logger.exception("批次级异常")
            self._errored = True
            self._emit(MsgType.ERROR, {
                "code": "BATCH_ERROR",
                "msg": "任务执行失败：%s" % redact_text(str(exc)),
                "detail": "%s: %s" % (type(exc).__name__, redact_text(str(exc))),
            })
        finally:
            # 工具若已自行发出 done，此处不再重复发送
            if not self._closed.is_set():
                self._emit(MsgType.DONE, {"summary": dict(self.stats)})

    def _finalize(self):
        if self.state in TERMINAL_STATES:
            return
        self._closed.set()
        total = self.stats["total"]
        success = self.stats["success"]
        fail = self.stats["fail"]
        partial = self.stats.get("partial", 0)
        cancelled = self.cancel_event.is_set()

        if cancelled:
            self.state = CANCELLED
        elif self._errored:
            self.state = FAILED
        elif total > 0 and fail == 0 and partial == 0:
            self.state = SUCCEEDED
        elif total > 0 and success == 0 and partial == 0:
            self.state = FAILED
        elif total == 0:
            # 无 result 项（如产图/纯落盘工具）且正常结束 → 已完成
            self.state = SUCCEEDED
        else:
            self.state = PARTIAL

        # 敏感参数（password/pwd/token/credential 等）一律不进审计快照
        params_snapshot = {k: v for k, v in self.params.items()
                           if not is_sensitive_key(k)}
        summary = {
            "task_id": self.task_id,
            "tool_id": self.tool.id,
            "tool_version": getattr(self.tool, "version", ""),
            "api_version": getattr(self.tool, "api_version", "1.0"),
            "operator": self.operator,
            "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else "",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "status": self.state,
            "stats": dict(self.stats),
            "fail_reason_count": dict(self.fail_reason_count),
            "params": params_snapshot,
            "result_file": "result.xlsx",
            "app_version": self.app_version,
        }
        try:
            write_summary(self.output_dir, summary)
            # 结果行也落盘：定时任务是子进程，行不会回到界面
            write_rows(self.output_dir, self.columns, self.rows,
                       self.row_statuses)
            self._write_audit(summary)
        except Exception as exc:
            self._emit(MsgType.LOG, {"level": "ERROR", "msg": "结果落盘失败：%s" % exc})
        self._emit(MsgType.LOG, {
            "level": "INFO",
            "msg": "任务结束：%s（成功 %d / 失败 %d / 跳过 %d）" % (self.state, success, fail, self.stats["skipped"]),
        })

    def _write_audit(self, summary):
        from core.result_writer import app_data_dir
        import json
        path = app_data_dir() / "audit.log"
        record = {
            "ts": summary["finished_at"],
            "operator": summary["operator"],
            "tool_id": summary["tool_id"],
            "tool_version": summary["tool_version"],
            "total": summary["stats"]["total"],
            "success": summary["stats"]["success"],
            "fail": summary["stats"]["fail"],
            "status": summary["status"],
            "output_dir": str(self.output_dir),
        }
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
