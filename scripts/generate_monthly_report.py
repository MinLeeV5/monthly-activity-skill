#!/usr/bin/env python3
"""汇总 Dayflow 与 GitLab 活动，生成中文月度工作总结表格。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

HEADERS = [
    "目标（含组织与个人）",
    "关键成果（交付物/数据）",
    "关键行动举措（对齐组织目标拆解，需体现延期情况）",
    "完成情况（成效、问题、风险、措施）",
    "工作质量",
    "工时/D（具体人天）",
    "自评难易（难/中/易）",
    "备注",
]

REFLECTION_HEADERS = ["类别", "分享"]
REFLECTION_ROWS = ["收获/启发/成长", "反思/自我批评"]

FALLBACK_THEME = "其他业务推进事项"
DELAY_KEYWORDS = ["延期", "delay", "blocked", "阻塞", "卡住", "返工", "待跟进", "reopen", "reopened"]

THEME_RULES: list[tuple[str, list[str]]] = [
    (
        "AI Agent与自动化工作流",
        [
            "agent",
            "agents",
            "ai",
            "llm",
            "gpt",
            "codex",
            "claude",
            "skill",
            "workflow",
            "automation",
            "automate",
            "prompt",
            "mcp",
            "tool",
            "智能",
            "自动化",
            "提示词",
            "大模型",
        ],
    ),
    (
        "业务研发与复杂问题排障",
        [
            "bug",
            "fix",
            "feature",
            "release",
            "deploy",
            "api",
            "gateway",
            "service",
            "frontend",
            "backend",
            "diagnosis",
            "研发",
            "功能",
            "发布",
            "上线",
            "排障",
            "修复",
            "需求",
            "接口",
            "故障",
            "项目",
        ],
    ),
    (
        "工程方法论与质量体系沉淀",
        [
            "refactor",
            "review",
            "test",
            "tests",
            "benchmark",
            "architecture",
            "design",
            "doc",
            "docs",
            "spec",
            "quality",
            "规范",
            "方法论",
            "质量",
            "测试",
            "文档",
            "方案",
            "重构",
            "流程",
            "沉淀",
            "标准",
            "架构",
        ],
    ),
    (
        "组织协同与人才事项",
        [
            "meeting",
            "sync",
            "align",
            "share",
            "interview",
            "hiring",
            "training",
            "mentor",
            "协同",
            "沟通",
            "对齐",
            "会议",
            "面试",
            "招聘",
            "培训",
            "分享",
            "复盘",
            "人才",
            "带教",
        ],
    ),
]

GOAL_HINTS = {
    "AI Agent与自动化工作流": (
        "提升 AI 研发效率与自动化工作流能力。",
        "沉淀可复用的 skill、agent、workflow 或提示词资产。",
    ),
    "业务研发与复杂问题排障": (
        "推进业务需求交付、系统稳定性治理与复杂问题闭环。",
        "完成关键研发事项、排障与交付闭环，降低业务风险。",
    ),
    "工程方法论与质量体系沉淀": (
        "提升团队工程质量、交付效率与方法沉淀能力。",
        "把经验固化为规范、测试、文档或流程资产。",
    ),
    "组织协同与人才事项": (
        "保障跨团队协同顺畅并推进组织相关事项。",
        "完成沟通对齐、人才事项或经验传递，降低协作摩擦。",
    ),
    FALLBACK_THEME: (
        "推进本月重点工作事项持续落地。",
        "补齐零散但必要的执行动作，避免任务悬空。",
    ),
}


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass
class ThemeBucket:
    label: str
    cards: list[dict[str, Any]]
    events: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总 Dayflow 与 GitLab 数据，并输出中文工作总结表格。")
    parser.add_argument("--month", help="整月，格式 YYYY-MM。")
    parser.add_argument("--from", dest="from_date", help="起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--to", dest="to_date", help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--gitlab-hostname", required=True, help="GitLab 主机，例如 gitlab.gz.cvte.cn。")
    parser.add_argument("--dayflow-skill-dir", help="dayflow-skill 仓库或安装目录。")
    parser.add_argument("--dayflow-db-path", help="透传给 dayflow-skill 的数据库路径。")
    parser.add_argument("--glab-bin", default="glab", help="glab 可执行文件路径。默认：glab")
    parser.add_argument("--python-bin", default=sys.executable, help="Python 可执行文件路径。默认使用当前解释器。")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="输出格式。默认：markdown",
    )
    parser.add_argument(
        "--include-all-cards",
        action="store_true",
        help="默认只统计 Work 类卡片；加上该参数后不过滤分类。",
    )
    parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="透传给 dayflow-skill 并在推断时纳入 metadata。",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数。填 0 表示紧凑输出。默认：2。")
    return parser.parse_args()


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"无效日期：{value}。期望格式为 YYYY-MM-DD。") from exc


def parse_month(value: str) -> DateWindow:
    try:
        month_start = datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise SystemExit(f"无效月份：{value}。期望格式为 YYYY-MM。") from exc
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return DateWindow(start=month_start, end=next_month - timedelta(days=1))


def resolve_window(args: argparse.Namespace) -> DateWindow:
    choices = sum(bool(value) for value in (args.month, args.from_date or args.to_date))
    if choices != 1:
        raise SystemExit("必须且只能提供一种范围：--month，或 --from/--to。")

    if args.month:
        return parse_month(args.month)

    if not args.from_date or not args.to_date:
        raise SystemExit("使用自定义范围时，必须同时提供 --from 和 --to。")

    start = parse_iso_date(args.from_date)
    end = parse_iso_date(args.to_date)
    if end < start:
        raise SystemExit("无效范围：--to 不能早于 --from。")
    return DateWindow(start=start, end=end)


def normalize_text(value: str | None) -> str:
    return (value or "").replace("_", " ").replace("/", " ").lower()


def run_json_command(command: list[str]) -> Any:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise SystemExit(f"命令执行失败：{' '.join(command)}\n{message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"无法解析 JSON 输出：{' '.join(command)}") from exc


def resolve_dayflow_reader(dayflow_skill_dir: str | None) -> Path:
    here = Path(__file__).resolve()
    candidates: list[Path] = []

    if dayflow_skill_dir:
        candidates.append(Path(dayflow_skill_dir).expanduser())

    env_path = os.environ.get("DAYFLOW_SKILL_DIR")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            here.parents[2] / "dayflow-skill",
            Path.home() / ".codex" / "skills" / "dayflow-skill",
            Path.home() / ".codex" / "skills" / "dayflow-work-summary",
        ]
    )

    for candidate in candidates:
        script_path = candidate / "scripts" / "read_dayflow.py"
        if script_path.exists():
            return script_path

    checked = "\n".join(str(path) for path in candidates)
    raise SystemExit(
        "未找到 dayflow-skill 的读取脚本，请通过 --dayflow-skill-dir 或 DAYFLOW_SKILL_DIR 指定。\n"
        f"已检查：\n{checked}"
    )


def resolve_gitlab_reader() -> Path:
    script_path = Path(__file__).resolve().with_name("fetch_gitlab_events.py")
    if not script_path.exists():
        raise SystemExit(f"未找到 GitLab 读取脚本：{script_path}")
    return script_path


def collect_dayflow(window: DateWindow, args: argparse.Namespace) -> dict[str, Any]:
    reader = resolve_dayflow_reader(args.dayflow_skill_dir)
    command = [
        args.python_bin,
        str(reader),
        "--from",
        window.start.isoformat(),
        "--to",
        window.end.isoformat(),
        "--include-details",
        "--indent",
        "0",
    ]
    if args.include_metadata:
        command.append("--include-metadata")
    if args.dayflow_db_path:
        command.extend(["--db-path", args.dayflow_db_path])
    return run_json_command(command)


def collect_gitlab(window: DateWindow, args: argparse.Namespace) -> dict[str, Any]:
    reader = resolve_gitlab_reader()
    command = [
        args.python_bin,
        str(reader),
        "--from",
        window.start.isoformat(),
        "--to",
        window.end.isoformat(),
        "--hostname",
        args.gitlab_hostname,
        "--glab-bin",
        args.glab_bin,
        "--indent",
        "0",
    ]
    payload = run_json_command(command)
    project_names_by_id = {
        int(key): value for key, value in (payload.get("project_names_by_id") or {}).items()
    }
    for event in payload.get("events", []):
        project_id = event.get("project_id")
        if project_id in project_names_by_id:
            event["_project_name"] = project_names_by_id[project_id]
    return payload


def is_work_card(card: dict[str, Any], include_all_cards: bool) -> bool:
    if include_all_cards:
        return True

    category = normalize_text(card.get("category"))
    subcategory = normalize_text(card.get("subcategory"))
    text = f"{category} {subcategory}".strip()
    if not text:
        return True
    return any(keyword in text for keyword in ("work", "工作", "研发", "engineering"))


def score_theme(text: str, keywords: list[str]) -> int:
    score = 0
    for keyword in keywords:
        if keyword in text:
            score += text.count(keyword)
    return score


def classify_text(text: str) -> str:
    best_label = FALLBACK_THEME
    best_score = 0
    for label, keywords in THEME_RULES:
        score = score_theme(text, keywords)
        if score > best_score:
            best_label = label
            best_score = score
    return best_label


def card_text(card: dict[str, Any]) -> str:
    fields = [
        card.get("title"),
        card.get("summary"),
        card.get("detailed_summary"),
        card.get("category"),
        card.get("subcategory"),
    ]
    metadata = card.get("metadata")
    if isinstance(metadata, dict):
        fields.append(json.dumps(metadata, ensure_ascii=False))
    return normalize_text(" ".join(str(field) for field in fields if field))


def event_text(event: dict[str, Any]) -> str:
    push_data = event.get("push_data") or {}
    fields = [
        event.get("action_name"),
        event.get("target_title"),
        event.get("target_type"),
        push_data.get("commit_title"),
        push_data.get("ref"),
    ]
    return normalize_text(" ".join(str(field) for field in fields if field))


def build_theme_buckets(cards: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[ThemeBucket]:
    buckets: dict[str, ThemeBucket] = {}

    def ensure_bucket(label: str) -> ThemeBucket:
        if label not in buckets:
            buckets[label] = ThemeBucket(label=label, cards=[], events=[])
        return buckets[label]

    for card in cards:
        label = classify_text(card_text(card))
        ensure_bucket(label).cards.append(card)

    for event in events:
        label = classify_text(event_text(event))
        ensure_bucket(label).events.append(event)

    if not buckets:
        buckets[FALLBACK_THEME] = ThemeBucket(label=FALLBACK_THEME, cards=[], events=[])

    def bucket_weight(bucket: ThemeBucket) -> tuple[float, int]:
        seconds = sum(max(0, card.get("duration_seconds") or 0) for card in bucket.cards)
        return (seconds / 3600.0, len(bucket.events))

    ordered = sorted(
        buckets.values(),
        key=lambda bucket: (-bucket_weight(bucket)[0], -bucket_weight(bucket)[1], bucket.label),
    )
    return ordered


def hours_and_days(cards: list[dict[str, Any]]) -> tuple[float, float]:
    total_seconds = sum(max(0, card.get("duration_seconds") or 0) for card in cards)
    hours = round(total_seconds / 3600.0, 2)
    person_days = round(total_seconds / 28800.0, 2)
    return hours, person_days


def top_card_titles(cards: list[dict[str, Any]], limit: int = 3) -> list[str]:
    totals: dict[str, float] = defaultdict(float)
    for card in cards:
        title = str(card.get("title") or card.get("summary") or "未命名事项").strip()
        totals[title] += max(0, card.get("duration_seconds") or 0)
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [title for title, _ in ordered[:limit]]


def top_event_titles(events: list[dict[str, Any]], limit: int = 3) -> list[str]:
    counter: Counter[str] = Counter()
    for event in events:
        push_data = event.get("push_data") or {}
        title = str(push_data.get("commit_title") or event.get("target_title") or "").strip()
        if title:
            counter[title] += 1
    return [title for title, _ in counter.most_common(limit)]


def action_counts(events: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(event.get("action_name") or "unknown") for event in events)


def project_names(events: list[dict[str, Any]], limit: int = 3) -> list[str]:
    counter: Counter[str] = Counter()
    for event in events:
        project_name = str(event.get("_project_name") or "").strip()
        project_id = event.get("project_id")
        if project_name:
            counter[project_name] += 1
        elif event.get("target_type") == "Project" and event.get("target_title"):
            counter[event["target_title"]] += 1
        elif project_id is not None:
            counter[f"项目#{project_id}"] += 1
    return [name for name, _ in counter.most_common(limit)]


def distinct_active_days(cards: list[dict[str, Any]]) -> int:
    return len({str(card.get("day")) for card in cards if card.get("day")})


def count_delay_signals(cards: list[dict[str, Any]], events: list[dict[str, Any]]) -> int:
    count = 0
    for card in cards:
        text = card_text(card)
        if any(keyword in text for keyword in DELAY_KEYWORDS):
            count += 1
    for event in events:
        text = event_text(event)
        if any(keyword in text for keyword in DELAY_KEYWORDS):
            count += 1
    return count


def collect_journal_hints(journal_entries: list[dict[str, Any]], limit: int = 2) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for entry in journal_entries:
        for key in ("intentions", "goals", "summary"):
            raw = str(entry.get(key) or "").strip()
            if not raw:
                continue
            compact = " ".join(raw.split())
            if compact in seen:
                continue
            seen.add(compact)
            hints.append(compact)
            if len(hints) >= limit:
                return hints
    return hints


def format_lines(lines: list[str]) -> str:
    return "<br>".join(line for line in lines if line)


def infer_target(bucket: ThemeBucket, journal_hints: list[str]) -> str:
    org_goal, personal_goal = GOAL_HINTS.get(bucket.label, GOAL_HINTS[FALLBACK_THEME])
    lines = [
        f"组织目标：{org_goal}",
        f"个人目标：{personal_goal}",
    ]
    if journal_hints:
        lines.append(f"Journal 线索：{journal_hints[0]}")
    else:
        lines.append("说明：以上目标主要根据 Dayflow / GitLab 活动轨迹保守推断。")
    return format_lines(lines)


def build_key_results(bucket: ThemeBucket) -> str:
    hours, person_days = hours_and_days(bucket.cards)
    lines: list[str] = []
    if bucket.cards:
        lines.append(
            f"Dayflow：记录到 {len(bucket.cards)} 条工作卡片，覆盖 {distinct_active_days(bucket.cards)} 天，累计 {hours:.2f} 小时 / {person_days:.2f} D。"
        )
    if bucket.events:
        counts = action_counts(bucket.events)
        action_text = "、".join(f"{action} {count} 次" for action, count in counts.most_common(3))
        projects = "、".join(project_names(bucket.events)) or "项目分布待补证"
        lines.append(f"GitLab：记录到 {len(bucket.events)} 次事件，主要动作为 {action_text}，主要涉及 {projects}。")
    card_titles = top_card_titles(bucket.cards)
    event_titles = top_event_titles(bucket.events)
    representatives = card_titles + [title for title in event_titles if title not in card_titles]
    if representatives:
        lines.append(f"代表性事项：{'；'.join(representatives[:4])}。")
    if not lines:
        lines.append("说明：本任务拆分缺少足够的 Dayflow / GitLab 证据，建议补充其他系统记录。")
    return format_lines(lines)


def build_key_actions(bucket: ThemeBucket) -> str:
    titles = top_card_titles(bucket.cards)
    projects = project_names(bucket.events)
    delay_signals = count_delay_signals(bucket.cards, bucket.events)
    lines: list[str] = []
    if titles:
        lines.append(f"关键动作：围绕 {'、'.join(titles[:3])} 持续推进，形成连续工作块。")
    if projects:
        lines.append(f"GitLab推进：主要围绕 {'、'.join(projects[:3])} 持续提交 / MR 推进，与组织交付目标保持对齐。")
    if delay_signals > 0:
        lines.append(f"延期情况：发现 {delay_signals} 处潜在阻塞或待跟进信号，建议结合 MR / issue 进一步复核。")
    else:
        lines.append("延期情况：未从 Dayflow / GitLab 数据中看到明确延期证据。")
    return format_lines(lines)


def build_completion(bucket: ThemeBucket) -> str:
    hours, _ = hours_and_days(bucket.cards)
    counts = action_counts(bucket.events)
    opened = counts.get("opened", 0)
    accepted = counts.get("accepted", 0)
    pushed = counts.get("pushed to", 0) + counts.get("pushed new", 0)
    title_count = len({title for title in top_card_titles(bucket.cards, limit=10)})

    success = f"成效：本主题投入约 {hours:.2f} 小时，并形成 {len(bucket.cards)} 条 Dayflow 工作卡片、{len(bucket.events)} 次 GitLab 事件，说明推进较为持续。"
    if title_count >= 6:
        problem = "问题：本主题下的事项较多，存在一定上下文切换，整理与归档成本偏高。"
    elif bucket.events and accepted == 0 and pushed > 0:
        problem = "问题：GitLab 侧更多体现为持续提交，闭环类动作证据相对有限。"
    else:
        problem = "问题：未看到明显异常，但部分交付闭环仍需要结合其他系统补证。"

    if opened > accepted:
        risk = "风险：已打开事项多于已合入事项，说明仍有部分工作处于推进中，后续需要继续跟进闭环。"
    elif count_delay_signals(bucket.cards, bucket.events) > 0:
        risk = "风险：存在潜在延期或阻塞信号，若不及时跟进可能影响后续节奏。"
    else:
        risk = "风险：未从当前两类数据中看到明确延期证据，但上线效果、业务结果仍需结合其他记录复核。"

    if projects := project_names(bucket.events):
        measures = f"措施：继续围绕 {'、'.join(projects[:2])} 聚焦推进，并补充 MR、issue 或发布记录作为闭环证据。"
    else:
        measures = "措施：建议补充相关 MR、issue、文档或发布记录，增强闭环与复盘证据。"

    return format_lines([success, problem, risk, measures])


def build_work_quality(bucket: ThemeBucket) -> str:
    hours, _ = hours_and_days(bucket.cards)
    counts = action_counts(bucket.events)
    accepted = counts.get("accepted", 0)
    pushed = counts.get("pushed to", 0) + counts.get("pushed new", 0)
    if hours >= 40:
        return "较高：该主题投入时间长，且伴随明显提交或 MR 闭环动作，交付连续性较强。"
    if accepted >= 8 or pushed >= 20:
        return "较高：虽然 Dayflow 工时占比不高，但 GitLab 侧存在密集提交或 MR 闭环，说明交付动作较集中。"
    if hours >= 16 or pushed >= 10:
        return "稳定：能看到持续推进与交付轨迹，但仍有部分结果需要结合更多证据确认。"
    return "常规：当前证据能证明在推进，但深度与闭环程度相对有限。"


def build_difficulty(bucket: ThemeBucket) -> str:
    hours, _ = hours_and_days(bucket.cards)
    signals = count_delay_signals(bucket.cards, bucket.events)
    projects = len(project_names(bucket.events, limit=10))
    action_types = len(action_counts(bucket.events))
    score = 0
    if hours >= 40:
        score += 2
    elif hours >= 16:
        score += 1
    if projects >= 3:
        score += 1
    if action_types >= 4:
        score += 1
    if signals > 0:
        score += 1

    if score >= 4:
        return "难：投入时间长、并行事项多，且存在一定协同或阻塞信号。"
    if score >= 2:
        return "中：需要持续推进并协调多类动作，但整体仍在可控范围内。"
    return "易：任务边界相对清晰，推进路径比较直接。"


def build_notes(bucket: ThemeBucket) -> str:
    notes = ["工时和人天仅根据 Dayflow 数据折算。"]
    if not bucket.cards:
        notes.append("本行主要依据 GitLab 事件归纳，缺少对应 Dayflow 工时。")
    if not bucket.events:
        notes.append("本行未匹配到 GitLab 事件，可能属于非 GitLab 交付或线下工作。")
    notes.append("目标、质量和难易度含保守推断成分。")
    return format_lines(notes)


def build_monthly_reflection(
    buckets: list[ThemeBucket],
    dayflow_payload: dict[str, Any],
    gitlab_payload: dict[str, Any],
) -> dict[str, str]:
    top_buckets = []
    for bucket in buckets:
        hours, person_days = hours_and_days(bucket.cards)
        top_buckets.append((bucket.label, hours, person_days, len(bucket.events)))
    top_buckets = sorted(top_buckets, key=lambda item: (-item[1], -item[3], item[0]))

    top_theme_text = "、".join(
        f"{label}（{hours:.2f} 小时 / {person_days:.2f} D）"
        for label, hours, person_days, _ in top_buckets[:3]
        if hours > 0
    )
    if not top_theme_text:
        top_theme_text = "本月主题分布较分散，暂未形成明显的单一高投入主题。"

    dayflow_aggregates = dayflow_payload.get("aggregates", {})
    gitlab_aggregates = gitlab_payload.get("aggregates", {})
    accepted = gitlab_aggregates.get("by_action", {}).get("accepted", 0)
    pushed = gitlab_aggregates.get("by_action", {}).get("pushed to", 0) + gitlab_aggregates.get("by_action", {}).get("pushed new", 0)
    journal_count = len(dayflow_payload.get("journal_entries", []))

    growth_lines = [
        f"从本月活动轨迹看，主要精力集中在 {top_theme_text}，说明本月已经形成较清晰的阶段性投入重心。",
        f"结合 GitLab 记录，本月累计有 {pushed} 次提交动作、{accepted} 次合入 / 接受动作，说明不少事项已经从执行推进到了交付闭环或接近闭环。",
    ]
    if top_buckets and top_buckets[0][0] == "AI Agent与自动化工作流":
        growth_lines.append("在 AI Agent、skill 和自动化工作流方面的投入较深，说明本月在“把能力固化为工具资产”上有明显成长。")
    elif top_buckets and top_buckets[0][0] == "工程方法论与质量体系沉淀":
        growth_lines.append("工程质量与方法沉淀类事项占比较高，说明本月不只是做交付，也在持续积累可复用的方法和规范。")
    else:
        growth_lines.append("从高频主题看，本月已经不只是零散处理任务，而是在若干重点方向上形成了持续推进和沉淀。")

    delay_signals = sum(count_delay_signals(bucket.cards, bucket.events) for bucket in buckets)
    reflection_lines = []
    if len([bucket for bucket in buckets if hours_and_days(bucket.cards)[0] > 0]) >= 4:
        reflection_lines.append("本月任务线偏多，存在较明显的上下文切换；后续可以进一步压缩并行主题，减少精力分散。")
    if delay_signals > 0:
        reflection_lines.append(f"从 Dayflow / GitLab 轨迹里能看到约 {delay_signals} 处潜在阻塞或待跟进信号，说明风险暴露和节奏管理还可以更前置。")
    if gitlab_aggregates.get("by_action", {}).get("opened", 0) > accepted:
        reflection_lines.append("已打开事项多于已闭环事项，说明部分工作仍停留在推进中，后续要更关注收尾和闭环证据沉淀。")
    if journal_count == 0:
        reflection_lines.append("本月几乎没有 journal 目标/反思记录，导致月报中的部分目标与反思只能根据活动轨迹推断，建议下月加强主动沉淀。")
    if not reflection_lines:
        reflection_lines.append("本月整体推进较稳，但仍建议在每周固定补一次目标、风险与复盘记录，避免月底回顾过度依赖轨迹推断。")

    return {
        "收获/启发/成长": format_lines(growth_lines),
        "反思/自我批评": format_lines(reflection_lines),
    }


def table_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def build_rows(
    buckets: list[ThemeBucket],
    journal_entries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    journal_hints = collect_journal_hints(journal_entries)
    rows: list[dict[str, str]] = []
    for bucket in buckets:
        hours, person_days = hours_and_days(bucket.cards)
        row = {
            HEADERS[0]: infer_target(bucket, journal_hints),
            HEADERS[1]: build_key_results(bucket),
            HEADERS[2]: build_key_actions(bucket),
            HEADERS[3]: build_completion(bucket),
            HEADERS[4]: build_work_quality(bucket),
            HEADERS[5]: f"{hours:.2f} 小时 / {person_days:.2f} D",
            HEADERS[6]: build_difficulty(bucket),
            HEADERS[7]: build_notes(bucket),
        }
        rows.append(row)
    return rows


def render_markdown(
    window: DateWindow,
    rows: list[dict[str, str]],
    reflection: dict[str, str],
    dayflow_payload: dict[str, Any],
    gitlab_payload: dict[str, Any],
) -> str:
    lines = [
        f"# 月度工作总结（{window.label}）",
        "",
        f"数据来源：Dayflow `{dayflow_payload['source']['db_path']}` + GitLab `{gitlab_payload['source']['hostname']}`",
        "说明：主表中每一行代表一个任务拆分；工时仅根据 Dayflow 折算，D 按 8 小时/天计算。",
        "",
        "| " + " | ".join(HEADERS) + " |",
        "| " + " | ".join(["---"] * len(HEADERS)) + " |",
    ]
    for row in rows:
        values = [table_escape(row[header]) for header in HEADERS]
        lines.append("| " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## 本月收获与反思",
            "",
            "| " + " | ".join(REFLECTION_HEADERS) + " |",
            "| " + " | ".join(["--"] * len(REFLECTION_HEADERS)) + " |",
        ]
    )
    for label in REFLECTION_ROWS:
        lines.append("| " + table_escape(label) + " | " + table_escape(reflection[label]) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    window = resolve_window(args)

    dayflow_payload = collect_dayflow(window, args)
    gitlab_payload = collect_gitlab(window, args)

    cards = [card for card in dayflow_payload.get("cards", []) if is_work_card(card, args.include_all_cards)]
    events = gitlab_payload.get("events", [])
    buckets = build_theme_buckets(cards, events)
    rows = build_rows(buckets, dayflow_payload.get("journal_entries", []))
    reflection = build_monthly_reflection(buckets, dayflow_payload, gitlab_payload)

    if args.format == "markdown":
        sys.stdout.write(render_markdown(window, rows, reflection, dayflow_payload, gitlab_payload))
        sys.stdout.write("\n")
        return 0

    payload = {
        "range": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
        },
        "sources": {
            "dayflow": dayflow_payload.get("source", {}),
            "gitlab": gitlab_payload.get("source", {}),
        },
        "rows": rows,
        "monthly_reflection": reflection,
    }
    indent = None if args.indent <= 0 else args.indent
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
