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

from core.message import ItemStatus
from core.tool_base import BaseTool, ParamSpec
from infra.connector import run_e1000
from infra.naming import device_stem, record_device_failure, write_text
from infra.vendor_profiles import (filter_supported, get_cmd, get_device_type_label,
                                 get_e1000, get_e1000_sequence, get_end_marker,
                                 get_paging_cmd)

# 回显长度低于此值视为疑似不完整（估算值，PRD FR-202）
MIN_CONFIG_LENGTH = 100



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

    # 设备清单跑完必须有行；0 行说明一台都没处理 → 判失败
    empty_result_ok = False

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

        def _backup(dev):
            start = time.time()
            # 进度区显示"当前正在处理的设备"（并发时以最近上报的为准）
            ctx.report_current("%s %s" % (dev.ip, dev.hostname or ""))
            # 配置命令必须带设备名：厂商列写「烽火」但设备名含 DP/FW1000 的
            # 是迪普防火墙，命令和分屏都不一样
            cfg_cmd = get_cmd(dev.vendor, "config", device_name=dev.hostname)
            cmd = cfg_cmd or get_cmd(dev.vendor, "interface", device_name=dev.hostname)
            if not cmd:
                raise ValueError("未配置该厂商的配置备份命令")
            if not cfg_cmd:
                # 顶替的是接口命令：存下来的文件其实不是配置，
                # 日志要说清楚，否则事后看 .cfg 会误以为是配置
                ctx.log("%s 没有配置备份命令，已改用接口命令顶替"
                        "（保存的不是配置，仅供排查）" % dev.ip,
                        level="WARN", ip=dev.ip)
            # 结束标记：烽火配置以 !end 收尾，读到它才算采全
            marker = get_end_marker(dev.vendor, "config" if cfg_cmd else "interface",
                                    device_name=dev.hostname)
            # 烽火按 IP 名单决定关分屏用 terminal line 0 还是 terminal length 0
            paging = get_paging_cmd(dev.vendor, dev.ip, dev.hostname, for_backup=True)
            with ctx.open_session(dev, timeout=timeout, paging_cmd=paging) as session:
                if get_e1000(dev.ip):
                    text = run_e1000(session, cmd, timeout)
                else:
                    text = session.send(cmd, timeout=timeout, end_marker=marker)
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
            if marker and marker not in (text or ""):
                # 没读到结束标记 = 很可能没采全（设备中途卡住/超时）
                tip = "未收到结束标记 %s，可能没采全" % marker
                ctx.log("%s %s" % (dev.ip, tip), level="WARN", ip=dev.ip)
                note = tip
            if len(text or "") < MIN_CONFIG_LENGTH:
                # 文件已经落盘，这里只是把「可能不完整」标出来：
                # 设备超时截断时配置看着像完整的一样，内容却少了尾巴
                tip = "配置回显过短，文件可能不完整（%d 字符）" % len(text or "")
                ctx.log("%s %s" % (dev.ip, tip), level="WARN", ip=dev.ip)
                note = ("%s；%s" % (note, tip)) if note else tip

            return ([dev.ip, dev.hostname, dev.vendor, "", elapsed, len(text or ""), note],
                    ItemStatus.PARTIAL_OK if note else ItemStatus.OK)

        def worker(dev):
            # 登录不上/超时等失败：记一份失败清单，失败设备本身不生成 .cfg
            # （半截配置混进归档目录会被当成有效备份）
            try:
                return _backup(dev)
            except Exception as exc:
                folder = Path(archive_root) / day if archive_root else ctx.output_dir
                record_device_failure(folder, dev, exc)
                raise

        ctx.log("开始备份：%d 台设备，并发 %d，超时 %ds" % (len(devices), concurrency, timeout))
        ctx.run_parallel(devices, worker, result_columns=self.result_columns, max_workers=concurrency)
        emit("done", {"summary": {"total": len(devices)}})
