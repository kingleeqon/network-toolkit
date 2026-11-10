# -*- coding: utf-8 -*-
"""为已有的 CRC对比_*.xlsx 批量补生成紧凑索引 JSON（历史结果秒开）。

界面「结果输出目录」里早先生成的结果没有配套 JSON，双击仍要全量读 Excel
（约 2.4s/份）。跑一次本脚本，全部历史结果即可变成毫秒级打开。
新跑的 CRC 对比会自动带索引，本脚本只需对老文件跑一次。

用法（默认干跑，只列清单不写文件）：
    python scripts/backfill_crc_index.py E:/backups/crc_compare
    python scripts/backfill_crc_index.py E:/backups/crc_compare --apply
    python scripts/backfill_crc_index.py D:/a E:/b --apply --force
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.crc_compare import (CrcCompareTool, _guess_date,
                               build_compact_from_excel, write_compact_json)


def index_is_fresh(xlsx, js):
    """索引存在、版本正确、且不早于 xlsx 才算有效。"""
    try:
        if not js.is_file() or js.stat().st_mtime < xlsx.stat().st_mtime:
            return False
        data = json.loads(js.read_text(encoding="utf-8"))
        return data.get("version") == 1 and bool(data.get("columns"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(
        description="为已有的 CRC对比_*.xlsx 补生成紧凑索引 JSON")
    ap.add_argument("dirs", nargs="+",
                    help="结果输出目录（可多个，即界面里填的「结果输出目录」）")
    ap.add_argument("--apply", action="store_true",
                    help="真的写 JSON（不加只干跑列清单）")
    ap.add_argument("--force", action="store_true",
                    help="已有有效索引也重建（默认跳过）")
    args = ap.parse_args()

    found, planned = 0, []
    for d in args.dirs:
        folder = Path(d)
        if not folder.is_dir():
            print("[WARN] 目录不存在，跳过：%s" % folder)
            continue
        for xlsx in sorted(folder.glob("CRC对比_*.xlsx"), key=lambda p: p.name):
            found += 1
            js = xlsx.with_suffix(".json")
            if index_is_fresh(xlsx, js) and not args.force:
                print("  跳过（索引有效）  %s" % xlsx.name)
                continue
            planned.append((xlsx, js))

    print("----")
    print("扫描 %d 份结果，需生成 %d 份索引；模式：%s"
          % (found, len(planned), "写入" if args.apply else "干跑（未写文件）"))
    if not args.apply:
        for xlsx, js in planned:
            print("  待生成  %s -> %s" % (xlsx.name, js.name))
        if planned:
            print("确认无误后加 --apply 生成。")
        return 0

    done = failed = 0
    started = time.time()
    for i, (xlsx, js) in enumerate(planned, 1):
        t0 = time.time()
        try:
            rows, total, changed = build_compact_from_excel(xlsx)
            write_compact_json(xlsx, CrcCompareTool.result_columns,
                               rows, total, changed, _guess_date(xlsx))
            # 有些机器的 xlsx 是从别处拷贝来的、mtime 在未来；把索引时间戳
            # 顶到 xlsx 之后，保证 load_result_file 的新鲜度判定能通过
            stamp = max(time.time(), xlsx.stat().st_mtime) + 1
            os.utime(str(js), (stamp, stamp))
            done += 1
            print("  [%d/%d] 已生成  %s  %d 行展示 / %d 行变化 / %d 行总量  %.2fs"
                  % (i, len(planned), js.name, len(rows), changed, total,
                     time.time() - t0))
        except Exception as exc:
            failed += 1
            print("  [%d/%d] 失败    %s：%s" % (i, len(planned), xlsx.name, exc),
                  file=sys.stderr)
    print("----")
    print("完成：生成 %d，跳过 %d，失败 %d，耗时 %.1fs"
          % (done, found - len(planned), failed, time.time() - started))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
