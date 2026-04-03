#!/usr/bin/env python3
"""汇总 Dayflow 与 GitLab 活动，生成中文月度工作总结表格。"""

from __future__ import annotations

import argparse
import json
import os
import re
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
MATCH_SPLIT_RE = re.compile(r"[\s,，；;、/\\|\n\r\t\-\+\(\)\[\]（）【】:：]+")
ASCII_WORD_RE = re.compile(r"^[a-z0-9.]+$")

FALLBACK_THEME = "其他业务推进事项"
DELAY_KEYWORDS = ["延期", "delay", "blocked", "阻塞", "卡住", "返工", "待跟进", "reopen", "reopened"]
LARK_GROWTH_HINTS = {
    "产品": "需求抽象、方案推进与结果闭环能力。",
    "项目": "项目拆解、节奏管理与跨团队协同能力。",
    "效果": "效果评估、方案打磨与体验判断能力。",
    "质量": "质量治理、风险识别与稳定性建设能力。",
}
DONE_STATUS_KEYWORDS = ["完成", "定版", "送测", "accepted", "merged", "closed", "done"]

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
    source: str = "theme"
    goal_context: dict[str, Any] | None = None
    related_tasks: list[dict[str, Any]] | None = None


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
    parser.add_argument("--lark-bin", default="lark-cli", help="lark-cli 可执行文件路径。默认：lark-cli")
    parser.add_argument("--python-bin", default=sys.executable, help="Python 可执行文件路径。默认使用当前解释器。")
    parser.add_argument(
        "--lark-url",
        dest="lark_urls",
        action="append",
        help="可重复传入飞书项目管理 URL；支持 /wiki/、/docx/、/doc/、/base/。",
    )
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


def resolve_lark_reader() -> Path:
    script_path = Path(__file__).resolve().with_name("read_lark_project_context.py")
    if not script_path.exists():
        raise SystemExit(f"未找到飞书读取脚本：{script_path}")
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


def collect_lark_context(args: argparse.Namespace) -> dict[str, Any]:
    if not args.lark_urls:
        return {"current_user": {}, "sources": [], "tasks": [], "goals": [], "warnings": []}

    reader = resolve_lark_reader()
    command = [
        args.python_bin,
        str(reader),
        "--lark-bin",
        args.lark_bin,
        "--indent",
        "0",
    ]
    for url in args.lark_urls:
        command.extend(["--url", url])
    return run_json_command(command)


def parse_optional_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
        event.get("_project_name"),
        event.get("target_title"),
        event.get("target_type"),
        push_data.get("commit_title"),
        push_data.get("ref"),
    ]
    return normalize_text(" ".join(str(field) for field in fields if field))


def bucket_sort_key(bucket: ThemeBucket) -> tuple[float, int, int, str]:
    seconds = sum(max(0, card.get("duration_seconds") or 0) for card in bucket.cards)
    return (-(seconds / 3600.0), -len(bucket.events), -(len(bucket.related_tasks or [])), bucket.label)


def split_match_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in MATCH_SPLIT_RE.split(value):
            normalized = normalize_text(part)
            if len(normalized) < 2:
                continue
            if ASCII_WORD_RE.match(normalized) and len(normalized) < 3:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
        normalized_value = normalize_text(value)
        if len(normalized_value) >= 3 and normalized_value not in seen:
            seen.add(normalized_value)
            terms.append(normalized_value)
    return terms


def task_is_closed(task: dict[str, Any]) -> bool:
    status = normalize_text(task.get("status"))
    return any(keyword in status for keyword in DONE_STATUS_KEYWORDS)


def group_window_relevant(group: dict[str, Any], window: DateWindow) -> bool:
    goal = group.get("goal") or {}
    tasks = group.get("tasks") or []

    dates: list[date] = []
    for value in (
        goal.get("start_date"),
        goal.get("due_date"),
        goal.get("planned_completion"),
        goal.get("actual_completion"),
    ):
        parsed = parse_optional_iso_date(value)
        if parsed:
            dates.append(parsed)

    for task in tasks:
        for value in (task.get("start_date"), task.get("due_date")):
            parsed = parse_optional_iso_date(value)
            if parsed:
                dates.append(parsed)

    if any(window.start <= item <= window.end for item in dates):
        return True

    goal_due = parse_optional_iso_date(goal.get("due_date"))
    progress = goal.get("progress")
    if goal_due and goal_due <= window.end and progress is not None and progress < 0.999:
        return True

    return False


def group_keywords(group: dict[str, Any]) -> list[str]:
    goal = group.get("goal") or {}
    tasks = group.get("tasks") or []
    values: list[str] = []
    for value in (goal.get("title"), goal.get("summary"), goal.get("type")):
        if value:
            values.append(str(value))
    for task in tasks:
        for value in (
            task.get("title"),
            task.get("status"),
            task.get("priority"),
            " ".join(str(item) for item in (task.get("mapped_goal") or [])),
            " ".join(str(item) for item in (task.get("mapped_key_results") or [])),
            " ".join(str(item) for item in (task.get("mapped_actions") or [])),
            " ".join(str(item) for item in (task.get("mapped_progress") or [])),
        ):
            if value:
                values.append(str(value))
    return split_match_terms(values)


def score_group_match(text: str, keywords: list[str]) -> int:
    score = 0
    for keyword in keywords:
        if keyword and keyword in text:
            score += max(2, min(len(keyword), 8))
    return score


def build_lark_buckets(
    cards: list[dict[str, Any]],
    events: list[dict[str, Any]],
    lark_payload: dict[str, Any],
    window: DateWindow,
) -> tuple[list[ThemeBucket], list[dict[str, Any]], list[dict[str, Any]]]:
    goals = lark_payload.get("goals", [])
    tasks = lark_payload.get("tasks", [])
    if not goals and not tasks:
        return [], cards, events

    groups_by_goal_id: dict[str, dict[str, Any]] = {}
    for goal in goals:
        groups_by_goal_id[str(goal.get("goal_id"))] = {"goal": goal, "tasks": []}

    standalone_groups: list[dict[str, Any]] = []
    for task in tasks:
        if not task.get("involves_current_user"):
            continue
        attached = False
        for goal_id in task.get("goal_ids", []):
            if goal_id in groups_by_goal_id:
                groups_by_goal_id[goal_id]["tasks"].append(task)
                attached = True
                break
        if not attached:
            standalone_groups.append({"goal": None, "tasks": [task]})

    groups: list[dict[str, Any]] = []
    for group in groups_by_goal_id.values():
        goal = group.get("goal") or {}
        resource_type = str(goal.get("resource_type") or "")
        if group["tasks"] or (resource_type in {"doc", "docx"} and not goal.get("linked_task_ids")):
            groups.append(group)
    groups.extend(standalone_groups)

    if not groups:
        return [], cards, events

    prepared_groups = [
        {
            "goal": group.get("goal"),
            "tasks": group.get("tasks", []),
            "keywords": group_keywords(group),
            "cards": [],
            "events": [],
        }
        for group in groups
    ]

    remaining_cards: list[dict[str, Any]] = []
    for card in cards:
        text = card_text(card)
        best_score = 0
        best_group: dict[str, Any] | None = None
        for group in prepared_groups:
            score = score_group_match(text, group["keywords"])
            if score > best_score:
                best_score = score
                best_group = group
        if best_group and best_score >= 5:
            best_group["cards"].append(card)
        else:
            remaining_cards.append(card)

    remaining_events: list[dict[str, Any]] = []
    for event in events:
        text = event_text(event)
        best_score = 0
        best_group = None
        for group in prepared_groups:
            score = score_group_match(text, group["keywords"])
            if score > best_score:
                best_score = score
                best_group = group
        if best_group and best_score >= 5:
            best_group["events"].append(event)
        else:
            remaining_events.append(event)

    buckets: list[ThemeBucket] = []
    for group in prepared_groups:
        has_evidence = bool(group["cards"] or group["events"])
        if not has_evidence and not group_window_relevant(group, window):
            continue
        goal = group.get("goal") or {}
        related_tasks = group.get("tasks", [])
        if goal.get("title"):
            label = str(goal["title"]).strip()
        elif related_tasks:
            label = str(related_tasks[0].get("title") or "未命名事项").strip()
        else:
            label = FALLBACK_THEME
        buckets.append(
            ThemeBucket(
                label=label,
                cards=group["cards"],
                events=group["events"],
                source="lark",
                goal_context=goal or None,
                related_tasks=related_tasks,
            )
        )

    ordered = sorted(buckets, key=bucket_sort_key)
    return ordered, remaining_cards, remaining_events


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

    ordered = sorted(buckets.values(), key=bucket_sort_key)
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


def dedupe_texts(values: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def task_mapped_lines(task: dict[str, Any], field_name: str) -> list[str]:
    value = task.get(field_name)
    if value is None:
        return []
    if isinstance(value, list):
        return dedupe_texts([str(item).strip() for item in value if str(item).strip()])
    text = str(value).strip()
    return [text] if text else []


def bucket_mapped_lines(bucket: ThemeBucket, field_name: str) -> list[str]:
    lines: list[str] = []
    for task in bucket.related_tasks or []:
        lines.extend(task_mapped_lines(task, field_name))
    return dedupe_texts(lines)


def bucket_has_source_mapping(bucket: ThemeBucket) -> bool:
    return any(
        bucket_mapped_lines(bucket, field_name)
        for field_name in ("mapped_goal", "mapped_key_results", "mapped_actions", "mapped_progress")
    )


def bucket_evidence_dates(bucket: ThemeBucket) -> list[date]:
    dates: list[date] = []
    for card in bucket.cards:
        parsed = parse_optional_iso_date(str(card.get("day") or ""))
        if parsed:
            dates.append(parsed)
    for event in bucket.events:
        created_at = str(event.get("created_at") or "")
        if len(created_at) >= 10:
            parsed = parse_optional_iso_date(created_at[:10])
            if parsed:
                dates.append(parsed)
    return sorted(dates)


def bucket_time_window_text(bucket: ThemeBucket) -> str:
    dates = bucket_evidence_dates(bucket)
    if not dates:
        return ""
    if dates[0] == dates[-1]:
        return f"实际时间窗口：{dates[0].isoformat()}"
    return f"实际时间窗口：{dates[0].isoformat()} 至 {dates[-1].isoformat()}"


def simplify_progress_item(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^\[\s*[xX]\s*\]\s*", "", text)
    text = re.sub(r"^\[\s*\]\s*", "", text)
    return text.strip()


def classify_progress_items(lines: list[str]) -> dict[str, list[str]]:
    groups = {"done": [], "pending": [], "risk": [], "adjustment": [], "conclusion": []}
    for line in lines:
        text = line.strip()
        lower = text.lower()
        if not text:
            continue
        if text.startswith("风险："):
            groups["risk"].append(text)
            continue
        if text.startswith("调整："):
            groups["adjustment"].append(text)
            continue
        if text.startswith("结论："):
            groups["conclusion"].append(text)
            continue
        if "[x]" in lower or text.startswith("完成") or text.startswith("已完成"):
            groups["done"].append(text)
            continue
        if "[ ]" in text or text.startswith("待办：") or "待办" in text or "未完成" in text:
            groups["pending"].append(text)
            continue
        groups["pending"].append(text)
    return groups


def display_titles(bucket: ThemeBucket, limit: int = 4) -> list[str]:
    if bucket.related_tasks:
        task_titles = dedupe_texts([str(task.get("title") or "").strip() for task in bucket.related_tasks], limit=limit)
        if task_titles:
            return task_titles[:limit]
    titles = top_card_titles(bucket.cards, limit=limit)
    extra_titles = [title for title in top_event_titles(bucket.events, limit=limit) if title not in titles]
    return (titles + extra_titles)[:limit]


def project_goal_text(bucket: ThemeBucket) -> str:
    goal = bucket.goal_context or {}
    title = str(goal.get("title") or bucket.label).strip()
    summary = str(goal.get("summary") or "").strip()
    if summary and summary != title:
        return f"围绕 {title} 推进 {summary} 对应的阶段性交付。"
    return f"围绕 {title} 推进项目管理表中定义的阶段目标落地。"


def project_growth_text(bucket: ThemeBucket) -> str:
    goal = bucket.goal_context or {}
    goal_type = str(goal.get("type") or "").strip()
    growth_hint = LARK_GROWTH_HINTS.get(goal_type, "需求抽象、方案推进与结果闭环能力。")
    titles = display_titles(bucket, limit=2)
    if titles:
        return f"在 {'、'.join(titles)} 等事项中，进一步沉淀 {growth_hint}"
    return f"结合该阶段目标的持续推进，进一步沉淀 {growth_hint}"


def project_delay_text(bucket: ThemeBucket, window: DateWindow) -> str:
    goal = bucket.goal_context or {}
    tasks = bucket.related_tasks or []

    planned_completion = parse_optional_iso_date(goal.get("planned_completion"))
    actual_completion = parse_optional_iso_date(goal.get("actual_completion"))
    due_date = parse_optional_iso_date(goal.get("due_date"))
    progress = goal.get("progress")
    variance_days = goal.get("variance_days")

    if planned_completion and actual_completion and actual_completion > planned_completion:
        return (
            f"延期情况：项目管理表显示实际完成晚于计划完成（计划 {planned_completion.isoformat()}，"
            f"实际 {actual_completion.isoformat()}）。"
        )

    overdue_tasks = [
        task
        for task in tasks
        if parse_optional_iso_date(task.get("due_date"))
        and parse_optional_iso_date(task.get("due_date")) <= window.end
        and not task_is_closed(task)
    ]
    if overdue_tasks:
        names = "、".join(str(task.get("title") or "").strip() for task in overdue_tasks[:2])
        return f"延期情况：{names} 等任务到期后仍需继续推进，存在阶段性顺延风险。"

    if due_date and due_date <= window.end and progress is not None and progress < 0.999:
        return f"延期情况：截至 {window.end.isoformat()}，项目管理表显示目标仍未完全闭环，存在顺延信号。"

    if variance_days is not None and variance_days < 0:
        return "延期情况：项目管理表存在负向计划偏差信号，建议评审时复核延期原因与收尾节奏。"

    if due_date:
        return f"延期情况：当前未看到明确延期证据，阶段目标计划交付时间为 {due_date.isoformat()}。"

    return "延期情况：未从项目管理表、Dayflow 与 GitLab 数据中看到明确延期证据。"


def project_stage_text(bucket: ThemeBucket) -> str:
    goal = bucket.goal_context or {}
    progress = goal.get("progress")
    counts = action_counts(bucket.events)
    accepted = counts.get("accepted", 0)
    pushed = counts.get("pushed to", 0) + counts.get("pushed new", 0)

    if accepted > 0:
        return "阶段结果：相关事项已形成提交、评审或合入闭环，阶段交付较明确。"
    if progress is not None and progress >= 0.8:
        return "阶段结果：项目管理表显示推进已接近收尾，阶段成果较为清晰。"
    if pushed > 0 or bucket.cards:
        return "阶段结果：相关事项保持持续推进，已经形成明确的过程输出。"
    return "阶段结果：当前以项目管理拆解推进为主，仍建议结合验收或评审结论补充闭环证明。"


def organization_goal_text(bucket: ThemeBucket) -> str:
    if bucket.goal_context:
        return project_goal_text(bucket)
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
    mapped_goals = bucket_mapped_lines(bucket, "mapped_goal")
    if mapped_goals:
        primary_goal = "；".join(mapped_goals[:2])
        growth_goal = (
            project_growth_text(bucket)
            if bucket.goal_context
            else PERSONAL_GROWTH_HINTS.get(bucket.label, PERSONAL_GROWTH_HINTS[FALLBACK_THEME])
        )
        return format_bullets([primary_goal, growth_goal])
    return format_bullets(
        [
            organization_goal_text(bucket),
            project_growth_text(bucket)
            if bucket.goal_context
            else PERSONAL_GROWTH_HINTS.get(bucket.label, PERSONAL_GROWTH_HINTS[FALLBACK_THEME]),
        ]
    )


def build_key_results(bucket: ThemeBucket) -> str:
    mapped_key_results = bucket_mapped_lines(bucket, "mapped_key_results")
    if mapped_key_results:
        return format_bullets(mapped_key_results)

    if bucket.goal_context:
        goal = bucket.goal_context
        lines: list[str] = []
        if goal.get("summary"):
            lines.append(f"对齐目标：{goal['title']}（{goal['summary']}）")
        else:
            lines.append(f"对齐目标：{goal.get('title') or bucket.label}")
        task_titles = display_titles(bucket, limit=4)
        if task_titles:
            lines.append(f"任务交付：{'；'.join(task_titles)}")
        lines.append(project_stage_text(bucket))
        return format_bullets(lines)

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


def build_key_actions(bucket: ThemeBucket, window: DateWindow) -> str:
    mapped_actions = bucket_mapped_lines(bucket, "mapped_actions")
    if mapped_actions:
        lines = list(mapped_actions[:6])
        if bucket.goal_context:
            lines.append(project_delay_text(bucket, window))
        elif count_delay_signals(bucket.cards, bucket.events) > 0:
            lines.append("延期情况：存在少量潜在阻塞或待跟进信号，建议评审时继续复核闭环状态。")
        else:
            lines.append("延期情况：未从 Dayflow / GitLab 数据中看到明确延期证据。")
        return format_bullets(lines)

    if bucket.goal_context:
        goal_title = str(bucket.goal_context.get("title") or bucket.label).strip()
        titles = display_titles(bucket, limit=4)
        lines: list[str] = []
        if titles:
            lines.append(f"关键动作：围绕 {'、'.join(titles)} 等任务分项持续推进")
        lines.append(f"对齐组织目标：以 {goal_title} 为主线拆解并推进阶段交付")
        lines.append(project_delay_text(bucket, window))
        return format_bullets(lines)

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


def build_completion(bucket: ThemeBucket, window: DateWindow) -> str:
    mapped_progress = bucket_mapped_lines(bucket, "mapped_progress")
    if mapped_progress:
        classified = classify_progress_items(mapped_progress)
        time_window = bucket_time_window_text(bucket)

        success_parts: list[str] = []
        if classified["done"]:
            success_parts.append("；".join(simplify_progress_item(item) for item in classified["done"][:2]))
        elif classified["conclusion"]:
            success_parts.append("；".join(simplify_progress_item(item) for item in classified["conclusion"][:1]))
        else:
            success_parts.append("；".join(simplify_progress_item(item) for item in mapped_progress[:1]))
        if time_window:
            success_parts.append(time_window)
        success = "成效：" + "；".join(part for part in success_parts if part)

        problem_source = classified["adjustment"] or classified["pending"]
        if problem_source:
            problem = "问题：" + simplify_progress_item(problem_source[0])
        else:
            problem = "问题：当前来源文档未显式标出阻塞项，但仍建议结合评审和验收记录复核闭环情况。"

        if classified["risk"]:
            risk = "风险：" + simplify_progress_item(classified["risk"][0])
        elif classified["pending"]:
            risk = "风险：仍有待办项尚未完全收尾，若后续验证和回归不集中，可能影响阶段闭环。"
        else:
            risk = "风险：当前未看到明确延期信号，但最终交付结果仍建议结合评审、发布或验收记录确认。"

        measure_source = classified["pending"] or classified["adjustment"]
        if measure_source:
            measures = "措施：" + "；".join(simplify_progress_item(item) for item in measure_source[:2])
        else:
            measures = "措施：继续补齐评审、验收和复盘信息，增强阶段结果表达与闭环证明。"
        return format_bullets([success, problem, risk, measures])

    if bucket.goal_context:
        titles = display_titles(bucket, limit=2)
        risk_notes = dedupe_texts(
            [str(task.get("risk_note") or "").strip() for task in (bucket.related_tasks or []) if task.get("risk_note")],
            limit=2,
        )

        if bucket.events or bucket.cards:
            success = "成效：结合项目管理拆解与实际活动轨迹，相关事项已经形成持续推进和阶段性输出。"
        else:
            success = "成效：项目管理表已完成任务拆解，本阶段围绕既定目标保持了持续推进。"

        if risk_notes:
            problem = f"问题：{risk_notes[0]}"
        elif bucket.related_tasks and len(bucket.related_tasks) >= 4:
            problem = "问题：任务拆分较多，阶段内存在一定并行推进和上下文切换。"
        else:
            problem = "问题：部分结果仍需补充验收、评审或发布侧的闭环证明，便于团队统一理解价值。"

        delay_text = project_delay_text(bucket, window)
        if "顺延" in delay_text or "延期" in delay_text:
            risk = "风险：部分任务存在顺延或计划偏差信号，若验证与收尾不够集中，可能影响阶段闭环质量。"
        elif risk_notes:
            risk = "风险：项目管理表已标出阶段性风险点，若跟进不及时，可能影响交付节奏或结果表达。"
        else:
            risk = "风险：当前未见强烈延期信号，但最终业务效果仍建议结合验收或评审记录确认。"

        if titles:
            measures = f"措施：围绕 {'、'.join(titles)} 继续聚焦收尾、验证与复盘沉淀，补齐结果证明。"
        else:
            measures = "措施：继续补齐验收、评审与复盘信息，增强阶段结果的表达与闭环证明。"
        return format_bullets([success, problem, risk, measures])

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
    if bucket.goal_context:
        progress = bucket.goal_context.get("progress")
        if progress is not None and progress >= 0.8:
            return "稳定：项目管理表显示推进已接近收尾，目标拆解清晰，阶段性结果表达较完整。"
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
    if bucket.related_tasks and len(bucket.related_tasks) >= 4:
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
    notes: list[str] = []
    uses_project_context = bool(bucket.goal_context or bucket.source == "lark")
    if bucket_has_source_mapping(bucket):
        notes.append("目标、关键交付、每周行动和完成情况优先按项目目标来源字段映射，再结合 Dayflow / GitLab 证据校准。")
    if bucket.goal_context:
        source_title = str(bucket.goal_context.get("source_title") or "飞书项目管理页").strip()
        notes.append(f"目标已按《{source_title}》中的项目管理目标对齐。")
    if dayflow_available:
        if uses_project_context:
            notes.append("工时和人天仅根据 Dayflow 数据折算；其余内容基于 Dayflow、GitLab 与飞书项目管理数据综合归纳。")
        else:
            notes.append("工时和人天仅根据 Dayflow 数据折算；其余内容基于 Dayflow 与 GitLab 轨迹综合归纳。")
    else:
        if uses_project_context:
            notes.append("当前设备未检测到 Dayflow，本行主要依据 GitLab 与飞书项目管理数据归纳；工时 / D 暂无法折算。")
        else:
            notes.append("当前设备未检测到 Dayflow，本行主要依据 GitLab 轨迹归纳；工时 / D 暂无法折算。")
    if uses_project_context and not bucket.cards and not bucket.events:
        notes.append("本行主要依据飞书项目管理表归纳，缺少明显的 Dayflow / GitLab 轨迹佐证。")
    elif dayflow_available and not bucket.cards:
        notes.append("本行主要依据 GitLab 轨迹归纳，缺少对应的 Dayflow 工时佐证。")
    if not dayflow_available and not bucket.events:
        notes.append("当前既未读取到 Dayflow，也未匹配到明显的 GitLab 记录，建议补充其他系统证据。")
    if not bucket.events and not (uses_project_context and not bucket.cards and not bucket.events):
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
    window: DateWindow,
) -> list[dict[str, str]]:
    journal_hints = collect_journal_hints(journal_entries)
    rows: list[dict[str, str]] = []
    for bucket in buckets:
        row = {
            HEADERS[0]: build_goal(bucket, journal_hints),
            HEADERS[1]: build_key_results(bucket),
            HEADERS[2]: build_key_actions(bucket, window),
            HEADERS[3]: build_completion(bucket, window),
            HEADERS[4]: build_work_quality(bucket),
            HEADERS[5]: format_effort_value(bucket.cards, dayflow_available),
            HEADERS[6]: build_difficulty(bucket),
            HEADERS[7]: build_notes(bucket, dayflow_available),
        }
        rows.append(row)
    return rows


def build_report_buckets(
    cards: list[dict[str, Any]],
    events: list[dict[str, Any]],
    lark_payload: dict[str, Any],
    window: DateWindow,
) -> list[ThemeBucket]:
    lark_buckets, remaining_cards, remaining_events = build_lark_buckets(cards, events, lark_payload, window)
    fallback_buckets = build_theme_buckets(remaining_cards, remaining_events)
    combined = lark_buckets + fallback_buckets
    if not combined:
        return build_theme_buckets(cards, events)
    return sorted(combined, key=bucket_sort_key)


def render_markdown(
    window: DateWindow,
    rows: list[dict[str, str]],
    reflection: dict[str, str],
    dayflow_payload: dict[str, Any],
    gitlab_payload: dict[str, Any],
    lark_payload: dict[str, Any],
) -> str:
    dayflow_source = dayflow_payload.get("source", {})
    dayflow_available = dayflow_is_available(dayflow_payload)
    lark_sources = lark_payload.get("sources", [])
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
    if lark_sources:
        labels = dedupe_texts(
            [
                str(source.get("title") or source.get("table_name") or source.get("url") or "").strip()
                for source in lark_sources
            ],
            limit=3,
        )
        source_lines.append(
            f"飞书项目管理：{'、'.join(labels) if labels else f'{len(lark_sources)} 个指定 URL'}"
        )
        source_lines.append("说明：若提供飞书项目管理 URL，目标列优先按项目管理文档中的目标对齐。")
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
    lark_payload = collect_lark_context(args)

    dayflow_available = dayflow_is_available(dayflow_payload)
    cards = [card for card in dayflow_payload.get("cards", []) if is_work_card(card, args.include_all_cards)]
    events = gitlab_payload.get("events", [])
    buckets = build_report_buckets(cards, events, lark_payload, window)
    rows = build_rows(buckets, dayflow_payload.get("journal_entries", []), dayflow_available, window)
    reflection = build_monthly_reflection(buckets, dayflow_payload, gitlab_payload)

    if args.format == "markdown":
        sys.stdout.write(render_markdown(window, rows, reflection, dayflow_payload, gitlab_payload, lark_payload))
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
            "lark": lark_payload.get("sources", []),
        },
        "project_context": {
            "current_user": lark_payload.get("current_user", {}),
            "warnings": lark_payload.get("warnings", []),
            "goal_count": len(lark_payload.get("goals", [])),
            "task_count": len(lark_payload.get("tasks", [])),
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
