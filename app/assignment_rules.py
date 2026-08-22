from __future__ import annotations

from typing import Any


def normalize_teacher_records(state: dict[str, Any]) -> bool:
    """Migrate teacher records and discard homeroom links to removed classes."""
    changed = False
    class_ids = {item["id"] for item in state.get("classes", [])}
    occupied: set[str] = set()
    for teacher in state.get("teachers", []):
        homeroom_class_id = teacher.get("homeroom_class_id")
        if homeroom_class_id not in class_ids or homeroom_class_id in occupied:
            homeroom_class_id = None
        if teacher.get("homeroom_class_id") != homeroom_class_id or "homeroom_class_id" not in teacher:
            teacher["homeroom_class_id"] = homeroom_class_id
            changed = True
        if homeroom_class_id:
            occupied.add(homeroom_class_id)
    return changed


def homeroom_teachers(state: dict[str, Any]) -> dict[str, str]:
    return {
        teacher["homeroom_class_id"]: teacher["id"]
        for teacher in state.get("teachers", [])
        if teacher.get("homeroom_class_id")
    }


def apply_required_teacher_assignments(state: dict[str, Any]) -> bool:
    """Bind reading to the Chinese teacher and class meeting to the homeroom teacher."""
    changed = normalize_teacher_records(state)
    homeroom_by_class = homeroom_teachers(state)
    for class_item in state.get("classes", []):
        class_id = class_item["id"]
        assignments = state.setdefault("assignments", {}).setdefault(class_id, {})
        required = {
            "reading": assignments.get("chinese"),
            "meeting": homeroom_by_class.get(class_id),
        }
        for subject_id, teacher_id in required.items():
            if subject_id not in assignments or assignments.get(subject_id) != teacher_id:
                assignments[subject_id] = teacher_id
                changed = True
    return changed
