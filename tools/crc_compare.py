# -*- coding: utf-8 -*-
"""接口 CRC 对比分析（收敛 ALL_CHECK_CRC-2.py，复用 infra/vendor_parser）。

对比昨日/今日两个目录下的接口采集日志，按接口汇总 CRC 值并计算差值，
输出 Excel（含日期列，供 CRC 趋势分析按天累积）。
支持华为/中兴/锐捷/华三/思科/烽火(交换机)/迪普(防火墙)。
"""
import os
import re
from datetime import datetime
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra.vendor_parser import detect_vendor, extract_crc_info

# 厂商中文名
_VENDOR_NAMES = {
    "huawei": "华为", "zte": "中兴", "ruijie": "锐捷", "h3c": "华三",
    "cisco": "思科", "fiberhome": "烽火(交换机)", "dptech": "迪普(防火墙)",
    "unknown": "未知",
}

# 结果表最多展示的「CRC 变化接口」行数（全量明细始终在 Excel 中）
_MAX_RESULT_ROWS = 5000

# Excel 落盘列（比结果表多一列「日期」，供趋势分析按天累积）
_EXCEL_COLUMNS = ["日期", "设备名称", "接口名称", "今日CRC值", "昨日CRC值", "CRC差值", "厂商"]


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


class CrcCompareTool(BaseTool):
    id = "crc_compare"
    name = "接口CRC对比分析"
    version = "1.1.1"
    category = "采集分析类"
    description = "对比昨日/今日接口采集日志，按接口汇总 CRC 值并计算差值（结果按日期落盘，供趋势分析）"

    params = [
        ParamSpec(key="yesterday_dir", label="昨日日志目录", type="dir", required=True,
                  help="时间1（昨日）接口采集日志目录"),
        ParamSpec(key="today_dir", label="今日日志目录", type="dir", required=True,
                  help="时间2（今日）接口采集日志目录"),
    ]

    result_columns = ["设备名称", "接口名称", "今日CRC值", "昨日CRC值", "CRC差值", "厂商", "状态", "失败原因"]

    # 本工具只产出结果表，无需图表标签页
    output_tabs = ["result", "log", "summary"]

    def run(self, params, ctx, emit):
        t1 = params["yesterday_dir"]
        t2 = params["today_dir"]

        if not Path(t2).is_dir():
            ctx.log("今日日志目录不存在", level="ERROR")
            emit("done", {"summary": {"total": 0}})
            return

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

            c2 = self._read_crc(f2, ctx)
            c1 = self._read_crc(f1, ctx)
            if not c2 or not c1:
                ctx.log("%s 提取失败，跳过" % filename, level="WARN")
                skipped += 1
                continue

            vendor = detect_vendor(f2.read_text(encoding="utf-8", errors="ignore"))
            vendor_stats[vendor] = vendor_stats.get(vendor, 0) + 1

            all_ifaces = set(c2.keys()) | set(c1.keys())
            for iface in sorted(all_ifaces):
                today = c2.get(iface, 0)
                yesterday = c1.get(iface, 0)
                # 结果表行（8 列，供 GUI 展示）
                results.append([device_name, iface, today, yesterday,
                                today - yesterday, _VENDOR_NAMES.get(vendor, vendor), "", ""])
            processed += 1

        out = self._write_excel(results, date_str, ctx)
        ctx.log("CRC 对比完成：成功 %d 设备 / 跳过 %d，记录 %d 条（日期 %s）-> %s"
                % (processed, skipped, len(results), date_str, out))

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

        原来固定 utf-8 + errors="ignore"：GBK 日志里的中文会被整段吞掉，
        虽然接口名/CRC 这些 ASCII 还能匹配上，但一旦日志是 GBK 且带损坏字节，
        整行会被丢弃、CRC 就解析不到了。
        """
        try:
            raw = path.read_bytes()
        except Exception as e:
            ctx.log("读取失败 %s: %s" % (path.name, e), level="WARN")
            return None
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
        return info if info else None

    @staticmethod
    def _write_excel(rows, date_str, ctx):
        import pandas as pd
        out = ctx.output_dir / "result.xlsx"
        if not rows:
            return None
        # Excel 落盘 7 列（含日期），去掉「状态」「失败原因」两列框架占位
        data = [[date_str, r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
        df = pd.DataFrame(data, columns=_EXCEL_COLUMNS)
        df.sort_values(["设备名称", "接口名称"], inplace=True)
        try:
            df.to_excel(str(out), index=False)
        except Exception as e:
            ctx.log("Excel 导出失败，降级 CSV: %s" % e, level="WARN")
            out = ctx.output_dir / "result.csv"
            df.to_csv(str(out), index=False, encoding="utf-8-sig")
        return out
