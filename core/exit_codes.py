# -*- coding: utf-8 -*-
"""进程退出码统一定义。

两套语义并存（历史上分开演化，值有重叠），按调用方区分：

- `main.py --cli <tool>` 单工具退出码（CLI_*）
- `scripts/daily_crc.py` 每日流水线退出码（PIPE_*）

调度器（core/scheduler.py）按任务类型选用对应文案映射。
"""


# —— 单工具（main.py --cli / GUI 定时子进程）——
CLI_OK = 0          # 全部成功（SUCCEEDED）
CLI_FAILED = 1      # 失败/异常（FAILED，含全部设备失败）
CLI_PARTIAL = 2     # 部分成功（PARTIAL，跑完但有失败项）


# —— 每日流水线（scripts/daily_crc.py）——
PIPE_OK = 0             # 全成功（采集 + 对比，备份无失败）
PIPE_TASK_FAIL = 1      # 计划任务安装/卸载失败
PIPE_DATE_DIR_LACK = 2  # 归档目录里可用日期不足 2 个，无法对比
PIPE_COLLECT_FAIL = 3   # 采集未成功（成功 0 台或工具失败）
PIPE_COMPARE_FAIL = 4   # CRC 对比未成功
PIPE_BAD_CONFIG = 5     # 配置有误
PIPE_BACKUP_FAIL = 6    # 配置备份无产出（不阻断对比，仅体现在退出码）
