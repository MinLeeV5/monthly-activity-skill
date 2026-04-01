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
    "目标",
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

DEFAULT_DAYFLOW_APP_PATH = Path("/Applications/Dayflow.app")
DEFAULT_DAYFLOW_DB_PATH = Path.home() / "Library" / "Application Support" / "Dayflow" / "chunks.sqlite"

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

PERSONAL_GROWTH_HINTS = {
    "AI Agent与自动化工作流": "提升 AI Agent 设计、自动化编排与工具化沉淀能力，增强把经验固化为可复用资产的能力。",
    "业务研发与复杂问题排障": "提升复杂问题定位、跨模块联调与业务落地能力，增强对关键问题的闭环推进能力。",
    "工程方法论与质量体系沉淀": "提升架构抽象、工程规范与质量治理能力，沉淀更可复用的方法、流程与标准。",
    "组织协同与人才事项": "提升跨团队协同、评审沟通、经验传递与人才判断能力，增强组织协作影响力。",
    FALLBACK_THEME: "提升多线任务统筹和结果导向表达能力，增强对零散事项的收敛与闭环能力。",
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


@dataclass(frozen=True)
class DayflowEnvironment:
    app_path: Path
    db_path: Path
    app_exists: bool
    db_exists: bool
    available: bool
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总 Dayflow 与 GitLab 数据，并输出中文工作总结表格。")
    parser.add_argument("--month", help="整月，格式 YYYY-MM。")
    parser.add_argument("--from", dest="from_date", help="起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--to", dest="to_date", help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--gitlab-hostname", required=True, help="GitLab 主机，例如 gitlab.gz.cvte.cn。")
    parser.add_argument("--dayflow-skill-dir", help="兼容旧配置：外部 dayflow-skill 仓库或安装目录。")
    parser.add_argument(
        "--dayflow-app-path",
        default=str(DEFAULT_DAYFLOW_APP_PATH),
        help=f"Dayflow 应用路径。默认：{DEFAULT_DAYFLOW_APP_PATH}",
    )
    parser.add_argument(
        "--dayflow-db-path",
        help=f"Dayflow SQLite 数据库路径。默认自动探测：{DEFAULT_DAYFLOW_DB_PATH}",
    )
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
    local_reader = Path(__file__).resolve().with_name("read_dayflow.py")
    if local_reader.exists():
        return local_reader

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
        "未找到内置或外部 Dayflow 读取脚本，请通过 --dayflow-skill-dir 或 DAYFLOW_SKILL_DIR 指定。\n"
        f"已检查：\n{checked}"
    )


def detect_dayflow_environment(
    app_path: str | Path,
    db_path: str | Path,
    db_path_explicit: bool,
) -> DayflowEnvironment:
    resolved_app_path = Path(app_path).expanduser()
    resolved_db_path = Path(db_path).expanduser()
    app_exists = resolved_app_path.exists()
    db_exists = resolved_db_path.exists()

    if db_path_explicit and db_exists:
        available = True
        reason = "explicit_db_path"
    elif not app_exists:
        available = False
        reason = "dayflow_app_missing"
    elif not db_exists:
        available = False
        reason = "dayflow_db_missing"
    else:
        available = True
        reason = "auto_detected"

    return DayflowEnvironment(
        app_path=resolved_app_path,
        db_path=resolved_db_path,
        app_exists=app_exists,
        db_exists=db_exists,
        available=available,
        reason=reason,
    )


def dayflow_reason_text(reason: str) -> str:
    if reason == "explicit_db_path":
        return "通过显式数据库路径启用 Dayflow"
    if reason == "dayflow_app_missing":
        return "未检测到 Dayflow 应用"
    if reason == "dayflow_db_missing":
        return "已检测到 Dayflow 应用，但未找到数据库"
    return "已自动检测到 Dayflow 应用与数据库"


def empty_dayflow_payload(window: DateWindow, environment: DayflowEnvironment) -> dict[str, Any]:
    by_day: dict[str, dict[str, Any]] = {}
    missing_days: list[str] = []
    current = window.start
    while current <= window.end:
        day_key = current.isoformat()
        by_day[day_key] = {
            "count": 0,
            "seconds": 0,
            "hours": 0.0,
            "person_days_8h": 0.0,
        }
        missing_days.append(day_key)
        current += timedelta(days=1)

    return {
        "source": {
            "available": False,
            "reason": environment.reason,
            "reason_text": dayflow_reason_text(environment.reason),
            "app_path": str(environment.app_path),
            "app_exists": environment.app_exists,
            "db_path": str(environment.db_path),
            "db_exists": environment.db_exists,
            "storage_dir": str(environment.db_path.parent),
            "reader": "scripts/read_dayflow.py",
        },
        "range": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aggregates": {
            "card_count": 0,
            "active_days": 0,
            "total_seconds": 0,
            "total_hours": 0.0,
            "total_person_days_8h": 0.0,
            "by_day": by_day,
            "missing_days": missing_days,
            "by_category": {},
            "by_subcategory": {},
        },
        "cards": [],
        "journal_entries": [],
    }


def dayflow_is_available(dayflow_payload: dict[str, Any]) -> bool:
    return bool(dayflow_payload.get("source", {}).get("available"))


def resolve_gitlab_reader() -> Path:
    script_path = Path(__file__).resolve().with_name("fetch_gitlab_events.py")
    if not script_path.exists():
        raise SystemExit(f"未找到 GitLab 读取脚本：{script_path}")
    return script_path


def collect_dayflow(window: DateWindow, args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.dayflow_db_path).expanduser() if args.dayflow_db_path else DEFAULT_DAYFLOW_DB_PATH
    environment = detect_dayflow_environment(
        app_path=args.dayflow_app_path,
        db_path=db_path,
        db_path_explicit=bool(args.dayflow_db_path),
    )
    if not environment.available:
        return empty_dayflow_payload(window, environment)

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
    command.extend(["--db-path", str(environment.db_path)])
    payload = run_json_command(command)
    payload.setdefault("source", {}).update(
        {
            "available": True,
            "reason": environment.reason,
            "reason_text": dayflow_reason_text(environment.reason),
            "app_path": str(environment.app_path),
            "app_exists": environment.app_exists,
            "db_path": str(environment.db_path),
            "db_exists": environment.db_exists,
        }
    )
    return payload


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


def format_effort_value(cards: list[dict[str, Any]], dayflow_available: bool) -> str:
    if not dayflow_available:
        return "未检测到 Dayflow，暂无法折算工时 / D"
    hours, person_days = hours_and_days(cards)
    return f"{hours:.2f} 小时 / {person_days:.2f} D"


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


def format_bullets(lines: list[str]) -> str:
    items = [line.strip() for line in lines if line and line.strip()]
    return "<br>".join(f"- {item}" for item in items)


def tidy_name(name: str) -> str:
    compact = name.strip()
    if "/" in compact:
        compact = compact.split("/")[-1]
    return compact


def display_projects(events: list[dict[str, Any]], limit: int = 3) -> list[str]:
    return [tidy_name(name) for name in project_names(events, limit=limit)]


def display_titles(bucket: ThemeBucket, limit: int = 4) -> list[str]:
    titles = top_card_titles(bucket.cards, limit=limit)
    extra_titles = [title for title in top_event_titles(bucket.events, limit=limit) if title not in titles]
    return (titles + extra_titles)[:limit]


def organization_goal_text(bucket: ThemeBucket) -> str:
    projects = display_projects(bucket.events, limit=3)
    if projects:
        if bucket.label == "AI Agent与自动化工作流":
            return f"围绕 {'、'.join(projects)} 等项目推进智能能力、自动化工作流与配套工具的落地交付。"
        if bucket.label == "业务研发与复杂问题排障":
            return f"围绕 {'、'.join(projects)} 等项目推进功能迭代、问题修复与关键链路稳定性优化。"
        if bucket.label == "工程方法论与质量体系沉淀":
            return f"围绕 {'、'.join(projects)} 等项目推进工程治理、质量优化与方法沉淀相关交付。"
        return f"围绕 {'、'.join(projects)} 等项目推进协同事项、评审支持与组织相关工作的落地。"

    titles = display_titles(bucket, limit=2)
    if titles:
        return f"围绕 {'、'.join(titles)} 等事项推进阶段性交付与结果落地。"
    return GOAL_HINTS.get(bucket.label, GOAL_HINTS[FALLBACK_THEME])[0]


def delivery_stage_text(bucket: ThemeBucket) -> str:
    counts = action_counts(bucket.events)
    accepted = counts.get("accepted", 0)
    opened = counts.get("opened", 0)
    pushed = counts.get("pushed to", 0) + counts.get("pushed new", 0)
    if accepted > 0:
        return "部分事项已完成提交、评审与合入，阶段性成果较明确。"
    if opened > 0:
        return "相关事项已推进到提交与评审阶段，交付节奏稳定。"
    if pushed > 0:
        return "相关改造已形成持续代码输出，交付推进较扎实。"
    return "相关事项以过程性推进为主，闭环情况需结合其他材料补证。"


def build_goal(bucket: ThemeBucket, journal_hints: list[str]) -> str:
    del journal_hints
    return format_bullets(
        [
            organization_goal_text(bucket),
            PERSONAL_GROWTH_HINTS.get(bucket.label, PERSONAL_GROWTH_HINTS[FALLBACK_THEME]),
        ]
    )


def build_key_results(bucket: ThemeBucket) -> str:
    lines: list[str] = []
    projects = display_projects(bucket.events, limit=3)
    deliverables = display_titles(bucket, limit=4)
    if projects:
        lines.append(f"交付项目：{'、'.join(projects)}")
    if deliverables:
        lines.append(f"代表性交付：{'；'.join(deliverables)}")
    lines.append(f"阶段结果：{delivery_stage_text(bucket)}")
    if not lines:
        lines.append("说明：本任务拆分缺少足够的 Dayflow / GitLab 证据，建议补充其他系统记录。")
    return format_bullets(lines)


def build_key_actions(bucket: ThemeBucket) -> str:
    titles = display_titles(bucket, limit=3)
    projects = display_projects(bucket.events, limit=3)
    delay_signals = count_delay_signals(bucket.cards, bucket.events)
    lines: list[str] = []
    if titles:
        lines.append(f"关键动作：围绕 {'、'.join(titles[:3])} 持续推进")
    if projects:
        lines.append(f"对齐组织目标：聚焦 {'、'.join(projects[:3])} 的交付推进与质量优化")
    if delay_signals > 0:
        lines.append("延期情况：存在少量潜在阻塞或待跟进信号，建议评审时继续复核闭环状态")
    else:
        lines.append("延期情况：未从 Dayflow / GitLab 数据中看到明确延期证据。")
    return format_bullets(lines)


def build_completion(bucket: ThemeBucket) -> str:
    counts = action_counts(bucket.events)
    opened = counts.get("opened", 0)
    accepted = counts.get("accepted", 0)
    active_days = distinct_active_days(bucket.cards)

    if accepted > 0:
        success = "成效：事项已推进到提交评审并形成阶段性闭环，整体交付较扎实。"
    elif bucket.events:
        success = "成效：事项保持持续推进，已形成明确交付输出。"
    else:
        success = "成效：事项在本月保持持续推进。"

    if active_days >= 10:
        problem = "问题：推进周期较长，阶段内存在多线并行和上下文切换。"
    else:
        problem = "问题：部分结果仍需补充业务上下文，便于评审理解闭环价值。"

    if opened > accepted:
        risk = "风险：部分事项仍在推进或评审中，若收尾不够集中，可能影响阶段性闭环质量。"
    elif count_delay_signals(bucket.cards, bucket.events) > 0:
        risk = "风险：仍有少量待确认或待跟进事项，若复核不及时，可能影响后续节奏。"
    else:
        risk = "风险：未见明确延期证据，但最终业务效果和验收结果仍建议结合发布或评审记录确认。"

    if projects := display_projects(bucket.events, limit=2):
        measures = f"措施：围绕 {'、'.join(projects[:2])} 继续聚焦收尾，并补齐评审结论、发布信息和复盘沉淀。"
    else:
        measures = "措施：补齐评审结论、文档记录或发布信息，增强结果表达与闭环证明。"

    return format_bullets([success, problem, risk, measures])


def build_work_quality(bucket: ThemeBucket) -> str:
    hours, _ = hours_and_days(bucket.cards)
    counts = action_counts(bucket.events)
    accepted = counts.get("accepted", 0)
    pushed = counts.get("pushed to", 0) + counts.get("pushed new", 0)
    if hours >= 40:
        return "较高：该主题投入持续、推进节奏稳定，且能看到较明确的交付收敛与结果沉淀。"
    if accepted >= 8 or pushed >= 20:
        return "较高：虽然显性工时不算最高，但交付动作集中、输出连续，说明整体执行质量较好。"
    if hours >= 16 or pushed >= 10:
        return "稳定：能看到持续推进与较明确的输出轨迹，整体质量处于稳步推进状态。"
    return "常规：当前能够证明事项在推进，但成果表达与闭环证据仍有继续加强空间。"


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


def build_notes(bucket: ThemeBucket, dayflow_available: bool) -> str:
    if dayflow_available:
        notes = ["工时和人天仅根据 Dayflow 数据折算；其余内容基于 Dayflow 与 GitLab 轨迹综合归纳。"]
    else:
        notes = ["当前设备未检测到 Dayflow，本行主要依据 GitLab 轨迹归纳；工时 / D 暂无法折算。"]
    if dayflow_available and not bucket.cards:
        notes.append("本行主要依据 GitLab 轨迹归纳，缺少对应的 Dayflow 工时佐证。")
    if not dayflow_available and not bucket.events:
        notes.append("当前既未读取到 Dayflow，也未匹配到 GitLab 记录，建议补充其他系统证据。")
    if not bucket.events:
        notes.append("本行未匹配到 GitLab 记录，可能属于非 GitLab 交付或线下推进事项。")
    notes.append("目标、质量和难易度含保守推断成分，建议结合评审材料进一步确认。")
    return format_bullets(notes)


def build_monthly_reflection(
    buckets: list[ThemeBucket],
    dayflow_payload: dict[str, Any],
    gitlab_payload: dict[str, Any],
) -> dict[str, str]:
    dayflow_available = dayflow_is_available(dayflow_payload)
    top_buckets = []
    for bucket in buckets:
        hours, person_days = hours_and_days(bucket.cards)
        top_buckets.append((bucket.label, hours, person_days, len(bucket.events)))
    if dayflow_available:
        top_buckets = sorted(top_buckets, key=lambda item: (-item[1], -item[3], item[0]))
        top_theme_text = "、".join(label for label, hours, _person_days, _event_count in top_buckets[:3] if hours > 0)
    else:
        top_buckets = sorted(top_buckets, key=lambda item: (-item[3], -item[1], item[0]))
        top_theme_text = "、".join(label for label, _hours, _person_days, event_count in top_buckets[:3] if event_count > 0)
    if not top_theme_text:
        top_theme_text = "本月主题分布较分散，暂未形成明显的单一高投入主题。"

    gitlab_aggregates = gitlab_payload.get("aggregates", {})
    accepted = gitlab_aggregates.get("by_action", {}).get("accepted", 0)
    journal_count = len(dayflow_payload.get("journal_entries", []))

    if dayflow_available:
        growth_lines = [
            f"从本月整体推进情况看，主要投入集中在 {top_theme_text} 等方向，说明本月的工作重心比较清晰，也形成了相对稳定的推进主线。",
            "结合 Dayflow 与 GitLab 轨迹，可以看到多项事项已经从执行推进逐步走向提交评审与阶段性闭环，说明结果导向和交付意识在持续增强。",
        ]
    else:
        growth_lines = [
            f"从本月 GitLab 活动轨迹看，主要推进集中在 {top_theme_text} 等方向，能够看出本月的交付重心与协作主线。",
            "当前设备未检测到 Dayflow，因此本次总结主要依据 GitLab 提交、MR 与项目活动归纳，适合用于交付评审回顾，但不包含完整工时视角。",
        ]
    if top_buckets and top_buckets[0][0] == "AI Agent与自动化工作流":
        growth_lines.append("在 AI Agent、skill 和自动化工作流方面的投入较深，说明本月在“把经验沉淀成工具能力”和“把想法转成可复用资产”上有比较明显的成长。")
    elif top_buckets and top_buckets[0][0] == "工程方法论与质量体系沉淀":
        growth_lines.append("工程质量与方法沉淀类事项占比较高，说明本月不只是完成交付，也在持续积累可复用的方法、规范与工程实践。")
    else:
        growth_lines.append("从高频主题看，本月已经不只是零散处理任务，而是在几个重点方向上形成了持续推进和阶段性沉淀。")

    delay_signals = sum(count_delay_signals(bucket.cards, bucket.events) for bucket in buckets)
    reflection_lines = []
    if dayflow_available and len([bucket for bucket in buckets if hours_and_days(bucket.cards)[0] > 0]) >= 4:
        reflection_lines.append("本月任务线相对偏多，阶段内存在比较明显的上下文切换；后续需要更主动地压缩并行主题，提升精力聚焦度。")
    if delay_signals > 0:
        reflection_lines.append("从过程轨迹里仍能看到少量阻塞或待跟进信号，说明风险识别与推进节奏管理还可以再前置一些。")
    if gitlab_aggregates.get("by_action", {}).get("opened", 0) > accepted:
        reflection_lines.append("部分事项仍停留在推进或评审阶段，后续需要更关注收尾动作，以及对阶段结果的文档化和闭环沉淀。")
    if not dayflow_available:
        reflection_lines.append("当前设备未检测到 Dayflow，导致本月工时、非 GitLab 工作块与 journal 目标缺失；如需完整月报，建议补充 Dayflow 数据或手工校正。")
    elif journal_count == 0:
        reflection_lines.append("本月较少留下明确的目标与反思记录，导致部分总结只能依赖活动轨迹反推；下月建议加强主动记录和阶段复盘。")
    if not reflection_lines:
        reflection_lines.append("本月整体推进较稳，但仍建议每周固定补一次目标、风险与复盘记录，避免月底回顾过度依赖轨迹推断。")

    return {
        "收获/启发/成长": format_bullets(growth_lines),
        "反思/自我批评": format_bullets(reflection_lines),
    }


def table_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def build_rows(
    buckets: list[ThemeBucket],
    journal_entries: list[dict[str, Any]],
    dayflow_available: bool,
) -> list[dict[str, str]]:
    journal_hints = collect_journal_hints(journal_entries)
    rows: list[dict[str, str]] = []
    for bucket in buckets:
        row = {
            HEADERS[0]: build_goal(bucket, journal_hints),
            HEADERS[1]: build_key_results(bucket),
            HEADERS[2]: build_key_actions(bucket),
            HEADERS[3]: build_completion(bucket),
            HEADERS[4]: build_work_quality(bucket),
            HEADERS[5]: format_effort_value(bucket.cards, dayflow_available),
            HEADERS[6]: build_difficulty(bucket),
            HEADERS[7]: build_notes(bucket, dayflow_available),
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
    dayflow_source = dayflow_payload.get("source", {})
    dayflow_available = dayflow_is_available(dayflow_payload)
    if dayflow_available:
        source_lines = [
            f"数据来源：Dayflow `{dayflow_source['db_path']}` + GitLab `{gitlab_payload['source']['hostname']}`",
            "说明：主表中每一行代表一个任务拆分；工时仅根据 Dayflow 折算，D 按 8 小时/天计算。",
        ]
    else:
        source_lines = [
            f"数据来源：GitLab `{gitlab_payload['source']['hostname']}`",
            f"Dayflow：{dayflow_source.get('reason_text', '当前不可用')}（App：`{dayflow_source.get('app_path', DEFAULT_DAYFLOW_APP_PATH)}`，DB：`{dayflow_source.get('db_path', DEFAULT_DAYFLOW_DB_PATH)}`）",
            "说明：主表中每一行代表一个任务拆分；当前设备未检测到可用 Dayflow，因此本次不含工时 / D 折算。",
        ]
    lines = [
        f"# 月度工作总结（{window.label}）",
        "",
        *source_lines,
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

    dayflow_available = dayflow_is_available(dayflow_payload)
    cards = [card for card in dayflow_payload.get("cards", []) if is_work_card(card, args.include_all_cards)]
    events = gitlab_payload.get("events", [])
    buckets = build_theme_buckets(cards, events)
    rows = build_rows(buckets, dayflow_payload.get("journal_entries", []), dayflow_available)
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
