---
name: monthly-activity-skill
description: 内置读取本机 Dayflow 时间线数据，通过 `glab` 读取 GitLab 活动事件，并可按指定飞书 URL 读取项目管理文档 / Base，按月或自定义时间范围生成中文工作总结表格。适用于每月工作总结、需要用飞书项目目标对齐组织目标、或在没有 Dayflow 的设备上先输出轻量月报。
---

# 月度工作总结

这个 skill 负责把 Dayflow、GitLab 与飞书项目管理三类证据汇总成中文 Markdown 输出：主表用于任务拆分，附表用于“本月收获与反思”。主表表头固定，每一行代表一个任务拆分，而不是把字段纵向展开。

## 默认约束场景

- 当用户要求“基于项目目标来源直接填写月总 / 月报 / 飞书总结表”时，默认输入应至少包含：
  - `1 个项目目标来源`
  - `1 个输出目标`
- `项目目标来源` 通常是项目周目标、项目周会文档、项目管理 Base，或包含“目标 / 关键交付 / 每周行动 / 完成情况”结构的飞书文档
- `输出目标` 通常是月总文档、月报表格或飞书文档中的指定章节 / 表格
- 在这个约束场景下，skill 默认先抽取来源语义，再结合 Dayflow / GitLab 证据校准事实，最后整理成目标输出中的月总表格
- 如果用户同时给了多个来源，优先选择与当前项目最直接相关、且字段结构最完整的一个作为主来源；其他来源只作补充证据

## 快速开始

1. 确保 `glab` 已安装且已经登录目标 GitLab 主机
2. 如需完整工时视角，确保设备存在 Dayflow 应用或可访问的 Dayflow 数据库
3. 运行总脚本：
   ```bash
   python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
   ```
4. 如果要按飞书项目管理页对齐“目标”，追加一个或多个 `--lark-url`：
   ```bash
   python3 scripts/generate_monthly_report.py \
     --month 2026-03 \
     --gitlab-hostname gitlab.gz.cvte.cn \
     --lark-url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI'
   ```
5. 默认输出为 Markdown 表格；如果要继续二次加工，可使用 `--format json`

## 工作流

### 1. 先探测 Dayflow，再决定是否读取

- 优先检查 Dayflow 应用路径，默认是 `/Applications/Dayflow.app`
- 再检查数据库路径，默认是 `~/Library/Application Support/Dayflow/chunks.sqlite`
- 如果显式传入 `--dayflow-db-path`，即使当前设备未安装 Dayflow App，也允许直接读取该数据库
- 常规场景统一使用内置脚本 `scripts/read_dayflow.py`，不要重复实现 Dayflow SQL
- `--dayflow-skill-dir` 与 `DAYFLOW_SKILL_DIR` 仅保留为兼容旧配置的后备选项

### 2. 再读取 GitLab

- 使用 `glab api events` 采集活动事件
- 必须显式带上 `-X GET`，否则带查询参数时容易落到错误的默认方法
- 常规场景使用 `scripts/fetch_gitlab_events.py`
- 需要切换 GitLab 主机时，用 `--gitlab-hostname`

### 3. 按需读取飞书项目管理 URL

- 常规场景统一使用内置脚本 `scripts/read_lark_project_context.py`
- skill 工作流层面按资源类型路由：
  - `/wiki/...` 先走 `lark-wiki`
  - 若 wiki 解析后是 `bitable`，继续走 `lark-base`
  - 若 wiki 解析后是 `docx/doc`，继续走 `lark-doc`
  - `/base/...` 直接按 `bitable` 读取
  - `/docx/...`、`/doc/...` 直接按文档读取
- 当 URL 指向项目管理 Base 时，优先抽取：
  - 当前用户参与的任务拆分
  - 任务关联的目标 / 里程碑
  - 计划完成、期望交付、风险备注等管理字段
- 当 URL 指向文档时，优先抽取包含“目标 / 里程碑 / 交付 / 范围”等标题的章节
- 目标优先级固定为：
  - 飞书项目管理文档 / Base 中的明确目标
  - Dayflow journal 中的目标提示
  - Dayflow / GitLab 轨迹推断
- 如果当前任务属于“提供 1 个项目目标来源 + 1 个输出目标”的直接填表场景，默认按下面的映射规则整理：
  - 周目标中的目标 -> 月报主表 `目标`
  - 周目标中的关键交付 -> 月报主表 `关键成果（交付物/数据）`
  - 周目标中的每周行动 -> 月报主表 `关键行动举措（对齐组织目标拆解，需体现延期情况）`
  - 周目标中的完成情况 -> 月报主表 `完成情况（成效、问题、风险、措施）` 的进展骨架
  - 工时 -> 月报主表 `工时/D（具体人天）`，必须量化统计
  - 主观评估 -> 月报主表 `工作质量` 与 `自评难易（难/中/易）`
- 当同时存在周目标文档与 Dayflow / GitLab 时：
  - 周目标负责提供 `目标 / 关键交付 / 每周行动 / 当前进展` 的语义骨架
  - Dayflow 负责提供工时统计与实际投入时间窗口
  - GitLab 负责校准交付动作、提交节奏、MR/协作痕迹
  - `完成情况` 中涉及的日期、阶段判断和已完成事实，必须再用 Dayflow / GitLab 校准，不能直接照抄周目标中的计划时间

### 4. 生成月报表格

- 输出字段固定为：
  - `目标`
  - `关键成果（交付物/数据）`
  - `关键行动举措（对齐组织目标拆解，需体现延期情况）`
  - `完成情况（成效、问题、风险、措施）`
  - `工作质量`
  - `工时/D（具体人天）`
  - `自评难易（难/中/易）`
  - `备注`
- 每一行是不同的任务拆分 / 工作流，不允许把字段做成纵向“栏目/内容”表
- 主表之后还要追加一张 `本月收获与反思` 表，固定为：
  - `| 类别 | 分享 |`
  - `| 收获/启发/成长 | ... |`
  - `| 反思/自我批评 | ... |`
- Dayflow 可用时，工时与 D 来自 Dayflow；GitLab 作为交付、协作、MR/提交线索的补充证据
- 未检测到 Dayflow 时，允许继续输出 GitLab-only 月报，但工时 / D 暂缺
- 无法直接证明的内容必须标注为保守判断或推断
- `工时/D（具体人天）` 必须优先按 Dayflow 可识别工作窗口统计；GitLab 只用于校准该时间段是否存在对应交付活动，不得反向补造工时
- 在有周目标来源时，`完成情况（成效、问题、风险、措施）` 必须优先沿用周目标中的当前进展语义，再用 Dayflow / GitLab 校准真实发生的时间窗口；周目标中的计划日期只能作为延期对照，不能直接写成“实际完成时间”
- 若同一批 Dayflow / GitLab 证据可能同时落入多个任务拆分，必须先做互斥归类，避免重复累计工时；无法稳定拆分时，优先并入最直接支撑的交付项，并在 `备注` 中说明
- `目标` 仍然是一列，但单元格内建议拆成 2 条短 bullet
- 第 1 条写这一行任务拆分对应的项目交付目标，第 2 条写对应的能力成长或沉淀目标
- 不要显式写 `组织目标：`、`个人目标：` 这类标签
- 若已提供飞书项目管理 URL，第 1 条必须优先对齐飞书中的目标 / 里程碑，不要再仅凭 Dayflow / GitLab 猜测
- `关键成果` 只写交付成果，不写 Dayflow / GitLab 的原始统计明细
- `完成情况` 必须把两类数据综合为一份阶段总结，语言要适合直接发给团队评审
- `工作质量` 写主观质量判断短句；`自评难易` 固定使用 `难 / 中 / 易`
- 表格单元格内优先用无序列表风格组织明细，并尽量用短句表达
- 如果某个“预研 / 方案验证”任务在 GitLab 有明确痕迹，但 Dayflow 无法稳定拆出独立工时，优先并入它直接支撑的正式交付项，而不是单独伪造一行工时

## 常用命令

- 生成月报表格：
  ```bash
  python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
  ```
- 显式指定 Dayflow 数据库：
  ```bash
  python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn --dayflow-db-path ~/Library/Application\ Support/Dayflow/chunks.sqlite
  ```
- 生成 JSON 结构：
  ```bash
  python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn --format json
  ```
- 携带飞书项目管理 URL 生成月报：
  ```bash
  python3 scripts/generate_monthly_report.py \
    --month 2026-03 \
    --gitlab-hostname gitlab.gz.cvte.cn \
    --lark-url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI'
  ```
- 单独查看 GitLab 事件：
  ```bash
  python3 scripts/fetch_gitlab_events.py --month 2026-03 --hostname gitlab.gz.cvte.cn
  ```
- 单独读取飞书项目上下文：
  ```bash
  python3 scripts/read_lark_project_context.py \
    --url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI'
  ```

## 参考资料

- Dayflow 数据读取说明：`references/dayflow-data.md`
- 表格规则与字段要求：`references/report-format.md`
- GitLab 采集方式与事件解释：`references/gitlab-data.md`
