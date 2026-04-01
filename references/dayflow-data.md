# Dayflow 数据读取说明

当你需要确认 Dayflow 数据存放位置、主表结构和推荐读取方式时，使用这份说明。

## 已验证的本地存储位置

- Dayflow 本地数据目录：`~/Library/Application Support/Dayflow/`
- 主 SQLite 数据库：`~/Library/Application Support/Dayflow/chunks.sqlite`
- 截图/录屏素材目录：`~/Library/Application Support/Dayflow/recordings/`

这些路径既来自公开的 Dayflow README，也已经结合本机安装的 App 和实际数据库做过验证。

## 推荐读取路径

常规场景使用 `scripts/read_dayflow.py`，不要每次临时手写 SQL。

原因：
- Dayflow 运行时数据库通常处于活跃状态
- SQLite 可能启用了 WAL
- 脚本会只读连接数据库，并先做 SQLite backup 快照，再查询快照，稳定性更高

## 关键数据表

### `timeline_cards`（从这里开始）

这是工作总结的首选主表。

核心字段：
- `day`：本地日历日，格式 `YYYY-MM-DD`
- `start`、`end`：人类可读的本地时间
- `start_ts`、`end_ts`：Unix 时间戳
- `title`：高层活动标题
- `summary`：简短摘要
- `category`、`subcategory`：粗粒度分类
- `detailed_summary`：更丰富的活动叙述
- `metadata`：JSON，常包含 `appSites` 和 `distractions`
- `is_deleted`：非 0 的记录要过滤掉

这个表适合用于：
- 日报 / 周报 / 月报
- 时长统计
- 交付物与主题提炼
- 分心线索补充

### `journal_entries`

这个表用于补充“目标”和“反思”。

核心字段：
- `day`
- `intentions`
- `goals`
- `reflections`
- `summary`
- `status`

这个表通常比较稀疏。如果没有记录，就回退到时间线证据，并明确说明目标是从活动轨迹推断出来的。

### `observations`

只有在 `timeline_cards` 加 `detailed_summary` 仍然不够具体时，才使用这张表。

这是绑定到 `analysis_batches` 的更底层证据，适合用于：
- 还原精确的 App / 窗口行为
- 看清一个短时间模糊区间内到底发生了什么
- 排查为什么卡片生成异常或缺失

### 其他表

- `daily_standup_entries`：存在时可作为 standup 缓存数据
- `screenshots`、`chunks`、`batch_screenshots`、`batch_chunks`、`analysis_batches`：采集与分析流水线内部表

## 查询原则

- 必须过滤 `timeline_cards.is_deleted = 0`
- 日期范围优先使用 `day`
- 时长计算优先使用 `start_ts` / `end_ts`
- 宽范围总结先用 card 级数据，不要一开始就下钻到底层
- 只有摘要太模糊或范围很窄时才带 `detailed_summary`
- 只有需要分心证据或站点线索时才带 `metadata`

## 实用判断规则

- 一张卡片代表一个连续的工作块
- 生成月报时，优先根据反复出现的标题/分类来归纳主题
- 填写目标、备注时，优先使用 journal 文本，推断只能作为补充
- 如果请求时间段没有卡片，要明确说 Dayflow 没有记录到该时段的工作块
