# -*- coding: utf-8 -*-
"""SecureCRT 会话信息提取（收敛 IniToNameandIP.py、getIpHostname.py、filesToexcel.py）。

从 .ini 会话文件提取 Username/Hostname（及可选字段），导出 Excel/CSV。
"""
from core.tool_base import BaseTool, ParamSpec
from infra.ini_utils import read_ini, extract_field, iter_ini_files


class SessionExtractTool(BaseTool):
    id = "session_extract"
    name = "会话信息提取"
    version = "1.0.0"
    category = "解析类"
    description = "从 SecureCRT .ini 会话文件提取 Username/Hostname 等信息，导出 Excel"

    params = [
        ParamSpec(key="folder", label="会话目录", type="dir", required=True,
                  help="包含 .ini 会话文件的目录（可递归）"),
        ParamSpec(key="mode", label="提取内容", type="choice", default="主机信息",
                  options=[
                      {"label": "主机信息", "value": "主机信息"},
                      {"label": "仅文件名", "value": "仅文件名"},
                  ],
                  help="主机信息：文件名+Username+Hostname；仅文件名：只列文件名"),
    ]

    result_columns = ["文件名", "Username", "Hostname", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        folder = params["folder"]
        mode = params.get("mode", "主机信息")

        files = iter_ini_files(folder, recursive=True)
        if not files:
            ctx.log("目录下未找到 .ini 文件", level="WARN")
            emit("done", {"summary": {"total": 0}})
            return

        rows = []
        for path in files:
            name = path.stem
            if mode == "仅文件名":
                rows.append([name, "", "", "", ""])
                continue
            content = read_ini(path)
            if content is None:
                rows.append([name, "编码错误", "编码错误", "失败", "无法解码"])
                continue
            username = extract_field(content, "username") or "未找到"
            hostname = extract_field(content, "hostname") or "未找到"
            rows.append([name, username, hostname, "", ""])

        out = ctx.output_dir / "result.xlsx"
        try:
            import pandas as pd
            df = pd.DataFrame(rows, columns=self.result_columns)
            df.to_excel(str(out), index=False)
        except Exception as e:
            ctx.log("Excel 导出失败，降级 CSV: %s" % e, level="WARN")
            import csv
            out = ctx.output_dir / "result.csv"
            with open(str(out), "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(self.result_columns)
                w.writerows(rows)

        # 结果表展示真实明细行
        for r in rows:
            emit("result", {"row": r, "status": "OK" if r[3] != "失败" else "FAIL_PARSE"})
        ctx.log("提取完成：%d 个文件 -> %s" % (len(files), out))
        emit("done", {"summary": {"total": len(files)}})
