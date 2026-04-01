---
name: monthly-activity-skill
description: 使用 `dayflow-skill` 采集本机 Dayflow 数据，并通过 `glab` 读取 GitLab 活动事件，按月或自定义时间范围生成中文工作总结表格。适用于每月工作总结、基于 Dayflow 和 GitLab 证据回顾工作拆分、或为月报补充工时与代码协作线索。
---

# 月度工作总结

这个 skill 负责把 Dayflow 与 GitLab 两类活动证据汇总成中文 Markdown 输出：主表用于任务拆分，附表用于“本月收获与反思”。主表表头固定，每一行代表一个任务拆分，而不是把字段纵向展开。

## 快速开始

1. 确保 `dayflow-skill` 可用，并且能正常运行 `scripts/read_dayflow.py`
2. 确保 `glab` 已安装且已经登录目标 GitLab 主机
3. 运行总脚本：
   ```bash
   python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
   ```
4. 默认输出为 Markdown 表格；如果要继续二次加工，可使用 `--format json`

## 工作流

### 1. 先读取 Dayflow

- 必须复用 `dayflow-skill` 的读取脚本，不要在这里重复实现 Dayflow SQL
- 通过 `--dayflow-skill-dir` 或环境变量 `DAYFLOW_SKILL_DIR` 指定 skill 路径
- 如果未显式指定，脚本会依次尝试：
  - 当前仓库的同级目录 `../dayflow-skill`
  - `~/.codex/skills/dayflow-skill`
  - 兼容旧安装目录 `~/.codex/skills/dayflow-work-summary`

### 2. 再读取 GitLab

- 使用 `glab api events` 采集活动事件
- 必须显式带上 `-X GET`，否则带查询参数时容易落到错误的默认方法
- 常规场景使用 `scripts/fetch_gitlab_events.py`
- 需要切换 GitLab 主机时，用 `--gitlab-hostname`

### 3. 生成月报表格

- 输出字段固定为：
  - `目标（含组织与个人）`
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
- 工时与 D 只来自 Dayflow；GitLab 作为交付、协作、MR/提交线索的补充证据
- 无法直接证明的内容必须标注为保守判断或推断
- `目标` 仍然是一列，但单元格内建议拆成 2 条短 bullet
- 第 1 条写这一行任务拆分对应的项目交付目标，第 2 条写对应的能力成长或沉淀目标
- 不要显式写 `组织目标：`、`个人目标：` 这类标签
- `关键成果` 只写交付成果，不写 Dayflow / GitLab 的原始统计明细
- `完成情况` 必须把两类数据综合为一份阶段总结，语言要适合直接发给团队评审
- 表格单元格内优先用无序列表风格组织明细，并尽量用短句表达

## 常用命令

- 生成月报表格：
  ```bash
  python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn
  ```
- 生成 JSON 结构：
  ```bash
  python3 scripts/generate_monthly_report.py --month 2026-03 --gitlab-hostname gitlab.gz.cvte.cn --format json
  ```
- 单独查看 GitLab 事件：
  ```bash
  python3 scripts/fetch_gitlab_events.py --month 2026-03 --hostname gitlab.gz.cvte.cn
  ```

## 参考资料

- 表格规则与字段要求：`references/report-format.md`
- GitLab 采集方式与事件解释：`references/gitlab-data.md`
