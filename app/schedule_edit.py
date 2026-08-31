from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .constants import (
    DAYS,
    PERIODS,
    SUBJECT_BY_ID,
    fixed_lessons_for_grade,
    reading_slot_pairs_for_grade,
    slots_for_grade,
)
from .scheduler import (
    SLOT_META,
    rebuild_schedule_analysis,
    subject_allowed,
    teacher_research_blocked_slots,
)


class ScheduleSwapError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = list(dict.fromkeys(issues))
        super().__init__("；".join(self.issues))


def _slot_name(slot: str) -> str:
    meta = SLOT_META.get(slot)
    if not meta:
        return slot
    day_name = next((item["name"] for item in DAYS if item["id"] == meta["day_id"]), meta["day_id"])
    period_name = next((item["name"] for item in PERIODS if item["id"] == meta["period_id"]), meta["period_id"])
    return f"{day_name}{period_name}"


def _validate_complete_schedule(state: dict[str, Any], lessons: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    issues: list[str] = []
    teacher_names = {teacher["id"]: teacher["name"] for teacher in state.get("teachers", [])}
    teacher_owners: dict[tuple[str, str], list[str]] = {}
    teacher_blocked_slots = teacher_research_blocked_slots(state)

    for class_item in state.get("classes", []):
        class_id = class_item["id"]
        grade = int(class_item["grade"])
        class_lessons = lessons.get(class_id, {})
        expected_slots = set(slots_for_grade(grade))
        if set(class_lessons) != expected_slots:
            issues.append(f"{class_item['name']}的课位不完整，请重新生成课表后再调整")
            continue

        for slot, fixed_subject in fixed_lessons_for_grade(grade).items():
            if class_lessons.get(slot, {}).get("subject_id") != fixed_subject:
                issues.append(f"{class_item['name']}的{_slot_name(slot)}是固定课位，不能更改")

        reading_slots = tuple(sorted(
            (
                slot
                for slot, lesson in class_lessons.items()
                if lesson.get("subject_id") == "reading"
            ),
            key=lambda slot: SLOT_META[slot]["period_index"],
        ))
        valid_reading_pairs = set(reading_slot_pairs_for_grade(grade))
        if reading_slots not in valid_reading_pairs:
            day_name = "星期三" if grade <= 2 else "星期四"
            issues.append(f"{class_item['name']}的阅读课必须安排在{day_name}下午连续两节")

        for slot, lesson in class_lessons.items():
            subject_id = lesson.get("subject_id")
            if subject_id not in SUBJECT_BY_ID:
                issues.append(f"{class_item['name']}的{_slot_name(slot)}包含未知课程")
                continue
            if not subject_allowed(grade, subject_id, slot):
                issues.append(
                    f"{class_item['name']}的{SUBJECT_BY_ID[subject_id]['name']}不能安排在{_slot_name(slot)}"
                )
            teacher_id = lesson.get("teacher_id")
            if teacher_id:
                if slot in teacher_blocked_slots.get(teacher_id, set()):
                    issues.append(
                        f"{teacher_names.get(teacher_id, '未知教师')}在{_slot_name(slot)}处于其语数英教研时段，不能安排任何课程"
                    )
                teacher_owners.setdefault((teacher_id, slot), []).append(class_item["name"])

    for (teacher_id, slot), class_names in teacher_owners.items():
        if len(class_names) > 1:
            issues.append(
                f"{teacher_names.get(teacher_id, '未知教师')}在{_slot_name(slot)}同时被安排到{'、'.join(class_names)}"
            )
    return list(dict.fromkeys(issues))


def apply_schedule_swaps(
    state: dict[str, Any],
    class_id: str,
    swaps: list[dict[str, str]],
) -> dict[str, Any]:
    schedule = state.get("schedule")
    if not schedule or not schedule.get("success"):
        raise ScheduleSwapError(["尚未生成可调整的课表"])
    class_item = next((item for item in state.get("classes", []) if item["id"] == class_id), None)
    if not class_item:
        raise ScheduleSwapError(["班级不存在"])
    if not swaps:
        raise ScheduleSwapError(["没有需要保存的课程交换"])

    grade = int(class_item["grade"])
    available_slots = set(slots_for_grade(grade))
    fixed_slots = set(fixed_lessons_for_grade(grade))
    staged_lessons = deepcopy(schedule.get("lessons", {}))
    class_lessons = staged_lessons.get(class_id)
    if class_lessons is None:
        raise ScheduleSwapError(["当前课表中没有该班级"])

    for index, swap in enumerate(swaps, start=1):
        from_slot = str(swap.get("from_slot", ""))
        to_slot = str(swap.get("to_slot", ""))
        prefix = f"第{index}次交换："
        if from_slot == to_slot:
            raise ScheduleSwapError([f"{prefix}请选择两个不同课位"])
        if from_slot not in available_slots or to_slot not in available_slots:
            raise ScheduleSwapError([f"{prefix}包含当前年级不可用的课位"])
        locked = [slot for slot in (from_slot, to_slot) if slot in fixed_slots]
        if locked:
            raise ScheduleSwapError([f"{prefix}{_slot_name(locked[0])}是固定课位，不能交换"])
        if from_slot not in class_lessons or to_slot not in class_lessons:
            raise ScheduleSwapError([f"{prefix}所选课位没有可交换的课程"])
        class_lessons[from_slot], class_lessons[to_slot] = class_lessons[to_slot], class_lessons[from_slot]

    issues = _validate_complete_schedule(state, staged_lessons)
    if issues:
        raise ScheduleSwapError(issues)

    warnings, quality = rebuild_schedule_analysis(state, staged_lessons)
    schedule["lessons"] = staged_lessons
    schedule["warnings"] = warnings
    schedule["quality"] = quality
    schedule["edited_at"] = datetime.now(timezone.utc).isoformat()
    schedule["manual_swap_count"] = int(schedule.get("manual_swap_count", 0)) + len(swaps)
    return schedule
