from __future__ import annotations

from collections import Counter, defaultdict

from app.constants import CORE_SUBJECTS, CURRICULUM, DAYS, SMALL_CLASS_SUBJECTS, fixed_lessons_for_grade, slots_for_grade
from app.scheduler import SLOT_META, generate_schedule
from app.store import default_state


def test_default_state_generates_complete_schedule() -> None:
    state = default_state()
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


def test_fixed_teacher_conflict_is_reported() -> None:
    state = default_state()
    state["teachers"] = [{"id": "t_read", "name": "阅读教师", "subject_ids": ["reading"], "min_weekly_lessons": 0}]
    state["assignments"]["g1c1"]["reading"] = "t_read"
    state["assignments"]["g1c2"]["reading"] = "t_read"

    result = generate_schedule(state, seed=1, attempts=2)
    assert not result["success"]
    assert any("固定课冲突" in error for error in result["errors"])


def test_shared_teacher_never_double_books_and_low_load_warns() -> None:
    state = default_state()
    state["teachers"] = [{"id": "t_math", "name": "数学教师", "subject_ids": ["math"], "min_weekly_lessons": 12}]
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
    state["teachers"] = [{"id": "t_art", "name": "美术教师", "subject_ids": ["art"], "min_weekly_lessons": 0}]
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
    state["teachers"] = [{"id": "t_music", "name": "音乐教师", "subject_ids": ["music"], "min_weekly_lessons": 0}]
    state["assignments"]["g1c1"]["music"] = "t_music"

    result = generate_schedule(state, seed=17, attempts=12)
    assert result["success"], result.get("errors")
    assert any("低于建议的6个班" in warning for warning in result["warnings"])
    assert any("低于小课教师建议的12节" in warning for warning in result["warnings"])


def test_english_teacher_over_three_classes_warns_but_still_schedules() -> None:
    state = default_state()
    state["classes"] = [item for item in state["classes"] if item["grade"] == 3][:4]
    state["assignments"] = {item["id"]: state["assignments"][item["id"]] for item in state["classes"]}
    state["teachers"] = [{"id": "t_english", "name": "英语教师", "subject_ids": ["english"], "min_weekly_lessons": 0}]
    for class_item in state["classes"]:
        state["assignments"][class_item["id"]]["english"] = "t_english"

    result = generate_schedule(state, seed=91, attempts=40)
    assert result["success"], result.get("errors")
    assert any("超过注意事项中常见的3个兄弟班" in warning for warning in result["warnings"])


def test_multi_lesson_special_subjects_are_spread_across_days() -> None:
    state = default_state()
    state["classes"] = [state["classes"][12]]  # 三年级1班
    state["assignments"] = {"g3c1": state["assignments"]["g3c1"]}
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
