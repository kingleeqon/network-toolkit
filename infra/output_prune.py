# -*- coding: utf-8 -*-
"""output/ 目录生命周期管理：清理过期的任务目录。

`output/<tool_id>/<YYYYMMDD-HHMMSS_xxxxxxxx>/` 这种任务目录会随运行次数无限
增长（本机已累积 350+ 个），既占磁盘，也拖慢 crc_history 的目录扫描。

安全边界（很重要）：
- **只删**名字形如 `20261104-103624_c127b317` 的任务目录，其他一律不碰
- **不删任何文件**（summary.json / result.json / .history_cache.pkl / *.cfg 都安全）
- **不删工具根目录**（output/crc_compare 本身永远保留）
- 默认 dry_run：只返回清单，不动磁盘；要真删必须显式传 dry_run=False

保留策略（按工具独立计算）：
- `keep_days` 天以内的全留
- 超过 `keep_days` 的，只保留最近 `keep_min` 个
"""
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# 任务目录名：20261104-103624_c127b317
_TASK_DIR_RE = re.compile(r"^(\d{8})-(\d{6})_[0-9a-z]{6,}$")


def _task_time(name):
    """从任务目录名解析时间；不是任务目录返回 None。"""
    m = _TASK_DIR_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _dir_size(path):
    total = 0
    try:
        for p in Path(path).rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:
        pass
    return total


def prune_output(base, keep_days=30, keep_min=10, dry_run=True, tools=None):
    """清理过期任务目录，返回 (命中列表, 统计)。

    命中列表元素：(目录路径, 大小字节, 是否已删除)。
    tools：只处理指定 tool_id（list/str），None 表示全部。
    """
    base = Path(base)
    stats = {"tools": 0, "scanned": 0, "kept": 0, "removed": 0, "freed": 0}
    hits = []
    if not base.is_dir():
        return hits, stats

    only = None
    if tools:
        only = {tools} if isinstance(tools, str) else set(tools)

    cutoff = datetime.now() - timedelta(days=max(0, int(keep_days)))
    keep_min = max(0, int(keep_min))

    for tool_dir in sorted(base.iterdir()):
        if not tool_dir.is_dir() or tool_dir.name.startswith("_"):
            continue
        if only is not None and tool_dir.name not in only:
            continue
        stats["tools"] += 1

        tasks = []
        for child in tool_dir.iterdir():
            if not child.is_dir():
                continue                      # 文件一律不碰
            when = _task_time(child.name)
            if when is None:
                continue                      # 不是任务目录，跳过
            tasks.append((when, child))
        if not tasks:
            continue

        # 新的在前：前 keep_min 个永远保留
        tasks.sort(key=lambda x: x[0], reverse=True)
        for i, (when, path) in enumerate(tasks):
            stats["scanned"] += 1
            if i < keep_min or when >= cutoff:
                stats["kept"] += 1
                continue
            size = _dir_size(path)
            removed = False
            if not dry_run:
                try:
                    shutil.rmtree(str(path))
                    removed = True
                except Exception:
                    removed = False
            if removed:
                stats["removed"] += 1
                stats["freed"] += size
            hits.append((path, size, removed))
    return hits, stats


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n
