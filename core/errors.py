# -*- coding: utf-8 -*-
"""异常分类与失败原因映射（对应 PRD 9.3.1）。

判定顺序：netmiko → paramiko → 标准库 → 回显特征兜底。
netmiko 底层依赖 paramiko，异常会嵌套，故必须按此顺序逐层判定。
"""
import re
from typing import Optional
from core.message import ItemStatus, STATUS_TEXT


class ParseError(Exception):
    """回显解析失败（自研异常）。"""


class DependencyMissing(Exception):
    """依赖库缺失（如未安装 netmiko / paramiko / openpyxl）。"""


class ConfigError(Exception):
    """参数/环境不满足运行条件（如没选目录、目录不存在）。

    这类错误是「用户还没配好」，不是代码异常：引擎只记一句中文理由，
    不打 traceback，但任务状态仍记 FAILED（不能当成正常完成）。
    """


class CredentialError(Exception):
    """凭证缺失或解密失败。"""


# 回显特征兜底：某些设备不抛异常，只返回提示文本
_AUTH_HINTS = (
    "access denied",
    "authentication failed",
    "login failed",
    "permission denied",
    "bad secrets",
    "认证失败",
    "拒绝",
)


def classify_by_text(text):
    """按回显文本判断是否为认证失败。命中返回 FAIL_AUTH，否则 None。"""
    if not text:
        return None
    low = text.lower()
    for hint in _AUTH_HINTS:
        if hint in low:
            return ItemStatus.FAIL_AUTH
    return None


def map_fail_reason(exc):
    """把异常映射为设备项状态枚举。未命中一律 FAIL_UNKNOWN。"""
    cls_name = type(exc).__name__
    mod = getattr(type(exc), "__module__", "") or ""

    # 1) netmiko 层
    if "netmiko" in mod:
        if cls_name in ("NetmikoTimeoutException", "ReadTimeout"):
            return ItemStatus.FAIL_TIMEOUT
        if cls_name == "NetmikoAuthenticationException":
            return ItemStatus.FAIL_AUTH
        if cls_name == "ConfigInvalidException":
            return ItemStatus.FAIL_UNKNOWN
        return ItemStatus.FAIL_UNKNOWN

    # 2) paramiko 层
    if "paramiko" in mod:
        if cls_name == "AuthenticationException":
            return ItemStatus.FAIL_AUTH
        if cls_name in ("NoValidConnectionsError", "SSHException"):
            return ItemStatus.FAIL_TIMEOUT if cls_name == "NoValidConnectionsError" else ItemStatus.FAIL_UNKNOWN
        return ItemStatus.FAIL_UNKNOWN

    # 3) 标准库层
    if cls_name in ("socket.timeout", "TimeoutError", "timeout"):
        return ItemStatus.FAIL_TIMEOUT
    if cls_name in ("ConnectionRefusedError", "ConnectionResetError", "OSError", "socket.error", "gaierror"):
        return ItemStatus.FAIL_TIMEOUT
    if cls_name == "EOFError":
        return ItemStatus.FAIL_UNKNOWN
    if cls_name == "UnicodeDecodeError":
        return ItemStatus.FAIL_PARSE
    if cls_name in ("PermissionError", "FileNotFoundError", "IsADirectoryError"):
        return ItemStatus.FAIL_OUTPUT

    # 4) 自研异常
    if isinstance(exc, ParseError):
        return ItemStatus.FAIL_PARSE
    if isinstance(exc, DependencyMissing):
        return ItemStatus.FAIL_UNKNOWN
    if isinstance(exc, CredentialError):
        return ItemStatus.FAIL_AUTH

    return ItemStatus.FAIL_UNKNOWN


def status_text(status):
    """状态枚举 → 结果表中文文案。"""
    return STATUS_TEXT.get(status, "未知错误")


# 参数快照/审计里要过滤的敏感字段名（小写子串匹配）
SENSITIVE_KEY_WORDS = ("password", "passwd", "pwd", "secret", "token", "credential")


def is_sensitive_key(key):
    """参数名是否敏感（password/pwd/token/credential 等）。"""
    k = str(key or "").lower()
    return any(w in k for w in SENSITIVE_KEY_WORDS)


# 异常文本里的「key=value / key: value」凭证片段（如 password=xxx、token: abc）
_REDACT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|credential)\b(\s*[=:：]\s*)(\S+)")


def redact_text(text):
    """掩码文本里的凭证片段，防止异常原文/日志落盘泄露。"""
    if not text:
        return text
    return _REDACT_PATTERN.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), str(text))
