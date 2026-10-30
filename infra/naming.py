# -*- coding: utf-8 -*-
"""归档文件名规则（采集类工具共用）。

每日采集把结果按 `<根目录>/<YYYY-MM-DD>/<设备名>.<后缀>` 归档，
人工整理的日志目录也是这个结构，两边要完全一致，所以规则收在一处。
"""
from datetime import datetime

# Windows 文件名非法字符（设备名里可能出现 SW/1F/CORE 这种）
BAD_CHARS = ('/', '\\', ':', '*', '?', '"', '<', '>', '|')


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


def day_dir(root, day=None):
    """返回 <root>/<YYYY-MM-DD>，并确保存在。"""
    from pathlib import Path
    folder = Path(root) / (day or datetime.now().strftime("%Y-%m-%d"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder
