# 网络运维工具箱 · UI 设计系统 v0.2

> **交付物**
> - `preview.html` — 可交互高保真稿（工具切换 / 深浅主题 / 运行模拟 / 图表）
> - `preview-dark.png`、`preview-light.png` — 双主题静态预览图（1440×1040）
> - `tokens.css` — 双主题语义 token，可直接移植
> - `DESIGN_SYSTEM.md` — 本文档（规范、落地映射、验收清单）
>
> **预览方式**：直接用浏览器打开 `preview.html`；右上角日/月按钮切换主题，也可用
> `preview.html?theme=light` 直接进浅色。
>
> **本次未修改任何现有 Python 文件**，仅在 `design/` 下新增设计资产。
> `README.md` 的既有改动为仓库原有状态，与本次交付无关。
>
> **验证记录**：本机 Chrome 无头渲染 + 40 项交互断言，**40 PASS / 0 FAIL**
> （工具与标签页切换、双主题 token 生效、结果筛选、字号、开始/中止状态机、参数折叠、导航搜索、图表与日志渲染）。

---

## 0. 现状诊断

对 `ui/main_window.py`（1721 行）、`ui/form_builder.py`、`ui/result_view.py`、`ui/chart_view.py`、`ui/log_view.py` 逐份走查后，问题集中在**视觉层**，而非功能层：

| # | 现状 | 问题 |
|---|---|---|
| 1 | 应用栏是一行 `Label` + 4 个 `Button` + 主题下拉 | 无品牌识别、无全局状态，按钮密度不均，9 套主题下拉占据高价值位置 |
| 2 | 导航是 `Treeview(show="tree")`，纯文字 | 12 个工具 / 6 个分类只有缩进区分，无图标、无分类色、选中态弱 |
| 3 | 统计只在状态栏以文字呈现（`已处理 24/543`） | 运维最关心的总量 / 成功率 / 失败分布没有视觉锚点 |
| 4 | 参数区是 `Labelframe` 内横向 label + 控件双列 | 标签与控件挤在同一行，帮助信息靠悬停 `?`，必填与状态不醒目 |
| 5 | 结果表失败行仅靠 tag 底色 | 缺状态 chip、数值非等宽，IP/CRC 长串难横向比对 |
| 6 | 输出区是原生 `Notebook` 方块标签 | 与整体无视觉语言关联，标签无内容计数 |
| 7 | 9 套 ttkbootstrap 主题可自由切换 | 语义色随主题漂移，深色下图表/日志对比度不可控 |

**功能层保持不动**：Schema 驱动表单、Registry 插件注册、Worker + Queue + `after(100ms)` 轮询、任务状态机、按工具输出缓存、定时任务、图表渲染链路均已成熟，是这套设计的约束条件而非改造对象。

---

## 1. 设计定位与原则

**定位**：面向网络运维 / NOC 值班的桌面工作台 —— 精密、克制、高信息密度、状态一眼可读。

1. **状态优先**：颜色只表达语义，不表达装饰。失败/劣化先于文字被看见。
2. **数值等宽**：IP、CRC、耗时、任务 ID、日志时间戳统一使用等宽字体 + `tabular-nums`，便于纵向扫读比对。工具版本号与工具 ID 按评审**不在导航与标题区展示**。
3. **层级靠表面与描边**：深色靠明度逐级抬升，浅色靠柔和投影；避免大面积渐变与装饰色块。
4. **插件友好**：工具从 12 个增长到 N 个时，导航的分类分组 + 图标 + 搜索仍能成立。
5. **业务逻辑零改动**：所有视觉改造收敛在 `ui/` 层，对外接口签名保持不变。

> **色彩语义约定**：CRC 差值增长 = 链路劣化 → `danger / warning`；CRC 下降 = 恢复 → `success`。
> 这不是金融涨跌场景，**不套用红涨绿跌**。

---

## 2. 设计基础（Design Foundations）

完整可复制的 token 见 `tokens.css`。根节点标记 `data-theme="dark" | "light"`，组件**只允许引用语义 token**，禁止硬编码色值。

### 2.1 表面层级

| Token | 深色 | 浅色 | 用途 |
|---|---|---|---|
| `--bg-app` | `#0a1120` | `#f2f6fb` | 窗口最底层 |
| `--bg-panel` | `#0f1a2d` | `#ffffff` | 顶栏 / 侧栏 / 状态栏 |
| `--bg-surface` | `#142138` | `#ffffff` | 卡片 |
| `--bg-elevated` | `#1a2b46` | `#f7fafd` | 输入框 / 下拉 / 卡片内分组 |
| `--bg-hover` | `#20334f` | `#eef3f9` | 悬停 |
| `--bg-inset` | `#0c1627` | `#f7fafd` | 表格 / 日志 / 图表底 |
| `--border` | `#243753` | `#dce4ef` | 常规描边 |
| `--border-strong` | `#33507a` | `#c2d0e2` | 强调描边 |

工作区叠加 `32px` 极淡网格纹理（`--bg-grid`，深色 3.5% / 浅色 2.8%），营造"仪器面板"质感，不影响文字可读性。

### 2.2 文本与对比度（WCAG AA）

| Token | 深色 | 对比度 on `--bg-surface` | 浅色 | 对比度 on `--bg-surface` |
|---|---|---|---|---|
| `--text-primary` | `#edf3fb` | **15.8:1** | `#15243b` | **14.6:1** |
| `--text-secondary` | `#a8bad2` | **7.6:1** | `#50627d` | **6.1:1** |
| `--text-muted` | `#7b8fac` | **4.7:1** | `#6d7f99` | **4.5:1** |

全部 ≥ 4.5:1，满足 AA 正文要求。`--brand` 在浅色下压暗为 `#0e8ba8`（深色为 `#22d3ee`），保证品牌实色按钮上的文字对比达标。

### 2.3 语义色

每个语义色配三件套：实色 / `-soft` 底 / `-border` 描边，用于 chip、失败行、风险项。

| 语义 | 深色 | 浅色 | 含义 |
|---|---|---|---|
| `--brand` | `#22d3ee` | `#0e8ba8` | 链路 / 主操作 / 选中 |
| `--accent` | `#7c96ff` | `#4f6bed` | 图表第二序列 / 任务 ID |
| `--success` | `#34d399` | `#0b8a61` | 成功、CRC 恢复 |
| `--warning` | `#fbbf24` | `#a3600a` | 部分成功、需人工确认 |
| `--danger` | `#f87171` | `#c83232` | 失败、CRC 增长、持续恶化 |
| `--info` | `#60a5fa` | `#2563eb` | INFO 日志、连接中 |
| `--neutral` | `#94a3b8` | `#64748b` | 空闲、等待中、已跳过 |

**工具分类色**（侧栏图标 + 分类徽章）：

| 分类 | Token | 深色 | 工具数 |
|---|---|---|---|
| 采集分析类 | `--cat-collect` | `#22d3ee` | 5 |
| 解析类 | `--cat-parse` | `#a78bfa` | 2 |
| 工具类 | `--cat-utility` | `#60a5fa` | 1 |
| 检查类 | `--cat-check` | `#fbbf24` | 1 |
| 会话管理类 | `--cat-session` | `#34d399` | 2 |
| 示例 | `--cat-demo` | `#94a3b8` | 1 |

### 2.4 图表色

`--chart-grid / --chart-axis / --chart-bar / --chart-bar-alt / --chart-bad / --chart-good`。
深色主题下 matplotlib 必须切深色底（`axes.facecolor = --bg-inset`），序列色取自 token，避免默认白底图嵌进深色界面产生刺眼白块。

### 2.5 排版

```css
--font-sans: "Microsoft YaHei UI","Microsoft YaHei","Segoe UI","PingFang SC",sans-serif;
--font-mono: "JetBrains Mono","Cascadia Mono",Consolas,monospace;
```

| 层级 | 字号 | 字重 | 行高 | 用途 |
|---|---|---|---|---|
| 11 | 11px | 400/600 | 1.5 | 徽标、元信息、表头、状态栏 |
| 12 | 12px | 400/600 | 1.5 | 次要正文、表单控件、导航项 |
| 13 | **13px** | 400 | 1.5 | **正文基准** |
| 14 | 14px | 700 | 1.25 | 卡片标题、品牌名 |
| 16 | 16px | 700 | 1.25 | 区块标题 |
| 20 | 20px | 700 | 1.25 | 工具名 H1 |
| 26 | 26px | 700 | 1.2 | KPI 数值（等宽） |

字重仅 400 / 600 / 700 三级。所有数值容器启用 `font-variant-numeric: tabular-nums`。

### 2.6 间距 / 圆角 / 动效

- **4px 基准**：4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48
- **圆角**：6（控件）/ 10（卡片）/ 14（大容器）/ 999（chip、进度条）
- **阴影**：`sm` 卡片 / `md` 浮层 / `lg` 抽屉；深色主要靠描边，浅色靠投影
- **过渡**：150ms 交互反馈 · 260ms 主题切换 · 420ms 展开收起，缓动 `cubic-bezier(.2,.8,.2,1)`
- **动效豁免**：`prefers-reduced-motion: reduce` 下全部降为 0.001ms

---

## 3. 布局框架

```
┌──────────────────────────────────────────────────────────┐
│ AppBar                                              58px  │  品牌 · 调度状态 · 图标组 · 头像
├───────────────┬──────────────────────────────────────────┤
│ Sidebar       │ Workspace（可滚动）                       │
│ 268px         │  Breadcrumb → ToolHead → KPI×4            │
│               │  → ParamsCard → RunBar → Output Tabs      │
│ 搜索 / 分类组 │                                          │
│ 工具项 / 页脚 ├──────────────────────────────────────────┤
├───────────────┴──────────────────────────────────────────┤
│ Statusbar                                           34px  │  状态徽章 · 统计 · 定时 · 结果目录
└──────────────────────────────────────────────────────────┘
```

| 断点 | 行为 |
|---|---|
| ≥ 1280px | 完整布局：侧栏 268px、KPI 4 列、参数双列 |
| 1024–1280px | 侧栏 232px、KPI 2 列、图表区改单列 |
| 900–1024px | 侧栏隐藏、参数单列、摘要双列改单列 |
| < 900px | 全局状态胶囊隐藏、运行条进度换行 |
| < 620px | KPI 2 列、工作区内边距收至 16px |

窗口最小尺寸沿用 `960×620`（`main.py` 的 `minsize(1000, 640)` 与 `MainWindow.minsize(960, 620)` 已存在），设计稿在该尺寸下不发生控件重叠。

**输出区高度**：参数卡片内容区上限 290px（超出内部滚动）；**输出卡片固定 `340px`**，与现有 `OUTPUT_MIN_H = 340` 业务约束一致，表格 / 图表 / 日志在卡片内部滚动。上方参数区与运行条按自然高度**完整显示**，保证主操作「开始执行」永不被裁切；窗口装不下时整块工作区滚动一次，**不做双层嵌套滚动**。图表放大时自动收起参数区。

> 这一条是设计评审时实测修正的：初版让输出区 `flex:1` 抢高度，导致运行条被挤出视口；
> 初版之前让工作区纯滚动，又导致结果表完全不在首屏。固定 340px 是两者之间的取舍点。

---

## 4. 组件库

每个组件统一定义 7 态：`default / hover / active / focus / disabled / loading / empty`。

### 4.1 导航与框架

| 组件 | 规格 |
|---|---|
| **BrandMark** | 34×34，六边形拓扑节点 SVG（中心节点 + 6 条链路），品牌色 1.6px 描边 |
| **AppBar** | 高 58px；左侧品牌区（34px 标记 + 中英文名，**不再挂版本胶囊**）；右侧全局调度状态（脉冲绿点）+ 5 个 34px 图标按钮 + 头像 |
| **NavGroup** | 10px 大写小字 + 3px 分类色条 + 数量徽章；组间距 16px |
| **NavItem** | 高 36px、圆角 6；分类色条 + 分类图标 14px + 名称；选中态 = 品牌 soft 底 + 左侧 3px 品牌色条。整行宽度留给名称，不显示版本号 |
| **Search** | 高 34px，放大镜前缀 + `/` 快捷键提示；输入即时过滤工具名与 id |
| **Statusbar** | 高 34px；状态徽章（圆点 + 中文文案）· 处理进度 · 每日流水线下次执行 · 结果目录等宽截断 + 悬浮全文 |

### 4.2 内容与数据

| 组件 | 规格 |
|---|---|
| **KpiCard** | 4 列网格；左侧 2px 语义色条；11px 标签 + 26px 等宽数值 + 11px 说明 |
| **ParamsCard** | 卡片头（图标 + 标题 + 参数计数 + 折叠按钮）；双列网格 gap 16/24；标签置顶、必填 `*` 用 danger、帮助用 `?` 悬浮卡；内容上限 290px 内部滚动 |
| **Field 控件** | 统一高 34px、圆角 6、focus 3px 焦点环；`int` 带范围提示、`dir/file/device_list` 带「选择…」按钮与设备数回显、`bool` 用 36×20 开关、`choice` 用自定义箭头下拉 |
| **SchedBar** | 卡片底栏：时间输入 + 开关 + 上次执行状态 + 查看结果 / 立即执行 / 总览 |
| **RunBar** | 主按钮品牌实色（播放图标）· 中止 danger-outline · 结果目录 ghost；右侧进度数字 + 当前设备 IP + 7px 渐变进度条 |
| **Tabs** | 高 42px 下划线式（替换原生 Notebook 方块）；图标 + 名称 + 内容计数徽章 |
| **DataTable** | 表头 sticky、10px 大写；斑马纹；失败行左侧 2px danger 条 + soft 底；状态列渲染 StatusChip；IP/接口/CRC 走等宽；工具栏含分段筛选、内容搜索、A± 字号、导出 |
| **StatusChip** | 高 20px 胶囊：5px 圆点 + 中文文案 + soft 底 + 语义描边；**颜色之外必带文字** |
| **Chart / BarRank** | 横向条形排行，正值渐变为 danger→warning（劣化）；对数坐标轴标注 10²–10⁷；配趋势折线 SVG 与「持续恶化接口」风险列表 |
| **LogConsole** | 等宽 11.5px；四列对齐：时间戳（muted）/ 等级（语义色）/ IP（accent）/ 消息；等级分段筛选 |
| **SummaryKV** | 双栏卡片：任务信息键值表 + 执行统计（等宽）+ 失败原因分布条形 + 结果目录路径框 |
| **SpecDrawer** | 右侧 520px 浮层，展示 token 色板 / 字号阶梯 / 组件清单 / 落地映射；遮罩 + Esc 关闭 |

### 4.3 状态文案映射（对齐 `core/message.py`）

| 任务状态 | 徽章语义 | 文案 |
|---|---|---|
| `IDLE` | neutral | 空闲 |
| `RUNNING` | brand | 运行中 |
| `CANCELLING` | warning | 中止中 |
| `SUCCEEDED` | success | 已完成 |
| `PARTIAL` | warning | 部分失败 |
| `FAILED` | danger | 失败 |
| `CANCELLED` | neutral | 已中止 |

设备项 `STATUS_TEXT`（成功 / 部分成功 / 连接超时 / 认证失败 / 解析失败 / 结果写入失败 / 未知错误 / 已跳过 / 等待中 / 连接中 / 执行中）按 §2.3 映射到 chip 语义色，与 `ItemStatus` 一一对应。

---

## 5. 信息架构

导航按 `BaseTool.category` 自动分组，顺序与 `Registry.by_category()` 一致；新增工具丢进 `tools/` 后无需改界面代码。设计稿内已按真实契约录入 12 个工具、6 个分类、真实 `description`、真实 `result_columns`（`version` / `id` 仍保留在数据契约里，只用于任务摘要追溯，不在导航与标题区展示）：

- **采集分析类**（5）：接口信息采集 `v1.4.0` · 设备配置备份 `v1.2.0` · 接口描述采集 `v1.0.0` · 接口CRC对比分析 `v1.1.1` · CRC趋势分析 `v1.4.1`
- **解析类**（2）：接口IP/管理口提取 · 会话信息提取
- **工具类**（1）：IP/CIDR转换计算
- **检查类**（1）：中兴交换机合规检查
- **会话管理类**（2）：会话Hostname修改 · 会话配置同步
- **示例**（1）：演示：批量模拟采集

标签页按 `output_tabs` 动态重建的规则保留：`crc_trend` 为 `chart/log/summary`，`crc_compare` 为 `result/log/summary`，其余为 `result/log/summary`。

---

## 6. 无障碍（WCAG AA 最低要求）

- 正文对比度 ≥ 4.5:1（实测深色 15.8:1 / 浅色 14.6:1），大字 ≥ 3:1
- 状态不只靠颜色：chip 含文案 + 圆点，失败行另有左侧色条，图表配数值标签
- 全部交互键盘可达；`:focus-visible` 提供 3px 焦点环；图标按钮带 `aria-label`
- 导航 / 标签页 / 开关使用 `aria-current` / `aria-selected` / `aria-checked` / `role="switch"`
- 交互目标 ≥ 34px；字号可在 8–32px 调节（沿用现有 `A±` / Ctrl+滚轮机制），200% 缩放下不重叠
- 尊重 `prefers-reduced-motion`

---

## 7. 落地映射（现有代码 → 新设计）

**业务逻辑零改动**：Worker 线程 + Queue + `after(100ms)` 轮询、任务状态机、按工具输出/参数缓存、Schema 驱动表单、定时任务参数快照、图表渲染链路全部保留。

| 设计组件 | 现有实现 | 落地方式 |
|---|---|---|
| AppBar | `main_window._build_layout()` 的 `appbar` | 拆 AppBar 组件：拓扑 Logo + 全局调度状态 + 图标按钮组；9 套主题下拉收敛为日/月切换 |
| Sidebar | `Treeview(show="tree")` | 改自定义 Frame 列表：分类色条 + 分类图标 + 选中态；保留搜索并加 `/` 快捷键；不显示版本号 |
| KpiCard | 无（统计只在状态栏文字） | 新增 KpiStrip，订阅 `progress` / `done` 消息，展示 total / success / failed / 耗时 |
| ParamsCard | `Labelframe` + `FormBuilder` 双列 grid | **保留 FormBuilder 对外 API**（`validate` / `get_values` / `set_enabled` / `footer_row`）；标签置顶、必填标记、帮助 ToolTip、路径回显设备数、分组滚动 |
| RunBar | `prog Frame` + `Floodgauge` | 保留 `Floodgauge`；主操作改品牌实色按钮，当前设备 IP 独立显示，中止按钮按状态机切禁用 |
| Tabs | `ttk.Notebook` | 保留 `_rebuild_output_tabs` 动态重建逻辑，样式改下划线 + 内容计数徽章 |
| DataTable | `ResultView` / `ttk.Treeview` | 保留排序 / 筛选 / 导出 / 分帧渲染（`CHUNK_ROWS=400`）；状态 chip、失败行色条、等宽数值 |
| Chart | `ChartView` / matplotlib | 保留 `FigureCanvasTkAgg`；配色取自 token，深色主题切深色底 + 高对比序列色 |
| LogConsole | `LogView` / `ScrolledText` | 保留等级 tag 机制；改等宽字体 + 时间戳/等级/IP 四列对齐 |
| Statusbar | `_build_statusbar()` | 保留；加状态圆点、每日流水线下次执行、结果目录等宽截断 + 悬浮全文 |
| 主题系统 | `THEMES` 9 套 + `load_ui_pref` | 收敛为 `dark` / `light` 两套语义主题；沿用 `~/.network_toolkit_ui.json` 偏好持久化，读取旧值时平滑迁移 |

### 建议实施顺序（确认设计后）

1. 引入 `tokens.css` 对应的 Python 常量表 `ui/theme.py`，把 9 套主题收敛为 2 套并验证 `on_change_theme()` 的重置链路（`Style().theme_use()` 会重置字号，需重新 `_apply_font_size()`）
2. 换 AppBar + Sidebar（纯视觉，风险最低）
3. 新增 KpiStrip（只读订阅消息，不碰 `TaskEngine`）
4. 改造 `FormBuilder` 渲染（保持 API 不变，重点回归 `_reflow_form` 单/双列重排与参数值缓存）
5. 结果表 / 日志 / 图表语义色与等宽字体
6. 全量回归：`python main.py --selftest`、`--list-tools`、GUI 主题切换 / 字号 / 参数折叠 / 图表放大 / 定时任务

---

## 8. 设计 QA 验收清单

- [ ] 双主题下所有文字达到 AA 对比度，无纯黑/纯白硬编码
- [ ] 12 个工具逐个切换：标题、描述、分类色、参数、结果列、标签页组合均正确；导航与标题区不出现版本号 / 工具 ID
- [ ] `crc_trend` 只显示 `chart/log/summary`，`crc_compare` 只显示 `result/log/summary`
- [ ] 960×620 最小窗口下参数区与「开始执行」按钮完整显示（工作区滚动可达）、输出卡片保持 340px、无双层嵌套滚动
- [ ] 运行中切换工具被拦截、中止后状态徽章为「已中止」、结束后自动切到最有内容的标签页
- [ ] 主题切换后表格 tag 色、日志等级色、字号设置不失效
- [ ] 新工具丢入 `tools/` 重启后自动出现在对应分类组，无需改界面代码
- [ ] 键盘全程可达；`prefers-reduced-motion` 下无持续动画
- [ ] `--selftest` / `--list-tools` / `--cli` 三个无界面入口不受影响

---

## 9. 落地状态（2026-11-12 已实施）

评审通过后已在 `ui/` 层完成视觉改造，**业务逻辑零改动**：Worker + Queue +
`after(100ms)` 轮询、任务状态机、按工具输出/参数缓存、分栏自动贴合、图表放大、
定时参数快照、调度事件队列全部一行未动。

| 文件 | 改动 |
|---|---|
| `ui/theme.py`（新增） | 双主题语义 token、9→2 套主题迁移、6 类分类色/几何标记、Canvas 拓扑 Logo 绘制 |
| `ui/icons.py`（新增） | Pillow 内存绘制 32 个线性图标，4× 超采样 + LANCZOS，零资源文件 |
| `ui/widgets.py`（新增） | 自绘组件：`round_corners` 圆角、`Meter` 细进度条、`Segmented` 分段控件、`TabView` 下划线页签 |
| `ui/main_window.py` | AppBar（品牌/全局状态/图标按钮/头像）、自定义 Sidebar、KPI×4 圆角卡、参数卡片头、运行条语义色、状态栏圆点、一键主题切换、`_configure_ttk_styles()` / `_refresh_theme_colors()` |
| `ui/form_builder.py` | 标签置顶 + 列数自适应（1/2/3 列）+ 控件撑满整列 |
| `ui/result_view.py` | 分段筛选、字号步进组、导出图标、状态列圆点前缀、`partial` 语义色 |
| `ui/log_view.py` | 日志改等宽 Consolas，时间戳/等级/IP 对齐 |
| `main.py` | 默认窗口尺寸 1180×760 → 1240×860 |

`core/`、`tools/`、`chart_view.py` **未修改**。

### 实测修掉的 3 个问题

1. **侧栏被撑到 584px**：侧栏自身用 grid 放进 body，但子控件用 pack —— 必须关
   `pack_propagate(False)`，只关 `grid_propagate` 无效，右侧工作区会被挤掉一半。修复后 268px。
2. **darkly 主题下 `secondary.TLabel` 前景是 `#444444`**：压在深蓝底上几乎不可读，
   状态栏/工具栏/描述全中招。在主题层统一改为语义次要文字色（深 `#a8bad2` / 浅 `#50627d`）。
3. **长工具名被截断**：超长名加省略号，完整名放 ToolTip，绝不静默切字。

### 设计评审变更（2026-11-12 11:46）

按实机截图反馈，**移除侧栏每个工具后的版本号，以及工具头的 `vX.Y.Z` / `tool_id` 两个徽章**；
分类徽章「采集分析类」保留。`version` / `id` 仍留在数据契约与任务摘要里用于追溯。
侧栏整行宽度还给工具名，长名截断阈值从 11 放宽到 14 字符。
真实代码、交互设计稿、静态预览图、本文档已同步。

**追加变更（2026-11-12 14:59）**：按实机截图反馈，顶栏「规范」按钮不需要显示。
已删除 `ui/main_window.py` 的按钮项与 `show_design_spec()` 方法，并移除
`design/preview.html` 中配套的规范抽屉（DOM / CSS / JS / 书本图标 / Esc 绑定）。
`design/DESIGN_SYSTEM.md` 与 `tokens.css` 作为设计资产保留，但界面不再有入口。

**追加变更（2026-11-12 15:15）**：进度条按实机比例缩小 —— `Floodgauge` 的
`thickness` 50 → 33（高度缩小 1/3）、`length` 220 → 165（长度缩短 1/4）；
同时去掉 `fill="x", expand=True`，否则 Canvas 被拉伸到剩余空间、`length` 参数不生效；
文字字号 12pt → 9pt 等宽以适配细条。实测请求尺寸与实际打包尺寸均为 165×33，
主题切换后保持不变，`value=47` 渲染正常。设计稿里的 7px 细条是目标意图，保留不动。

**追加变更（2026-11-12 15:24）**：补齐真实界面图标。新增 `ui/icons.py` ——
用已在 `requirements.txt` 里的 Pillow 在内存绘制 32 个 24×24 线性图标，4× 超采样
+ LANCZOS 抗锯齿、带缓存、`ImageTk` 延迟导入；**零资源文件、零新增依赖、高 DPI 不发糊**。
`ui/theme.py` 增加「分类 / 标签页 → 图标名」映射。

| 落地位置 | 图标 |
|---|---|
| 顶栏 | 凭证 `key`、定时 `clock`、关于 `info`、主题 `contrast` |
| 侧栏 | 搜索 `search`、6 个分类头、12 个工具项（按分类专属色着色） |
| 输出区 | 结果表 `table`、图表 `chart`、日志 `terminal`、摘要 `clipboard` |
| 运行条 | 开始 `play`、中止 `square`、结果目录 `folder`、折叠 `chevron_up/down` |

主题切换时清缓存并按新色板重画；`PhotoImage` 由控件持有 `_ntk_icon` 引用，避免被 GC
导致图标静默消失。顶栏按钮去掉固定 `width`（字符单位会把「图标+文字」挤成单字）。
实测：9 个常驻绑定 + 12 个导航项 + 3 个标签页图标全部生效，切主题后不丢失。
结果表工具栏（导出 / 筛选 / 字号）暂未加图标，属可选补齐项。

**追加变更（2026-11-12 15:45）**：按实机截图修正「图标歪斜」与「配色发黑」。

- **图标**：`ui/icons.py` 的 `_pen` 增加 round cap —— 给线段端点、多边形顶点补
  半径 = 线宽/2 的圆点，模拟 SVG 的 `stroke-linecap/linejoin="round"`；线宽统一为
  `1.8`（与 HTML 一致），尺寸统一提到 15–16px。主按钮图标改用新增的
  `on_primary` 语义色（深浅两套都是白），修掉浅色主题下深图标看不见的问题。
- **配色统一**：新增 `_configure_ttk_styles()`，把 ttkbootstrap 默认的
  `#222222` / `#2f2f2f` 中性灰黑统一到语义 token。扫描确认变体样式名格式是
  `<color>.<Type>.<Class>`（`secondary.Outline.TButton`、`primary.TSpinbox`、
  `primary.TCombobox`、`secondary.Round.Vertical.TScrollbar` 等），按真实名字配置
  根级 + 8 个变体；容器统一 `app` 色、输入框 `elevated`、表格 `inset`、
  选中页签 `surface`。`theme_use()` 会重置样式，因此每次主题刷新都重配一遍。
- **侧栏分类头**：创建时背景被 option database 覆盖成 `#222222`（工具项因为
  `_highlight_nav` 会二次 `configure` 所以正常）；改为记录 `_nav_groups`，在
  `_render_tool_list` 末尾按角色显式重涂。
- **表单校验提示**：`ui/form_builder.py` 从 `bootstyle="danger"` 改为
  `style="Error.TLabel"`，由主窗口按 token 统一着色。
- 实测：`#222222` 灰黑全部消除；深浅两套容器背景分别为 `#0a1120` / `#f2f6fb`；
  `Error.TLabel` 与分类头随主题切换正确；12 工具 / 7 状态 / 搜索 / 折叠 /
  主题往返 / 图标持久性全部通过。

**追加变更（2026-11-12 16:40）**：「A 档保真度补齐」—— 把上一轮记在
「暂未落地」里、也是与设计稿差得最远的三项一次做完，并新增 `ui/widgets.py`
（自绘组件层：圆角、细进度条、分段控件、下划线页签）。

| 项 | 落地方式 |
|---|---|
| 表单标签置顶 | `FormBuilder` 改为「一个参数 = 一个单元格容器」：标签在上、控件在下并 `sticky="ew"` 撑满整列。`footer_row()` 从 `rows*2` 简化为行数，新增 `footer_span()`；`_build_sched_bar` / 结果面板 rowspan 跟着改。不再需要标签列最小宽度 |
| 列数自适应 | 新增 `columns_for(count, width)`：≥6 个参数且 ≥860px 排 3 列，≥5 个且 ≥700px 排 2 列，否则 1 列。标签置顶后每行更高，1180 宽下 2 列 × 3 行放不进参数区只能滚动，3 列 × 2 行才一屏看全。`_reflow_form` 改为比较 `form.cols` |
| 圆角卡片 | 新增 `round_corners()`：四角各叠一个 8×8 Canvas，底色取**页面色**（盖掉直角）、再画一个朝内的**卡片色** 90° 扇形。方向一开始写反了（扇形用了页面色），四角变成缺口而非圆角，已修正 |
| 细进度条 | 新增 `Meter(Canvas)`：`capstyle="round"` 画线自然成胶囊。取代 `Floodgauge` —— 后者只能用主题的 primary 蓝 `#375a7f`，缩到 33px 高就是一块与品牌青撞色的蓝方块。兼容 `configure(value=…)`，主窗口进度代码未改。浅色主题 track 自动改用 `border`（`elevated` 在白底上看不见） |
| 下划线页签 | 新增 `TabView`（实现 Notebook 的 `add/tabs/forget/select/tab` 子集）：页签 = 图标 + 文字 + 2px 下划线，选中态品牌色加粗 |
| 参数卡片头 | `ttk.Labelframe` → `tk.Frame`（1px 描边）+ 卡片头：`sliders` 图标 + 「运行参数」+ 「N 项参数」徽章 + 分隔线。`on_select_tool` 只更新徽章文字 |
| 结果表工具栏 | 「筛选：」+ Combobox → 扁平分段控件（全部/成功/失败）；A-/当前字号/A+ 成组，字号可点击复位；导出 CSV / Excel 加图标；修掉 `A-` 被裁切 |
| 主操作按钮 | `primary.TButton` 改用新增 token `primary_fill`（深 `#2b6cb0` / 浅 `#2563eb`，白字），不再是 darkly 的 `#375a7f` / flatly 近黑的 `#2c3e50` |
| 字号入口收敛 | 删掉参数区底部重复的「显示字号」Spinbox，只保留结果表工具栏的 A-/A+（同一个功能的第二入口白占 38px）。`_add_font_row()` 保留为空实现避免外部调用报错 |
| 默认窗口 | `main.py` 默认尺寸 1180×760 → **1240×860**。新布局（卡片头 + 3 列表单 + 页签 + 工具栏）比原「标签在左」高约 100px，760 高会把参数区第三行和结果表同时挤成半截 |

**踩到的两个坑**（都写进代码注释了）：

1. **`option_add("*Background", …)` 会让「创建时传入的 `bg=`」失效**，只有事后
   `configure` 才生效。KPI 卡片顶部那一行、未选中的页签都因此留成 `#222222` 深灰块。
   结论：新增的 tk 原生控件必须登记进 `_refresh_theme_colors()` 显式重涂，
   `TabView.add()` 也在每个页签加完后就 `_paint()` 一次，不能只靠 `select()`。
2. **分栏不会自己收敛**。`_fit_form_pane()` 原本只在切换工具时定时调几次；
   卡片高度会因「列数重排 / 历史结果面板显隐」二次变化，于是参数区停在
   按带面板时算出的 333px，输出区只剩 147px。现在 `_on_form_inner_configure`
   会防抖 120ms 后重新贴合（手动拖过分隔条时按 `_user_sash` 优先）。

**布局取舍（记录在案）**：1240×860、120% DPI 下，参数卡片约 270px、输出区约 207px，
结果表大约能看到 2 行。这是「标签置顶表单 + KPI 条 + 卡片头 + 页签」在 2160 字高下
的物理上限，不是 bug；需要大表格时点运行条上的「收起参数」把整块高度让给结果区。

**追加变更（2026-11-12 16:47）**：导航分类头微调（按实机截图评审）。

- **分类头去掉图标**：图标只保留给工具项，分类靠「3px 分类色条 + 分类名」区分，
  减少一行里的元素密度。
- **计数徽章改为靠右**（`side="right", padx=(0, 8)`），与工具行的右边距对齐，
  形成清晰的左右两栏；徽章底色从 `elevated` 改成 `neutral_soft`、文字从
  `text_muted` 提到 `text_secondary` —— 浅色主题下 `elevated(#f7fafd)` 贴白底
  几乎看不见，且 8pt 小字需要 ≥4.5:1 的对比度。
- **连带修掉两块 `#222222`**：侧栏 `head`（搜索区）与 `foot`（页脚）两个容器没有
  登记进 `_refresh_theme_colors()`，空白处一直是 Tk 默认深灰。采样确认后补上
  `self.sidebar_head` / `self.sidebar_foot`。这是 `option_add("*Background", …)`
  让创建时 `bg=` 失效的同一类问题，第三次踩到。

**追加变更（2026-11-12 17:05）**：移除顶栏「UI v0.2」概念胶囊（按实机截图评审）。

- **删除 `concept_tag`**：`_build_layout()` 里那段「1px 分隔线 + `UI v0.2` 胶囊」
  整段删除，`_refresh_theme_colors()` 里对应的 `paint(self.concept_tag, …)` 一并去掉；
  设计稿 `preview.html` 的 `<span class="tag">UI CONCEPT · v0.2</span>` 与 `.tag`
  样式规则同步删除（`.vr` 分隔线保留给头像前那一处）。品牌区到此刻为止。
- **连带修掉品牌名右下的黑方块**：品牌容器 `brand = tk.Frame(...)` 的宽度取两个
  Label 的较大值，`NETOPS TOOLKIT` 右侧会留一条 28×22 的自身底色空档。它从没被
  重涂过，深色主题下就是一块 `#222222`。登记为 `self.brand_box` 并加入
  `_refresh_theme_colors()` —— `option_add("*Background", …)` 让创建时 `bg=` 失效的
  **第四次**复发，采样定位：`landed-dark.png` 的 `(201..228, 81..102)`。
- **顺带确认**：顶栏区域 `#222222` 像素已归零（截图逐像素扫描 `appbar` 区域）。

### 暂未落地（保留原实现）

| 项 | 原因 |
|---|---|
| 参数卡片用 surface 底 | 会让 `TLabel` 的 `app` 底色露出成一块块色斑，需要新增 `Card.TLabel` 系列样式并贯穿 `FormBuilder`，收益小于风险；当前用 1px 描边 + 卡片头表达层级 |
| matplotlib 图内配色 | Figure 由各工具生成，改动会波及「导出白底」逻辑 |
| 标签页动态计数 | `TabView` 未加计数徽章，`_rebuild_output_tabs` 只按 keys 比较，加计数需新增刷新点 |
| 分栏拖拽条颜色 | `ttk.Sash` 样式被 theme 固定成 `#222222`，深色下是参数区与输出区之间一条 6px 灰带。试过 `st.configure("Sash", background=app)`：**在主题切换路径上会让后续 `Treeview` 等样式全部失效**（整表退化成 `#222222`，实测多出 9.7 万像素），已回滚。要彻底解决得改用自绘分栏（`tk.Frame` + Canvas 拖拽），收益不抵风险 |

### 验证记录

- 61 个 `.py` 文件按 **Python 3.8 语法**解析通过；改动文件 `py_compile` 全通过
- `python main.py --list-tools` → 12 个工具；`--selftest` → 全链路通过
  （队列/并发/失败隔离/落盘/清单导入）
- 真实 GUI 回归（Tk 8.6 + ttkbootstrap 1.17.0，1240×860）：12 个工具逐个切换并逐个
  校验页签数量、参数控件齐全、`get_values()` / `validate()` 可调用；折叠/展开、
  导航搜索、深浅主题往返两次、4 个页签逐个切换 —— **0 异常**；往返结束后额外断言
  `Treeview` 底色仍等于 `inset`（主题重置曾把整表刷回 `#222222`，加断言防回归）
- 真实窗口截图（Win32 `PrintWindow`，`ImageGrab` 在 Tk 窗口上只会拿到桌面壁纸）：
  `design/landed-dark.png` / `design/landed-light.png`

> 本机默认 Python 3.13 不带 tkinter，验证改用系统 Python 3.14 + 临时目录安装的
> ttkbootstrap 1.17.0 完成；**未在目标环境 Python 3.8.10 实机运行**，但全部代码按
> 3.8 语法解析通过，且未引入任何新依赖。现场首次启动建议先跑 `python main.py --selftest`。

### 使用注意

- 新装默认主题为深色 `darkly`，顶栏「☀ 浅色」一键切换；旧偏好里的 `journal` /
  `cyborg` 等 9 套主题按深浅平滑迁移，不会静默回退默认值。
- `~/.network_toolkit_ui.json` 仍存主题 / 字号 / 分栏位置，格式未变。
- 规范文档与交互稿作为设计资产保留在 `design/` 目录，界面**不提供入口**（按评审移除）。

---

**UI Designer · 设计系统 v0.2 · 2026-11-12**
**Implementation**: 已落地 `ui/` 层，验证通过（详见 §9）
