# 网络运维工具箱 · Network Operations Toolkit

![Python](https://img.shields.io/badge/Python-3.8.10-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)
![Version](https://img.shields.io/badge/Version-0.1.0-green.svg)
![License](https://img.shields.io/badge/License-Internal%20use-orange.svg)

**把零散运维脚本整合为可视化工具箱：新脚本按契约包装后自动出现在界面上，不用改主程序。**  
**Consolidates scattered ops scripts into a visual toolbox: drop in a script following the contract and it appears in the UI automatically — no main-program changes.**

<p align="center">
  <a href="#chinese">🇨🇳 中文</a> · <a href="#english">🇬🇧 English</a>
</p>

---

<a name="chinese"></a>

## 🇨🇳 中文文档

### ✨ 特性

- 🔌 **插件式接入** — 丢一个 `.py` 进 `tools/` 即自动注册，重启即可见
- 🔐 **凭证不落盘** — Windows DPAPI 加密（Fernet 降级），工具只拿引用不碰明文
- 🌐 **双通道连接** — netmiko / paramiko 按厂商自动切换，覆盖 7 家设备
- ⏰ **双套定时** — 应用内调度（每工具独立时间点）+ Windows 计划任务（系统级流水线）
- 🧩 **12 个内置工具** — 采集 / 备份 / CRC 对比 / 趋势 / 会话管理 / 合规检查
- 🎨 **双主题 UI** — 深色 / 浅色语义色板，高分屏自适应，运行时生成图标
- 📊 **秒开历史结果** — 紧凑 JSON 索引 + 分帧渲染，几千行结果不卡顿

<details>
<summary>📑 目录 / Table of Contents</summary>

- [1. 环境](#1-环境)
- [2. 运行](#2-运行)
- [3. 目录结构](#3-目录结构)
- [4. 如何接入一个新工具](#4-如何接入一个新工具照抄-demo_scanpy)
- [5. 踩坑清单](#5-踩坑清单写工具前必读)
- [6. 连接层说明](#6-连接层说明双通道)
- [7. 当前版本范围](#7-当前版本范围)
- [English Documentation](#english)

</details>

## 1. 环境

- 目标环境：**Python 3.8.10**（与现有脚本一致，不要升级）
- 安装依赖（缺哪个装哪个，全部可选）：

```bash
pip install -r requirements.txt
```

> 依赖都是可选的：没装 netmiko/paramiko 也能启动界面，只是连接类工具会提示依赖缺失；
> 没装 openpyxl 时 Excel 导入不可用、导出自动降级为 CSV。

## 2. 运行

```bash
python main.py              # 启动 GUI
python main.py --list-tools # 列出已注册工具与加载错误
python main.py --selftest   # 无界面冒烟：验证队列/并发/异常隔离/落盘/清单导入
python main.py --cli <tool_id> key=value ...  # 无界面运行指定工具（参数 key=value）
python scripts/prune_output.py               # 清理 output/ 过期任务目录（默认干跑）
python scripts/backfill_crc_index.py         # 给历史 CRC 结果补生成 .json 索引（秒开）
python tests/run_all.py                      # 跑全部回归脚本（仅开发机有 tests/）
```

首次建议先跑 `--selftest`：它用内置的**演示工具**（不连真实设备）跑完整链路，
输出形如 `✅ 自检通过：全链路可跑通（队列/并发/失败隔离/落盘/清单导入）`。

### 日志在哪

| 想知道什么 | 看哪里 |
|---|---|
| 整个程序跑过什么、有没有报错 | **`logs/app.log`**（按天切分备份 `app.log.YYYYMMDD`，保留 30 天） |
| 某一次运行为什么失败 | `output/<工具id>/<时间戳>_<任务id>/run.log`（+ `error.log` / `summary.json`） |
| 全局跑过哪些任务（审计） | `%APPDATA%\NetworkToolkit\audit.log`（每行一条 JSON） |
| 每日流水线 | `logs/daily_crc_YYYYMMDD.log` |
| 定时任务实时输出 | 界面「运行日志」标签（子进程自身的日志仍写在自己的任务目录） |
| 设备侧采集/备份产物 | 你配置的归档目录：`<根>/<日期>/<设备>.log` / `.cfg` / `error_log.txt` |

`logs/` 与 `output/` 都不入库；`app.log` 在**双击启动（无控制台）**时同样会记录，
这是现场排查的第一入口。

### 定时执行：两套机制，别搞混

| 机制 | 入口 | 适用场景 |
|---|---|---|
| **应用内调度**（`core/scheduler.py`） | 界面右上角「定时任务总览」→ 每个工具各设一个时间点 | 程序开着时到点自跑；每个工具都能设，改参数自动同步进快照 |
| **Windows 计划任务**（`scripts/daily_crc.py`） | 下面的命令行 / `install_task.bat` | 程序不用开着，系统级每天定点跑「采集+备份+CRC 对比」整条流水线 |

两者互不影响：应用内调度在 `scripts/schedules.json` 存配置（不入库），
Windows 计划任务由系统自己管。定时任务都是**子进程**跑（`--cli`），
输出回流到界面「运行日志」，结果落各自任务目录。

### 每日自动跑「接口采集 + 配置备份 + CRC 对比」（Windows 计划任务）

**图形操作**：右上角「定时任务」按钮 → 选设备清单 / 日志目录 / 每天几点 → 
「保存配置」→「立即执行一次」验证 →「注册定时任务」。不用改 JSON。

```
读设备清单(Excel)
  └─ 登录每台设备（一次登录取两份）
       ├─ 接口回显 → <接口日志根>/<YYYY-MM-DD>/<设备名>.log
       └─ 设备配置 → <配置备份根>/<YYYY-MM-DD>/<设备名>.cfg
  └─ 取最新两个日期目录做 CRC 对比 → output/crc_compare/<时间戳>/result.xlsx
```

接口数据和配置是**两个文件**（`.log` / `.cfg`，分两个目录），但**只登录一次设备**：
接口命令和配置命令在同一个会话里先后发。

配置备份失败**不阻断** CRC 对比（对比只依赖 `.log`），但退出码会返回 `6`，
方便从计划任务的"上次结果"里看出问题。

```bat
rem 1) 改配置（设备清单路径、日志归档根目录）
notepad scripts\daily_config.json

rem 2) 先看会做什么，不连设备
python scripts\daily_crc.py --config scripts\daily_config.json --dry-run

rem 3) 手动跑一次
python scripts\daily_crc.py --config scripts\daily_config.json

rem 4) 注册成每天 08:30 自动跑
scripts\install_task.bat scripts\daily_config.json 08:30

rem 取消
scripts\install_task.bat --remove
```

`daily_config.json` 只有四项要改：

| 键 | 含义 |
|---|---|
| `devices` | 设备清单 xlsx/csv/txt。表头：`设备名称 / 厂商 / IP / 用户名 / 密码`（可选 `enable password`、`port`） |
| `archive_root` | 接口日志根目录，脚本自动建 `<root>/<日期>/` |
| `backup_enabled` / `backup_root` | 是否顺带备份配置、备份根目录（写 `<设备名>.cfg`） |
| `concurrency` / `timeout` | 并发设备数、单条命令超时 |

行为说明：
- **不假设"今天"这个日期** —— 从目录名解析日期（`2026-10-29` / `20261029` 都认），
  取最新两个目录当「今日/昨日」，采集晚一天也照样能比。
- 采集成功 0 台时**不会**往下跑对比（避免写入空结果）；退出码 `3`。
- 同一天重复触发自动跳过（`--force` 强跑）；日志在 `logs\daily_crc_YYYYMMDD.log`。
- 退出码：`0` 全成功 / `2` 日期目录不足两个 / `3` 采集失败 / `4` 对比失败 / `5` 配置有误 /
  `6` 配置备份失败（其余成功）。
- 手工跑采集时，在「接口信息采集」里填「按日期归档目录」即可，效果一样。

跑完之后「CRC趋势分析」直接读这些历史，攒够 2 天就能出走势图。

### 高分屏（3K 屏，125%~175% 缩放）

界面在 `Window()` 创建之前先声明 Windows DPI 感知（`ui/dpi.py`），
Tk 随后会用真实 DPI 自算 `tk scaling`（dpi/72），文字与控件均按矢量重绘、
不会发糊。切换显示器拖窗时 Tk 8.6.9 无法自动重算 —— 请关闭后重新打开窗口。
"关于"窗口里会显示当前实测 DPI 与 scaling，便于排查。

## 3. 目录结构

```
network-toolkit/
├── main.py                 # 入口（GUI / 自检 / 列工具）
├── core/                   # 框架层（与业务无关）
│   ├── message.py          # 消息契约：MsgType / ItemStatus / 中文文案
│   ├── tool_base.py        # Tool 契约：BaseTool / ParamSpec
│   ├── registry.py         # 注册中心：扫描 tools/ 自动加载
│   ├── task_engine.py      # 任务引擎：Worker 线程 + Queue + 状态机
│   ├── task_context.py     # TaskContext + run_parallel（并发/取消/异常隔离）
│   ├── result_writer.py    # 输出目录、summary.json、导出、审计日志
│   ├── errors.py           # 异常→失败原因映射（netmiko→paramiko→标准库）
│   ├── exit_codes.py       # 退出码统一定义（CLI_* / 流水线两套语义）
│   └── scheduler.py        # 应用内定时调度器（每工具一个时间点，子进程执行）
├── infra/                 # 基础设施层
│   ├── connector.py        # 双通道：netmiko / paramiko（+ telnetlib）
│   ├── credential_store.py # 凭证加密（DPAPI 优先，Fernet 降级）
│   ├── vendor_profiles.py  # 厂商适配加载器，配置见 vendor_profiles.json
│   ├── vendor_parser.py    # 厂商识别 + 配置解析 + CRC 提取（离线文本解析）
│   ├── ini_utils.py        # SecureCRT .ini 读写/字段提取/备份
│   ├── ip_utils.py         # IP/掩码/CIDR 转换（纯函数）
│   ├── naming.py           # 归档文件名规则（<根>/<日期>/<设备名>.<后缀>）
│   ├── crc_history.py      # CRC 历史趋势库（扫描 output 按天聚合 + 紧凑索引）
│   ├── output_prune.py     # output/ 生命周期管理（只删过期任务目录，默认干跑）
│   ├── device_loader.py    # Excel/CSV/TXT 清单导入与校验
│   └── vendor_profiles.json# 厂商命令集/驱动/特殊设备名单（现场唯一维护点）
├── ui/                    # 界面层（tkinter + ttkbootstrap 主题层）
│   ├── dpi.py              # 高 DPI 感知（3K 屏 125%~175%；必须在 Tk 创建前调用）
│   ├── theme.py            # UI 设计系统 v0.2：双主题语义 token + 图标映射
│   ├── widgets.py          # 自绘组件：圆角卡片/进度条/分段控件/页签
│   ├── icons.py            # Pillow 运行时画线性图标（不落盘、带缓存）
│   ├── chart_view.py       # matplotlib 图表网格卡片（放大/悬浮取值/导出）
│   ├── toggle_style.py     # 拨钮配色（开=青 #00d4ff / 关=灰）
│   ├── main_window.py      # 主窗口、主题切换、控件状态机、after 轮询
│   ├── form_builder.py     # Schema 驱动表单生成（ToolTip 帮助提示、双列自适应）
│   ├── result_view.py      # 结果表（排序/筛选/导出；失败行使用主题语义色）
│   ├── log_view.py         # 运行日志（等级配色跟随主题）
│   ├── auto_task_dialog.py # 「定时任务」设置窗口（应用内到点自跑）
│   ├── schedule_overview.py# 「定时任务总览」窗口（下次时间/运行状态/上次结果）
│   └── credential_dialog.py# 凭证管理窗口（DPAPI 加密，明文不落盘）
├── tools/                 # 工具插件目录：丢一个文件进去即可
│   ├── demo_scan.py        # 演示工具（无需真实设备）
│   ├── batch_backup.py     # 批量备份配置（真实样板）
│   ├── iface_collect.py    # 接口信息采集（迪普/烽火等）
│   ├── iface_desc.py       # 接口描述采集
│   ├── crc_compare.py      # 接口 CRC 对比分析（今 vs 昨）
│   ├── crc_trend.py        # CRC 趋势分析（走势图/排行条形图）
│   ├── ip_extract.py       # 接口IP/管理口/环回口提取
│   ├── ip_calc.py          # IP/CIDR 转换计算
│   ├── session_extract.py  # 会话信息提取
│   ├── session_rename.py   # 会话 Hostname 批量修改
│   ├── session_sync.py     # 会话配置项同步（密码等）
│   └── zte_check.py        # 中兴交换机合规检查
├── scripts/               # 独立脚本（定时/维护，不进界面工具列表）
│   ├── daily_crc.py        # 每日流水线：采集+备份+CRC 对比（Windows 计划任务用）
│   ├── install_task.bat    # 注册/取消 Windows 计划任务
│   ├── prune_output.py     # 清理过期任务目录（默认干跑）
│   ├── backfill_crc_index.py# 给历史 xlsx 批量补 .json 索引（秒开）
│   └── push_via_api.py     # 通过 GitHub API 推送（内部脚本，不入库）
└── output/                # 结果输出：output/{tool_id}/{时间戳}_{task}/...

> `tests/`（本地回归脚本）**不入库**，只在开发机上保留、已被 .gitignore 忽略。
> 它不属于程序本体，运行完全不依赖；内容与用途见 tests/README_本地测试.md。
```

## 4. 如何接入一个新工具（照抄 demo_scan.py）

```python
from core.tool_base import BaseTool, ParamSpec


class MyTool(BaseTool):
    id = "my_tool"                     # 唯一，决定结果目录名
    name = "我的工具"
    version = "1.0.0"
    category = "采集类"

    params = [
        ParamSpec(key="devices", label="设备清单", type="device_list", required=True),
        ParamSpec(key="concurrency", label="并发数", type="int", default=10, min=1, max=50),
    ]

    result_columns = ["IP", "设备名", "状态", "耗时(ms)", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params["devices"])

        def worker(dev):                     # 只写单台逻辑，异常不用管
            with ctx.open_session(dev, timeout=30) as s:
                text = s.send("display version")
            return [dev.ip, dev.hostname, "", 100, ""]   # 5 项，对应 5 列

        ctx.run_parallel(devices, worker, result_columns=self.result_columns,
                         max_workers=params["concurrency"])
        emit("done", {"summary": {"total": len(devices)}})
```

然后**重启工具箱**即可在左侧看到它。参数类型支持：
`text / password / int / bool / choice / file / dir / device_list`。

## 5. 踩坑清单（写工具前必读）

| # | 坑 | 正确做法 |
|---|----|----------|
| 1 | 在 Worker 线程里改界面控件 | **禁止**。只用 `emit()` / `ctx.log()`，界面由主线程更新 |
| 2 | `row` 长度与 `result_columns` 不一致 | 必须等长，否则该行被丢弃并记 ERROR |
| 3 | 自己 try/except 后 continue，导致失败项没进结果表 | 异常直接往外抛，`run_parallel` 会自动隔离并归类原因 |
| 4 | 自己建线程池 | 用 `ctx.run_parallel`，它已内置并发、取消检查、异常隔离 |
| 5 | 用 `list[str]` / `int \| None` | Python 3.8 不支持，必须用 `List[str]` / `Optional[int]` |
| 6 | 自己读凭证文件 | 用 `ctx.get_credential(ref)`；凭证永不明文落盘 |
| 7 | 往结果目录外写文件 | 只写 `ctx.output_dir` 与 `ctx.raw_dir` |
| 8 | 「状态」「失败原因」两列自己填 | 框架会覆盖成统一中文文案，工具填空字符串即可 |

## 6. 连接层说明（双通道）

`ctx.open_session()` 对工具屏蔽差异，通道由 `infra/vendor_profiles.json` 决定：

| 通道 | 触发 | 适用 |
|------|------|------|
| netmiko | `driver: netmiko`（默认），协议 ssh | 华为/华三/中兴/思科/锐捷，自动处理分页与 enable |
| paramiko | `driver: paramiko` | 烽火/迪普，自建分页处理 |
| telnetlib | 设备 `protocol: telnet` | Python 3.8 标准库内置 |

**新增厂商**：改 `vendor_profiles.json` 即可（填 `cmds`、`paging`、`driver`、`netmiko_device_type`），不改代码。

### 各厂商采集命令与特殊处理

命令与特殊逻辑**全部来自现场实测脚本**：
`D://test//object//InterfaceCollectAndanalysis&comparison//netmiko_SSH_device_system.py`（接口采集）
与 `D://test//object//BackupConifg//netmiko_SSH.py`（配置备份）。

| 厂商 | 通道 | 接口采集 | 配置备份 | 登录后关分屏 |
|------|------|----------|----------|--------------|
| 华为 | netmiko | `display interface` | `display current-configuration` | `screen-length 0 temporary` |
| 华三 | netmiko | `display interface` | `display current-configuration` | — |
| 中兴 | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| 思科 | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| 锐捷 | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| 烽火 | paramiko | 防火墙 `show interface`／交换机 `show interface verbose` | `show running-config` | 防火墙 `terminal line 0`／交换机 `terminal length 0` |
| 迪普 | paramiko | `show interface` | `show running-config` | `terminal line 0` |

两处特殊设备处理（保留现场逻辑）：

1. **华为 E1000**：普通关分屏命令不生效，要进 vty 改 ——
   `sys` → `user-interface vty 0 4` → `screen-length 0` → 执行命令 → `undo screen-length` → `return`。
   这几条命令没有可识别的结束标志，用 `send_timing` 发（不等提示符）；
   **命令必须夹在 `undo screen-length` 之前**（undo 后分页立刻恢复，长输出会卡住），
   **收尾必须 `return`**（不退出 vty 视图，同会话后续命令会等错提示符）。
2. **烽火配置备份分屏**：一批指定 IP 用 `terminal line 0`，其余用 `terminal length 0`。

> 以上名单（E1000 的 IP、烽火分屏 IP、迪普防火墙的设备名标记 `DP`/`FW1000`）
> **只在 `infra/vendor_profiles.json` 里维护**（`e1000.ips` /
> `vendors.fiberhome.paging_off_by_ip.ips` / `...products.firewall.name_marks`），
> README 不再抄写，避免文档与配置漂移。换设备只改这一个文件。

另外：归档的 `.log` / `.cfg` **首行写设备类型标识**（`zte_zxros`/`hp_comware`/`cisco_ios`/
`ruijie_os`/`huawei`/`FiberHome`/`DPtech`）—— CRC 对比的解析器靠它准确识别厂商，
只靠正文关键词猜会把华三设备误判成华为。

## 7. 当前版本范围

程序版本号 `APP_VERSION = 0.1.0`（`main.py`），UI 设计系统为 v0.2（`ui/theme.py`）——
两套版本号各自独立，不要混为一谈。

**框架能力**：注册中心（丢文件即接入）、异步引擎、取消、异常隔离、结果落盘与导出、
清单导入（CSV/TXT/xlsx）、凭证加密（DPAPI/Fernet）、双通道连接层、
应用内定时调度 + Windows 计划任务、`--cli` 无界面运行、双主题 UI。

**已整合工具（12 个）**：批量备份、接口信息采集、接口描述采集、接口 CRC 对比分析、
CRC 趋势分析、接口IP/管理口提取、IP/CIDR 转换、会话信息提取、会话 Hostname 修改、
会话配置同步、中兴交换机合规检查，另有演示工具 `demo_scan`。

**后续版本**：地址扫描与信息采集工具、结果统计页、参数模板、失败项重试、打包 exe。
**明确不做**：账号权限、Web 化、配置下发。

> 本工具会登录网络设备执行命令，请确认已获得设备管理方授权，并使用只读账号。

---

<a name="english"></a>

## 🇬🇧 English Documentation

### ✨ Features

- 🔌 **Plugin-based** — drop a `.py` into `tools/` to auto-register; visible after restart
- 🔐 **No plaintext credentials** — Windows DPAPI (Fernet fallback); tools receive references only
- 🌐 **Dual-channel connectivity** — netmiko / paramiko switched per vendor, covering 7 device families
- ⏰ **Two scheduling mechanisms** — in-app scheduler (per-tool timer) + Windows Task Scheduler (system-level pipeline)
- 🧩 **12 built-in tools** — collection / backup / CRC comparison / trends / session management / compliance
- 🎨 **Dual-theme UI** — dark / light semantic palettes, HiDPI-aware, runtime-generated icons
- 📊 **Instant history open** — compact JSON index + chunked rendering; thousands of rows stay responsive

<p align="center"><a href="#chinese">⬆ 返回中文 / Back to Chinese</a></p>
### 1. Environment

- Target environment: **Python 3.8.10** (matches the existing scripts; do not upgrade)
- Install dependencies (install only what's missing; all are optional):

```bash
pip install -r requirements.txt
```

> All dependencies are optional: the UI starts even without netmiko/paramiko, but connection-based tools will report missing dependencies.
> Without openpyxl, Excel import is unavailable and exports fall back to CSV automatically.

### 2. Running

```bash
python main.py              # Launch the GUI
python main.py --list-tools # List registered tools and load errors
python main.py --selftest   # Headless smoke test: queue / concurrency / failure isolation / output / device list import
python main.py --cli <tool_id> key=value ...  # Run a tool headlessly (parameters as key=value)
python scripts/prune_output.py               # Clean expired task directories in output/ (dry run by default)
python scripts/backfill_crc_index.py         # Generate .json indexes for historical CRC results (fast open)
python tests/run_all.py                      # Run all regression scripts (tests/ is dev-machine only)
```

Run `--selftest` first: it uses the built-in **demo tool** (no real devices) to exercise the full pipeline,
printing something like `✅ Self-test passed: full pipeline OK (queue / concurrency / failure isolation / output / device list import)`.

#### Where to find logs

| What you want to know | Where to look |
|---|---|
| What the program did, and any errors | **`logs/app.log`** (rotated daily to `app.log.YYYYMMDD`, kept for 30 days) |
| Why a specific run failed | `output/<tool_id>/<timestamp>_<task_id>/run.log` (+ `error.log` / `summary.json`) |
| Global task history (audit) | `%APPDATA%\NetworkToolkit\audit.log` (one JSON line per entry) |
| Daily pipeline | `logs/daily_crc_YYYYMMDD.log` |
| Live scheduled-task output | The "Run Log" tab in the UI (the subprocess's own log stays in its task directory) |
| Device-side collection/backup artifacts | Your configured archive root: `<root>/<date>/<device>.log` / `.cfg` / `error_log.txt` |

Neither `logs/` nor `output/` is tracked by git. `app.log` is also written when launching by double-click
(no console), making it the first stop for field troubleshooting.

#### Scheduled execution: two mechanisms, don't confuse them

| Mechanism | Entry point | Use case |
|---|---|---|
| **In-app scheduler** (`core/scheduler.py`) | UI top-right "Scheduled Tasks" → set a time per tool | Runs automatically while the app is open; every tool can have its own schedule; parameter changes sync into the snapshot |
| **Windows Task Scheduler** (`scripts/daily_crc.py`) | The commands below / `install_task.bat` | Runs the full "collect + backup + CRC compare" pipeline daily at system level, even when the app is closed |

They are independent: the in-app scheduler keeps its config in `scripts/schedules.json` (not tracked by git),
while Windows Task Scheduler is managed by the OS. Scheduled tasks always run in a **subprocess** (`--cli`);
output flows back to the UI's "Run Log" and results land in their own task directories.

#### Daily pipeline: "interface collection + config backup + CRC compare" (Windows Task Scheduler)

**GUI approach**: top-right "Scheduled Tasks" button → pick device list / log directory / daily time →
"Save Config" → "Run Once Now" to verify → "Register Scheduled Task". No JSON editing needed.

```
Read device list (Excel)
  └─ Log in to each device (one login, two artifacts)
       ├─ Interface output → <interface log root>/<YYYY-MM-DD>/<device>.log
       └─ Device config    → <config backup root>/<YYYY-MM-DD>/<device>.cfg
  └─ Compare the two latest date directories → output/crc_compare/<timestamp>/result.xlsx
```

Interface data and config are **two files** (`.log` / `.cfg`, in two directories), but the device is
**logged into only once**: the interface command and the config command are sent in the same session.

A config backup failure does **not block** the CRC comparison (comparison depends only on `.log`),
but the exit code is `6` so the problem is visible in Task Scheduler's "Last Run Result".

```bat
rem 1) Edit the config (device list path, log archive root)
notepad scripts\daily_config.json

rem 2) Preview what would happen, without connecting to devices
python scripts\daily_crc.py --config scripts\daily_config.json --dry-run

rem 3) Run once manually
python scripts\daily_crc.py --config scripts\daily_config.json

rem 4) Register to run daily at 08:30
scripts\install_task.bat scripts\daily_config.json 08:30

rem Unregister
scripts\install_task.bat --remove
```

Only four keys in `daily_config.json` need changing:

| Key | Meaning |
|---|---|
| `devices` | Device list xlsx/csv/txt. Headers: `device name / vendor / IP / username / password` (optional `enable password`, `port`) |
| `archive_root` | Interface log root; the script creates `<root>/<date>/` automatically |
| `backup_enabled` / `backup_root` | Whether to back up config, and where (writes `<device>.cfg`) |
| `concurrency` / `timeout` | Concurrent device count, per-command timeout |

Behavior notes:
- **No assumption about "today's" date** — dates are parsed from directory names (`2026-10-29` / `20261029` both work),
  and the two newest directories are used as "today/yesterday", so a delayed collection still compares correctly.
- If collection succeeds for 0 devices, the comparison is **not** run (avoids empty results); exit code `3`.
- Re-triggering on the same day is skipped automatically (`--force` overrides); logs go to `logs\daily_crc_YYYYMMDD.log`.
- Exit codes: `0` all success / `2` fewer than two date directories / `3` collection failed / `4` comparison failed / `5` bad config /
  `6` config backup failed (everything else succeeded).
- For manual collection, fill in "Archive Directory by Date" in "Interface Info Collection" for the same effect.

Once done, "CRC Trend Analysis" reads these histories directly; two days of data are enough for a trend chart.

#### High-DPI displays (3K screens, 125%–175% scaling)

The UI declares Windows DPI awareness before creating `Window()` (`ui/dpi.py`),
so Tk computes `tk scaling` (dpi/72) from the real DPI; text and controls are redrawn as vectors and stay sharp.
Tk 8.6.9 cannot recompute when dragging the window between monitors — close and reopen the window.
The "About" dialog shows the measured DPI and scaling for troubleshooting.

### 3. Directory structure

```
network-toolkit/
├── main.py                      # Entry point (GUI / self-test / list tools)
├── core/                        # Framework layer (business-agnostic)
│   ├── message.py               # Message contract: MsgType / ItemStatus / Chinese text
│   ├── tool_base.py             # Tool contract: BaseTool / ParamSpec
│   ├── registry.py              # Registry: scans tools/ and auto-loads
│   ├── task_engine.py           # Task engine: Worker thread + Queue + state machine
│   ├── task_context.py          # TaskContext + run_parallel (concurrency / cancel / failure isolation)
│   ├── result_writer.py         # Output dir, summary.json, export, audit log
│   ├── errors.py                # Exception → failure reason mapping (netmiko → paramiko → stdlib)
│   ├── exit_codes.py            # Exit codes (two semantics: CLI_* / pipeline)
│   └── scheduler.py             # In-app scheduler (one time per tool, subprocess execution)
├── infra/                       # Infrastructure layer
│   ├── connector.py             # Dual channel: netmiko / paramiko (+ telnetlib)
│   ├── credential_store.py      # Credential encryption (DPAPI first, Fernet fallback)
│   ├── vendor_profiles.py       # Vendor profile loader, config in vendor_profiles.json
│   ├── vendor_parser.py         # Vendor detection + config parsing + CRC extraction (offline)
│   ├── ini_utils.py             # SecureCRT .ini read/write / field extraction / backup
│   ├── ip_utils.py              # IP / mask / CIDR conversion (pure functions)
│   ├── naming.py                # Archive file naming (<root>/<date>/<device>.<ext>)
│   ├── crc_history.py           # CRC history store (scans output, aggregates by day + compact index)
│   ├── output_prune.py          # output/ lifecycle (deletes only expired task dirs, dry run by default)
│   ├── device_loader.py         # Excel/CSV/TXT device list import and validation
│   └── vendor_profiles.json     # Vendor commands / drivers / special-device lists (single field source)
├── ui/                          # UI layer (tkinter + ttkbootstrap theme)
│   ├── dpi.py                   # High-DPI awareness (must be called before Tk is created)
│   ├── theme.py                 # UI design system v0.2: dual-theme semantic tokens + icon map
│   ├── widgets.py               # Custom widgets: rounded cards / progress bar / segmented control / tabs
│   ├── icons.py                 # Pillow-generated line icons (in-memory, cached)
│   ├── chart_view.py            # matplotlib chart grid cards (zoom / hover values / export)
│   ├── toggle_style.py          # Toggle switch colors (on = cyan #00d4ff / off = neutral gray)
│   ├── main_window.py           # Main window, theme switching, widget state machine, after() polling
│   ├── form_builder.py          # Schema-driven form generation (ToolTips, adaptive two columns)
│   ├── result_view.py           # Result table (sort / filter / export; semantic colors for failures)
│   ├── log_view.py              # Run log (level colors follow theme)
│   ├── auto_task_dialog.py      # "Scheduled Tasks" settings dialog (in-app timed execution)
│   ├── schedule_overview.py     # "Scheduled Task Overview" (next run / running / last result)
│   └── credential_dialog.py     # Credential manager (DPAPI encrypted, never plain text)
├── tools/                       # Tool plugin directory: drop a file in to register
│   ├── demo_scan.py             # Demo tool (no real devices needed)
│   ├── batch_backup.py          # Batch config backup (real-world template)
│   ├── iface_collect.py         # Interface info collection (DPtech / FiberHome / etc.)
│   ├── iface_desc.py            # Interface description collection
│   ├── crc_compare.py           # Interface CRC comparison (today vs yesterday)
│   ├── crc_trend.py             # CRC trend analysis (line chart / ranking bar chart)
│   ├── ip_extract.py            # Interface IP / management port / loopback extraction
│   ├── ip_calc.py               # IP / CIDR conversion
│   ├── session_extract.py       # Session info extraction
│   ├── session_rename.py        # Batch session hostname modification
│   ├── session_sync.py          # Session config sync (passwords, etc.)
│   └── zte_check.py             # ZTE switch compliance check
├── scripts/                     # Standalone scripts (scheduling / maintenance, not in the tool list)
│   ├── daily_crc.py             # Daily pipeline: collect + backup + CRC compare (Windows Task Scheduler)
│   ├── install_task.bat         # Register / unregister Windows scheduled task
│   ├── prune_output.py          # Clean expired task directories (dry run by default)
│   ├── backfill_crc_index.py    # Generate .json indexes for historical xlsx (fast open)
│   └── push_via_api.py          # Push via GitHub API (internal, not tracked)
└── output/                      # Results: output/{tool_id}/{timestamp}_{task}/...
```

> `tests/` (local regression scripts) is **not tracked by git**; it exists only on dev machines and is ignored via .gitignore.
> It is not part of the program and is not needed at runtime; see `tests/README_本地测试.md` for its purpose.

### 4. Adding a new tool (copy demo_scan.py)

```python
from core.tool_base import BaseTool, ParamSpec


class MyTool(BaseTool):
    id = "my_tool"                     # Unique; determines the result directory name
    name = "My Tool"
    version = "1.0.0"
    category = "Collection"

    params = [
        ParamSpec(key="devices", label="Device List", type="device_list", required=True),
        ParamSpec(key="concurrency", label="Concurrency", type="int", default=10, min=1, max=50),
    ]

    result_columns = ["IP", "Hostname", "Status", "Elapsed (ms)", "Failure Reason"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params["devices"])

        def worker(dev):                     # Only per-device logic; don't handle exceptions
            with ctx.open_session(dev, timeout=30) as s:
                text = s.send("display version")
            return [dev.ip, dev.hostname, "", 100, ""]   # 5 items, matching 5 columns

        ctx.run_parallel(devices, worker, result_columns=self.result_columns,
                         max_workers=params["concurrency"])
        emit("done", {"summary": {"total": len(devices)}})
```

Then **restart the toolbox** and the tool appears in the left navigation. Supported parameter types:
`text / password / int / bool / choice / file / dir / device_list`.

### 5. Pitfalls (read before writing a tool)

| # | Pitfall | Correct approach |
|---|---------|------------------|
| 1 | Touching UI widgets from a Worker thread | **Forbidden**. Only use `emit()` / `ctx.log()`; the main thread updates the UI |
| 2 | `row` length differs from `result_columns` | They must match, or the row is dropped and an ERROR is logged |
| 3 | Catching exceptions and `continue`-ing yourself, so failures never reach the result table | Let exceptions propagate; `run_parallel` isolates them and categorizes the reason |
| 4 | Creating your own thread pool | Use `ctx.run_parallel`; it already handles concurrency, cancellation checks, and failure isolation |
| 5 | Using `list[str]` / `int \| None` | Not supported on Python 3.8; use `List[str]` / `Optional[int]` |
| 6 | Reading credential files yourself | Use `ctx.get_credential(ref)`; credentials are never stored in plain text |
| 7 | Writing outside the result directory | Only write to `ctx.output_dir` and `ctx.raw_dir` |
| 8 | Filling in "Status" / "Failure Reason" columns yourself | The framework overwrites them with unified Chinese text; leave empty strings |

### 6. Connection layer (dual channel)

`ctx.open_session()` hides the differences from tools; the channel is chosen by `infra/vendor_profiles.json`:

| Channel | Trigger | Suitable for |
|---|---|---|
| netmiko | `driver: netmiko` (default), protocol ssh | Huawei / H3C / ZTE / Cisco / Ruijie; handles paging and enable automatically |
| paramiko | `driver: paramiko` | FiberHome / DPtech; custom paging handling |
| telnetlib | device `protocol: telnet` | Built into the Python 3.8 standard library |

**Adding a vendor**: just edit `vendor_profiles.json` (`cmds`, `paging`, `driver`, `netmiko_device_type`); no code changes.

#### Per-vendor collection commands and special handling

Commands and special logic come entirely from **field-tested scripts**:
`D://test//object//InterfaceCollectAndanalysis&comparison//netmiko_SSH_device_system.py` (interface collection)
and `D://test//object//BackupConifg//netmiko_SSH.py` (config backup).

| Vendor | Channel | Interface collection | Config backup | Disable paging after login |
|---|---|---|---|---|
| Huawei | netmiko | `display interface` | `display current-configuration` | `screen-length 0 temporary` |
| H3C | netmiko | `display interface` | `display current-configuration` | — |
| ZTE | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| Cisco | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| Ruijie | netmiko | `show interface` | `show running-config` | `terminal length 0` |
| FiberHome | paramiko | firewall `show interface` / switch `show interface verbose` | `show running-config` | firewall `terminal line 0` / switch `terminal length 0` |
| DPtech | paramiko | `show interface` | `show running-config` | `terminal line 0` |

Two special-device behaviors (preserved from field logic):

1. **Huawei E1000**: the normal paging-disable command does not work; you must enter vty —
   `sys` → `user-interface vty 0 4` → `screen-length 0` → run commands → `undo screen-length` → `return`.
   These commands have no recognizable end marker, so they are sent with `send_timing` (no prompt wait);
   **commands must come before `undo screen-length`** (paging returns immediately after undo, and long output hangs),
   and **the sequence must end with `return`** (staying in vty view makes subsequent commands wait for the wrong prompt).
2. **FiberHome config backup paging**: a specific set of IPs uses `terminal line 0`, all others use `terminal length 0`.

> The above lists (E1000 IPs, FiberHome paging IPs, DPtech firewall name marks `DP`/`FW1000`)
> are maintained **only in `infra/vendor_profiles.json`** (`e1000.ips` /
> `vendors.fiberhome.paging_off_by_ip.ips` / `...products.firewall.name_marks`);
> the README no longer copies them, to avoid doc/config drift. Changing devices means editing that one file.

Also: archived `.log` / `.cfg` files have the device-type identifier on the **first line** (`zte_zxros`/`hp_comware`/
`cisco_ios`/`ruijie_os`/`huawei`/`FiberHome`/`DPtech`) — the CRC comparison parser relies on it to identify the vendor
accurately; guessing from body keywords alone would misidentify H3C devices as Huawei.

### 7. Current scope

The program version is `APP_VERSION = 0.1.0` (`main.py`), while the UI design system is v0.2 (`ui/theme.py`) —
two independent version numbers; do not confuse them.

**Framework capabilities**: registry (drop in a file to register), async engine, cancellation, failure isolation,
result output and export, device list import (CSV/TXT/xlsx), credential encryption (DPAPI/Fernet),
dual-channel connection layer, in-app scheduling + Windows Task Scheduler, `--cli` headless execution, dual-theme UI.

**Integrated tools (12)**: batch backup, interface info collection, interface description collection,
interface CRC comparison, CRC trend analysis, interface IP / management port extraction, IP/CIDR conversion,
session info extraction, session hostname modification, session config sync, ZTE switch compliance check,
plus the `demo_scan` demo tool.

**Future versions**: address scanning and info collection tools, a results statistics page, parameter templates,
failed-item retry, and exe packaging.
**Explicitly out of scope**: account permissions, web UI, and config deployment.

> This tool logs into network devices to run commands. Confirm that you have authorization from the device owner and use a read-only account.

---

<p align="center">
  <a href="#网络运维工具箱--network-operations-toolkit">⬆ Back to top</a> ·
  <a href="#chinese">🇨🇳 中文</a> ·
  <a href="#english">🇬🇧 English</a>
</p>
