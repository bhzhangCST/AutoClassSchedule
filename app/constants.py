from __future__ import annotations

from copy import deepcopy

DAYS = [
    {"id": "mon", "name": "星期一"},
    {"id": "tue", "name": "星期二"},
    {"id": "wed", "name": "星期三"},
    {"id": "thu", "name": "星期四"},
    {"id": "fri", "name": "星期五"},
]

PERIODS = [
    {"id": "am1", "name": "上午第1节", "section": "上午", "order": 1},
    {"id": "am2", "name": "上午第2节", "section": "上午", "order": 2},
    {"id": "am3", "name": "上午第3节", "section": "上午", "order": 3},
    {"id": "am4", "name": "上午第4节", "section": "上午", "order": 4},
    {"id": "pm1", "name": "下午第1节", "section": "下午", "order": 1},
    {"id": "pm2", "name": "下午第2节", "section": "下午", "order": 2},
    {"id": "pm3", "name": "下午第3节", "section": "下午", "order": 3},
]

SUBJECTS = [
    {"id": "chinese", "name": "语文", "short": "语文", "group": "core", "color": "#f97316"},
    {"id": "math", "name": "数学", "short": "数学", "group": "core", "color": "#2563eb"},
    {"id": "english", "name": "英语", "short": "英语", "group": "core", "color": "#8b5cf6"},
    {"id": "morality", "name": "道德与法治", "short": "道法", "group": "special", "color": "#db2777"},
    {"id": "science", "name": "科学", "short": "科学", "group": "special", "color": "#059669"},
    {"id": "it", "name": "信息科技", "short": "信息", "group": "special", "color": "#0891b2"},
    {"id": "pe", "name": "体育与健康", "short": "体育", "group": "special", "color": "#16a34a"},
    {"id": "music", "name": "艺术（音乐）", "short": "音乐", "group": "special", "color": "#c026d3"},
    {"id": "art", "name": "艺术（美术）", "short": "美术", "group": "special", "color": "#e11d48"},
    {"id": "labor", "name": "劳动", "short": "劳动", "group": "special", "color": "#a16207"},
    {"id": "practice", "name": "综合实践活动", "short": "实践", "group": "special", "color": "#0d9488"},
    {"id": "local", "name": "地方课程", "short": "地方", "group": "special", "color": "#4f46e5"},
    {"id": "school", "name": "校本课程", "short": "校本", "group": "special", "color": "#7c3aed"},
    {"id": "reading", "name": "阅读", "short": "阅读", "group": "activity", "color": "#d97706"},
    {"id": "fun", "name": "趣味课堂", "short": "趣味", "group": "activity", "color": "#ea580c"},
    {"id": "meeting", "name": "班队会（安全课）", "short": "班会", "group": "activity", "color": "#475569"},
]

SUBJECT_BY_ID = {subject["id"]: subject for subject in SUBJECTS}
CORE_SUBJECTS = {"chinese", "math", "english"}
SMALL_CLASS_SUBJECTS = {"music", "pe", "art", "morality", "science", "local", "labor", "it"}

# Source: 高唐县民族实验小学课程安排标准 2025.8
CURRICULUM = {
    1: {"chinese": 8, "math": 3, "morality": 2, "science": 1, "pe": 4, "music": 2, "art": 2, "labor": 1, "practice": 1, "local": 1, "school": 1, "reading": 2, "fun": 1, "meeting": 1},
    2: {"chinese": 8, "math": 3, "morality": 2, "science": 1, "pe": 4, "music": 2, "art": 2, "labor": 1, "practice": 1, "local": 1, "school": 1, "reading": 2, "fun": 1, "meeting": 1},
    3: {"chinese": 7, "math": 5, "english": 2, "morality": 2, "science": 2, "it": 1, "pe": 3, "music": 2, "art": 2, "labor": 1, "practice": 1, "local": 1, "school": 1, "reading": 2, "fun": 2, "meeting": 1},
    4: {"chinese": 7, "math": 5, "english": 2, "morality": 2, "science": 2, "it": 1, "pe": 3, "music": 2, "art": 2, "labor": 1, "practice": 1, "local": 1, "school": 1, "reading": 2, "fun": 2, "meeting": 1},
    5: {"chinese": 6, "math": 5, "english": 3, "morality": 2, "science": 2, "it": 1, "pe": 3, "music": 2, "art": 2, "labor": 1, "practice": 1, "local": 1, "school": 1, "reading": 2, "fun": 2, "meeting": 1},
    6: {"chinese": 6, "math": 5, "english": 3, "morality": 3, "science": 2, "it": 1, "pe": 3, "music": 1, "art": 1, "labor": 1, "practice": 1, "local": 2, "school": 1, "reading": 2, "fun": 2, "meeting": 1},
}

GRADE_NAMES = {1: "一年级", 2: "二年级", 3: "三年级", 4: "四年级", 5: "五年级", 6: "六年级"}

RULE_DESCRIPTIONS = [
    {"level": "hard", "title": "班会固定", "detail": "1—6年级每周一下午第3节安排班队会（安全课）；已配置班主任时由班主任承担，否则教师留空。"},
    {"level": "hard", "title": "低年级阅读", "detail": "1—2年级每周三下午安排两节阅读连堂，可使用前两节或后两节；已配置语文教师时由语文教师承担，否则教师留空。"},
    {"level": "hard", "title": "中高年级阅读", "detail": "3—6年级每周四下午安排两节阅读连堂，可使用前两节或后两节；已配置语文教师时由语文教师承担，否则教师留空。"},
    {"level": "hard", "title": "趣味课堂", "detail": "3—6年级每周三下午前两节安排趣味课堂。"},
    {"level": "hard", "title": "教研避让", "detail": "承担英语、语文、数学的教师，分别在周一、周二、周四下午前两节不得安排任何课程（含兼任小课）。"},
    {"level": "hard", "title": "上午核心课", "detail": "每天上午前两节只安排语数英；1—2年级只安排语数。"},
    {"level": "hard", "title": "兄弟班与教师错峰", "detail": "数学教师跨两个班、英语教师跨三个班或小课教师跨多个班时，同一课时只能在一个班任课。"},
    {"level": "soft", "title": "上午第3节优先", "detail": "3—6年级上午第3节尽量安排语数英。"},
    {"level": "soft", "title": "副科跨天分散", "detail": "体育、音乐、美术、科学等每周多于1节的副科尽量安排在不同日期，避免同一天重复。"},
    {"level": "soft", "title": "教师课时均衡", "detail": "教师承担的全部课程按周一至周五合并计算，尽量均匀分布，避免单日过密或整日无课。"},
    {"level": "soft", "title": "兼课教师末节减压", "detail": "同时承担语数英和其他课程的教师，尽量避开上午第4节、下午第3节；遇固定课位或教师冲突时允许安排。"},
    {"level": "soft", "title": "小课教师任课量", "detail": "音体美、道法、科学、地方、劳动、信息教师的任课班级数和周课时仅作配置参考统计。"},
    {"level": "soft", "title": "教师工作量", "detail": "教师最低周课时尽量达到配置值；不足时生成警告，不阻止排课。"},
]


def curriculum_for_grade(grade: int) -> dict[str, int]:
    return deepcopy(CURRICULUM[grade])


def subject_metadata() -> list[dict[str, object]]:
    return deepcopy(SUBJECTS)


def slot_key(day_id: str, period_id: str) -> str:
    return f"{day_id}-{period_id}"


def slots_for_grade(grade: int) -> list[str]:
    period_ids = ["am1", "am2", "am3", "pm1", "pm2", "pm3"] if grade <= 2 else [period["id"] for period in PERIODS]
    return [slot_key(day["id"], period_id) for day in DAYS for period_id in period_ids]


def fixed_lessons_for_grade(grade: int) -> dict[str, str]:
    fixed = {slot_key("mon", "pm3"): "meeting"}
    if grade >= 3:
        fixed[slot_key("wed", "pm1")] = "fun"
        fixed[slot_key("wed", "pm2")] = "fun"
    return fixed


def reading_slot_pairs_for_grade(grade: int) -> list[tuple[str, str]]:
    day_id = "wed" if grade <= 2 else "thu"
    return [
        (slot_key(day_id, "pm1"), slot_key(day_id, "pm2")),
        (slot_key(day_id, "pm2"), slot_key(day_id, "pm3")),
    ]
