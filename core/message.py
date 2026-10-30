# -*- coding: utf-8 -*-
"""消息契约（对应 PRD FR-114 / 15.6.1）。

工具只允许通过 emit(type, payload) 与框架通信。
Python 3.8 兼容：类型注解一律使用 typing。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import time


class MsgType(str, Enum):
    LOG = "log"
    PROGRESS = "progress"
    RESULT = "result"
    CHART = "chart"      # 图表（matplotlib Figure 对象），主线程渲染到内嵌画布
    ERROR = "error"
    DONE = "done"


class LogLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class ItemStatus(str, Enum):
    """设备项状态枚举（PRD 9.3）。"""
    PENDING = "PENDING"
    CONNECTING = "CONNECTING"
    EXECUTING = "EXECUTING"
    OK = "OK"
    PARTIAL_OK = "PARTIAL_OK"
    FAIL_TIMEOUT = "FAIL_TIMEOUT"
    FAIL_AUTH = "FAIL_AUTH"
    FAIL_PARSE = "FAIL_PARSE"
    FAIL_OUTPUT = "FAIL_OUTPUT"
    FAIL_UNKNOWN = "FAIL_UNKNOWN"
    SKIPPED = "SKIPPED"


# 状态 → 结果表中文展示（PRD 9.3「中文展示」列）
STATUS_TEXT = {
    ItemStatus.PENDING: "等待中",
    ItemStatus.CONNECTING: "连接中",
    ItemStatus.EXECUTING: "执行中",
    ItemStatus.OK: "成功",
    ItemStatus.PARTIAL_OK: "部分成功",
    ItemStatus.FAIL_TIMEOUT: "连接超时",
    ItemStatus.FAIL_AUTH: "认证失败",
    ItemStatus.FAIL_PARSE: "解析失败",
    ItemStatus.FAIL_OUTPUT: "结果写入失败",
    ItemStatus.FAIL_UNKNOWN: "未知错误",
    ItemStatus.SKIPPED: "已跳过",
}

# 主线程每轮 drain 上限（PRD EH-16）
DRAIN_LIMIT_PER_TICK = 500


@dataclass
class Message:
    type: MsgType
    payload: Dict[str, Any]
    ts: float = field(default_factory=time.time)

    def __repr__(self):
        return "Message(%s, %s)" % (self.type.value, self.payload)
