# -*- coding: utf-8 -*-
"""结果落盘与导出（对应 PRD FR-112 / 15.6.6）。"""
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def create_task_dirs(base_dir, tool_id, task_id):
    """output/{tool_id}/{YYYYMMDD-HHMMSS}_{task_id前8位}/，并创建 raw 子目录。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(base_dir) / tool_id / ("%s_%s" % (stamp, str(task_id)[:8]))
    (path / "raw").mkdir(parents=True, exist_ok=True)
    return path


def write_summary(task_dir, summary):
    path = Path(task_dir) / "summary.json"
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return path


def write_rows(task_dir, columns, rows, statuses=None):
    """把结果行落到 task_dir/result.json，供界面/定时任务回填结果表。

    GUI 里跑任务时结果行走内存消息，定时任务是子进程，行不会回到界面——
    落一份到磁盘，定时运行结束后界面读回来显示（否则结果表永远是空的）。
    """
    items = []
    for i, row in enumerate(rows or []):
        status = ""
        if statuses and i < len(statuses):
            status = statuses[i] or ""
        items.append({"row": list(row), "status": str(status)})
    path = Path(task_dir) / "result.json"
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump({"columns": list(columns or []), "items": items},
                  f, ensure_ascii=False, indent=2)
    return path


def read_rows(task_dir):
    """读 write_rows 落盘的结果行 → (columns, [(row, status), ...])。

    与 write_rows 成对存在：界面回填定时结果时走这个函数，
    不再自己在 UI 里拼解析逻辑（两端字段名漂移过一次，加测试锁住）。
    """
    import json as _json
    path = Path(task_dir) / "result.json"
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    columns = list(data.get("columns") or [])
    rows = []
    for item in data.get("items") or []:
        rows.append((list(item.get("row") or []), item.get("status") or "OK"))
    return columns, rows


def export_rows(rows, columns, target_path):
    """导出结果表。xlsx 用 openpyxl，缺失则退化为 csv；csv 带 BOM 防 Excel 中文乱码。"""
    target = Path(target_path)
    suffix = target.suffix.lower()
    if suffix == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            target = target.with_suffix(".csv")
            suffix = ".csv"
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "结果"
            if columns:
                ws.append(list(columns))
            for row in rows:
                ws.append(list(row))
            wb.save(str(target))
            return str(target)

    with open(str(target), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if columns:
            writer.writerow(list(columns))
        for row in rows:
            writer.writerow(list(row))
    return str(target)


def app_data_dir():
    """配置/凭证/日志目录（PRD 15.6.9）。

    可用环境变量 NETWORK_TOOLKIT_DATA 覆盖（测试隔离用，也方便绿色版把
    审计日志/凭证放到指定目录，不污染当前用户的 %APPDATA%）。
    """
    override = os.environ.get("NETWORK_TOOLKIT_DATA", "").strip()
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = Path(base) / "NetworkToolkit"
    path.mkdir(parents=True, exist_ok=True)
    return path
