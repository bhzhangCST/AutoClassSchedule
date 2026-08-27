from __future__ import annotations

from typing import Any, Iterable

from .constants import CURRICULUM


Allocation = dict[str, Any]


def full_allocation(teacher_id: str | None, lessons: int) -> list[Allocation]:
    if not teacher_id or lessons <= 0:
        return []
    return [{"teacher_id": str(teacher_id), "lessons": int(lessons)}]


def normalize_allocations(value: Any, total_lessons: int) -> list[Allocation]:
    """Return the canonical list representation, accepting legacy scalar values."""
    if isinstance(value, str):
        return full_allocation(value, total_lessons)
    if not isinstance(value, list):
        return []

    combined: dict[str, Allocation] = {}
    order: list[str] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        teacher_id = str(raw.get("teacher_id") or "").strip()
        try:
            lessons = int(raw.get("lessons", 0))
        except (TypeError, ValueError):
            continue
        if not teacher_id or lessons <= 0:
            continue
        if teacher_id not in combined:
            combined[teacher_id] = {
                "teacher_id": teacher_id,
                "lessons": lessons,
            }
            order.append(teacher_id)
        else:
            combined[teacher_id]["lessons"] += lessons
    return [combined[teacher_id] for teacher_id in order]


def allocation_total(value: Any) -> int:
    if isinstance(value, str):
        raise ValueError("旧版任课值需要课程总节数才能计算")
    return sum(int(item.get("lessons", 0)) for item in value or [] if isinstance(item, dict))


def highest_lesson_teacher_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    allocations = [
        item
        for item in value or []
        if isinstance(item, dict) and item.get("teacher_id") and int(item.get("lessons", 0)) > 0
    ] if isinstance(value, list) else []
    if not allocations:
        return None
    return str(max(allocations, key=lambda item: int(item["lessons"]))["teacher_id"])


def teacher_lessons(value: Any, teacher_id: str) -> int:
    if isinstance(value, str):
        return 0
    return sum(
        int(item.get("lessons", 0))
        for item in value or []
        if isinstance(item, dict) and item.get("teacher_id") == teacher_id
    )


def remove_teacher(value: Any, teacher_id: str, total_lessons: int) -> list[Allocation]:
    allocations = normalize_allocations(value, total_lessons)
    return normalize_allocations(
        [item for item in allocations if item["teacher_id"] != teacher_id],
        total_lessons,
    )


def allocation_teacher_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    return {
        str(item["teacher_id"])
        for item in value or []
        if isinstance(item, dict) and item.get("teacher_id")
    }


def normalize_state_assignments(state: dict[str, Any]) -> bool:
    changed = False
    assignments = state.setdefault("assignments", {})
    valid_class_ids: set[str] = set()
    for class_item in state.get("classes", []):
        class_id = class_item["id"]
        grade = int(class_item["grade"])
        valid_class_ids.add(class_id)
        class_assignments = assignments.setdefault(class_id, {})
        normalized_subjects: dict[str, list[Allocation]] = {}
        for subject_id, total_lessons in CURRICULUM[grade].items():
            normalized_subjects[subject_id] = normalize_allocations(
                class_assignments.get(subject_id), total_lessons
            )
        if class_assignments != normalized_subjects:
            assignments[class_id] = normalized_subjects
            changed = True

    stale_class_ids = set(assignments) - valid_class_ids
    for class_id in stale_class_ids:
        del assignments[class_id]
        changed = True
    return changed


def validate_allocation_totals(
    allocations: Iterable[Allocation], total_lessons: int
) -> tuple[int, int]:
    assigned = sum(int(item.get("lessons", 0)) for item in allocations)
    return assigned, total_lessons - assigned
