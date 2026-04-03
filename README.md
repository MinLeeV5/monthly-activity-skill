# monthly-activity-skill

一个面向 Codex 的月度工作总结 skill：内置读取 Dayflow 时间线数据，通过 `glab` 读取 GitLab 活动事件，并可按指定飞书 URL 读取项目管理文档 / Base，最终输出“字段为表头、每行一个任务拆分”的中文 Markdown 主表，并追加一张“本月收获与反思”附表。

## 解决的问题

这个仓库负责：

- 内置采集 Dayflow 数据
- 在执行前先检查设备是否安装 Dayflow App，以及本地数据库是否存在
- 未检测到 Dayflow 时自动降级为 GitLab-only 月报
- 使用 `glab` 采集 GitLab 活动事件
- 读取指定飞书 URL 中的项目管理目标、里程碑与任务拆分
- 把两类数据汇总成月度工作总结
- 当提供飞书项目管理 URL 时，优先按飞书中的明确目标对齐“目标”列
- 固化固定表头的 Markdown 表格格式
- 输出每个任务拆分的工时、人天、完成情况、工作质量与难易度
- 补充输出“本月收获与反思”附表

## 前置依赖

### 1. Dayflow（可选但推荐）

如果设备安装了 Dayflow，脚本会默认检查下面两个位置：

- App：`/Applications/Dayflow.app`
- DB：`~/Library/Application Support/Dayflow/chunks.sqlite`

也可以显式指定数据库路径：

```bash
python3 scripts/read_dayflow.py --month 2026-03
```

如果当前设备没有安装 Dayflow，但你手头有数据库文件，也可以这样运行：

```bash
python3 scripts/generate_monthly_report.py \
  --month 2026-03 \
  --gitlab-hostname gitlab.gz.cvte.cn \
  --dayflow-db-path /path/to/chunks.sqlite
```

如果既没有 Dayflow App，也没有可读数据库，脚本会自动降级为 GitLab-only 模式，不再直接报错退出。

### 2. glab

需要安装并登录目标 GitLab 主机，例如：

```bash
glab auth status
glab auth login --hostname gitlab.gz.cvte.cn
```

### 3. lark-cli（可选，用于飞书项目管理 URL）

如果希望按飞书项目管理文档 / Base 对齐“目标”列，需要安装并完成授权：

```bash
lark-cli auth status --verify
```

当前仓库内部使用 `scripts/read_lark_project_context.py` 统一读取飞书 URL；在 skill 工作流层面，会按下面的资源类型路由：

- `/wiki/...`：先解析 wiki 节点
- 若解析为 `bitable`：按 Base 读取任务、目标和里程碑
- 若解析为 `docx/doc`：按文档读取“目标 / 交付 / 里程碑”等章节
- `/base/...`：直接按 Base 读取
- `/docx/...`、`/doc/...`：直接按文档读取

## 仓库结构

```text
.
├── SKILL.md                             # Skill 主说明
├── README.md                            # 中文仓库说明
├── agents/openai.yaml                   # Codex 界面元信息
├── scripts/read_dayflow.py              # Dayflow 数据采集脚本
├── scripts/fetch_gitlab_events.py       # GitLab 活动采集脚本
├── scripts/read_lark_project_context.py # 飞书项目管理 URL 采集脚本
├── scripts/generate_monthly_report.py   # 月报生成脚本
├── references/dayflow-data.md           # Dayflow 采集说明
├── references/report-format.md          # 表格模板与字段规则
├── references/gitlab-data.md            # GitLab 采集说明
└── tests/test_monthly_activity_skill.py # 关键回归测试
```

## 使用方式

默认使用约束：

- 在“按项目目标来源直接填月总”的场景里，建议至少提供 `1 个项目目标来源` 和 `1 个输出目标`
- 项目目标来源通常是周目标 / 周会文档 / 项目管理 Base；输出目标通常是月总文档、月报表格或飞书文档中的指定章节
- skill 会先按来源字段抽取语义，再结合 Dayflow / GitLab 校准事实，最后整理到输出目标中

### 生成月报 Markdown 表格

```bash
python3 scripts/generate_monthly_report.py \
  --month 2026-03 \
  --gitlab-hostname gitlab.gz.cvte.cn
```

### 生成带飞书项目目标对齐的月报

```bash
python3 scripts/generate_monthly_report.py \
  --month 2026-03 \
  --gitlab-hostname gitlab.gz.cvte.cn \
  --lark-url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI'
```

### 输出 JSON 以便二次加工

```bash
python3 scripts/generate_monthly_report.py \
  --month 2026-03 \
  --gitlab-hostname gitlab.gz.cvte.cn \
  --format json
```

### 单独采集 GitLab 活动

```bash
python3 scripts/fetch_gitlab_events.py --month 2026-03 --hostname gitlab.gz.cvte.cn
```

### 单独采集飞书项目上下文

```bash
python3 scripts/read_lark_project_context.py \
  --url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI'
```

## 输出格式

月报默认输出两张 Markdown 表：

### 1. 主任务拆分表

表头固定为：

- `目标`
- `关键成果（交付物/数据）`
- `关键行动举措（对齐组织目标拆解，需体现延期情况）`
- `完成情况（成效、问题、风险、措施）`
- `工作质量`
- `工时/D（具体人天）`
- `自评难易（难/中/易）`
- `备注`

注意：

- 每一行代表一个任务拆分 / 工作流
- Dayflow 可用时，工时和人天根据 Dayflow 数据折算
- 未检测到 Dayflow 时，仍可输出 GitLab-only 月报，但工时 / D 会明确标记为暂缺
- GitLab 数据用于补充提交、MR、项目推进和协作证据
- 如果同时提供周目标 / 项目周会文档，默认按以下规则映射：
- 周目标中的目标 -> `目标`
- 周目标中的关键交付 -> `关键成果（交付物/数据）`
- 周目标中的每周行动 -> `关键行动举措（对齐组织目标拆解，需体现延期情况）`
- 周目标中的完成情况 -> `完成情况（成效、问题、风险、措施）` 的进展骨架
- 工时必须量化统计，并优先根据 Dayflow 数据折算
- `工作质量` 为主观质量短评，`自评难易` 固定填 `难 / 中 / 易`
- 无法直接验证的结论会明确写成保守判断或推断
- `目标` 仍然是一列，但单元格内建议拆成 2 条短 bullet
- 第 1 条写项目交付目标，第 2 条写个人成长或能力沉淀目标
- 不需要显式写 `组织目标：`、`个人目标：` 这类标签
- 如果提供了飞书项目管理 URL，第 1 条必须优先对齐飞书中的目标 / 里程碑，而不是只凭 Dayflow / GitLab 轨迹猜测
- `关键成果` 列聚焦交付内容本身，不展开 Dayflow / GitLab 明细数据
- `完成情况` 列输出综合判断，默认先沿用周目标中的当前进展，再用 Dayflow / GitLab 校准其中涉及的真实时间节点和已完成事实，而不是直接照抄周会中的计划时间
- 如果同一批 Dayflow / GitLab 证据可能命中多个任务拆分，需要先做互斥归类，避免重复累计工时
- 表格单元格内优先使用无序列表风格表达明细，并尽量保持短句

### 2. 本月收获与反思附表

固定格式为：

```markdown
## 本月收获与反思

| 类别 | 分享 |
| -- | -- |
| 收获/启发/成长 | ... |
| 反思/自我批评 | ... |
```

## 设计原则

- 单技能闭环：Dayflow 读取和月报汇总都内置在当前 skill 中，不再强依赖外部 `dayflow-skill`
- 环境自适应：先探测 Dayflow App / DB，缺失时自动降级而不是直接失败
- 目标对齐优先级：飞书项目管理目标 > Dayflow journal 提示 > Dayflow / GitLab 轨迹推断
- 输出结构固定：保证每次总结都能稳定落成同一张表
- 事实优先：先写可追溯事实，再写保守推断
- 兼容自动化：脚本支持 JSON 输出，便于后续接自动化流程

## 本地校验

```bash
python3 scripts/read_dayflow.py --month 2026-03
python3 scripts/fetch_gitlab_events.py --month 2026-03 --hostname gitlab.gz.cvte.cn --indent 0
python3 scripts/read_lark_project_context.py --url 'https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI' --indent 0
python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
python3 -m unittest discover -s tests -p 'test_*.py'
```
