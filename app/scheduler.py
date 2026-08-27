from __future__ import annotations

import random
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .assignment_rules import apply_required_teacher_assignments
from .constants import (
    CORE_SUBJECTS,
    CURRICULUM,
    DAYS,
    GRADE_NAMES,
    PERIODS,
    SMALL_CLASS_SUBJECTS,
    SUBJECT_BY_ID,
    fixed_lessons_for_grade,
    slots_for_grade,
)
from .teaching_allocations import allocation_total, normalize_allocations

SLOT_META: dict[str, dict[str, Any]] = {}
for day_index, day in enumerate(DAYS):
    for period_index, period in enumerate(PERIODS):
        key = f"{day['id']}-{period['id']}"
        SLOT_META[key] = {
            "day_id": day["id"],
            "day_index": day_index,
            "period_id": period["id"],
            "period_index": period_index,
        }


def _allocations_for(
    assignments: dict[str, Any], class_id: str, subject_id: str, total_lessons: int
) -> list[dict[str, Any]]:
    return normalize_allocations(
        assignments.get(class_id, {}).get(subject_id), total_lessons
    )


def _subject_allowed(grade: int, subject_id: str, slot: str) -> bool:
    meta = SLOT_META[slot]
    period = meta["period_id"]
    day = meta["day_id"]

    if period in {"am1", "am2"}:
        if subject_id not in CORE_SUBJECTS:
            return False
        if grade <= 2 and subject_id == "english":
            return False

    if subject_id == "english" and grade >= 3 and day == "mon" and period in {"pm1", "pm2"}:
        return False
    if subject_id == "chinese" and day == "tue" and period in {"pm1", "pm2"}:
        return False
    if subject_id == "math" and day == "thu" and period in {"pm1", "pm2"}:
        return False
    return True


def subject_allowed(grade: int, subject_id: str, slot: str) -> bool:
    """Public hard-rule check used by manual schedule edits."""
    return slot in SLOT_META and _subject_allowed(grade, subject_id, slot)


def _candidate_score(
    grade: int,
    subject_id: str,
    slot: str,
    class_lessons: dict[str, dict[str, Any]],
    rng: random.Random,
) -> float:
    meta = SLOT_META[slot]
    period = meta["period_id"]
    day_id = meta["day_id"]
    score = rng.random() * 5

    if grade >= 3 and period == "am3":
        score += 120 if subject_id in CORE_SUBJECTS else -20
    elif period == "am4":
        score += 25 if subject_id in CORE_SUBJECTS else 8
    elif period.startswith("pm") and subject_id in CORE_SUBJECTS:
        score -= 18

    same_day = sum(
        1
        for existing_slot, lesson in class_lessons.items()
        if SLOT_META[existing_slot]["day_id"] == day_id and lesson["subject_id"] == subject_id
    )
    score += 16 if same_day == 0 else (-8 if same_day == 1 else -45)
    if subject_id in SMALL_CLASS_SUBJECTS and CURRICULUM[grade].get(subject_id, 0) > 1:
        # 体育、音乐、美术、科学等每周多节的副科优先分散到不同日期。
        # 这是柔性目标：教师冲突或固定课位不足时仍允许同日安排。
        score += 55 if same_day == 0 else -150 * same_day

    period_index = meta["period_index"]
    for adjacent in (-1, 1):
        neighbor_index = period_index + adjacent
        if 0 <= neighbor_index < len(PERIODS):
            neighbor_slot = f"{day_id}-{PERIODS[neighbor_index]['id']}"
            neighbor = class_lessons.get(neighbor_slot)
            if neighbor and neighbor["subject_id"] == subject_id:
                score -= 30
    return score


def _build_lesson(subject_id: str, teacher_id: str | None, fixed: bool = False) -> dict[str, Any]:
    return {"subject_id": subject_id, "teacher_id": teacher_id, "fixed": fixed}


def _validate_inputs(state: dict[str, Any]) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    teachers = {teacher["id"]: teacher for teacher in state.get("teachers", [])}
    assignments = state.get("assignments", {})
    assigned_loads: Counter[str] = Counter()
    classes_by_teacher_subject: dict[tuple[str, str], set[str]] = defaultdict(set)
    for class_item in state.get("classes", []):
        class_id = class_item["id"]
        grade = int(class_item["grade"])
        expected = len(slots_for_grade(grade))
        actual = sum(CURRICULUM[grade].values())
        if expected != actual:
            errors.append(f"{class_item['name']}课程总课时为{actual}，但可用课位为{expected}。")
        for subject_id, hours in CURRICULUM[grade].items():
            allocations = _allocations_for(assignments, class_id, subject_id, hours)
            assigned = allocation_total(allocations)
            if assigned > hours:
                errors.append(
                    f"{class_item['name']}的{SUBJECT_BY_ID[subject_id]['name']}合计分配{assigned}节，超过标准{hours}节。"
                )
            for allocation in allocations:
                teacher_id = allocation["teacher_id"]
                teacher = teachers.get(teacher_id)
                if not teacher:
                    errors.append(f"{class_item['name']}的{SUBJECT_BY_ID[subject_id]['name']}引用了不存在的教师。")
                    continue
                assigned_loads[teacher_id] += int(allocation["lessons"])
                classes_by_teacher_subject[(teacher_id, subject_id)].add(class_id)

    for teacher_id, load in assigned_loads.items():
        if load > 35:
            teacher_name = teachers[teacher_id]["name"]
            errors.append(f"{teacher_name}被分配{load}节课，超过每周35个可用课位，无法排开。")

    for (teacher_id, subject_id), class_ids in classes_by_teacher_subject.items():
        usual_limit = 2 if subject_id == "math" else (3 if subject_id == "english" else None)
        if usual_limit and len(class_ids) > usual_limit:
            warnings.append(
                f"{teachers[teacher_id]['name']}承担{len(class_ids)}个班的{SUBJECT_BY_ID[subject_id]['short']}，"
                f"超过注意事项中常见的{usual_limit}个兄弟班，请核对任课配置。"
            )

    return errors, warnings, dict(assigned_loads)


def _prepare_fixed_lessons(
    state: dict[str, Any],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, set[str]], list[str]]:
    assignments = state.get("assignments", {})
    lessons: dict[str, dict[str, dict[str, Any]]] = {}
    teacher_busy: dict[str, set[str]] = defaultdict(set)
    busy_owner: dict[tuple[str, str], str] = {}
    conflicts: list[str] = []
    teacher_map = {teacher["id"]: teacher["name"] for teacher in state.get("teachers", [])}

    for class_item in state.get("classes", []):
        class_id = class_item["id"]
        lessons[class_id] = {}
        grade = int(class_item["grade"])
        fixed_by_subject: dict[str, list[str]] = defaultdict(list)
        for slot, subject_id in fixed_lessons_for_grade(grade).items():
            fixed_by_subject[subject_id].append(slot)
        for subject_id, fixed_slots in fixed_by_subject.items():
            total = CURRICULUM[grade][subject_id]
            allocations = _allocations_for(assignments, class_id, subject_id, total)
            remaining_teachers: Counter[str | None] = Counter({
                item["teacher_id"]: int(item["lessons"]) for item in allocations
            })
            remaining_teachers[None] = total - allocation_total(allocations)
            for slot in sorted(fixed_slots, key=lambda item: SLOT_META[item]["period_index"]):
                candidates = [
                    teacher_id
                    for teacher_id, count in remaining_teachers.items()
                    if count > 0
                ]
                candidates.sort(key=lambda teacher_id: (bool(teacher_id and slot in teacher_busy[teacher_id]), teacher_id or ""))
                teacher_id = candidates[0] if candidates else None
                remaining_teachers[teacher_id] -= 1
                lessons[class_id][slot] = _build_lesson(subject_id, teacher_id, fixed=True)
                if teacher_id:
                    previous_class = busy_owner.get((teacher_id, slot))
                    if previous_class:
                        day_name = next(day["name"] for day in DAYS if day["id"] == SLOT_META[slot]["day_id"])
                        period_name = next(period["name"] for period in PERIODS if period["id"] == SLOT_META[slot]["period_id"])
                        conflicts.append(
                            f"固定课冲突：{teacher_map.get(teacher_id, '未知教师')}在{day_name}{period_name}同时被分配到{previous_class}、{class_item['name']}。"
                        )
                    teacher_busy[teacher_id].add(slot)
                    busy_owner[(teacher_id, slot)] = class_item["name"]
    return lessons, teacher_busy, conflicts


def _schedule_one_class(
    class_item: dict[str, Any],
    assignments: dict[str, Any],
    class_lessons: dict[str, dict[str, Any]],
    teacher_busy: dict[str, set[str]],
    rng: random.Random,
    node_limit: int = 180_000,
) -> bool:
    class_id = class_item["id"]
    grade = int(class_item["grade"])
    remaining = Counter(CURRICULUM[grade])
    remaining_allocations: dict[str, Counter[str | None]] = {}
    for subject_id, total_lessons in CURRICULUM[grade].items():
        subject_allocations = _allocations_for(
            assignments, class_id, subject_id, total_lessons
        )
        shares: Counter[str | None] = Counter({
            item["teacher_id"]: int(item["lessons"])
            for item in subject_allocations
        })
        shares[None] = total_lessons - allocation_total(subject_allocations)
        remaining_allocations[subject_id] = shares
    for lesson in class_lessons.values():
        subject_id = lesson["subject_id"]
        teacher_id = lesson.get("teacher_id")
        remaining[subject_id] -= 1
        remaining_allocations[subject_id][teacher_id] -= 1
    if any(value < 0 for value in remaining.values()) or any(
        value < 0 for shares in remaining_allocations.values() for value in shares.values()
    ):
        return False

    empty_slots = set(slots_for_grade(grade)) - set(class_lessons)
    nodes = 0

    def available(subject_id: str, teacher_id: str | None, slot: str) -> bool:
        if (
            remaining[subject_id] <= 0
            or remaining_allocations[subject_id][teacher_id] <= 0
            or not _subject_allowed(grade, subject_id, slot)
        ):
            return False
        return not teacher_id or slot not in teacher_busy[teacher_id]

    def candidates_for(slot: str) -> list[tuple[str, str | None]]:
        return [
            (subject_id, teacher_id)
            for subject_id, shares in remaining_allocations.items()
            for teacher_id, count in shares.items()
            if count > 0 and available(subject_id, teacher_id, slot)
        ]

    def forward_check() -> bool:
        core_slots = sum(
            1 for slot in empty_slots if SLOT_META[slot]["period_id"] in {"am1", "am2"}
        )
        remaining_core = sum(remaining[subject_id] for subject_id in CORE_SUBJECTS)
        if remaining_core < core_slots:
            return False
        for subject_id, count in remaining.items():
            if count <= 0:
                continue
            possible = sum(
                1
                for slot in empty_slots
                if any(
                    share_count > 0 and available(subject_id, teacher_id, slot)
                    for teacher_id, share_count in remaining_allocations[subject_id].items()
                )
            )
            if possible < count:
                return False
            for teacher_id, share_count in remaining_allocations[subject_id].items():
                if share_count <= 0:
                    continue
                teacher_possible = sum(
                    1 for slot in empty_slots if available(subject_id, teacher_id, slot)
                )
                if teacher_possible < share_count:
                    return False
        return True

    def search() -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            return False
        if not empty_slots:
            return all(count == 0 for count in remaining.values())
        if not forward_check():
            return False

        selected_slot: str | None = None
        selected_candidates: list[tuple[str, str | None]] | None = None
        selected_priority = 999
        for slot in empty_slots:
            candidates = candidates_for(slot)
            if not candidates:
                return False
            period = SLOT_META[slot]["period_id"]
            priority = 0 if period in {"am1", "am2"} else (1 if period == "am3" else 2)
            if selected_candidates is None or (len(candidates), priority) < (len(selected_candidates), selected_priority):
                selected_slot = slot
                selected_candidates = candidates
                selected_priority = priority

        assert selected_slot is not None and selected_candidates is not None
        selected_candidates.sort(
            key=lambda candidate: _candidate_score(
                grade, candidate[0], selected_slot, class_lessons, rng
            ),
            reverse=True,
        )

        empty_slots.remove(selected_slot)
        for subject_id, teacher_id in selected_candidates:
            remaining[subject_id] -= 1
            remaining_allocations[subject_id][teacher_id] -= 1
            class_lessons[selected_slot] = _build_lesson(subject_id, teacher_id)
            if teacher_id:
                teacher_busy[teacher_id].add(selected_slot)

            if search():
                return True

            if teacher_id:
                teacher_busy[teacher_id].remove(selected_slot)
            del class_lessons[selected_slot]
            remaining[subject_id] += 1
            remaining_allocations[subject_id][teacher_id] += 1
        empty_slots.add(selected_slot)
        return False

    return search()


def _slot_order(slot: str) -> tuple[int, int]:
    meta = SLOT_META[slot]
    return int(meta["day_index"]), int(meta["period_index"])


def _best_interleaved_teacher_order(
    slots: list[str],
    original: list[str | None],
    teacher_busy: dict[str, set[str]],
) -> list[str | None]:
    """Reorder fixed teacher quotas across a subject's slots without creating conflicts."""
    remaining: Counter[str | None] = Counter(original)
    candidate_order = list(dict.fromkeys(original))
    best_cost = float("inf")
    best_sequence: list[str | None] | None = None

    def search(
        index: int,
        sequence: list[str | None],
        last_seen: dict[str, int],
        last_day: dict[str, int],
        cost: float,
    ) -> None:
        nonlocal best_cost, best_sequence
        if cost >= best_cost:
            return
        if index == len(slots):
            best_cost = cost
            best_sequence = list(sequence)
            return

        slot = slots[index]
        day_index = int(SLOT_META[slot]["day_index"])
        scored: list[tuple[float, str | None]] = []
        for teacher_id in candidate_order:
            if remaining[teacher_id] <= 0:
                continue
            if teacher_id and slot in teacher_busy.get(teacher_id, set()):
                continue
            added_cost = 0.2 if teacher_id != original[index] else 0.0
            if teacher_id:
                previous_index = last_seen.get(teacher_id)
                if previous_index is not None:
                    gap = index - previous_index
                    if gap == 1:
                        added_cost += 120
                    elif gap == 2:
                        added_cost += 25
                if last_day.get(teacher_id) == day_index:
                    added_cost += 45
            scored.append((added_cost, teacher_id))

        scored.sort(key=lambda item: (item[0], str(item[1] or "")))
        for added_cost, teacher_id in scored:
            remaining[teacher_id] -= 1
            previous_seen = last_seen.get(teacher_id) if teacher_id else None
            previous_day = last_day.get(teacher_id) if teacher_id else None
            if teacher_id:
                last_seen[teacher_id] = index
                last_day[teacher_id] = day_index
            sequence.append(teacher_id)
            search(index + 1, sequence, last_seen, last_day, cost + added_cost)
            sequence.pop()
            if teacher_id:
                if previous_seen is None:
                    last_seen.pop(teacher_id, None)
                else:
                    last_seen[teacher_id] = previous_seen
                if previous_day is None:
                    last_day.pop(teacher_id, None)
                else:
                    last_day[teacher_id] = previous_day
            remaining[teacher_id] += 1

    search(0, [], {}, {}, 0.0)
    return best_sequence or original


def _interleave_shared_subject_teachers(
    lessons: dict[str, dict[str, dict[str, Any]]]
) -> None:
    """Prefer alternating teachers for a shared subject while preserving all hard constraints."""
    teacher_busy: dict[str, set[str]] = defaultdict(set)
    for class_lessons in lessons.values():
        for slot, lesson in class_lessons.items():
            teacher_id = lesson.get("teacher_id")
            if teacher_id:
                teacher_busy[teacher_id].add(slot)

    for class_lessons in lessons.values():
        slots_by_subject: dict[str, list[str]] = defaultdict(list)
        for slot, lesson in class_lessons.items():
            slots_by_subject[lesson["subject_id"]].append(slot)
        for subject_slots in slots_by_subject.values():
            ordered_slots = sorted(subject_slots, key=_slot_order)
            original = [class_lessons[slot].get("teacher_id") for slot in ordered_slots]
            if len({teacher_id for teacher_id in original if teacher_id}) < 2:
                continue
            for slot, teacher_id in zip(ordered_slots, original):
                if teacher_id:
                    teacher_busy[teacher_id].discard(slot)
            optimized = _best_interleaved_teacher_order(ordered_slots, original, teacher_busy)
            for slot, teacher_id in zip(ordered_slots, optimized):
                class_lessons[slot]["teacher_id"] = teacher_id
                if teacher_id:
                    teacher_busy[teacher_id].add(slot)


def _class_difficulty(class_item: dict[str, Any], state: dict[str, Any], assigned_loads: dict[str, int]) -> int:
    score = 0
    class_assignments = state.get("assignments", {}).get(class_item["id"], {})
    for subject_id, hours in CURRICULUM[int(class_item["grade"])].items():
        for allocation in normalize_allocations(class_assignments.get(subject_id), hours):
            score += int(allocation["lessons"]) * assigned_loads.get(allocation["teacher_id"], 0)
    return score


def _build_warnings_and_quality(
    state: dict[str, Any], lessons: dict[str, dict[str, dict[str, Any]]], base_warnings: list[str]
) -> tuple[list[str], dict[str, Any]]:
    warnings = list(dict.fromkeys(base_warnings))
    assignments = state.get("assignments", {})
    teachers = {teacher["id"]: teacher for teacher in state.get("teachers", [])}
    small_classes_by_teacher: dict[str, set[str]] = defaultdict(set)
    small_subjects_by_teacher: dict[str, set[str]] = defaultdict(set)
    shared_groups: dict[tuple[str, str, int], list[str]] = defaultdict(list)

    unassigned_pairs = 0
    unassigned_lessons = 0
    for class_item in state.get("classes", []):
        for subject_id, hours in CURRICULUM[int(class_item["grade"])].items():
            allocations = _allocations_for(assignments, class_item["id"], subject_id, hours)
            assigned = allocation_total(allocations)
            if assigned < hours:
                unassigned_pairs += 1
                unassigned_lessons += hours - assigned
            for allocation in allocations:
                if subject_id in SMALL_CLASS_SUBJECTS:
                    small_classes_by_teacher[allocation["teacher_id"]].add(class_item["id"])
                    small_subjects_by_teacher[allocation["teacher_id"]].add(subject_id)
                shared_groups[(allocation["teacher_id"], subject_id, int(class_item["grade"]))].append(class_item["name"])
    if unassigned_pairs:
        warnings.append(
            f"有{unassigned_pairs}项班级课程（共{unassigned_lessons}节）尚未分配教师；这些课程未参与教师冲突校验。"
        )

    actual_loads: Counter[str] = Counter()
    for class_lessons in lessons.values():
        for lesson in class_lessons.values():
            if lesson.get("teacher_id"):
                actual_loads[lesson["teacher_id"]] += 1

    teacher_loads: list[dict[str, Any]] = []
    small_teacher_stats: list[dict[str, Any]] = []
    for teacher in state.get("teachers", []):
        actual = actual_loads[teacher["id"]]
        configured_minimum = max(0, int(teacher.get("min_weekly_lessons", 0)))
        small_class_count = len(small_classes_by_teacher.get(teacher["id"], set()))
        is_small_teacher = small_class_count > 0
        minimum = max(configured_minimum, 12 if is_small_teacher else 0)
        status = "达标" if minimum == 0 or actual >= minimum else "不足"
        teacher_loads.append({
            "teacher_id": teacher["id"],
            "name": teacher["name"],
            "actual": actual,
            "minimum": minimum,
            "configured_minimum": configured_minimum,
            "status": status,
        })
        if is_small_teacher:
            small_teacher_stats.append({
                "teacher_id": teacher["id"],
                "name": teacher["name"],
                "class_count": small_class_count,
                "lesson_count": actual,
                "subject_ids": sorted(small_subjects_by_teacher[teacher["id"]]),
            })
            if small_class_count < 6:
                warnings.append(f"{teacher['name']}承担小课的班级数为{small_class_count}个，低于建议的6个班。")
            elif small_class_count > 12:
                warnings.append(f"{teacher['name']}承担小课的班级数为{small_class_count}个，超过建议的12个班。")
        if configured_minimum and actual < configured_minimum:
            warnings.append(f"{teacher['name']}当前周课时为{actual}节，低于期望的{configured_minimum}节。")
        elif is_small_teacher and actual < 12:
            warnings.append(f"{teacher['name']}当前周课时为{actual}节，低于小课教师建议的12节。")

    third_total = 0
    third_core = 0
    for class_item in state.get("classes", []):
        if int(class_item["grade"]) < 3:
            continue
        for day in DAYS:
            third_total += 1
            lesson = lessons[class_item["id"]].get(f"{day['id']}-am3")
            if lesson and lesson["subject_id"] in CORE_SUBJECTS:
                third_core += 1

    quality = {
        "morning_third_core": third_core,
        "morning_third_total": third_total,
        "morning_third_rate": round(third_core / third_total, 3) if third_total else 1,
        "teacher_loads": teacher_loads,
        "small_course_teacher_stats": small_teacher_stats,
        "shared_teacher_groups": [
            {
                "teacher_id": teacher_id,
                "teacher_name": teachers.get(teacher_id, {}).get("name", "未知教师"),
                "subject_id": subject_id,
                "grade": grade,
                "class_names": class_names,
            }
            for (teacher_id, subject_id, grade), class_names in shared_groups.items()
            if len(class_names) > 1
        ],
        "teacher_conflicts": 0,
        "unassigned_course_pairs": unassigned_pairs,
        "unassigned_lessons": unassigned_lessons,
    }
    return warnings, quality


def rebuild_schedule_analysis(
    state: dict[str, Any], lessons: dict[str, dict[str, dict[str, Any]]]
) -> tuple[list[str], dict[str, Any]]:
    """Recalculate warnings and quality after a validated manual edit."""
    _, base_warnings, _ = _validate_inputs(state)
    return _build_warnings_and_quality(state, lessons, base_warnings)


def generate_schedule(state: dict[str, Any], seed: int | None = None, attempts: int = 80) -> dict[str, Any]:
    working_state = deepcopy(state)
    apply_required_teacher_assignments(working_state)
    errors, warnings, assigned_loads = _validate_inputs(working_state)
    fixed_lessons, fixed_teacher_busy, fixed_conflicts = _prepare_fixed_lessons(working_state)
    errors.extend(fixed_conflicts)
    if errors:
        return {"success": False, "errors": errors, "warnings": warnings}

    base_seed = int(seed if seed is not None else random.SystemRandom().randint(1, 2_147_483_647))
    attempts = max(1, min(int(attempts), 300))
    classes = working_state.get("classes", [])

    for attempt in range(1, attempts + 1):
        rng = random.Random(base_seed + attempt * 104_729)
        lessons = deepcopy(fixed_lessons)
        teacher_busy = defaultdict(set, {teacher_id: set(slots) for teacher_id, slots in fixed_teacher_busy.items()})
        class_order = list(classes)
        rng.shuffle(class_order)
        class_order.sort(
            key=lambda item: _class_difficulty(item, working_state, assigned_loads) + rng.random(),
            reverse=True,
        )

        solved = True
        for class_item in class_order:
            if not _schedule_one_class(
                class_item,
                working_state.get("assignments", {}),
                lessons[class_item["id"]],
                teacher_busy,
                rng,
            ):
                solved = False
                break

        if solved:
            _interleave_shared_subject_teachers(lessons)
            final_warnings, quality = _build_warnings_and_quality(working_state, lessons, warnings)
            return {
                "success": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "seed": base_seed,
                "attempt": attempt,
                "warnings": final_warnings,
                "quality": quality,
                "lessons": lessons,
            }

    return {
        "success": False,
        "errors": [
            f"在{attempts}次尝试内未找到无教师冲突的课表。请检查教师是否承担过多班级，或固定课是否由同一教师同时承担。"
        ],
        "warnings": warnings,
        "seed": base_seed,
    }
