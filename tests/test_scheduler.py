from __future__ import annotations

from collections import Counter, defaultdict

from app.assignment_rules import apply_required_teacher_assignments
from app.constants import CORE_SUBJECTS, CURRICULUM, DAYS, SMALL_CLASS_SUBJECTS, fixed_lessons_for_grade, slots_for_grade
from app.schedule_edit import ScheduleSwapError, apply_schedule_swaps
from app.scheduler import (
    RESEARCH_SLOTS_BY_SUBJECT,
    SLOT_META,
    _optimize_teacher_daily_balance,
    _teacher_daily_balance_penalty,
    generate_schedule,
)
from app.store import default_state, normalize_state


def _configure_required_teachers(state: dict) -> None:
    for class_item in state["classes"]:
        teacher_id = f"t_required_{class_item['id']}"
        state["teachers"].append({
            "id": teacher_id,
            "name": f"{class_item['name']}班主任",
            "subject_ids": ["chinese"],
            "min_weekly_lessons": 0,
            "homeroom_class_id": class_item["id"],
        })
        state["assignments"][class_item["id"]]["chinese"] = teacher_id
    apply_required_teacher_assignments(state)


def test_default_state_generates_complete_schedule() -> None:
    state = default_state()
    _configure_required_teachers(state)
    assert len(state["classes"]) == 36
    assert Counter(item["grade"] for item in state["classes"]) == Counter({grade: 6 for grade in range(1, 7)})
    result = generate_schedule(state, seed=202508, attempts=12)
    assert result["success"], result.get("errors")

    for class_item in state["classes"]:
        lessons = result["lessons"][class_item["id"]]
        grade = class_item["grade"]
        assert set(lessons) == set(slots_for_grade(grade))
        counts = Counter(lesson["subject_id"] for lesson in lessons.values())
        assert counts == Counter(CURRICULUM[grade])
        for slot, subject_id in fixed_lessons_for_grade(grade).items():
            assert lessons[slot]["subject_id"] == subject_id
        for day in DAYS:
            for period in ("am1", "am2"):
                assert lessons[f"{day['id']}-{period}"]["subject_id"] in CORE_SUBJECTS


def test_old_allocation_metadata_is_removed_during_migration() -> None:
    state = default_state()
    state["schema_version"] = 4
    state["teachers"] = [{
        "id": "t_legacy",
        "name": "旧版语文教师",
        "subject_ids": ["chinese"],
        "min_weekly_lessons": 0,
        "homeroom_class_id": None,
    }]
    state["assignments"]["g1c1"]["chinese"] = [
        {"teacher_id": "t_legacy", "lessons": 8, "primary": True}
    ]

    assert normalize_state(state) is True
    assert state["schema_version"] == 5
    assert state["assignments"]["g1c1"]["chinese"] == [
        {"teacher_id": "t_legacy", "lessons": 8}
    ]
    assert state["assignments"]["g1c1"]["reading"] == [
        {"teacher_id": "t_legacy", "lessons": 2}
    ]


def test_reading_uses_the_chinese_teacher_with_the_most_lessons() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}
    state["teachers"] = [
        {"id": "t_chinese_minor", "name": "语文教师乙", "subject_ids": ["chinese"], "min_weekly_lessons": 0, "homeroom_class_id": None},
        {"id": "t_chinese_major", "name": "语文教师甲", "subject_ids": ["chinese"], "min_weekly_lessons": 0, "homeroom_class_id": None},
    ]
    state["assignments"]["g1c1"]["chinese"] = [
        {"teacher_id": "t_chinese_minor", "lessons": 2},
        {"teacher_id": "t_chinese_major", "lessons": 6},
    ]

    apply_required_teacher_assignments(state)

    assert state["assignments"]["g1c1"]["reading"] == [
        {"teacher_id": "t_chinese_major", "lessons": 2}
    ]


def test_schedule_allows_missing_chinese_teacher_and_homeroom() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}

    result = generate_schedule(state, seed=20260822, attempts=12)

    assert result["success"], result.get("errors")
    lessons = result["lessons"]["g1c1"]
    assert lessons["wed-pm2"]["subject_id"] == "reading"
    assert lessons["wed-pm2"]["teacher_id"] is None
    assert lessons["mon-pm3"]["subject_id"] == "meeting"
    assert lessons["mon-pm3"]["teacher_id"] is None


def test_fixed_teacher_conflict_is_reported() -> None:
    state = default_state()
    state["classes"] = state["classes"][:2]
    state["assignments"] = {item["id"]: state["assignments"][item["id"]] for item in state["classes"]}
    state["teachers"] = [
        {"id": "t_chinese", "name": "共享语文教师", "subject_ids": ["chinese"], "min_weekly_lessons": 0, "homeroom_class_id": "g1c1"},
        {"id": "t_home2", "name": "二班班主任", "subject_ids": [], "min_weekly_lessons": 0, "homeroom_class_id": "g1c2"},
    ]
    state["assignments"]["g1c1"]["chinese"] = "t_chinese"
    state["assignments"]["g1c2"]["chinese"] = "t_chinese"
    apply_required_teacher_assignments(state)

    result = generate_schedule(state, seed=1, attempts=2)
    assert not result["success"]
    assert any("固定课冲突" in error for error in result["errors"])


def test_shared_teacher_never_double_books_and_low_load_warns() -> None:
    state = default_state()
    _configure_required_teachers(state)
    state["teachers"].append({"id": "t_math", "name": "数学教师", "subject_ids": ["math"], "min_weekly_lessons": 12, "homeroom_class_id": None})
    state["assignments"]["g1c1"]["math"] = "t_math"
    state["assignments"]["g1c2"]["math"] = "t_math"

    result = generate_schedule(state, seed=33, attempts=30)
    assert result["success"], result.get("errors")
    busy: dict[str, list[str]] = defaultdict(list)
    for class_lessons in result["lessons"].values():
        for slot, lesson in class_lessons.items():
            if lesson.get("teacher_id"):
                busy[slot].append(lesson["teacher_id"])
    assert all(len(values) == len(set(values)) for values in busy.values())
    assert any("低于期望" in warning for warning in result["warnings"])


def test_small_course_teacher_can_cover_six_sibling_classes_without_conflict() -> None:
    state = default_state()
    state["classes"] = [item for item in state["classes"] if item["grade"] == 1]
    state["assignments"] = {item["id"]: state["assignments"][item["id"]] for item in state["classes"]}
    _configure_required_teachers(state)
    state["teachers"].append({"id": "t_art", "name": "美术教师", "subject_ids": ["art"], "min_weekly_lessons": 0, "homeroom_class_id": None})
    for class_item in state["classes"]:
        state["assignments"][class_item["id"]]["art"] = "t_art"

    result = generate_schedule(state, seed=202508, attempts=80)
    assert result["success"], result.get("errors")
    art_slots = [
        slot
        for class_lessons in result["lessons"].values()
        for slot, lesson in class_lessons.items()
        if lesson.get("teacher_id") == "t_art"
    ]
    assert len(art_slots) == 12
    assert len(art_slots) == len(set(art_slots))
    assert result["quality"]["small_course_teacher_stats"][0]["class_count"] == 6
    assert not any("美术教师承担小课的班级数" in warning for warning in result["warnings"])
    assert result["quality"]["shared_teacher_groups"][0]["class_names"] == [f"一年级{number}班" for number in range(1, 7)]
    daily = Counter(SLOT_META[slot]["day_id"] for slot in art_slots)
    assert max(daily.values()) - min(daily.values()) <= 1
    balance = next(item for item in result["quality"]["teacher_daily_balance"] if item["teacher_id"] == "t_art")
    assert balance["spread"] <= 1


def test_small_course_recommendation_shortfall_is_not_reported() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}
    _configure_required_teachers(state)
    state["teachers"].append({"id": "t_music", "name": "音乐教师", "subject_ids": ["music"], "min_weekly_lessons": 0, "homeroom_class_id": None})
    state["assignments"]["g1c1"]["music"] = "t_music"

    result = generate_schedule(state, seed=17, attempts=12)
    assert result["success"], result.get("errors")
    assert not any("低于建议的6个班" in warning for warning in result["warnings"])
    assert not any("低于小课教师建议的12节" in warning for warning in result["warnings"])

    load = next(item for item in result["quality"]["teacher_loads"] if item["teacher_id"] == "t_music")
    assert load["minimum"] == 0
    assert load["status"] == "达标"

    stats = next(item for item in result["quality"]["small_course_teacher_stats"] if item["teacher_id"] == "t_music")
    assert stats["class_count"] == 1
    assert stats["lesson_count"] == 2


def test_english_teacher_over_three_classes_warns_but_still_schedules() -> None:
    state = default_state()
    state["classes"] = [item for item in state["classes"] if item["grade"] == 3][:4]
    state["assignments"] = {item["id"]: state["assignments"][item["id"]] for item in state["classes"]}
    _configure_required_teachers(state)
    state["teachers"].append({"id": "t_english", "name": "英语教师", "subject_ids": ["english"], "min_weekly_lessons": 0, "homeroom_class_id": None})
    for class_item in state["classes"]:
        state["assignments"][class_item["id"]]["english"] = "t_english"

    result = generate_schedule(state, seed=91, attempts=40)
    assert result["success"], result.get("errors")
    assert any("超过注意事项中常见的3个兄弟班" in warning for warning in result["warnings"])


def test_multi_lesson_special_subjects_are_spread_across_days() -> None:
    state = default_state()
    state["classes"] = [state["classes"][12]]  # 三年级1班
    state["assignments"] = {"g3c1": state["assignments"]["g3c1"]}
    _configure_required_teachers(state)
    result = generate_schedule(state, seed=20260822, attempts=40)
    assert result["success"], result.get("errors")

    lessons = result["lessons"]["g3c1"]
    for subject_id, hours in CURRICULUM[3].items():
        if subject_id not in SMALL_CLASS_SUBJECTS or hours <= 1:
            continue
        lesson_days = {
            SLOT_META[slot]["day_id"]
            for slot, lesson in lessons.items()
            if lesson["subject_id"] == subject_id
        }
        assert len(lesson_days) == min(hours, len(DAYS)), subject_id


def test_reading_and_meeting_follow_required_teachers() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}
    _configure_required_teachers(state)
    required_teacher = state["teachers"][0]["id"]

    result = generate_schedule(state, seed=77, attempts=20)
    assert result["success"], result.get("errors")
    lessons = result["lessons"]["g1c1"]
    assert lessons["wed-pm2"]["teacher_id"] == required_teacher
    assert lessons["wed-pm3"]["teacher_id"] == required_teacher
    assert lessons["mon-pm3"]["teacher_id"] == required_teacher


def test_one_subject_can_be_split_between_multiple_teachers_by_exact_lesson_count() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}
    state["teachers"] = [
        {"id": "t_pe_2", "name": "体育教师", "subject_ids": ["pe"], "min_weekly_lessons": 0, "homeroom_class_id": None},
        {"id": "t_pe_1", "name": "代课教师甲", "subject_ids": ["pe"], "min_weekly_lessons": 0, "homeroom_class_id": None},
        {"id": "t_pe_1b", "name": "代课教师乙", "subject_ids": ["pe"], "min_weekly_lessons": 0, "homeroom_class_id": None},
    ]
    state["assignments"]["g1c1"]["pe"] = [
        {"teacher_id": "t_pe_2", "lessons": 2},
        {"teacher_id": "t_pe_1", "lessons": 1},
        {"teacher_id": "t_pe_1b", "lessons": 1},
    ]

    result = generate_schedule(state, seed=20260827, attempts=20)

    assert result["success"], result.get("errors")
    pe_teachers = Counter(
        lesson["teacher_id"]
        for lesson in result["lessons"]["g1c1"].values()
        if lesson["subject_id"] == "pe"
    )
    assert pe_teachers == Counter({"t_pe_2": 2, "t_pe_1": 1, "t_pe_1b": 1})
    ordered_pe_teachers = [
        result["lessons"]["g1c1"][slot]["teacher_id"]
        for slot in sorted(
            (
                slot
                for slot, lesson in result["lessons"]["g1c1"].items()
                if lesson["subject_id"] == "pe"
            ),
            key=lambda slot: (SLOT_META[slot]["day_index"], SLOT_META[slot]["period_index"]),
        )
    ]
    assert all(left != right for left, right in zip(ordered_pe_teachers, ordered_pe_teachers[1:]))
    assert result["quality"]["unassigned_lessons"] == sum(CURRICULUM[1].values()) - 4


def test_core_teacher_research_time_blocks_every_subject_and_manual_swaps() -> None:
    state = default_state()
    class_item = next(item for item in state["classes"] if item["id"] == "g3c1")
    state["classes"] = [class_item]
    state["assignments"] = {"g3c1": state["assignments"]["g3c1"]}
    teacher_subjects = {
        "t_chinese_pe": "chinese",
        "t_math_pe": "math",
        "t_english_pe": "english",
    }
    state["teachers"] = [
        {
            "id": teacher_id,
            "name": f"{subject_id}兼体育教师",
            "subject_ids": [subject_id, "pe"],
            "min_weekly_lessons": 0,
            "homeroom_class_id": None,
        }
        for teacher_id, subject_id in teacher_subjects.items()
    ]
    for teacher_id, subject_id in teacher_subjects.items():
        state["assignments"]["g3c1"][subject_id] = teacher_id
    state["assignments"]["g3c1"]["pe"] = [
        {"teacher_id": teacher_id, "lessons": 1} for teacher_id in teacher_subjects
    ]
    apply_required_teacher_assignments(state)

    result = generate_schedule(state, seed=4242, attempts=40)
    assert result["success"], result.get("errors")
    lessons = result["lessons"]["g3c1"]
    for teacher_id, core_subject in teacher_subjects.items():
        assert not [
            slot
            for slot, lesson in lessons.items()
            if lesson.get("teacher_id") == teacher_id
            and slot in RESEARCH_SLOTS_BY_SUBJECT[core_subject]
        ]

    state["schedule"] = result
    chinese_pe_slot = next(
        slot
        for slot, lesson in lessons.items()
        if lesson.get("teacher_id") == "t_chinese_pe" and lesson["subject_id"] == "pe"
    )
    try:
        apply_schedule_swaps(
            state,
            "g3c1",
            [{"from_slot": chinese_pe_slot, "to_slot": "tue-pm1"}],
        )
    except ScheduleSwapError as exc:
        assert any("教研时段" in issue and "不能安排任何课程" in issue for issue in exc.issues)
    else:
        raise AssertionError("兼任小课不应允许交换到该教师的主科教研时段")


def test_global_balance_repair_can_swap_across_periods() -> None:
    state = default_state()
    state["classes"] = [
        item for item in state["classes"] if item["id"] in {"g2c3", "g2c4"}
    ]
    lessons = {
        "g2c3": {
            "fri-am2": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
            "mon-pm2": {"subject_id": "fun", "teacher_id": "t_mix", "fixed": False},
            "tue-am2": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
            "tue-pm1": {"subject_id": "pe", "teacher_id": "t_mix", "fixed": False},
            "fri-pm3": {"subject_id": "pe", "teacher_id": "t_mix", "fixed": False},
            "wed-am2": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
        },
        "g2c4": {
            "fri-am3": {"subject_id": "pe", "teacher_id": "t_mix", "fixed": False},
            "thu-am2": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
            "thu-am3": {"subject_id": "fun", "teacher_id": "t_mix", "fixed": False},
            "tue-pm2": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
            "tue-pm3": {"subject_id": "pe", "teacher_id": "t_mix", "fixed": False},
            "wed-am1": {"subject_id": "math", "teacher_id": "t_mix", "fixed": False},
            "mon-pm1": {"subject_id": "practice", "teacher_id": "t_other", "fixed": False},
        },
    }
    before_subjects = {
        class_id: Counter(lesson["subject_id"] for lesson in class_lessons.values())
        for class_id, class_lessons in lessons.items()
    }

    stats = _optimize_teacher_daily_balance(
        state,
        lessons,
        {"t_mix": {"thu-pm1", "thu-pm2"}},
    )

    daily = Counter(
        SLOT_META[slot]["day_id"]
        for class_lessons in lessons.values()
        for slot, lesson in class_lessons.items()
        if lesson.get("teacher_id") == "t_mix"
    )
    assert sorted(daily[day["id"]] for day in DAYS) == [2, 2, 2, 3, 3]
    assert stats["direct_swaps"] >= 1
    assert stats["before_total_penalty"] == 2
    assert stats["after_total_penalty"] == 0
    assert {
        class_id: Counter(lesson["subject_id"] for lesson in class_lessons.values())
        for class_id, class_lessons in lessons.items()
    } == before_subjects


def test_global_balance_repair_can_use_a_two_step_cross_class_chain() -> None:
    state = default_state()
    state["classes"] = [
        item for item in state["classes"] if item["id"] in {"g2c3", "g2c4", "g2c5"}
    ]
    lessons = {
        "g2c3": {
            "tue-pm1": {"subject_id": "pe", "teacher_id": "t_target", "fixed": False},
            "mon-pm1": {"subject_id": "practice", "teacher_id": "t_blocker", "fixed": False},
        },
        "g2c4": {
            "tue-pm1": {"subject_id": "math", "teacher_id": "t_blocker", "fixed": False},
            "wed-pm1": {"subject_id": "science", "teacher_id": "t_release", "fixed": False},
        },
        "g2c5": {
            "tue-pm2": {"subject_id": "math", "teacher_id": "t_target", "fixed": False},
            "wed-am2": {"subject_id": "math", "teacher_id": "t_target", "fixed": False},
            "thu-am2": {"subject_id": "math", "teacher_id": "t_target", "fixed": False},
        },
    }
    before_teacher_counts = Counter(
        lesson.get("teacher_id")
        for class_lessons in lessons.values()
        for lesson in class_lessons.values()
    )

    stats = _optimize_teacher_daily_balance(state, lessons, {})

    target_daily = Counter(
        SLOT_META[slot]["day_id"]
        for class_lessons in lessons.values()
        for slot, lesson in class_lessons.items()
        if lesson.get("teacher_id") == "t_target"
    )
    assert sorted(target_daily[day["id"]] for day in DAYS) == [0, 1, 1, 1, 1]
    assert stats["direct_swaps"] == 0
    assert stats["chain_operations"] == 1
    assert stats["applied_swaps"] == 2
    assert _teacher_daily_balance_penalty(lessons) == 0
    assert Counter(
        lesson.get("teacher_id")
        for class_lessons in lessons.values()
        for lesson in class_lessons.values()
    ) == before_teacher_counts
