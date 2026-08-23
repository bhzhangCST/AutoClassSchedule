from __future__ import annotations

from functools import lru_cache
from html import escape
from io import BytesIO
from typing import Any

from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image as ReportLabImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import PROJECT_ROOT
from .constants import DAYS, PERIODS, SUBJECT_BY_ID, slots_for_grade

PDF_FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))

PAGE_SIZE = A4
PAGE_MARGIN = 10 * mm
GRID_COLOR = colors.HexColor("#555555")
HEADER_COLOR = colors.HexColor("#222222")
MUTED_COLOR = colors.HexColor("#888888")
LOGO_PATH = PROJECT_ROOT / "static" / "logo.jpg"


def _validated_schedule(state: dict[str, Any]) -> dict[str, Any]:
    schedule = state.get("schedule")
    if not schedule or not schedule.get("success"):
        raise ValueError("尚未生成可导出的课表")
    return schedule


def _paragraph(value: str, *, size: int = 12, leading: int | None = None, bold: bool = False) -> Paragraph:
    content = escape(value).replace("\n", "<br/>")
    if bold:
        content = f"<b>{content}</b>"
    return Paragraph(
        content,
        ParagraphStyle(
            name="Cell",
            fontName=PDF_FONT,
            fontSize=size,
            leading=leading or size + 4,
            alignment=TA_CENTER,
            textColor=HEADER_COLOR,
            wordWrap="CJK",
        ),
    )


def _title(value: str) -> Paragraph:
    return Paragraph(
        f"<b>{escape(value)}</b>",
        ParagraphStyle(
            name="Title",
            fontName=PDF_FONT,
            fontSize=20,
            leading=26,
            alignment=TA_CENTER,
            textColor=HEADER_COLOR,
            wordWrap="CJK",
        ),
    )


@lru_cache(maxsize=1)
def _pdf_logo_bytes() -> bytes:
    """Create a print-sized RGB copy so each exported PDF stays compact."""
    with PillowImage.open(LOGO_PATH) as source:
        source.thumbnail((600, 425), PillowImage.Resampling.LANCZOS)
        image = source.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()


def _page_header(value: str, subtitle: str | None = None) -> Table:
    usable_width = PAGE_SIZE[0] - 2 * PAGE_MARGIN
    title_width = usable_width - 52 * mm
    logo_width = 20 * mm
    logo_height = logo_width * 2480 / 3508
    logo = ReportLabImage(BytesIO(_pdf_logo_bytes()), width=logo_width, height=logo_height)
    title_block: Any = _title(value)
    header_height = 16 * mm
    if subtitle:
        title_block = Table(
            [[_title(value)], [_paragraph(subtitle, size=10, leading=12)]],
            colWidths=[title_width],
        )
        title_block.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        header_height = 20 * mm
    header = Table(
        [[logo, title_block, ""]],
        colWidths=[26 * mm, title_width, 26 * mm],
        rowHeights=[header_height],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def _schedule_table(cell_value: Any, unavailable_slots: set[str] | None = None) -> Table:
    unavailable_slots = unavailable_slots or set()
    rows: list[list[Any]] = [[
        _paragraph("时段", size=10, bold=True),
        _paragraph("节次", size=10, bold=True),
        *[_paragraph(day["name"], size=10, bold=True) for day in DAYS],
    ]]
    for index, period in enumerate(PERIODS):
        row: list[Any] = [
            _paragraph(period["section"] if index in (0, 4) else "", size=10, bold=True),
            _paragraph(f"第{period['order']}节", size=9, bold=True),
        ]
        for day in DAYS:
            slot = f"{day['id']}-{period['id']}"
            if slot in unavailable_slots:
                row.append(_paragraph("—", size=12))
            else:
                row.append(cell_value(slot))
        rows.append(row)

    usable_width = PAGE_SIZE[0] - 2 * PAGE_MARGIN
    table = Table(
        rows,
        colWidths=[14 * mm, 16 * mm, *[(usable_width - 30 * mm) / 5] * 5],
        rowHeights=[12 * mm, *[34.5 * mm] * len(PERIODS)],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, -1), HEADER_COLOR),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.7, GRID_COLOR),
        ("LINEBELOW", (0, 0), (-1, 0), 1.1, GRID_COLOR),
        ("SPAN", (0, 1), (0, 4)),
        ("SPAN", (0, 5), (0, 7)),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _build_document(stories: list[tuple[str, str | None, Table]], *, title: str, author: str) -> BytesIO:
    if not stories:
        raise ValueError("没有可导出的课表")
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=PAGE_SIZE,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=8 * mm,
        bottomMargin=7 * mm,
        title=title,
        author=author,
        subject="课程表",
    )
    story: list[Any] = []
    for index, (page_title, page_subtitle, table) in enumerate(stories):
        story.extend([_page_header(page_title, page_subtitle), Spacer(1, 4 * mm), table])
        if index < len(stories) - 1:
            story.append(PageBreak())
    document.build(story)
    output.seek(0)
    return output


def build_class_schedule_pdf(state: dict[str, Any], grade: int | None = None) -> BytesIO:
    schedule = _validated_schedule(state)
    school_name = state.get("school_name", "")
    teacher_map = {teacher["id"]: teacher["name"] for teacher in state.get("teachers", [])}
    classes = [
        class_item for class_item in state.get("classes", [])
        if grade is None or int(class_item["grade"]) == grade
    ]
    if not classes:
        raise ValueError("所选年级没有可导出的班级课表")

    stories: list[tuple[str, str | None, Table]] = []
    for class_item in classes:
        class_grade = int(class_item["grade"])
        available_slots = set(slots_for_grade(class_grade))
        all_slots = {f"{day['id']}-{period['id']}" for day in DAYS for period in PERIODS}
        lessons = schedule.get("lessons", {}).get(class_item["id"], {})

        def class_cell(slot: str, class_lessons: dict[str, Any] = lessons) -> Paragraph:
            lesson = class_lessons.get(slot)
            if not lesson:
                return _paragraph("", size=12)
            subject = SUBJECT_BY_ID.get(lesson.get("subject_id"), {"name": lesson.get("subject_id", "")})
            teacher_name = teacher_map.get(lesson.get("teacher_id"), "")
            value = str(subject["name"]) + (f"\n{teacher_name}" if teacher_name else "")
            return _paragraph(value, size=9, leading=13, bold=True)

        stories.append((
            f"{school_name}{class_item['name']}课程表",
            None,
            _schedule_table(class_cell, unavailable_slots=all_slots - available_slots),
        ))
    scope_name = f"{grade}年级" if grade else "全部年级"
    return _build_document(stories, title=f"{school_name}{scope_name}课程表", author=school_name)


def build_teacher_schedule_pdf(state: dict[str, Any], teacher_id: str) -> BytesIO:
    schedule = _validated_schedule(state)
    teacher = next((item for item in state.get("teachers", []) if item["id"] == teacher_id), None)
    if not teacher:
        raise ValueError("教师不存在")
    class_map = {item["id"]: item for item in state.get("classes", [])}

    teacher_lessons: dict[str, tuple[str, dict[str, Any]]] = {}
    for class_id, class_lessons in schedule.get("lessons", {}).items():
        for slot, lesson in class_lessons.items():
            if lesson.get("teacher_id") == teacher_id:
                teacher_lessons[slot] = (class_id, lesson)
    if not teacher_lessons:
        raise ValueError("该教师在当前课表中没有课程")

    def teacher_cell(slot: str) -> Paragraph:
        item = teacher_lessons.get(slot)
        if not item:
            return _paragraph("", size=11)
        class_id, lesson = item
        class_item = class_map.get(class_id)
        subject = SUBJECT_BY_ID.get(lesson.get("subject_id"), {"name": lesson.get("subject_id", "")})
        class_name = class_item["name"] if class_item else class_id
        return _paragraph(f"{class_name}\n{subject['name']}", size=9, leading=13, bold=True)

    school_name = state.get("school_name", "")
    page_title = f"{school_name}教师课程表"
    page_subtitle = f"教师：{teacher['name']}"
    return _build_document(
        [(page_title, page_subtitle, _schedule_table(teacher_cell))],
        title=f"{page_title}-{teacher['name']}",
        author=school_name,
    )
