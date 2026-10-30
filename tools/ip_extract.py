# -*- coding: utf-8 -*-
"""接口 IP / 管理口 / 环回口 提取工具（收敛 getIp.py 与 extract_ips.py 两者）。

离线解析配置文件（.txt/.cfg/.conf/.log 等），输出 Excel：
- 模式「接口地址」：提取每个接口的 IP/CIDR + VRRP 网关（原 extract_ips.py 超集）
- 模式「管理/环回」：提取管理口与环回口地址（原 getIp.py）
"""
import os
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra.vendor_parser import (
    detect_vendor, extract_interface_ips, extract_mgmt_loopback,
)

CONFIG_EXTS = {".txt", ".cfg", ".conf", ".log", ".config"}


class IpExtractTool(BaseTool):
    id = "ip_extract"
    name = "接口IP/管理口提取"
    version = "1.0.0"
    category = "解析类"
    description = "离线解析设备配置文件，提取接口IP/CIDR、VRRP网关或管理口/环回口地址，导出 Excel"

    params = [
        ParamSpec(key="folder", label="配置文件目录", type="dir", required=True,
                  help="包含设备配置文件的目录（.txt/.cfg/.conf/.log 等）"),
        ParamSpec(key="mode", label="提取模式", type="choice", default="接口地址",
                  options=[
                      {"label": "接口地址", "value": "接口地址"},
                      {"label": "管理/环回口", "value": "管理/环回口"},
                  ],
                  help="接口地址：接口IP/CIDR+VRRP网关；管理/环回口：管理口与环回口地址"),
    ]

    result_columns = ["设备名", "厂商", "接口", "地址段", "网关", "IP版本", "网关类型", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        folder = params["folder"]
        mode = params.get("mode", "接口地址")

        files = [p for p in Path(folder).rglob("*")
                 if p.is_file() and p.suffix.lower() in CONFIG_EXTS]
        if not files:
            ctx.log("目录下未找到配置文件", level="WARN")
            emit("done", {"summary": {"total": 0}})
            return

        ctx.log("开始解析 %d 个配置文件，模式=%s" % (len(files), mode))

        def worker(path):
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                raise IOError("读取失败: %s" % e)

            device_name = path.stem
            vendor = detect_vendor(content)

            if mode == "接口地址":
                rows = []
                for cfg in extract_interface_ips(content, vendor):
                    rows.append([
                        device_name, vendor.upper(), cfg["interface"], cfg["cidr"],
                        cfg["gateway"], "IPv%d" % cfg["version"],
                        "VRRP虚拟IP" if cfg["has_vrrp"] else "接口IP", "", "",
                    ])
                return rows
            else:
                result = extract_mgmt_loopback(content)
                mgmt = "; ".join(result["管理地址"]) or "未找到"
                loop = "; ".join(result["环回口地址"]) or "未找到"
                # 管理/环回模式复用列：接口列放「管理地址」，地址段放「环回口地址」
                return [[device_name, result["vendor"].upper(), "管理地址", mgmt,
                         loop, "", "", "", ""]]

        all_rows = []
        for i, path in enumerate(files, 1):
            emit("progress", {"current": i, "total": len(files)})
            try:
                out = worker(path)
            except Exception as e:
                ctx.log("%s 解析失败: %s" % (path.name, e), level="WARN")
                # 单文件失败记一行失败，不中断整批
                all_rows.append([path.stem, "", "", "", "", "", "", "失败", str(e)])
                continue
            if isinstance(out, list) and out and isinstance(out[0], list):
                all_rows.extend(out)

        # 落盘 Excel
        xlsx_path = self._write_excel(all_rows, ctx)
        ctx.log("解析完成：%d 条记录 -> %s" % (len(all_rows), xlsx_path))

        # 结果表展示真实明细行（列与内容对应）
        for r in all_rows:
            status = r[-2] if len(r) > 6 else ""
            emit("result", {"row": r, "status": "OK" if status != "失败" else "FAIL_PARSE"})
        emit("done", {"summary": {"total": len(files), "records": len(all_rows)}})

    def _write_excel(self, rows, ctx):
        import pandas as pd
        df = pd.DataFrame(rows, columns=self.result_columns)
        out = ctx.output_dir / "result.xlsx"
        try:
            df.to_excel(str(out), index=False)
            return out
        except Exception as e:
            ctx.log("Excel 导出失败，降级 CSV: %s" % e, level="WARN")
            out_csv = ctx.output_dir / "result.csv"
            df.to_csv(str(out_csv), index=False, encoding="utf-8-sig")
            return out_csv
