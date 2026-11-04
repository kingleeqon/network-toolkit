# -*- coding: utf-8 -*-
"""接口原始数据采集（收敛 netmiko_SSH_device_system.py）。

登录设备执行接口采集命令（display/show interface 及迪普/烽火 show interface verbose），
原始回显落盘到 raw 目录，供 crc_compare 等分析工具消费。

可选「归档目录」：采集成功后额外写
    <归档目录>/<YYYY-MM-DD>/<设备名>.log
与人工整理的日志目录结构完全一致（一天一个日期目录、一台设备一个 .log），
「接口CRC对比分析」可以直接把其中两个日期目录当作「今日/昨日」来比，
每日定时任务也是靠它把"采集"和"对比"串成一条链。

勾选「同时备份配置」时，**同一个会话里**再发一条配置命令，
配置另存为 <配置备份目录>/<YYYY-MM-DD>/<设备名>.cfg —— 一次登录拿两份，
不用为了备份再登一遍设备。
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

# 回显长度低于此值视为疑似不完整（空回显/半截回显都算）
MIN_OUTPUT_LENGTH = 100


def device_log_name(device):
    """日志文件名（不含扩展名）：优先设备名，没有设备名才退回 IP。"""
    return device_stem(device)


def archive_one(archive_root, device, text, day=None):
    """把一台设备的回显归档为 <root>/<YYYY-MM-DD>/<设备名>.log，返回路径。

    day 由调用方传入（整批用同一个日期）：采集跨零点时不会把同一批设备
    分裂到两个日期目录里。
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    folder = Path(archive_root) / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%s.log" % device_log_name(device))
    write_text(path, text or "")
    return path



class IfaceCollectTool(BaseTool):
    id = "iface_collect"
    name = "接口信息采集"
    version = "1.4.0"
    category = "采集分析类"
    description = ("批量登录设备采集接口原始数据（含迪普/烽火），落盘到结果目录；"
                   "可同时归档成 <日期>/<设备名>.log，供「接口CRC对比分析」直接消费")

    params = [
        ParamSpec(key="devices", label="设备清单", type="device_list", required=True,
                  ext=".xlsx;.csv;.txt", help="需含 IP、厂商、凭证列"),
        ParamSpec(key="concurrency", label="并发数", type="int", default=5, min=1, max=50),
        ParamSpec(key="timeout", label="超时(秒)", type="int", default=40, min=5, max=300),
        ParamSpec(key="archive_dir", label="按日期归档目录（可选）", type="dir", required=False,
                  help="结果同时写成 <目录>/<YYYY-MM-DD>/<设备名>.log，"
                       "「接口CRC对比分析」可直接把该日期目录作为今日/昨日目录；"
                       "留空则只在任务目录的 raw/ 下保留原始回显"),
        ParamSpec(key="backup_config", label="同时备份配置", type="bool", default=False,
                  help="在同一次登录里再取一份配置，存成 <配置备份目录>/<日期>/<设备名>.cfg；"
                       "不用额外再登一遍设备"),
        ParamSpec(key="backup_dir", label="配置备份目录", type="dir", required=False,
                  help="「同时备份配置」时配置保存的位置"),
    ]

    # 设备清单跑完必须有行；0 行说明一台都没处理 → 判失败
    empty_result_ok = False

    result_columns = ["IP", "设备名", "厂商", "状态", "耗时(ms)", "字符数", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params.get("devices"))
        if not devices:
            emit("done", {"summary": {"total": 0}})
            return

        concurrency = int(params.get("concurrency", 5))
        timeout = int(params.get("timeout", 40))
        archive_root = (params.get("archive_dir") or "").strip()
        # 清单里可能混着服务器/日志监控之类非网络设备（厂商未收录），
        # 硬采只会连接失败白跑，先按厂商过滤掉并说明
        devices, unsupported = filter_supported(devices)
        if unsupported:
            top = sorted(unsupported.items(), key=lambda kv: -kv[1])[:6]
            ctx.log("清单里有 %d 台设备厂商未收录（%s%s），已跳过"
                    % (sum(unsupported.values()),
                       "、".join("%s×%d" % (k, v) for k, v in top),
                       " 等" if len(unsupported) > 6 else ""),
                    level="WARN")
        also_cfg = bool(params.get("backup_config", False))
        backup_root = (params.get("backup_dir") or "").strip()
        if also_cfg and not backup_root:
            ctx.log("勾选了「同时备份配置」但没填配置备份目录，本次只采接口数据", level="WARN")
            also_cfg = False
        # 整批固定同一个日期：跨零点采集时不会分裂到两个日期目录
        day = datetime.now().strftime("%Y-%m-%d")

        if archive_root:
            names = [device_log_name(d) for d in devices]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                ctx.log("设备清单里有重名：%s —— 同名日志会互相覆盖，请改成唯一设备名"
                        % "、".join(dup), level="WARN")
            no_name = [d.ip for d in devices if not (d.hostname or "").strip()]
            if no_name:
                ctx.log("%d 台设备没有「设备名」，将用 IP 当文件名：%s%s"
                        % (len(no_name), "、".join(no_name[:5]),
                           " 等" if len(no_name) > 5 else ""), level="WARN")
            ctx.log("采集结果归档到：%s%s" % (Path(archive_root), "\\" + day))

        def _collect(dev):
            start = time.time()
            # 进度区显示"当前正在处理的设备"（并发时以最近上报的为准）
            ctx.report_current("%s %s" % (dev.ip, dev.hostname or ""))
            # 命令按厂商 + 产品类型取（烽火按设备名分防火墙/交换机）
            cmd = get_cmd(dev.vendor, "interface", device_name=dev.hostname)
            if not cmd:
                # 没有接口采集命令的厂商只能拿配置顶替：配置里没有 CRC 统计，
                # 这类设备的 CRC 差值会全是 0。必须明确告警，否则用户无从查起。
                cmd = get_cmd(dev.vendor, "config")
                if cmd:
                    ctx.log("%s 没有配置接口采集命令，已改用配置命令顶替，"
                            "CRC 很可能解析不到" % dev.ip, level="WARN", ip=dev.ip)
            if not cmd:
                raise ValueError("未配置该厂商的采集命令")
            paging = get_paging_cmd(dev.vendor, dev.ip, dev.hostname)
            with ctx.open_session(dev, timeout=timeout, paging_cmd=paging) as session:
                if get_e1000(dev.ip):
                    # 华为 E1000：普通关分屏不生效，要进 vty 改
                    text = run_e1000(session, cmd, timeout)
                else:
                    text = session.send(cmd, timeout=timeout)
                # 同一次会话里顺带把配置也取走，省一次登录
                cfg_text = ""
                cfg_failed = ""
                if also_cfg:
                    # 厂商列写「烽火」的设备按设备名分产品（DP/FW1000 是迪普防火墙），
                    # 配置命令必须带上设备名，否则取不到对应产品的那套命令
                    ccmd = get_cmd(dev.vendor, "config", device_name=dev.hostname)
                    if ccmd and ccmd != cmd:
                        marker = get_end_marker(dev.vendor, "config",
                                                 device_name=dev.hostname)
                        try:
                            if get_e1000(dev.ip):
                                # E1000 也要走 vty 关屏显那套（现场脚本
                                # netmiko_SSH.py 的 execute_commands_e1000）
                                cfg_text = run_e1000(session, ccmd, timeout)
                            else:
                                cfg_text = session.send(ccmd, timeout=timeout,
                                                        end_marker=marker)
                            if marker and marker not in (cfg_text or ""):
                                ctx.log("%s 配置里没有结束标记 %s，配置可能没采全"
                                        % (dev.ip, marker), level="WARN", ip=dev.ip)
                        except Exception as exc:
                            # 配置没取到：结果表不能还写「成功」，
                            # 否则用户以为备份好了，其实 .cfg 根本没生成
                            cfg_failed = "配置备份失败（%s）" % type(exc).__name__
                            ctx.log("%s 配置采集失败：%s" % (dev.ip, exc),
                                    level="WARN", ip=dev.ip)
                    elif ccmd == cmd:
                        cfg_text = text
            elapsed = int((time.time() - start) * 1000)

            raw_file = ctx.raw_dir / ("%s_iface.txt" % dev.ip)
            write_text(raw_file, text or "")
            if cfg_text:
                # 配置原文也留一份：归档目录没配时，配置内容不至于凭空消失
                write_text(ctx.raw_dir / ("%s_config.txt" % dev.ip), cfg_text)

            # 日志首行写设备类型标识：CRC 对比的解析器靠它准确识别厂商
            # （现场脚本也是这么写的），否则只能靠正文关键词猜，容易把
            # 华三的设备判成华为
            label = get_device_type_label(dev.vendor, dev.hostname)
            if archive_root:
                # 归档失败要让这台设备算失败：否则对比阶段会用到半截日志
                archive_one(archive_root, dev, "%s\n\n%s" % (label, text or ""), day=day)

            if also_cfg and cfg_text:
                folder = Path(backup_root) / day
                folder.mkdir(parents=True, exist_ok=True)
                cfg_path = folder / ("%s.cfg" % device_stem(dev))
                # 与 batch_backup 的 .cfg 格式一致：首行厂商标识 + 空行 + 配置
                write_text(cfg_path, "%s\n\n%s" % (label, cfg_text))

            # 回显过短（空回显/半截回显）要当场告警：否则问题会拖到
            # CRC 对比阶段才暴露成「一堆设备提取失败」，根因难查
            note = cfg_failed
            if len(text or "") < MIN_OUTPUT_LENGTH:
                tip = "回显可能不完整（%d 字符）" % len(text or "")
                ctx.log("%s %s" % (dev.ip, tip), level="WARN", ip=dev.ip)
                note = ("%s；%s" % (note, tip)) if note else tip

            row = [dev.ip, dev.hostname, dev.vendor, "", elapsed, len(text or ""), note]
            return (row, ItemStatus.PARTIAL_OK) if note else (row, ItemStatus.OK)

        def worker(dev):
            # 登录不上/超时等失败：记一份失败清单（归档目录里一眼能看到
            # 哪几台没采到），失败设备本身不生成 .log —— 半截文件混进归档
            # 会被 CRC 对比当成有效数据
            try:
                return _collect(dev)
            except Exception as exc:
                folder = Path(archive_root) / day if archive_root else ctx.output_dir
                record_device_failure(folder, dev, exc)
                raise

        ctx.log("开始采集：%d 台设备，并发 %d" % (len(devices), concurrency))
        ctx.run_parallel(devices, worker, result_columns=self.result_columns,
                         max_workers=concurrency)
        emit("done", {"summary": {"total": len(devices)}})
