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
    """配置/凭证/日志目录（PRD 15.6.9）。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = Path(base) / "NetworkToolkit"
    path.mkdir(parents=True, exist_ok=True)
    return path
