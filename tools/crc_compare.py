# -*- coding: utf-8 -*-
"""接口 CRC 对比分析（收敛 ALL_CHECK_CRC-2.py，复用 infra/vendor_parser）。

对比昨日/今日两个目录下的接口采集日志，按接口汇总 CRC 值并计算差值，
输出 Excel（含日期列，供 CRC 趋势分析按天累积）。
支持华为/中兴/锐捷/华三/思科/烽火(交换机)/迪普(防火墙)。
"""
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from core.errors import ConfigError
from core.tool_base import BaseTool, ParamSpec
from infra.vendor_parser import (detect_vendor, extract_crc_info,
                                 extract_interface_desc)

# 厂商中文名
_VENDOR_NAMES = {
    "huawei": "华为", "zte": "中兴", "ruijie": "锐捷", "h3c": "华三",
    "cisco": "思科", "fiberhome": "烽火(交换机)", "dptech": "迪普(防火墙)",
    "unknown": "未知",
}

# 结果表最多展示的「CRC 变化接口」行数（全量明细始终在 Excel 中）
_MAX_RESULT_ROWS = 5000

# Excel 落盘列（比结果表多一列「日期」，供趋势分析按天累积）
# Excel 落盘列（日期放最后：界面/Excel 打开先看数据，趋势分析按列名取日期不受影响）
_EXCEL_COLUMNS = ["设备名称", "接口名称", "今日CRC值", "昨日CRC值", "CRC差值", "厂商",
                  "备注", "日期"]

# CRC 差值超过这个值才在「备注」里写上接口描述（方便直接看出是哪个口出问题）
REMARK_CRC_DIFF = 1000


def _guess_date(dir_path):
    """从目录名解析日期（形如 2026-10-28 / 20261028），失败返回今天。"""
    name = Path(dir_path).name
    m = re.search(r"(20\d{2})[-_]?(\d{1,2})[-_]?(\d{1,2})", name)
    if m:
        try:
            return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def _latest_two_day_dirs(root):
    """取根目录下最近两个「YYYY-MM-DD」子目录 → (昨日目录, 今日目录)；不够两个返回 None。

    归档结构是 <根>/<日期>/<设备名>.log，定时任务每天跑都想比"最近两天"，
    所以按日期目录名排序取最后两个，不用每天改参数。
    """
    base = Path(root)
    if not base.is_dir():
        return None
    days = [p for p in base.iterdir()
            if p.is_dir() and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", p.name)]
    if len(days) < 2:
        return None
    days.sort(key=lambda p: p.name)
    return days[-2], days[-1]


class CrcCompareTool(BaseTool):
    id = "crc_compare"
    name = "接口CRC对比分析"
    version = "1.1.1"
    category = "采集分析类"
    description = "对比昨日/今日接口采集日志，按接口汇总 CRC 值并计算差值（结果按日期落盘，供趋势分析）"

    params = [
        ParamSpec(key="auto_root", label="自动取最近两日（根目录，可选）", type="dir",
                  required=False,
                  help="填了它就不用选下面两个目录：自动取该根目录下最近两个日期子目录"
                       "（形如 2026-11-03）。定时任务建议用这个——每天自动对比最近两天"),
        ParamSpec(key="yesterday_dir", label="昨日日志目录", type="dir", required=False,
                  help="时间1（昨日）接口采集日志目录"),
        ParamSpec(key="today_dir", label="今日日志目录", type="dir", required=False,
                  help="时间2（今日）接口采集日志目录"),
        ParamSpec(key="out_dir", label="结果输出目录（可选）", type="dir", required=False,
                  help="填了就把 Excel 另存一份到该目录（按日期命名 CRC对比_YYYY-MM-DD.xlsx，"
                       "同日重复运行会覆盖）；任务目录里始终保留一份"),
    ]

    result_columns = ["设备名称", "接口名称", "今日CRC值", "昨日CRC值", "CRC差值", "厂商",
                      "备注", "状态", "失败原因"]

    # 本工具只产出结果表，无需图表标签页
    output_tabs = ["result", "log", "summary"]

    # 界面在参数区右侧展示「结果输出目录」里的历史结果
    result_files_param = "out_dir"

    def list_result_files(self, folder):
        """列出结果输出目录里的历史对比结果（新的在前）。"""
        try:
            files = sorted(Path(folder).glob("CRC对比_*.xlsx"),
                           key=lambda p: p.name, reverse=True)
        except Exception:
            return []
        return [(p.name, str(p)) for p in files]

    def load_result_file(self, path):
        """把历史 Excel 读成结果表行：只留有变化的接口（与运行时的展示口径一致）。

        Excel 里是全量明细（可能 5 万+行），直接塞进表格会卡死界面，
        所以按 CRC差值 != 0 过滤并按差值绝对值排序，最多 5000 行。
        """
        import pandas as pd
        df = pd.read_excel(path)
        rows = []
        for _, r in df.iterrows():
            diff = r.get("CRC差值", 0)
            try:
                if float(diff or 0) == 0:
                    continue
            except (TypeError, ValueError):
                pass
            rows.append([r.get("设备名称", ""), r.get("接口名称", ""),
                         r.get("今日CRC值", 0), r.get("昨日CRC值", 0),
                         diff, r.get("厂商", ""), r.get("备注", "") or "",
                         "成功", ""])
        to_num = lambda x: abs(float(x)) if isinstance(x, (int, float)) else 0
        rows.sort(key=lambda row: to_num(row[4]), reverse=True)
        return list(self.result_columns), rows[:_MAX_RESULT_ROWS]

    def validate_params(self, params):
        """启用定时前的前置校验：要么填自动根目录，要么两个目录都选。"""
        if (params.get("auto_root") or "").strip():
            return ""
        if not (params.get("yesterday_dir") or "").strip():
            return "请选择「昨日日志目录」，或填写「自动取最近两日」的根目录"
        if not (params.get("today_dir") or "").strip():
            return "请选择「今日日志目录」，或填写「自动取最近两日」的根目录"
        return ""

    def run(self, params, ctx, emit):
        # 目录解析：优先「自动取最近两日」，否则用选定的两个目录。
        # 缺参时给中文提示，不能直接 params["x"] 崩成 KeyError
        auto_root = (params.get("auto_root") or "").strip()
        if auto_root:
            pair = _latest_two_day_dirs(auto_root)
            if not pair:
                raise ConfigError("「自动取最近两日」的根目录下不足两个日期子目录"
                                  "（需形如 2026-11-03）：%s" % auto_root)
            t1, t2 = str(pair[0]), str(pair[1])
            ctx.log("自动选取最近两日：昨日 %s，今日 %s"
                    % (Path(t1).name, Path(t2).name))
        else:
            t1 = (params.get("yesterday_dir") or "").strip()
            t2 = (params.get("today_dir") or "").strip()
            if not t1 or not t2:
                raise ConfigError("请先选择「昨日日志目录」和「今日日志目录」，"
                                  "或填写「自动取最近两日」的根目录")

        if not Path(t2).is_dir():
            raise ConfigError("今日日志目录不存在：%s" % t2)
        if not Path(t1).is_dir():
            raise ConfigError("昨日日志目录不存在：%s" % t1)

        date_str = _guess_date(t2)

        results = []
        processed = 0
        skipped = 0
        vendor_stats = {}

        files = sorted(f for f in os.listdir(t2) if f.lower().endswith(".log"))
        total_files = len(files)

        for i, filename in enumerate(files, 1):
            device_name = os.path.splitext(filename)[0]
            f2 = Path(t2) / filename
            f1 = Path(t1) / filename

            # 进度反馈，让 GUI 进度条与日志可见
            emit("progress", {"current": i, "total": total_files})

            if not f1.exists():
                ctx.log("%s 缺少昨日同名文件，跳过" % filename, level="WARN")
                skipped += 1
                continue

            c2, text2 = self._read_crc(f2, ctx)
            c1, _ = self._read_crc(f1, ctx)
            if not c2 or not c1:
                ctx.log("%s 提取失败，跳过" % filename, level="WARN")
                skipped += 1
                continue

            # 厂商识别复用 _read_crc 已解码的文本（utf-8→gbk 回退），
            # 不再二次读盘，也避免纯 utf-8 重读时 GBK 日志中文被吞导致误判
            vendor = detect_vendor(text2)
            vendor_stats[vendor] = vendor_stats.get(vendor, 0) + 1

            # 接口描述：差值异常时写进「备注」，方便直接看出是哪个口
            descs = extract_interface_desc(text2, vendor)
            all_ifaces = set(c2.keys()) | set(c1.keys())
            for iface in sorted(all_ifaces):
                today = c2.get(iface, 0)
                yesterday = c1.get(iface, 0)
                diff = today - yesterday
                remark = ""
                if abs(diff) > REMARK_CRC_DIFF:
                    remark = descs.get(iface, "")
                # 结果表行（9 列，供 GUI/Excel 展示）
                results.append([device_name, iface, today, yesterday, diff,
                                _VENDOR_NAMES.get(vendor, vendor), remark, "", ""])
            processed += 1

        out = self._write_excel(results, date_str, ctx)
        ctx.log("CRC 对比完成：成功 %d 设备 / 跳过 %d，记录 %d 条（日期 %s）-> %s"
                % (processed, skipped, len(results), date_str, out))

        # 另存到指定目录（定时任务用参数快照，配一次就一直生效）
        out_dir = (params.get("out_dir") or "").strip()
        if out_dir and out:
            try:
                folder = Path(out_dir)
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / ("CRC对比_%s%s" % (date_str, out.suffix))
                shutil.copy2(str(out), str(target))
                ctx.log("结果已另存：%s（同日重复运行会覆盖同名文件）" % target)
            except Exception as exc:
                ctx.log("另存到「%s」失败，结果仍在任务目录：%s" % (out_dir, exc),
                        level="WARN")

        # 结果表展示「有 CRC 变化」的接口明细（列与内容一一对应）；
        # 全量明细始终在 Excel 中，避免 5.8 万行卡死界面。
        changed_rows = [r for r in results if r[4] != 0]
        changed_rows.sort(key=lambda r: abs(r[4]), reverse=True)
        shown = changed_rows[:_MAX_RESULT_ROWS]
        for r in shown:
            emit("result", {"row": r, "status": "OK"})
        if len(changed_rows) > len(shown):
            ctx.log("CRC 变化的接口 %d 个，结果表仅展示前 %d 个（差值最大），完整清单见 Excel"
                    % (len(changed_rows), len(shown)), level="WARN")

        emit("done", {"summary": {"total": processed, "skipped": skipped,
                                  "records": len(results),
                                  "changed": len(changed_rows),
                                  "date": date_str}})

    @staticmethod
    def _read_crc(path, ctx):
        """读日志：utf-8 优先，失败回退 GBK（SecureCRT 之类导出的日志常见）。

        返回 (crc_info, content)；读取/解析失败返回 (None, None)。
        content 一并返回供厂商识别复用，避免重复读盘与编码不一致。

        原来固定 utf-8 + errors="ignore"：GBK 日志里的中文会被整段吞掉，
        虽然接口名/CRC 这些 ASCII 还能匹配上，但一旦日志是 GBK 且带损坏字节，
        整行会被丢弃、CRC 就解析不到了。
        """
        try:
            raw = path.read_bytes()
        except Exception as e:
            ctx.log("读取失败 %s: %s" % (path.name, e), level="WARN")
            return None, None
        content = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            content = raw.decode("utf-8", errors="ignore")
        info = extract_crc_info(content)
        return (info, content) if info else (None, None)

    @staticmethod
    def _write_excel(rows, date_str, ctx):
        import pandas as pd
        out = ctx.output_dir / "result.xlsx"
        if not rows:
            return None
        # Excel 落盘 7 列（含日期），去掉「状态」「失败原因」两列框架占位
        data = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], date_str] for r in rows]
        df = pd.DataFrame(data, columns=_EXCEL_COLUMNS)
        df.sort_values(["设备名称", "接口名称"], inplace=True)
        try:
            df.to_excel(str(out), index=False)
        except Exception as e:
            ctx.log("Excel 导出失败，降级 CSV: %s" % e, level="WARN")
            out = ctx.output_dir / "result.csv"
            df.to_csv(str(out), index=False, encoding="utf-8-sig")
        return out
