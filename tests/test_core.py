# -*- coding: utf-8 -*-
"""验证框架层：退出码文案收口、0 结果判定、定时配置并发写不丢条目。

运行：python tests/test_core.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.exit_codes import describe
from core.scheduler import SchedulerManager
from core.task_engine import TaskEngine
from core.tool_base import BaseTool

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-52s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


print("=== ① 退出码文案只在一处定义 ===")
check("0 → 成功", describe(0), "成功")
check("2 → 部分失败", describe(2), "部分失败")
check("1 → 失败(退出码 1)", describe(1), "失败(退出码 1)")
check("pipeline 2 → 日期目录不足", describe(2, True), "日期目录不足")
check("pipeline 6 → 备份失败", describe(6, True), "备份失败(对比已跑)")
check("非法值不炸", describe("abc"), "失败(abc)")


class _EmptyTool(BaseTool):
    """采集类：声明 0 行不合法。"""
    id = "t_empty_bad"
    name = "空结果采集"
    result_columns = ["A", "状态", "失败原因"]
    empty_result_ok = False

    def run(self, params, ctx, emit):
        emit("done", {"summary": {"total": 0}})


class _ChartTool(BaseTool):
    """产图类：0 行属正常。"""
    id = "t_empty_ok"
    name = "纯图表"
    result_columns = ["说明", "状态", "失败原因"]
    empty_result_ok = True

    def run(self, params, ctx, emit):
        emit("done", {"summary": {"total": 0}})


def run_engine(tool):
    eng = TaskEngine(output_base=tempfile.mkdtemp(), app_version="1", operator="t")
    eng.start(tool, {})
    t0 = time.time()
    while eng.state in ("RUNNING", "CANCELLING") and time.time() - t0 < 10:
        eng.poll(lambda m: None)
        time.sleep(0.02)
    eng.poll(lambda m: None)
    return eng


print("=== ② 0 结果：采集类判失败，产图类判成功 ===")
check("采集类 0 行 → FAILED", run_engine(_EmptyTool()).state, "FAILED")
check("产图类 0 行 → SUCCEEDED", run_engine(_ChartTool()).state, "SUCCEEDED")

print("=== ③ 定时配置并发写不丢条目 ===")
STORE = Path(tempfile.mkdtemp()) / "schedules.json"
mgr = SchedulerManager(store_path=STORE)
errors = []


def writer(i):
    try:
        for j in range(20):
            mgr.set_schedule("k%d" % i, at="08:30", enabled=True,
                             params={"i": i, "j": j})
    except Exception as exc:            # 写盘异常不能让线程静默死掉
        errors.append(exc)


threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
data = mgr._read()
check("8 个条目全部保留", len(data), 8)
check("每个条目的参数是最后一次写入", all(data["k%d" % i]["params"]["j"] == 19
                                          for i in range(8)), True)
check("没有写盘异常", len(errors), 0)

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
