#!/usr/bin/env python3
"""读取 Dayflow 时间线数据，支持单日、整月或自定义日期范围。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "Dayflow" / "chunks.sqlite"


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
    parser = argparse.ArgumentParser(
        description="读取 Dayflow 数据，并导出指定日期、月份或时间范围的标准化 JSON。"
    )
    parser.add_argument("--date", dest="single_date", help="单日，格式 YYYY-MM-DD。")
    parser.add_argument("--month", help="整月，格式 YYYY-MM。")
    parser.add_argument("--from", dest="from_date", help="起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--to", dest="to_date", help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Dayflow SQLite 数据库路径。默认：{DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--include-details",
        action="store_true",
        help="在输出中包含 timeline_cards.detailed_summary。",
    )
    parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="解析并输出 timeline_cards.metadata JSON。",
    )
    parser.add_argument(
        "--skip-journal",
        action="store_true",
        help="即使存在 journal_entries，也不读取。",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON 缩进空格数。填 0 表示紧凑输出。默认：2。",
    )
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
    month_end = next_month - timedelta(days=1)
    return DateWindow(start=month_start, end=month_end)


def resolve_window(args: argparse.Namespace) -> DateWindow:
    choices = sum(bool(value) for value in (args.single_date, args.month, args.from_date or args.to_date))
    if choices != 1:
        raise SystemExit("必须且只能提供一种范围：--date、--month，或 --from/--to。")

    if args.single_date:
        single = parse_iso_date(args.single_date)
        return DateWindow(start=single, end=single)

    if args.month:
        return parse_month(args.month)

    if not args.from_date or not args.to_date:
        raise SystemExit("使用自定义范围时，必须同时提供 --from 和 --to。")

    start = parse_iso_date(args.from_date)
    end = parse_iso_date(args.to_date)
    if end < start:
        raise SystemExit("无效范围：--to 不能早于 --from。")
    return DateWindow(start=start, end=end)


def snapshot_database(db_path: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    if not db_path.exists():
        raise SystemExit(f"未找到 Dayflow 数据库：{db_path}")

    tempdir = tempfile.TemporaryDirectory(prefix="dayflow-snapshot-")
    snapshot_path = Path(tempdir.name) / "chunks.snapshot.sqlite"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    target = sqlite3.connect(snapshot_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    return tempdir, snapshot_path


def fetch_cards(conn: sqlite3.Connection, window: DateWindow) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            id,
            batch_id,
            day,
            start,
            end,
            start_ts,
            end_ts,
            title,
            summary,
            category,
            subcategory,
            detailed_summary,
            metadata
        FROM timeline_cards
        WHERE is_deleted = 0
          AND day BETWEEN ? AND ?
        ORDER BY day, start_ts, id
        """,
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in rows:
        duration_seconds = max(0, (row["end_ts"] or 0) - (row["start_ts"] or 0))
        card = {
            "id": row["id"],
            "batch_id": row["batch_id"],
            "day": row["day"],
            "start": row["start"],
            "end": row["end"],
            "start_ts": row["start_ts"],
            "end_ts": row["end_ts"],
            "duration_seconds": duration_seconds,
            "duration_minutes": round(duration_seconds / 60.0, 2),
            "title": row["title"],
            "summary": row["summary"],
            "category": row["category"],
            "subcategory": row["subcategory"],
        }
        card["_details"] = row["detailed_summary"]
        card["_metadata"] = row["metadata"]
        cards.append(card)
    return cards


def fetch_journal_entries(conn: sqlite3.Connection, window: DateWindow) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT day, intentions, goals, reflections, summary, status, created_at, updated_at
            FROM journal_entries
            WHERE day BETWEEN ? AND ?
            ORDER BY day
            """,
            (window.start.isoformat(), window.end.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return [dict(row) for row in rows]


def daterange(window: DateWindow) -> list[str]:
    days: list[str] = []
    current = window.start
    while current <= window.end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def aggregate_cards(cards: list[dict[str, Any]], window: DateWindow) -> dict[str, Any]:
    total_seconds = sum(card["duration_seconds"] for card in cards)

    by_day: dict[str, dict[str, Any]] = {}
    by_category: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "seconds": 0})
    by_subcategory: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "seconds": 0})

    for day_key in daterange(window):
        by_day[day_key] = {"count": 0, "seconds": 0}

    for card in cards:
        day_bucket = by_day.setdefault(card["day"], {"count": 0, "seconds": 0})
        day_bucket["count"] += 1
        day_bucket["seconds"] += card["duration_seconds"]

        category = card["category"] or "未分类"
        by_category[category]["count"] += 1
        by_category[category]["seconds"] += card["duration_seconds"]

        subcategory = card["subcategory"] or "未细分"
        by_subcategory[subcategory]["count"] += 1
        by_subcategory[subcategory]["seconds"] += card["duration_seconds"]

    def finalize(bucket: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        finalized: dict[str, dict[str, Any]] = {}
        for key, values in sorted(bucket.items(), key=lambda item: (-item[1]["seconds"], item[0])):
            seconds = values["seconds"]
            finalized[key] = {
                "count": values["count"],
                "seconds": seconds,
                "hours": round(seconds / 3600.0, 2),
                "person_days_8h": round(seconds / 28800.0, 2),
            }
        return finalized

    by_day_final: dict[str, dict[str, Any]] = {}
    for key in sorted(by_day):
        seconds = by_day[key]["seconds"]
        by_day_final[key] = {
            "count": by_day[key]["count"],
            "seconds": seconds,
            "hours": round(seconds / 3600.0, 2),
            "person_days_8h": round(seconds / 28800.0, 2),
        }

    missing_days = [day_key for day_key, values in by_day_final.items() if values["count"] == 0]

    return {
        "card_count": len(cards),
        "active_days": sum(1 for values in by_day_final.values() if values["count"] > 0),
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600.0, 2),
        "total_person_days_8h": round(total_seconds / 28800.0, 2),
        "by_day": by_day_final,
        "missing_days": missing_days,
        "by_category": finalize(by_category),
        "by_subcategory": finalize(by_subcategory),
    }


def finalize_cards(
    cards: list[dict[str, Any]], include_details: bool, include_metadata: bool
) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for card in cards:
        copy = dict(card)
        details = copy.pop("_details", None)
        raw_metadata = copy.pop("_metadata", None)
        if include_details:
            copy["detailed_summary"] = details
        if include_metadata:
            try:
                copy["metadata"] = json.loads(raw_metadata) if raw_metadata else None
            except json.JSONDecodeError:
                copy["metadata"] = {"raw": raw_metadata}
        finalized.append(copy)
    return finalized


def main() -> int:
    args = parse_args()
    window = resolve_window(args)
    db_path = Path(args.db_path).expanduser()
    tempdir, snapshot_path = snapshot_database(db_path)
    conn = sqlite3.connect(snapshot_path)
    try:
        cards = fetch_cards(conn, window)
        journal_entries = [] if args.skip_journal else fetch_journal_entries(conn, window)
    finally:
        conn.close()
        tempdir.cleanup()

    payload = {
        "source": {
            "db_path": str(db_path),
            "storage_dir": str(db_path.parent),
            "reader": "scripts/read_dayflow.py",
        },
        "range": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aggregates": aggregate_cards(cards, window),
        "cards": finalize_cards(cards, args.include_details, args.include_metadata),
        "journal_entries": journal_entries,
    }

    indent = None if args.indent <= 0 else args.indent
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
