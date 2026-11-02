# -*- coding: utf-8 -*-
"""归档文件名规则（采集类工具共用）。

每日采集把结果按 `<根目录>/<YYYY-MM-DD>/<设备名>.<后缀>` 归档，
人工整理的日志目录也是这个结构，两边要完全一致，所以规则收在一处。
"""
from datetime import datetime
import threading

# Windows 文件名非法字符（设备名里可能出现 SW/1F/CORE 这种）
BAD_CHARS = ('/', '\\', ':', '*', '?', '"', '<', '>', '|')

# 失败清单是多个 worker 并发追加的，要串行写
_ERROR_LOG_LOCK = threading.Lock()


def safe_filename(name, fallback="unknown"):
    """把设备名转成合法文件名：只替换非法字符，尽量保留原名。"""
    out = (name or "").strip() or fallback
    for ch in BAD_CHARS:
        out = out.replace(ch, "_")
    # Windows 不允许以点或空格结尾
    return out.strip(" .") or fallback


def device_stem(device):
    """设备对应的文件名（不含扩展名）：优先设备名，没有设备名才退回 IP。"""
    return safe_filename(getattr(device, "hostname", "") or getattr(device, "ip", ""))


def write_text(path, text):
    """把设备回显写成 Windows 文本文件（CRLF 行尾），行尾与现场脚本产出一致。

    经典记事本只认 CRLF 换行：LF-only 的文件会整个挤成一行显示。
    netmiko 把行尾归一成 \\n、paramiko 原始是 \\r\\n——先统一成 \\n 再翻译成
    CRLF，避免混合来源写出 \\r\\r\\n。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    with open(str(path), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)


def record_device_failure(folder, device, reason):
    """把一台设备的失败追加到 <folder>/error_log.txt，返回文件路径。

    采集失败（登录不上、超时等）的设备**不会**生成 .log/.cfg——半截文件混进
    归档目录会被 CRC 对比当成有效数据。这里单独留一份失败清单（与现场脚本
    netmiko_SSH.py 的 error_log.txt 同款习惯），网管在归档目录就能看到
    哪几台没采到。多个 worker 并发写，用锁串行化。
    """
    if not folder:
        return None
    try:
        from pathlib import Path
        path = Path(folder) / "error_log.txt"
        # 失败设备可能没走到建目录那步，目录还不存在
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ip = getattr(device, "ip", "") or ""
        name = getattr(device, "hostname", "") or ip
        line = "%s - Failed to process %s (%s): %s" % (stamp, name, ip, reason)
        with _ERROR_LOG_LOCK:
            with open(str(path), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return path
    except Exception:
        return None


def day_dir(root, day=None):
    """返回 <root>/<YYYY-MM-DD>，并确保存在。"""
    from pathlib import Path
    folder = Path(root) / (day or datetime.now().strftime("%Y-%m-%d"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder
