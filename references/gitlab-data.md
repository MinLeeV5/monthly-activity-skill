# GitLab 数据采集说明

当你需要确认 monthly-activity-skill 如何读取 GitLab 数据时，使用这份说明。

## 推荐命令

常规场景统一使用：

```bash
glab api events --hostname <host> -X GET --paginate --output ndjson -f after=<YYYY-MM-DD> -f before=<YYYY-MM-DD>
```

## 关键注意点

- 带查询参数时必须显式加 `-X GET`
- 不加 `-X GET` 时，`glab api` 很容易走默认方法，导致返回 404 或其他误导性错误
- `--paginate --output ndjson` 适合采集整月事件并逐行解析

## 当前脚本使用方式

`fetch_gitlab_events.py` 会：

1. 先调用 `glab api user --hostname <host>` 获取当前登录用户
2. 再调用 `glab api events --hostname <host> -X GET --paginate --output ndjson ...`
3. 把原始事件整理为标准化 JSON

## 常见事件类型

- `pushed to`
- `pushed new`
- `opened`
- `accepted`
- `deleted`

这些事件通常足以支撑以下判断：

- 本月主要在哪些项目上活跃
- 提交与 MR 行为是否持续发生
- 哪些事项更像开发交付，哪些更像重构或协作推进

## 证据边界

GitLab `events` 更适合提供“动作轨迹”，不一定能完整表达：

- 业务背景
- 详细需求上下文
- 实际上线效果
- 非 GitLab 系统中的交付闭环

因此在月报中，GitLab 数据应作为 Dayflow 的补充证据，而不是唯一依据。
