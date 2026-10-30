# -*- coding: utf-8 -*-
"""验证：多任务定时器（存储/状态/立即执行）+ 工具定时条 + 总览窗口。"""
import json, sys, time
from pathlib import Path
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from core.scheduler import SchedulerManager, PIPELINE_KEY

STORE = Path(r"C:/temp/sched_test/schedules.json")
if STORE.exists(): STORE.unlink()
logs = []
mgr = SchedulerManager(store_path=STORE, on_log=logs.append)

print("=== ① 设定两个定时 ===")
mgr.set_schedule("iface_collect", at="09:00", enabled=True,
                  params={"devices": "D:/test/device.xlsx", "concurrency": "5",
                          "timeout": "40", "backup_config": "true"})
mgr.set_schedule("batch_backup", at="09:30", enabled=True,
                  params={"devices": "D:/test/device.xlsx"})
mgr.set_schedule(PIPELINE_KEY, at="08:30", enabled=False)
print("   schedules.json:", json.dumps(json.loads(STORE.read_text(encoding="utf-8")),
                                  ensure_ascii=False)[:160])

print("\n=== ② 状态 ===")
for k in ("iface_collect", "batch_backup", PIPELINE_KEY, "crc_compare"):
    print("   %-14s %-16s %s" % (k, mgr.title_of(k), mgr.status_of(k)))
print("   下次执行(iface_collect):", mgr.next_run_at("iface_collect"))

print("\n=== ③ 生成的命令 ===")
cmd, cwd = mgr.build_cmd("iface_collect")
print("   ", " ".join(cmd[1:]))
cmd2, _ = mgr.build_cmd(PIPELINE_KEY)
print("   ", " ".join(cmd2[1:]))

print("\n=== ④ 立即执行（demo_scan，最快）===")
# ④-a 全部成功 → 退出码 0
mgr.set_schedule("demo_scan", at="23:59", enabled=False,
                 params={"count": 6, "concurrency": 3, "fail_rate": 0})
ok, msg = mgr.run_now("demo_scan")
print("   ", msg)
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
print("   跑完后状态:", mgr.status_of("demo_scan"))
last = mgr.get("demo_scan").get("last")
print("   last 记录:", last, "→ 期望 code=0", "PASS" if last.get("code") == 0 else "FAIL")

# ④-b 部分失败 → 退出码 2（跑完但有失败项）
mgr.set_schedule("demo_scan", params={"count": 20, "concurrency": 5, "fail_rate": 50})
ok, msg = mgr.run_now("demo_scan")
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
last = mgr.get("demo_scan").get("last")
print("   部分失败 last:", last, "→ 期望 code=2", "PASS" if last.get("code") == 2 else "FAIL")

# ④-c 全部失败 → 退出码 1（FAILED，与「部分失败」区分）
mgr.set_schedule("demo_scan", params={"count": 6, "concurrency": 3, "fail_rate": 100})
ok, msg = mgr.run_now("demo_scan")
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
last = mgr.get("demo_scan").get("last")
print("   全部失败 last:", last, "→ 期望 code=1", "PASS" if last.get("code") == 1 else "FAIL")
print("   日志片段:", logs[:2])

print("\n=== ⑤ 一天只跑一次 ===")
print("   ran_today(demo_scan):", mgr.ran_today("demo_scan"))

print("\n=== ⑥ 界面：工具定时条 + 总览窗口 ===")
import ttkbootstrap as ttk
import ui.dpi as dpi
import ui.schedule_overview as so
from ui.main_window import MainWindow
for n in ("show_info", "show_warning", "show_error"):
    setattr(so.Messagebox, n, staticmethod(lambda *a, **k: None))
so.Messagebox.yesno = staticmethod(lambda *a, **k: True)

dpi.set_dpi_awareness()
root = ttk.Window(themename="cosmo", size=(1180, 760), hdpi=True)
dpi.configure_fonts(root)
mw = MainWindow(root, tools_dir=BASE/"tools", output_base=str(BASE/"output"), app_version="1")
root.update_idletasks(); time.sleep(0.5); root.update()
# 指向测试 store
mw.scheduler.store_path = STORE
mw.tool_tree.selection_set("iface_collect"); mw.on_select_tool()
root.update_idletasks(); time.sleep(0.4); root.update()
print("   定时条 时间框:", mw.var_sched_at.get(), " 启用:", mw.var_sched_on.get())
print("   定时条 状态:", mw.lbl_sched.cget("text"))
mw.var_sched_at.set("11:15")
mw._save_sched()
root.update_idletasks(); time.sleep(0.3); root.update()
print("   改时间后 → 存盘:", json.loads(STORE.read_text(encoding="utf-8"))["iface_collect"]["at"])
print("   状态栏:", repr(mw.sched_label.cget("text")))

mw.show_auto_task()
ov = root._schedule_overview
root.update_idletasks(); time.sleep(0.5); root.update()
rows = ov.tree.get_children()
print("   总览行数:", len(rows))
for r in rows[:4]:
    print("     ", ov.tree.item(r, "values"))
root.destroy()
