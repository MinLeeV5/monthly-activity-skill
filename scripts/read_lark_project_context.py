#!/usr/bin/env python3
"""读取飞书项目管理 URL，并输出标准化的目标/任务上下文。"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

TASK_TABLE_HINTS = ("任务", "需求", "工作项", "排期")
GOAL_TABLE_HINTS = ("目标", "里程碑", "goal")
TASK_TITLE_HINTS = ("文本", "标题", "任务", "事项", "需求", "名称", "主题")
GOAL_TITLE_HINTS = ("里程碑", "目标", "项目", "标题", "名称", "版本")
GOAL_DETAIL_HINTS = ("内容", "说明", "描述", "范围", "备注")
OWNER_FIELD_HINTS = ("负责人", "主导人", "前端研发", "后端研发", "owner")
DOC_GOAL_HEADING_HINTS = ("目标", "里程碑", "交付", "范围", "计划", "方向")
TASK_GOAL_FIELD_HINTS = ("团队目标", "周目标", "目标")
TASK_KEY_RESULT_FIELD_HINTS = ("关键交付", "关键成果", "交付")
TASK_ACTION_FIELD_HINTS = ("每周行动", "关键行动", "关键举措", "目标分解", "关键里程碑计划", "行动")
TASK_PROGRESS_FIELD_HINTS = ("完成情况", "当前进展", "进展")
DOC_TABLE_GOAL_COLUMN_HINTS = ("团队目标", "周目标", "目标")
DOC_TABLE_ACTION_COLUMN_HINTS = ("目标分解", "关键里程碑计划", "每周行动", "关键行动", "关键举措")
DOC_SECTION_LABELS = (
    "目标",
    "交付",
    "关键交付",
    "关键成果",
    "待办",
    "调整",
    "结论",
    "风险",
    "背景",
    "价值",
    "内容",
    "依赖",
)
EXCEL_EPOCH = date(1899, 12, 30)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="读取飞书项目管理 URL，并输出标准化 JSON。")
    parser.add_argument(
        "--url",
        dest="urls",
        action="append",
        required=True,
        help="飞书 URL；支持 /wiki/、/docx/、/doc/、/base/。",
    )
    parser.add_argument("--lark-bin", default="lark-cli", help="lark-cli 可执行文件路径。默认：lark-cli")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数。填 0 表示紧凑输出。默认：2。")
    return parser.parse_args()


def run_json_command(command: list[str]) -> Any:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise SystemExit(f"命令执行失败：{' '.join(command)}\n{message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"无法解析 JSON 输出：{' '.join(command)}") from exc


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").replace("_", " ").replace("/", " ").split()).lower()


def stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(item, str) for item in value):
            return "、".join(item.strip() for item in value if item and item.strip())
        parts: list[str] = []
        for item in value:
            text = stringify_value(item)
            if text:
                parts.append(text)
        return "、".join(parts)
    if isinstance(value, dict):
        if value.get("name"):
            return str(value["name"]).strip()
        if value.get("text"):
            return str(value["text"]).strip()
        if value.get("id"):
            return str(value["id"]).strip()
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def extract_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]).strip())
            else:
                text = stringify_value(item)
                if text:
                    names.extend(part.strip() for part in text.split("、") if part.strip())
        return names
    text = stringify_value(value)
    if not text:
        return []
    return [part.strip() for part in text.split("、") if part.strip()]


def extract_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        identifiers: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("id"):
                identifiers.append(str(item["id"]).strip())
        return identifiers
    if isinstance(value, dict) and value.get("id"):
        return [str(value["id"]).strip()]
    return []


def extract_link_ids(record: dict[str, Any], field_names: list[str]) -> list[str]:
    identifiers: list[str] = []
    for field_name in field_names:
        identifiers.extend(extract_ids(record.get(field_name)))
    seen: set[str] = set()
    result: list[str] = []
    for identifier in identifiers:
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append(identifier)
    return result


def parse_numeric(value: Any) -> float | None:
    text = stringify_value(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip())):
        number = float(value)
        if 30000 <= number <= 60000:
            serial_day = int(round(number))
            return (EXCEL_EPOCH + timedelta(days=serial_day)).isoformat()
    text = stringify_value(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def first_nonempty(record: dict[str, Any], field_names: tuple[str, ...] | list[str]) -> str:
    for field_name in field_names:
        text = stringify_value(record.get(field_name))
        if text:
            return text
    return ""


def collect_matching_field_texts(record: dict[str, Any], field_hints: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for field_name, value in record.items():
        if field_name.startswith("_"):
            continue
        if not any(hint in field_name for hint in field_hints):
            continue
        text = stringify_value(value)
        if text:
            values.extend(split_plain_lines(text) or [text])
    return values


def split_plain_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.splitlines():
        cleaned = " ".join(raw_line.split())
        if cleaned:
            lines.append(cleaned)
    return lines


def build_keywords(*values: Any) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = stringify_value(value)
        if not text:
            continue
        parts = re.split(r"[\s,，；;、/\\|\n\r\t\-\+\(\)\[\]（）【】:：]+", text)
        parts.append(text)
        for part in parts:
            normalized = normalize_text(part)
            if len(normalized) < 2:
                continue
            if normalized in {"目标", "项目", "产品", "效果", "质量", "任务", "事项", "方案", "功能", "能力"}:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            phrases.append(normalized)
    return phrases


def parse_lark_url(url: str) -> dict[str, str | None]:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        raise SystemExit(f"无效飞书 URL：{url}")

    query = parse_qs(parsed.query)
    table_id = query.get("table", [None])[0]
    view_id = query.get("view", [None])[0]

    if len(segments) >= 3 and segments[0] == "wiki" and segments[1] == "space":
        return {
            "entry_type": "wiki-space",
            "token": segments[2],
            "table_id": table_id,
            "view_id": view_id,
        }

    entry_type = segments[0]
    if entry_type not in {"wiki", "docx", "doc", "base"}:
        raise SystemExit(f"暂不支持的飞书 URL：{url}")
    if len(segments) < 2:
        raise SystemExit(f"无法从 URL 中提取 token：{url}")

    return {
        "entry_type": entry_type,
        "token": segments[1],
        "table_id": table_id,
        "view_id": view_id,
    }


def fetch_identity(lark_bin: str) -> dict[str, str]:
    payload = run_json_command([lark_bin, "auth", "status", "--verify"])
    return {
        "name": str(payload.get("userName") or "").strip(),
        "open_id": str(payload.get("userOpenId") or "").strip(),
        "identity": str(payload.get("identity") or "").strip(),
    }


def resolve_resource(url: str, lark_bin: str) -> dict[str, Any]:
    parsed = parse_lark_url(url)
    if parsed["entry_type"] == "wiki-space":
        raise SystemExit("当前只支持具体文档或表格 URL，不支持直接传知识空间 URL。")

    if parsed["entry_type"] == "wiki":
        payload = run_json_command(
            [
                lark_bin,
                "wiki",
                "spaces",
                "get_node",
                "--params",
                json.dumps({"token": parsed["token"]}, ensure_ascii=False),
            ]
        )
        node = payload.get("data", {}).get("node", {})
        return {
            "source_url": url,
            "entry_type": "wiki",
            "resource_type": node.get("obj_type"),
            "resource_token": node.get("obj_token"),
            "title": node.get("title") or "",
            "space_id": node.get("space_id"),
            "table_id": parsed["table_id"],
            "view_id": parsed["view_id"],
        }

    resource_type = "bitable" if parsed["entry_type"] == "base" else parsed["entry_type"]
    return {
        "source_url": url,
        "entry_type": parsed["entry_type"],
        "resource_type": resource_type,
        "resource_token": parsed["token"],
        "title": "",
        "space_id": None,
        "table_id": parsed["table_id"],
        "view_id": parsed["view_id"],
    }


def list_tables(lark_bin: str, base_token: str) -> list[dict[str, Any]]:
    payload = run_json_command(
        [lark_bin, "base", "+table-list", "--base-token", base_token, "--offset", "0", "--limit", "100"]
    )
    return payload.get("data", {}).get("items", [])


def list_fields(lark_bin: str, base_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = run_json_command(
        [
            lark_bin,
            "base",
            "+field-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--offset",
            "0",
            "--limit",
            "200",
        ]
    )
    return payload.get("data", {}).get("items", [])


def list_records(
    lark_bin: str,
    base_token: str,
    table_id: str,
    view_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    offset = 0
    rows: list[list[Any]] = []
    record_ids: list[str] = []
    field_names: list[str] = []

    while True:
        command = [
            lark_bin,
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--offset",
            str(offset),
            "--limit",
            str(limit),
        ]
        if view_id:
            command.extend(["--view-id", view_id])
        payload = run_json_command(command)
        data = payload.get("data", {})
        rows.extend(data.get("data", []))
        record_ids.extend(data.get("record_id_list", []))
        if not field_names:
            field_names = data.get("fields", [])
        if not data.get("has_more"):
            break
        offset += int(data.get("limit", limit))

    return {"fields": field_names, "rows": rows, "record_ids": record_ids}


def map_records(field_names: list[str], rows: list[list[Any]], record_ids: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        record = {field_name: value for field_name, value in zip(field_names, row)}
        if index < len(record_ids):
            record["_record_id"] = record_ids[index]
        records.append(record)
    return records


def infer_task_table(tables: list[dict[str, Any]], preferred_table_id: str | None) -> dict[str, Any]:
    if preferred_table_id:
        for table in tables:
            if table.get("table_id") == preferred_table_id:
                return table
        raise SystemExit(f"未在 Base 中找到 table_id={preferred_table_id}。")

    for keyword in TASK_TABLE_HINTS:
        for table in tables:
            if keyword in str(table.get("table_name") or ""):
                return table
    if not tables:
        raise SystemExit("Base 中没有任何表。")
    return tables[0]


def find_goal_table(
    lark_bin: str,
    base_token: str,
    tables: list[dict[str, Any]],
    task_table_id: str,
    linked_goal_ids: set[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [table for table in tables if table.get("table_id") != task_table_id]
    prioritized = sorted(
        candidates,
        key=lambda table: (
            0 if any(keyword in str(table.get("table_name") or "") for keyword in GOAL_TABLE_HINTS) else 1,
            str(table.get("table_name") or ""),
        ),
    )

    cached_fields: dict[str, list[dict[str, Any]]] = {}
    cached_records: dict[str, list[dict[str, Any]]] = {}

    for table in prioritized:
        table_id = str(table.get("table_id"))
        fields = list_fields(lark_bin, base_token, table_id)
        record_payload = list_records(lark_bin, base_token, table_id)
        records = map_records(record_payload["fields"], record_payload["rows"], record_payload["record_ids"])
        cached_fields[table_id] = fields
        cached_records[table_id] = records
        if linked_goal_ids and linked_goal_ids.intersection(record_payload["record_ids"]):
            return table, fields, records

    if prioritized:
        fallback_table = prioritized[0]
        table_id = str(fallback_table.get("table_id"))
        return fallback_table, cached_fields.get(table_id, []), cached_records.get(table_id, [])
    return None, [], []


def normalize_task_entries(
    records: list[dict[str, Any]],
    current_user: dict[str, str],
    goal_field_names: list[str],
    source_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        owner_names: set[str] = set()
        owner_ids: set[str] = set()
        for field_name, value in record.items():
            if field_name.startswith("_"):
                continue
            if any(hint in field_name for hint in OWNER_FIELD_HINTS):
                owner_names.update(extract_names(value))
                owner_ids.update(extract_ids(value))

        title = first_nonempty(record, TASK_TITLE_HINTS)
        if not title:
            title = f"任务#{str(record.get('_record_id') or '')[-6:]}"

        entry = {
            "task_id": record.get("_record_id"),
            "source_url": source_meta["source_url"],
            "source_title": source_meta["title"],
            "resource_type": source_meta.get("resource_type"),
            "title": title,
            "status": stringify_value(record.get("状态")),
            "priority": stringify_value(record.get("优先级")),
            "start_date": parse_date_value(record.get("开始日期")),
            "due_date": parse_date_value(record.get("期望交付")) or parse_date_value(record.get("结束日期")),
            "risk_note": first_nonempty(record, ("风险备注", "备注")),
            "risk_flag": stringify_value(record.get("风险")),
            "goal_ids": extract_link_ids(record, goal_field_names),
            "owner_names": sorted(name for name in owner_names if name),
            "owner_ids": sorted(identifier for identifier in owner_ids if identifier),
            "mapped_goal": collect_matching_field_texts(record, TASK_GOAL_FIELD_HINTS),
            "mapped_key_results": collect_matching_field_texts(record, TASK_KEY_RESULT_FIELD_HINTS),
            "mapped_actions": collect_matching_field_texts(record, TASK_ACTION_FIELD_HINTS),
            "mapped_progress": collect_matching_field_texts(record, TASK_PROGRESS_FIELD_HINTS),
        }
        entry["involves_current_user"] = bool(
            current_user.get("name") and current_user["name"] in entry["owner_names"]
            or current_user.get("open_id") and current_user["open_id"] in entry["owner_ids"]
        )
        entry["keywords"] = build_keywords(
            entry["title"],
            entry["status"],
            entry["priority"],
            entry["risk_note"],
            " ".join(entry["owner_names"]),
        )
        entries.append(entry)
    return entries


def strip_markers(value: str) -> str:
    text = value
    text = re.sub(r"<mention-doc [^>]*>(.*?)</mention-doc>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<mention-user[^>]*/>", "", text)
    text = re.sub(r"<image[^>]*/>", "", text)
    text = re.sub(r"</?text[^>]*>", "", text)
    text = re.sub(r"</?callout[^>]*>", "", text)
    text = re.sub(r"</?lark-[^>]*>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[*_~`]+", "", text)
    return text


def extract_mention_user_ids(value: str) -> list[str]:
    return [item.strip() for item in re.findall(r'<mention-user[^>]*id="([^"]+)"', value) if item.strip()]


def normalize_doc_cell_lines(value: str) -> list[str]:
    text = strip_markers(value)
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = strip_markdown(raw_line)
        cleaned = " ".join(cleaned.split())
        if cleaned:
            lines.append(cleaned)
    return lines


def parse_doc_month_heading(heading: str) -> tuple[str, str] | None:
    match = re.search(r"(?P<year>\d{2,4})[-/年](?P<month>\d{1,2})", heading)
    if not match:
        return None
    year = int(match.group("year"))
    if year < 100:
        year += 2000
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def nearest_doc_heading(markdown: str, position: int) -> str:
    heading = ""
    for match in re.finditer(r"(?m)^#\s+(.+)$", markdown):
        if match.start() >= position:
            break
        heading = strip_markdown(match.group(1))
    return heading


def parse_doc_table_rows(table_block: str) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for row_match in re.finditer(r"<lark-tr>(.*?)</lark-tr>", table_block, flags=re.DOTALL):
        cells: list[dict[str, Any]] = []
        for cell_match in re.finditer(r"<lark-td>(.*?)</lark-td>", row_match.group(1), flags=re.DOTALL):
            raw = cell_match.group(1)
            cells.append(
                {
                    "raw": raw,
                    "lines": normalize_doc_cell_lines(raw),
                    "user_ids": extract_mention_user_ids(raw),
                }
            )
        if cells:
            rows.append(cells)
    return rows


def doc_cell_text(cell: dict[str, Any]) -> str:
    return " ".join(cell.get("lines", []))


def find_doc_column_index(header_cells: list[dict[str, Any]], hints: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(header_cells):
        text = doc_cell_text(cell)
        if any(hint in text for hint in hints):
            return index
    return None


def split_doc_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_leading": []}
    current = "_leading"
    for line in lines:
        matched_label = None
        remainder = line
        for label in DOC_SECTION_LABELS:
            prefix = f"{label}："
            if line.startswith(prefix):
                matched_label = label
                remainder = line[len(prefix) :].strip()
                break
        if matched_label:
            current = matched_label
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(remainder)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def flatten_doc_sections(sections: dict[str, list[str]], keep_labels: bool) -> list[str]:
    lines: list[str] = []
    for key, values in sections.items():
        if key == "_leading":
            lines.extend(values)
            continue
        if keep_labels:
            if not values:
                lines.append(f"{key}：")
            else:
                lines.extend(f"{key}：{value}" for value in values)
            continue
        lines.extend(values)
    return lines


def extract_doc_tasks(
    title: str,
    markdown: str,
    resource: dict[str, Any],
    current_user: dict[str, str],
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for table_match in re.finditer(r"<lark-table\b.*?</lark-table>", markdown, flags=re.DOTALL):
        heading = nearest_doc_heading(markdown, table_match.start())
        month_window = parse_doc_month_heading(heading)
        rows = parse_doc_table_rows(table_match.group(0))
        if len(rows) < 2:
            continue
        header = rows[0]
        goal_index = find_doc_column_index(header, DOC_TABLE_GOAL_COLUMN_HINTS)
        owner_index = find_doc_column_index(header, ("负责人",))
        action_index = find_doc_column_index(header, DOC_TABLE_ACTION_COLUMN_HINTS)
        if goal_index is None or owner_index is None:
            continue

        for row in rows[1:]:
            if owner_index >= len(row):
                continue
            owner_ids = row[owner_index].get("user_ids", [])
            if current_user.get("open_id") and current_user["open_id"] not in owner_ids:
                continue

            goal_cell = row[goal_index] if goal_index < len(row) else {"lines": []}
            action_cell = row[action_index] if action_index is not None and action_index < len(row) else {"lines": []}
            progress_cells = row[action_index + 1 :] if action_index is not None and action_index + 1 < len(row) else []

            goal_sections = split_doc_sections(goal_cell.get("lines", []))
            action_sections = split_doc_sections(action_cell.get("lines", []))
            progress_sections = [split_doc_sections(cell.get("lines", [])) for cell in progress_cells]

            title_candidates = goal_sections.get("_leading", [])
            row_title = title_candidates[0] if title_candidates else ""
            mapped_goal = [row_title] if row_title else []
            mapped_goal.extend(goal_sections.get("目标", []))
            mapped_key_results = (
                goal_sections.get("关键交付", [])
                or goal_sections.get("关键成果", [])
                or goal_sections.get("交付", [])
            )
            mapped_actions = flatten_doc_sections(action_sections, keep_labels=True)
            mapped_progress: list[str] = []
            for sections in progress_sections:
                mapped_progress.extend(flatten_doc_sections(sections, keep_labels=True))

            if not row_title and not mapped_key_results and not mapped_actions and not mapped_progress:
                continue

            start_date = month_window[0] if month_window else None
            due_date = month_window[1] if month_window else None
            entry = {
                "task_id": f"{resource['source_url']}#{heading or title}#{len(tasks)}",
                "source_url": resource["source_url"],
                "source_title": title,
                "resource_type": resource.get("resource_type"),
                "title": row_title or f"文档事项#{len(tasks) + 1}",
                "status": "",
                "priority": "",
                "start_date": start_date,
                "due_date": due_date,
                "risk_note": "",
                "risk_flag": "",
                "goal_ids": [],
                "goal_titles": [],
                "owner_names": [],
                "owner_ids": owner_ids,
                "involves_current_user": True,
                "mapped_goal": mapped_goal,
                "mapped_key_results": mapped_key_results,
                "mapped_actions": mapped_actions,
                "mapped_progress": mapped_progress,
            }
            entry["keywords"] = build_keywords(
                entry["title"],
                " ".join(entry["mapped_goal"]),
                " ".join(entry["mapped_key_results"]),
                " ".join(entry["mapped_actions"]),
                " ".join(entry["mapped_progress"]),
            )
            tasks.append(entry)
    return tasks


def normalize_goal_entries(
    records: list[dict[str, Any]],
    current_user: dict[str, str],
    link_field_names: list[str],
    source_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        title = first_nonempty(record, GOAL_TITLE_HINTS)
        if not title:
            title = f"目标#{str(record.get('_record_id') or '')[-6:]}"
        owner_names = extract_names(record.get("主导人")) or extract_names(record.get("负责人"))
        owner_ids = extract_ids(record.get("主导人")) or extract_ids(record.get("负责人"))
        entry = {
            "goal_id": record.get("_record_id"),
            "source_url": source_meta["source_url"],
            "source_title": source_meta["title"],
            "resource_type": source_meta.get("resource_type"),
            "title": title,
            "summary": first_nonempty(record, GOAL_DETAIL_HINTS),
            "type": stringify_value(record.get("类型")),
            "quarter": stringify_value(record.get("季度")),
            "priority": stringify_value(record.get("优先级")),
            "start_date": parse_date_value(record.get("计划开始")),
            "due_date": parse_date_value(record.get("期望交付")),
            "planned_completion": parse_date_value(record.get("计划完成")),
            "actual_completion": parse_date_value(record.get("实际完成")),
            "progress": parse_numeric(record.get("进度")),
            "variance_days": parse_numeric(record.get("预估偏差")),
            "owner_names": owner_names,
            "owner_ids": owner_ids,
            "linked_task_ids": extract_link_ids(record, link_field_names),
        }
        entry["involves_current_user"] = bool(
            current_user.get("name") and current_user["name"] in entry["owner_names"]
            or current_user.get("open_id") and current_user["open_id"] in entry["owner_ids"]
        )
        entry["keywords"] = build_keywords(
            entry["title"],
            entry["summary"],
            entry["type"],
            entry["quarter"],
            " ".join(entry["owner_names"]),
        )
        entries.append(entry)
    return entries


def fetch_bitable_context(resource: dict[str, Any], lark_bin: str, current_user: dict[str, str]) -> dict[str, Any]:
    base_token = str(resource.get("resource_token") or "")
    tables = list_tables(lark_bin, base_token)
    task_table = infer_task_table(tables, resource.get("table_id"))
    task_table_id = str(task_table.get("table_id"))
    task_fields = list_fields(lark_bin, base_token, task_table_id)
    task_record_payload = list_records(lark_bin, base_token, task_table_id, view_id=resource.get("view_id"))
    task_records = map_records(
        task_record_payload["fields"],
        task_record_payload["rows"],
        task_record_payload["record_ids"],
    )
    task_link_fields = [field["field_name"] for field in task_fields if field.get("type") == "link"]
    goal_field_names = [field_name for field_name in task_link_fields if "目标" in field_name] or task_link_fields
    task_entries = normalize_task_entries(task_records, current_user, goal_field_names, resource)
    linked_goal_ids = {goal_id for entry in task_entries for goal_id in entry["goal_ids"]}

    goal_table, goal_fields, goal_records = find_goal_table(
        lark_bin,
        base_token,
        tables,
        task_table_id,
        linked_goal_ids,
    )
    goal_entries: list[dict[str, Any]] = []
    if goal_table:
        goal_link_fields = [field["field_name"] for field in goal_fields if field.get("type") == "link"]
        goal_entries = normalize_goal_entries(goal_records, current_user, goal_link_fields, resource)

    goal_map = {entry["goal_id"]: entry for entry in goal_entries}
    for goal_entry in goal_entries:
        goal_entry["linked_task_titles"] = []

    for task_entry in task_entries:
        task_entry["goal_titles"] = []
        for goal_id in task_entry["goal_ids"]:
            goal_entry = goal_map.get(goal_id)
            if not goal_entry:
                continue
            task_entry["goal_titles"].append(goal_entry["title"])
            goal_entry["linked_task_titles"].append(task_entry["title"])

    for goal_entry in goal_entries:
        goal_entry["linked_task_titles"] = list(dict.fromkeys(goal_entry["linked_task_titles"]))
        goal_entry["keywords"] = build_keywords(
            goal_entry["title"],
            goal_entry["summary"],
            " ".join(goal_entry["linked_task_titles"]),
        )

    source = {
        "url": resource["source_url"],
        "resource_type": "bitable",
        "title": resource.get("title") or task_table.get("table_name") or "",
        "space_id": resource.get("space_id"),
        "base_token": base_token,
        "table_id": task_table_id,
        "table_name": task_table.get("table_name"),
        "view_id": resource.get("view_id"),
        "goal_table_id": goal_table.get("table_id") if goal_table else None,
        "goal_table_name": goal_table.get("table_name") if goal_table else None,
    }
    return {"sources": [source], "tasks": task_entries, "goals": goal_entries, "warnings": []}


def strip_markdown(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"^[-*+]\s*", "", cleaned)
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    return " ".join(cleaned.split())


def extract_doc_goals(title: str, markdown: str, resource: dict[str, Any]) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    candidates: list[dict[str, Any]] = []

    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            continue
        heading = strip_markdown(line)
        if not heading or not any(keyword in heading for keyword in DOC_GOAL_HEADING_HINTS):
            continue

        block: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].lstrip().startswith("#"):
            text = strip_markdown(lines[cursor])
            if text:
                block.append(text)
            cursor += 1
        summary = "；".join(block[:4])
        candidates.append(
            {
                "goal_id": f"{resource['source_url']}#{index}",
                "source_url": resource["source_url"],
                "source_title": title,
                "resource_type": resource.get("resource_type"),
                "title": heading,
                "summary": summary,
                "type": "文档目标",
                "quarter": "",
                "priority": "",
                "start_date": None,
                "due_date": None,
                "planned_completion": None,
                "actual_completion": None,
                "progress": None,
                "variance_days": None,
                "owner_names": [],
                "owner_ids": [],
                "linked_task_ids": [],
                "linked_task_titles": [],
                "keywords": build_keywords(heading, summary),
                "involves_current_user": False,
            }
        )

    if candidates:
        return candidates[:5]

    fallback_lines = [strip_markdown(line) for line in lines if strip_markdown(line)]
    summary = "；".join(fallback_lines[:4])
    return [
        {
            "goal_id": resource["source_url"],
            "source_url": resource["source_url"],
            "source_title": title,
            "resource_type": resource.get("resource_type"),
            "title": title or "项目目标",
            "summary": summary,
            "type": "文档目标",
            "quarter": "",
            "priority": "",
            "start_date": None,
            "due_date": None,
            "planned_completion": None,
            "actual_completion": None,
            "progress": None,
            "variance_days": None,
            "owner_names": [],
            "owner_ids": [],
            "linked_task_ids": [],
            "linked_task_titles": [],
            "keywords": build_keywords(title, summary),
            "involves_current_user": False,
        }
    ]


def fetch_doc_context(resource: dict[str, Any], lark_bin: str, current_user: dict[str, str]) -> dict[str, Any]:
    offset = 0
    title = resource.get("title") or ""
    markdown_chunks: list[str] = []

    while True:
        payload = run_json_command(
            [
                lark_bin,
                "docs",
                "+fetch",
                "--doc",
                resource["source_url"],
                "--offset",
                str(offset),
                "--limit",
                "50",
            ]
        )
        data = payload.get("data", {})
        title = str(data.get("title") or title or "").strip()
        markdown_chunks.append(str(data.get("markdown") or ""))
        if not data.get("has_more"):
            break
        next_offset = data.get("next_offset")
        if next_offset is None:
            next_offset = int(data.get("offset", offset)) + int(data.get("limit", 50))
        next_offset = int(next_offset)
        if next_offset <= offset:
            break
        offset = next_offset

    markdown = "\n".join(chunk for chunk in markdown_chunks if chunk)
    tasks = extract_doc_tasks(title, markdown, resource, current_user)
    goals = [] if tasks else extract_doc_goals(title, markdown, resource)
    source = {
        "url": resource["source_url"],
        "resource_type": resource["resource_type"],
        "title": title,
        "space_id": resource.get("space_id"),
        "table_id": None,
        "table_name": None,
        "view_id": None,
        "goal_table_id": None,
        "goal_table_name": None,
    }
    return {"sources": [source], "tasks": tasks, "goals": goals, "warnings": []}


def collect_from_url(url: str, lark_bin: str, current_user: dict[str, str]) -> dict[str, Any]:
    resource = resolve_resource(url, lark_bin)
    if resource["resource_type"] == "bitable":
        return fetch_bitable_context(resource, lark_bin, current_user)
    if resource["resource_type"] in {"doc", "docx"}:
        return fetch_doc_context(resource, lark_bin, current_user)

    source = {
        "url": resource["source_url"],
        "resource_type": resource["resource_type"],
        "title": resource.get("title") or "",
        "space_id": resource.get("space_id"),
        "table_id": resource.get("table_id"),
        "table_name": None,
        "view_id": resource.get("view_id"),
        "goal_table_id": None,
        "goal_table_name": None,
    }
    warning = f"暂不支持解析 {resource['resource_type']} 资源：{resource['source_url']}"
    return {"sources": [source], "tasks": [], "goals": [], "warnings": [warning]}


def main() -> int:
    args = parse_args()
    current_user = fetch_identity(args.lark_bin)
    payload = {"current_user": current_user, "sources": [], "tasks": [], "goals": [], "warnings": []}

    for url in args.urls:
        context = collect_from_url(url, args.lark_bin, current_user)
        payload["sources"].extend(context.get("sources", []))
        payload["tasks"].extend(context.get("tasks", []))
        payload["goals"].extend(context.get("goals", []))
        payload["warnings"].extend(context.get("warnings", []))

    indent = None if args.indent <= 0 else args.indent
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
