# 网络运维工具箱 v0.1（骨架）

把零散运维脚本整合为可视化工具箱：**新脚本按契约包装后自动出现在界面上**，不用改主程序。

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
```

首次建议先跑 `--selftest`：它用内置的**演示工具**（不连真实设备）跑完整链路，
输出形如 `✅ 自检通过：全链路可跑通（队列/并发/失败隔离/落盘/清单导入）`。

### 每日自动跑「接口采集 + 配置备份 + CRC 对比」

工具本身没有内置定时器，用 `scripts/daily_crc.py` + Windows 计划任务串成一条链。

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
│   ├── result_writer.py    # 输出目录、summary.json、导出
│   └── errors.py           # 异常→失败原因映射（netmiko→paramiko→标准库）
├── infra/                 # 基础设施层
│   ├── connector.py        # 双通道：netmiko / paramiko（+ telnetlib）
│   ├── credential_store.py # 凭证加密（DPAPI 优先，Fernet 降级）
│   ├── vendor_profiles.py  # 厂商适配（命令集/驱动），配置见 .json
│   ├── vendor_parser.py    # 厂商识别 + 配置解析 + CRC 提取（离线文本解析）
│   ├── ini_utils.py        # SecureCRT .ini 读写/字段提取/备份
│   ├── ip_utils.py         # IP/掩码/CIDR 转换（纯函数）
│   ├── crc_history.py      # CRC 历史趋势库（扫描 output 按天聚合）
│   └── device_loader.py    # Excel/CSV/TXT 清单导入与校验
├── ui/                    # 界面层（tkinter + ttkbootstrap 主题层）
│   ├── dpi.py              # 高 DPI 感知（3K 屏 125%~175%；必须在 Tk 创建前调用）
│   ├── main_window.py      # 主窗口、主题切换、控件状态机、after(100ms) 轮询
│   ├── form_builder.py     # Schema 驱动表单生成（ToolTip 帮助提示、双列自适应）
│   ├── result_view.py      # 结果表（排序/筛选/导出；失败行使用主题语义色）
│   └── log_view.py         # 运行日志（等级配色跟随主题）
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
├── tests/                 # 验证脚本（不属于程序本体，改完代码跑一遍）
│   ├── run_all.py         # 一键跑全部：python tests/run_all.py
│   ├── test_fenghuo.py    # 烽火/迪普产品分流、!end 结束标记、paramiko 读取完整性
│   ├── test_tools.py      # 采集类工具：命令选择 / 状态语义 / 归档内容（假会话）
│   └── test_sched.py      # 多任务定时器 + 工具定时条 + 总览窗口（需要 GUI 环境）
└── output/                # 结果输出：output/{tool_id}/{时间戳}_{task}/...
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

1. **华为 E1000**（`10.185.2.3 / 10.185.2.4 / 172.24.249.34 / 172.24.252.58 /
   10.182.250.15 / 10.182.250.16`）：普通关分屏命令不生效，要进 vty 改 ——
   `sys` → `user-interface vty 0 4` → `screen-length 0` → 执行命令 → `undo screen-length`。
   这几条命令没有可识别的结束标志，用 `send_timing` 发（不等提示符）。
2. **烽火配置备份**：`172.20.249.201 / .202 / 172.22.164.89 / .90 / 172.20.249.184 /
   172.20.249.88 / 172.24.249.38 / .39` 这批用 `terminal line 0`，其余 `terminal length 0`。

另外：归档的 `.log` / `.cfg` **首行写设备类型标识**（`zte_zxros`/`hp_comware`/`cisco_ios`/
`ruijie_os`/`huawei`/`FiberHome`/`DPtech`）—— CRC 对比的解析器靠它准确识别厂商，
只靠正文关键词猜会把华三设备误判成华为。

## 7. 当前版本范围（v0.1）

已实现：框架骨架、注册中心、异步引擎、取消、异常隔离、结果落盘与导出、清单导入（CSV/TXT/xlsx）、凭证加密、双通道连接层、批量备份样板工具。

已整合（v0.2）：接口信息采集、设备配置备份、接口描述采集、接口 CRC 对比分析、接口IP/管理口提取、IP/CIDR 转换、会话信息提取、会话 Hostname 修改、会话配置同步、中兴交换机合规检查，以及 `--cli` 无界面运行入口。

未实现（后续版本）：地址扫描与信息采集工具、结果持久化与统计页、参数模板、失败项重试、打包 exe。
未实现（本期明确不做）：账号权限、Web 化、定时调度、配置下发。

> 本工具会登录网络设备执行命令，请确认已获得设备管理方授权，并使用只读账号。
