#!/usr/bin/env python3
"""使用 glab 读取指定时间范围内的 GitLab 活动事件。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 glab 读取 GitLab 活动事件，并导出标准化 JSON。")
    parser.add_argument("--month", help="整月，格式 YYYY-MM。")
    parser.add_argument("--from", dest="from_date", help="起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--to", dest="to_date", help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--hostname", required=True, help="GitLab 主机，例如 gitlab.gz.cvte.cn。")
    parser.add_argument("--glab-bin", default="glab", help="glab 可执行文件路径。默认：glab")
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


def run_json_command(command: list[str]) -> Any:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise SystemExit(f"命令执行失败：{' '.join(command)}\n{message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"无法解析 JSON 输出：{' '.join(command)}") from exc


def fetch_user(glab_bin: str, hostname: str) -> dict[str, Any]:
    return run_json_command([glab_bin, "api", "user", "--hostname", hostname, "--output", "json"])


def fetch_events(glab_bin: str, hostname: str, window: DateWindow) -> list[dict[str, Any]]:
    before = (window.end + timedelta(days=1)).isoformat()
    command = [
        glab_bin,
        "api",
        "events",
        "--hostname",
        hostname,
        "-X",
        "GET",
        "--paginate",
        "--output",
        "ndjson",
        "-f",
        f"after={window.start.isoformat()}",
        "-f",
        f"before={before}",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise SystemExit(f"命令执行失败：{' '.join(command)}\n{message}")

    events: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit("无法解析 GitLab 事件 NDJSON 输出。") from exc
    return events


def fetch_project_name(glab_bin: str, hostname: str, project_id: int) -> str:
    command = [glab_bin, "api", f"projects/{project_id}", "--hostname", hostname, "-X", "GET", "--output", "json"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return f"项目#{project_id}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"项目#{project_id}"
    return payload.get("path_with_namespace") or payload.get("name") or f"项目#{project_id}"


def resolve_project_names(glab_bin: str, hostname: str, events: list[dict[str, Any]]) -> dict[int, str]:
    project_names: dict[int, str] = {}
    unknown_ids: set[int] = set()
    for event in events:
        project_id = event.get("project_id")
        if project_id is None:
            continue
        if event.get("target_type") == "Project" and event.get("target_title"):
            project_names[project_id] = event["target_title"]
        elif project_id not in project_names:
            unknown_ids.add(project_id)

    for project_id in sorted(unknown_ids):
        project_names[project_id] = fetch_project_name(glab_bin, hostname, project_id)
    return project_names


def aggregate_events(events: list[dict[str, Any]], project_names: dict[int, str]) -> dict[str, Any]:
    by_action: Counter[str] = Counter()
    by_day: Counter[str] = Counter()
    by_project: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "actions": Counter()})
    representative_titles: Counter[str] = Counter()

    for event in events:
        action_name = event.get("action_name") or "unknown"
        by_action[action_name] += 1

        created_at = event.get("created_at") or ""
        if len(created_at) >= 10:
            by_day[created_at[:10]] += 1

        project_id = event.get("project_id")
        project_name = project_names.get(project_id)
        if not project_name:
            if event.get("target_type") == "Project" and event.get("target_title"):
                project_name = event["target_title"]
            elif project_id is not None:
                project_name = f"项目#{project_id}"
            else:
                project_name = "未知项目"

        by_project[project_name]["count"] += 1
        by_project[project_name]["actions"][action_name] += 1

        push_data = event.get("push_data") or {}
        title = push_data.get("commit_title") or event.get("target_title")
        if title:
            representative_titles[title] += 1

    projects_payload = []
    for project_name, values in sorted(by_project.items(), key=lambda item: (-item[1]["count"], item[0])):
        actions = {
            key: count
            for key, count in sorted(values["actions"].items(), key=lambda item: (-item[1], item[0]))
        }
        projects_payload.append({"project": project_name, "count": values["count"], "actions": actions})

    titles_payload = [
        {"title": title, "count": count}
        for title, count in representative_titles.most_common(10)
    ]

    return {
        "event_count": len(events),
        "by_action": dict(sorted(by_action.items(), key=lambda item: (-item[1], item[0]))),
        "by_day": dict(sorted(by_day.items())),
        "by_project": projects_payload,
        "representative_titles": titles_payload,
    }


def main() -> int:
    args = parse_args()
    window = resolve_window(args)
    user = fetch_user(args.glab_bin, args.hostname)
    events = fetch_events(args.glab_bin, args.hostname, window)
    project_names = resolve_project_names(args.glab_bin, args.hostname, events)

    payload = {
        "source": {
            "hostname": args.hostname,
            "glab_bin": args.glab_bin,
            "reader": "scripts/fetch_gitlab_events.py",
        },
        "range": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "name": user.get("name"),
            "web_url": user.get("web_url"),
        },
        "project_names_by_id": {str(key): value for key, value in sorted(project_names.items())},
        "aggregates": aggregate_events(events, project_names),
        "events": events,
    }

    indent = None if args.indent <= 0 else args.indent
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
