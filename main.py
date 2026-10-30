# -*- coding: utf-8 -*-
"""网络运维工具箱入口。

用法：
    python main.py                 # 启动 GUI
    python main.py --list-tools    # 列出已注册工具与加载错误
    python main.py --selftest      # 无界面冒烟：跑演示工具，验证全链路
    python main.py --cli <tool_id> key=value ...   # 无界面运行指定工具
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

from core.message import MsgType

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

APP_VERSION = "0.1.0"


def _setup_logging():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def list_tools():
    from core.registry import Registry
    reg = Registry(BASE_DIR / "tools")
    reg.discover()
    print("已注册工具：%d" % len(reg.tools))
    for t in reg.tools:
        print("  - [%s] %s (v%s) id=%s" % (t.category, t.name, t.version, t.id))
    if reg.load_errors:
        print("加载失败：%d" % len(reg.load_errors))
        for name, err in reg.load_errors:
            print("  ! %s -> %s" % (name, err))
    return 0 if reg.tools else 1


def selftest():
    """无 GUI 冒烟测试：验证队列、并发、异常隔离、结果落盘、summary.json。"""
    from core.message import MsgType
    from core.registry import Registry
    from core.task_engine import TaskEngine

    _setup_logging()
    reg = Registry(BASE_DIR / "tools")
    reg.discover()
    tool = reg.get("demo_scan")
    if tool is None:
        print("未找到 demo_scan 工具，自检失败")
        return 1

    engine = TaskEngine(output_base=str(BASE_DIR / "output"), app_version=APP_VERSION,
                        operator="selftest")
    params = {"count": 30, "concurrency": 8, "fail_rate": 20}
    print("启动任务：%s，参数 %s" % (tool.name, params))
    engine.start(tool, params)

    deadline = time.time() + 60
    while engine.state in ("RUNNING", "CANCELLING") and time.time() < deadline:
        engine.poll(lambda msg: _print_msg(msg))
        time.sleep(0.05)
    engine.poll(lambda msg: _print_msg(msg))

    print("\n—— 自检结果 ——")
    print("任务状态：%s" % engine.state)
    print("统计：%s" % engine.stats)
    print("失败原因分布：%s" % engine.fail_reason_count)
    print("结果行数：%d" % len(engine.rows))

    summary_path = Path(engine.output_dir) / "summary.json"
    ok = summary_path.exists() and engine.stats["total"] == 30
    print("summary.json：%s" % ("存在 [OK]" if summary_path.exists() else "缺失 [FAIL]"))
    print("结果目录：%s" % engine.output_dir)

    # 校验：失败项必须有原因、总数一致
    fail_rows = [r for r in engine.rows if r[3] not in ("成功", "部分成功")]
    print("失败行数：%d（应 > 0，验证异常隔离）" % len(fail_rows))

    loader_ok = _test_device_loader()
    if ok and len(fail_rows) > 0 and loader_ok:
        print("\n[OK] 自检通过：全链路可跑通（队列/并发/失败隔离/落盘/清单导入）")
        return 0
    print("\n[FAIL] 自检未通过")
    return 1


def _test_device_loader():
    """验证清单导入（CSV/TXT）与异常行识别。"""
    from infra.device_loader import load_devices

    sample_dir = BASE_DIR / "output" / "_sample"
    sample_dir.mkdir(parents=True, exist_ok=True)

    csv_path = sample_dir / "devices.csv"
    rows = [
        "IP,设备名,厂商,凭证",
        "192.168.1.1,core-1,huawei,default",
        "192.168.1.2,acc-1,cisco,default",
        "192.168.1.3,acc-2,h3c,default",
        "999.1.1.1,bad-ip,huawei,default",
        "192.168.1.1,dup-ip,huawei,default",
        "192.168.1.4,no-cred,huawei,",
    ]
    with open(str(csv_path), "w", encoding="utf-8") as f:
        f.write("\n".join(rows))
    devices, errors = load_devices(str(csv_path))
    print("\n—— 清单导入自检（CSV）——")
    print("有效设备：%d（期望 3）" % len(devices))
    for e in errors:
        print("  异常：%s" % e)

    txt_path = sample_dir / "devices.txt"
    with open(str(txt_path), "w", encoding="utf-8") as f:
        f.write("192.168.1.10\r\n192.168.1.11,备注设备\r\n")
    devices2, errors2 = load_devices(str(txt_path), default_credential_ref="default")
    print("TXT 有效设备：%d（期望 2）" % len(devices2))
    for e in errors2:
        print("  异常：%s" % e)

    ok = len(devices) == 3 and len(errors) == 3 and len(devices2) == 2
    print("清单导入自检：%s" % ("通过 [OK]" if ok else "未通过 [FAIL]"))
    return ok


def _print_msg(msg):
    type_ = msg.get("type")
    value = getattr(type_, "value", type_)
    payload = msg.get("payload", {})
    if value == MsgType.LOG:
        print("  [%s] %s" % (payload.get("level"), payload.get("msg")))
    elif value == MsgType.PROGRESS:
        print("  进度 %s/%s" % (payload.get("current"), payload.get("total")))
    elif value == MsgType.DONE:
        print("  收到 done")


def _launch_gui():
    """启动 GUI。返回进程退出码。

    三个顺序不能换：
        1. 先设置 DPI 感知 —— 进程级、不可撤销，且必须在 Window() 之前
        2. 再创建 Window（主题从用户偏好读取）
        3. 最后统一字体并校验 scaling
    """
    try:
        import ttkbootstrap
    except ImportError:
        print("缺少 ttkbootstrap，无法启动 GUI：")
        print("    pip install ttkbootstrap==1.17.0")
        print("当前可使用 --selftest 在无界面模式下验证框架。")
        return 1

    import ui.dpi as dpi

    dpi.set_dpi_awareness()  # 必须在创建任何 Tk 对象之前

    from ui.main_window import MainWindow, load_ui_pref

    pref = load_ui_pref()
    root = ttkbootstrap.Window(
        title="网络运维工具箱 v%s" % APP_VERSION,
        themename=pref["theme"],
        size=(1180, 760),
        hdpi=True,
    )
    root.minsize(1000, 640)  # 避免窗口被缩到控件重叠
    dpi.configure_fonts(root)

    MainWindow(root, tools_dir=BASE_DIR / "tools",
               output_base=str(BASE_DIR / "output"), app_version=APP_VERSION)

    # 等窗口真正映射到屏幕后再校验 —— 未映射时读到的 DPI 不可信
    root.update_idletasks()
    dpi.verify_scaling(root)
    root.mainloop()
    return 0


def run_cli(tool_id, params):
    """无界面运行指定工具，返回退出码。params 为 dict。"""
    from core.registry import Registry
    from core.task_engine import TaskEngine

    _setup_logging()
    reg = Registry(BASE_DIR / "tools")
    reg.discover()
    tool = reg.get(tool_id)
    if tool is None:
        print("未找到工具：%s" % tool_id)
        print("可用工具请用 --list-tools 查看")
        return 1

    engine = TaskEngine(output_base=str(BASE_DIR / "output"), app_version=APP_VERSION,
                        operator="cli")
    engine.start(tool, params)
    deadline = time.time() + 3600
    while engine.state in ("RUNNING", "CANCELLING") and time.time() < deadline:
        engine.poll(lambda msg: _print_msg(msg))
        time.sleep(0.05)
    engine.poll(lambda msg: _print_msg(msg))

    print("\n—— 运行结果 ——")
    print("工具：%s（%s）" % (tool.name, tool.id))
    print("状态：%s" % engine.state)
    print("统计：%s" % engine.stats)
    if engine.output_dir:
        print("结果目录：%s" % engine.output_dir)
    return 0 if engine.state == "SUCCEEDED" else 1


def _parse_cli_params(args):
    """把 --cli 后的 key=value 参数解析为 dict，并对常见类型做转换。"""
    params = {}
    for item in args:
        if "=" not in item:
            print("忽略无效参数（应为 key=value）：%s" % item)
            continue
        key, _, value = item.partition("=")
        # 布尔 / 整数自动转换
        if value.lower() in ("true", "false"):
            params[key] = value.lower() == "true"
        elif value.isdigit():
            params[key] = int(value)
        else:
            params[key] = value
    return params


def main():
    parser = argparse.ArgumentParser(description="网络运维工具箱")
    parser.add_argument("--list-tools", action="store_true", help="列出工具后退出")
    parser.add_argument("--selftest", action="store_true", help="无界面冒烟测试")
    parser.add_argument("--cli", metavar="TOOL_ID", help="无界面运行指定工具")
    parser.add_argument("cli_args", nargs="*", help="--cli 参数，格式 key=value")
    args = parser.parse_args()

    if args.list_tools:
        return list_tools()
    if args.selftest:
        return selftest()
    if args.cli:
        params = _parse_cli_params(args.cli_args)
        return run_cli(args.cli, params)

    return _launch_gui()


if __name__ == "__main__":
    sys.exit(main())
