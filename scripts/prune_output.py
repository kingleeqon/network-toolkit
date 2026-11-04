# -*- coding: utf-8 -*-
"""清理 output/ 下过期的任务目录。

用法（默认干跑，只列清单不动磁盘）：
    python scripts/prune_output.py
    python scripts/prune_output.py --apply                 # 真删
    python scripts/prune_output.py --days 30 --keep-min 10
    python scripts/prune_output.py --tool crc_trend        # 只清某个工具
    python scripts/prune_output.py --apply --tool demo_scan
"""
import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from infra.output_prune import human_size, prune_output


def main():
    ap = argparse.ArgumentParser(description="清理 output/ 下过期的任务目录")
    ap.add_argument("--base", default=str(BASE_DIR / "output"), help="output 根目录")
    ap.add_argument("--days", type=int, default=30, help="保留天数（默认 30）")
    ap.add_argument("--keep-min", type=int, default=10, help="每个工具至少保留几个（默认 10）")
    ap.add_argument("--tool", default="", help="只处理指定 tool_id")
    ap.add_argument("--apply", action="store_true", help="真的删除（不加只干跑）")
    args = ap.parse_args()

    hits, stats = prune_output(args.base,
                               keep_days=args.days,
                               keep_min=args.keep_min,
                               dry_run=not args.apply,
                               tools=args.tool or None)
    mode = "删除" if args.apply else "干跑（未删除）"
    print("模式：%s" % mode)
    print("目录：%s" % args.base)
    print("规则：%d 天内全留；超期的每个工具保留最近 %d 个" % (args.days, args.keep_min))
    print("----")
    for path, size, removed in hits:
        print("  %s  %9s  %s" % ("已删" if removed else "命中", human_size(size), path))
    print("----")
    total = sum(s for _p, s, _r in hits)
    print("扫描 %d 个任务目录：保留 %d，%s %d 个，可释放 %s"
          % (stats["scanned"], stats["kept"], "删除" if args.apply else "待删",
             len(hits), human_size(total)))
    if not args.apply and hits:
        print("确认无误后加 --apply 真删。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
