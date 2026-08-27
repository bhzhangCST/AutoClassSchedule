from __future__ import annotations

from collections import Counter, defaultdict

from app.assignment_rules import apply_required_teacher_assignments
from app.constants import CORE_SUBJECTS, CURRICULUM, DAYS, SMALL_CLASS_SUBJECTS, fixed_lessons_for_grade, slots_for_grade
from app.scheduler import SLOT_META, generate_schedule
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


def test_small_course_teacher_shortfall_is_a_warning() -> None:
    state = default_state()
    state["classes"] = [state["classes"][0]]
    state["assignments"] = {"g1c1": state["assignments"]["g1c1"]}
    _configure_required_teachers(state)
    state["teachers"].append({"id": "t_music", "name": "音乐教师", "subject_ids": ["music"], "min_weekly_lessons": 0, "homeroom_class_id": None})
    state["assignments"]["g1c1"]["music"] = "t_music"

    result = generate_schedule(state, seed=17, attempts=12)
    assert result["success"], result.get("errors")
    assert any("低于建议的6个班" in warning for warning in result["warnings"])
    assert any("低于小课教师建议的12节" in warning for warning in result["warnings"])


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
