# monthly-activity-skill

一个面向 Codex 的月度工作总结 skill：通过 `dayflow-skill` 读取 Dayflow 时间线数据，通过 `glab` 读取 GitLab 活动事件，最终输出“字段为表头、每行一个任务拆分”的中文 Markdown 表格。

## 解决的问题

这个仓库负责：

- 复用 `dayflow-skill` 采集 Dayflow 数据
- 使用 `glab` 采集 GitLab 活动事件
- 把两类数据汇总成月度工作总结
- 固化固定表头的 Markdown 表格格式
- 输出每个任务拆分的工时、人天、完成情况、工作质量与难易度

## 前置依赖

### 1. dayflow-skill

需要先准备好 `dayflow-skill` 仓库或已安装 skill，并确保下面的脚本可用：

```bash
python3 /Users/min/AI/dayflow-skill/scripts/read_dayflow.py --month 2026-03
```

可通过以下方式指定路径：

- 命令行：`--dayflow-skill-dir /Users/min/AI/dayflow-skill`
- 环境变量：`DAYFLOW_SKILL_DIR=/Users/min/AI/dayflow-skill`

如果都没有指定，脚本会自动尝试同级目录和 `~/.codex/skills/` 下的默认位置。

### 2. glab

需要安装并登录目标 GitLab 主机，例如：

```bash
glab auth status
glab auth login --hostname gitlab.gz.cvte.cn
```

## 仓库结构

```text
.
├── SKILL.md                             # Skill 主说明
├── README.md                            # 中文仓库说明
├── agents/openai.yaml                   # Codex 界面元信息
├── scripts/fetch_gitlab_events.py       # GitLab 活动采集脚本
├── scripts/generate_monthly_report.py   # 月报生成脚本
├── references/report-format.md          # 表格模板与字段规则
└── references/gitlab-data.md            # GitLab 采集说明
```

## 使用方式

### 生成月报 Markdown 表格

```bash
python3 scripts/generate_monthly_report.py \
  --month 2026-03 \
  --gitlab-hostname gitlab.gz.cvte.cn \
  --dayflow-skill-dir /Users/min/AI/dayflow-skill
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

## 输出格式

月报默认输出一张 Markdown 表格，表头固定为：

- `目标（含组织与个人）`
- `关键成果（交付物/数据）`
- `关键行动举措（对齐组织目标拆解，需体现延期情况）`
- `完成情况（成效、问题、风险、措施）`
- `工作质量`
- `工时/D（具体人天）`
- `自评难易（难/中/易）`
- `备注`

注意：

- 每一行代表一个任务拆分 / 工作流
- 工时和人天只根据 Dayflow 数据折算
- GitLab 数据用于补充提交、MR、项目推进和协作证据
- 无法直接验证的结论会明确写成保守判断或推断

## 设计原则

- 数据采集职责分离：Dayflow 由 `dayflow-skill` 负责，月报 skill 只负责汇总
- 输出结构固定：保证每次总结都能稳定落成同一张表
- 事实优先：先写可追溯事实，再写保守推断
- 兼容自动化：脚本支持 JSON 输出，便于后续接自动化流程

## 本地校验

```bash
python3 scripts/fetch_gitlab_events.py --month 2026-03 --hostname gitlab.gz.cvte.cn --indent 0
python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
```
