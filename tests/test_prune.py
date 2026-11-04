# -*- coding: utf-8 -*-
"""验证 output 目录清理：只删过期任务目录，不碰文件、不碰工具根目录。

运行：python tests/test_prune.py
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from infra.output_prune import prune_output

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-52s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


def build(root):
    """造一个 output 结构：3 个任务目录（老/中/新）+ 状态文件 + 非任务目录。"""
    tool = root / "crc_compare"
    tool.mkdir(parents=True)
    now = datetime.now()
    stamps = [(now - timedelta(days=90), "old"),      # 90 天前
              (now - timedelta(days=40), "mid"),      # 40 天前
              (now - timedelta(days=1), "new")]       # 昨天
    for when, tag in stamps:
        d = tool / ("%s_%s" % (when.strftime("%Y%m%d-%H%M%S"), tag.ljust(8, "0")[:8]))
        d.mkdir()
        (d / "summary.json").write_text("{}", encoding="utf-8")
    # 不该被碰的东西
    (tool / ".history_cache.pkl").write_text("cache", encoding="utf-8")
    (tool / "result.xlsx").write_text("x", encoding="utf-8")
    (tool / "_sample").mkdir()
    (root / "_nl_test.cfg").write_text("cfg", encoding="utf-8")
    return tool


print("=== ① 干跑：只列清单不动磁盘 ===")
root = Path(tempfile.mkdtemp())
tool = build(root)
before = sorted(p.name for p in tool.iterdir())
hits, stats = prune_output(root, keep_days=30, keep_min=0, dry_run=True)
check("命中 2 个（老的+中等的）", len(hits), 2)
check("干跑后目录数不变", sorted(p.name for p in tool.iterdir()), before)
check("统计：扫描 3 保留 1", (stats["scanned"], stats["kept"]), (3, 1))

print("=== ② 真删：只删过期任务目录 ===")
hits, stats = prune_output(root, keep_days=30, keep_min=0, dry_run=False)
left = sorted(p.name for p in tool.iterdir())
check("已删 2 个", stats["removed"], 2)
check("剩最新任务目录 + 2 个文件 + _sample", len(left), 4)
check("状态文件还在", (tool / ".history_cache.pkl").exists(), True)
check("工具根目录下的 result.xlsx 还在", (tool / "result.xlsx").exists(), True)
check("_sample 目录还在", (tool / "_sample").is_dir(), True)
check("根目录的 _nl_test.cfg 还在", (root / "_nl_test.cfg").exists(), True)

print("=== ③ keep_min 保底 ===")
root2 = Path(tempfile.mkdtemp())
tool2 = build(root2)
hits, stats = prune_output(root2, keep_days=0, keep_min=2, dry_run=False)
check("keep_min=2 时最多删到剩 2 个", stats["removed"], 1)
check("剩下 2 个任务目录", len([p for p in tool2.iterdir() if p.is_dir()
                                and p.name != "_sample"]), 2)

print("=== ④ 非任务目录/文件不会被当成任务目录 ===")
root3 = Path(tempfile.mkdtemp())
t3 = root3 / "demo_scan"
t3.mkdir(parents=True)
(t3 / "不是任务目录").mkdir()
(t3 / "20261104-103624_zz").mkdir()          # 后缀不是 hex，不算
hits, stats = prune_output(root3, keep_days=0, keep_min=0, dry_run=False)
check("扫描到 0 个任务目录", stats["scanned"], 0)
check("非任务目录原封不动", len(list(t3.iterdir())), 2)

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
