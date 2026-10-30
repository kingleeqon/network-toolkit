# -*- coding: utf-8 -*-
"""设备配置备份（PRD FR-202）——新工具接入的样板实现。

要点：
1. 不碰 UI，只用 emit / ctx；
2. 不自己建目录，只用 ctx.raw_dir；
3. 每台设备由 ctx.run_parallel 隔离异常，单台失败不影响整批；
4. 并发由 run_parallel 处理，工具只写单台逻辑。

可选「归档目录」：备份成功后额外写
    <归档目录>/<YYYY-MM-DD>/<设备名>.cfg
和接口采集保持同一套日期目录结构，每天一份配置留档、可直接比对。
"""
import time
from datetime import datetime
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra.naming import device_stem, write_text
from infra.vendor_profiles import (filter_supported, get_cmd, get_device_type_label,
                                 get_e1000, get_e1000_sequence, get_paging_cmd)

# 回显长度低于此值视为疑似不完整（估算值，PRD FR-202）
MIN_CONFIG_LENGTH = 100


def _run_e1000(session, cmd, timeout):
    """华为 E1000 的配置备份：进 vty 关屏显 → display current-configuration → 还原。

    现场脚本 netmiko_SSH.py 的 execute_commands_e1000。
    """
    enter, leave = get_e1000_sequence()
    for c in enter:
        try:
            session.send_timing(c, timeout=min(timeout, 15))
        except Exception:
            pass
    out = ""
    try:
        out = session.send(cmd, timeout=timeout)
    finally:
        for c in leave:
            try:
                session.send_timing(c, timeout=min(timeout, 15))
            except Exception:
                pass
    return out


class BatchBackupTool(BaseTool):
    id = "batch_backup"
    name = "设备配置备份"
    version = "1.2.0"
    category = "采集分析类"
    description = "批量登录设备抓取配置并保存到结果目录；可按日期归档留档"

    params = [
        ParamSpec(key="devices", label="设备清单", type="device_list", required=True,
                  ext=".xlsx;.csv;.txt", help="Excel/CSV/TXT，需含 IP 与凭证列"),
        ParamSpec(key="concurrency", label="并发数", type="int", default=10, min=1, max=50),
        ParamSpec(key="timeout", label="超时(秒)", type="int", default=30, min=5, max=300),
        ParamSpec(key="archive_dir", label="按日期归档目录（可选）", type="dir", required=False,
                  help="备份同时写成 <目录>/<YYYY-MM-DD>/<设备名>.cfg，"
                       "每天一份留档；留空则只在任务目录的 raw/ 下保留"),
    ]

    result_columns = ["IP", "设备名", "厂商", "状态", "耗时(ms)", "文件大小", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params.get("devices"))
        if not devices:
            emit("log", {"level": "WARN", "msg": "设备清单为空，任务结束"})
            emit("done", {"summary": {"total": 0}})
            return

        concurrency = int(params.get("concurrency", 10))
        timeout = int(params.get("timeout", 30))
        devices, unsupported = filter_supported(devices)
        if unsupported:
            top = sorted(unsupported.items(), key=lambda kv: -kv[1])[:6]
            ctx.log("清单里有 %d 台设备厂商未收录（%s%s），已跳过"
                    % (sum(unsupported.values()),
                       "、".join("%s×%d" % (k, v) for k, v in top),
                       " 等" if len(unsupported) > 6 else ""),
                    level="WARN")
        archive_root = (params.get("archive_dir") or "").strip()
        # 整批固定同一个日期：跨零点跑不会分裂到两个日期目录
        day = datetime.now().strftime("%Y-%m-%d")
        if archive_root:
            names = [device_stem(d) for d in devices]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                ctx.log("设备清单里有重名：%s —— 同名配置会互相覆盖" % "、".join(dup),
                        level="WARN")
            ctx.log("配置归档到：%s" % (Path(archive_root) / day))

        def worker(dev):
            start = time.time()
            # 进度区显示"当前正在处理的设备"（并发时以最近上报的为准）
            ctx.report_current("%s %s" % (dev.ip, dev.hostname or ""))
            cmd = get_cmd(dev.vendor, "config") or get_cmd(dev.vendor, "interface")
            if not cmd:
                raise ValueError("未配置该厂商的配置备份命令")
            if not get_cmd(dev.vendor, "config"):
                ctx.log("%s 没有配置备份命令，已改用接口命令顶替" % dev.ip,
                        level="WARN", ip=dev.ip)
            # 烽火按 IP 名单决定关分屏用 terminal line 0 还是 terminal length 0
            paging = get_paging_cmd(dev.vendor, dev.ip, dev.hostname, for_backup=True)
            with ctx.open_session(dev, timeout=timeout, paging_cmd=paging) as session:
                if get_e1000(dev.ip):
                    text = _run_e1000(session, cmd, timeout)
                else:
                    text = session.send(cmd, timeout=timeout)
            elapsed = int((time.time() - start) * 1000)

            raw_file = ctx.raw_dir / ("%s_config.txt" % dev.ip)
            write_text(raw_file, text or "")

            if archive_root:
                folder = Path(archive_root) / day
                folder.mkdir(parents=True, exist_ok=True)
                cfg_path = folder / ("%s.cfg" % device_stem(dev))
                label = get_device_type_label(dev.vendor, dev.hostname)
                write_text(cfg_path, "%s\n\n%s" % (label, text or ""))

            note = ""
            if len(text or "") < MIN_CONFIG_LENGTH:
                note = "疑似配置不完整（%d 字符）" % len(text or "")
                ctx.log("%s %s" % (dev.ip, note), level="WARN", ip=dev.ip)

            return [dev.ip, dev.hostname, dev.vendor, "", elapsed, len(text or ""), note]

        ctx.log("开始备份：%d 台设备，并发 %d，超时 %ds" % (len(devices), concurrency, timeout))
        ctx.run_parallel(devices, worker, result_columns=self.result_columns, max_workers=concurrency)
        emit("done", {"summary": {"total": len(devices)}})
