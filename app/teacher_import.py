from __future__ import annotations

import re
import uuid
from collections import Counter
from io import BytesIO
from itertools import islice
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .constants import SUBJECTS

MAX_IMPORT_ROWS = 1000
NAME_HEADERS = {"教师姓名", "姓名", "老师姓名"}
LESSON_HEADERS = {"期望最低周课时", "课时数", "最低周课时", "周课时"}
SUBJECT_HEADERS = {"可任科目", "可任课程", "任教学科", "任教科目", "学科"}


def _normalize_header(value: Any) -> str:
    return re.sub(r"[\s（）()：:]", "", str(value or "").strip())


def _header_index(headers: list[Any], candidates: set[str]) -> int | None:
    normalized_candidates = {_normalize_header(item) for item in candidates}
    for index, value in enumerate(headers):
        normalized = _normalize_header(value)
        if normalized in normalized_candidates:
            return index
        if any(normalized.startswith(candidate) for candidate in normalized_candidates):
            return index
    return None


def _subject_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for subject in SUBJECTS:
        for value in (subject["id"], subject["name"], subject["short"]):
            aliases[_normalize_header(value).lower()] = subject["id"]
    aliases.update({
        "道德法治": "morality",
        "体育健康": "pe",
        "信息技术": "it",
        "班队会": "meeting",
        "安全课": "meeting",
    })
    return aliases


SUBJECT_ALIASES = _subject_aliases()


def _parse_subjects(value: Any, row_number: int) -> list[str]:
    text = str(value or "").strip()
    if not text or text in {"不限", "未限定", "无"}:
        return []
    tokens = [item for item in re.split(r"[、,，;；/|\s]+", text) if item]
    subject_ids: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        subject_id = SUBJECT_ALIASES.get(_normalize_header(token).lower())
        if not subject_id:
            unknown.append(token)
        elif subject_id not in subject_ids:
            subject_ids.append(subject_id)
    if unknown:
        raise ValueError(f"第{row_number}行包含未知科目：{'、'.join(unknown)}")
    return subject_ids


def parse_teacher_workbook(content: bytes) -> list[dict[str, Any]]:
    if not content:
        raise ValueError("上传文件为空")
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError) as exc:
        raise ValueError("无法读取该文件，请上传有效的 .xlsx 教师名单") from exc

    sheet = workbook["教师名单"] if "教师名单" in workbook.sheetnames else workbook.active
    rows = list(islice(sheet.iter_rows(values_only=True), MAX_IMPORT_ROWS + 2))
    if not rows:
        raise ValueError("教师名单中没有表头")
    headers = list(rows[0])
    name_index = _header_index(headers, NAME_HEADERS)
    lesson_index = _header_index(headers, LESSON_HEADERS)
    subject_index = _header_index(headers, SUBJECT_HEADERS)
    missing = []
    if name_index is None:
        missing.append("教师姓名")
    if lesson_index is None:
        missing.append("期望最低周课时")
    if subject_index is None:
        missing.append("可任科目")
    if missing:
        raise ValueError(f"缺少表头：{'、'.join(missing)}，请使用下载模板填写")

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, row in enumerate(rows[1 : MAX_IMPORT_ROWS + 1], start=2):
        values = list(row)
        relevant_values = [
            values[index] if index < len(values) else None
            for index in (name_index, lesson_index, subject_index)
        ]
        if not any(value is not None and str(value).strip() for value in relevant_values):
            continue
        name = str(values[name_index] or "").strip() if name_index < len(values) else ""
        if not name:
            errors.append(f"第{row_number}行缺少教师姓名")
            continue
        if len(name) > 40:
            errors.append(f"第{row_number}行教师姓名超过40个字符")
            continue
        raw_lessons = values[lesson_index] if lesson_index < len(values) else None
        try:
            if raw_lessons is None or str(raw_lessons).strip() == "":
                raise ValueError
            lessons_number = float(raw_lessons)
            if not lessons_number.is_integer():
                raise ValueError
            min_weekly_lessons = int(lessons_number)
            if not 0 <= min_weekly_lessons <= 35:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"第{row_number}行课时数必须是0—35的整数")
            continue
        try:
            subject_ids = _parse_subjects(values[subject_index] if subject_index < len(values) else None, row_number)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        parsed.append({
            "row": row_number,
            "name": name,
            "min_weekly_lessons": min_weekly_lessons,
            "subject_ids": subject_ids,
        })

    if len(rows) > MAX_IMPORT_ROWS + 1 and any(
        index < len(row) and row[index] is not None and str(row[index]).strip()
        for row in rows[MAX_IMPORT_ROWS + 1 :]
        for index in (name_index, lesson_index, subject_index)
    ):
        errors.append(f"单次最多导入{MAX_IMPORT_ROWS}名教师")
    if errors:
        shown = errors[:12]
        suffix = f"；另有{len(errors) - len(shown)}项错误" if len(errors) > len(shown) else ""
        raise ValueError("；".join(shown) + suffix)
    if not parsed:
        raise ValueError("教师名单中没有可导入的数据")
    return parsed


def merge_teacher_rows(state: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    teachers = state.setdefault("teachers", [])
    by_name = {teacher["name"]: teacher for teacher in teachers}
    occurrences: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    created = 0
    updated = 0
    renamed = 0

    for row in rows:
        base_name = row["name"]
        occurrences[base_name] += 1
        occurrence = occurrences[base_name]
        final_name = base_name if occurrence == 1 else f"{base_name}（{occurrence}）"
        target = by_name.get(final_name)
        if target:
            target.update({
                "subject_ids": list(row["subject_ids"]),
                "min_weekly_lessons": int(row["min_weekly_lessons"]),
            })
            action = "updated"
            updated += 1
        else:
            target = {
                "id": f"t_{uuid.uuid4().hex[:10]}",
                "name": final_name,
                "subject_ids": list(row["subject_ids"]),
                "min_weekly_lessons": int(row["min_weekly_lessons"]),
            }
            teachers.append(target)
            by_name[final_name] = target
            action = "created"
            created += 1
        if final_name != base_name:
            renamed += 1
        details.append({
            "row": row["row"],
            "source_name": base_name,
            "teacher_name": final_name,
            "action": action,
        })

    state["schedule"] = None
    return {
        "created": created,
        "updated": updated,
        "renamed": renamed,
        "total": len(rows),
        "details": details,
    }
