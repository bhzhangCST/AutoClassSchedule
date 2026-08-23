from __future__ import annotations

import os
import re
import uuid
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .assignment_rules import apply_required_teacher_assignments
from .auth import (
    COOKIE_NAME,
    TOKEN_TTL_SECONDS,
    USERNAME,
    create_token,
    decode_token,
    require_auth,
    verify_credentials,
)
from .constants import CURRICULUM, DAYS, GRADE_NAMES, PERIODS, RULE_DESCRIPTIONS, SUBJECTS, fixed_lessons_for_grade
from .pdf_export import build_class_schedule_pdf, build_teacher_schedule_pdf
from .schedule_edit import ScheduleSwapError, apply_schedule_swaps
from .scheduler import generate_schedule
from .store import PROJECT_ROOT, StorageError, store
from .teacher_import import merge_teacher_rows, parse_teacher_workbook
from .xlsx_export import XLSX_MEDIA_TYPE, build_class_schedule_xlsx, build_teacher_schedules_xlsx

STATIC_DIR = PROJECT_ROOT / "static"
TEACHER_TEMPLATE_PATH = STATIC_DIR / "教师名单导入模板.xlsx"
MAX_TEACHER_IMPORT_BYTES = 5 * 1024 * 1024

app = FastAPI(title="小学课程智能排课", version="1.0.0", docs_url=None, redoc_url=None)


@app.exception_handler(StorageError)
async def storage_error_handler(_: Request, exc: StorageError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


class NoCacheStaticFiles(StaticFiles):
    """Keep the HTML shell and its assets on the same deployed version."""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


app.mount("/assets", NoCacheStaticFiles(directory=STATIC_DIR), name="assets")
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static-files")


class LoginPayload(BaseModel):
    username: str
    password: str


class SettingsPayload(BaseModel):
    school_name: str = Field(min_length=1, max_length=80)
    class_counts: dict[str, int]


class ClassPayload(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class TeacherPayload(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    subject_ids: list[str] = Field(default_factory=list)
    min_weekly_lessons: int = Field(default=12, ge=0, le=35)
    homeroom_class_id: str | None = Field(default=None, max_length=40)
    teaching_assignments: dict[str, list[str]] | None = None


class AssignmentPayload(BaseModel):
    assignments: dict[str, str | None]


class AssignmentBatchPayload(BaseModel):
    classes: dict[str, dict[str, str | None]]


class GeneratePayload(BaseModel):
    seed: int | None = None
    attempts: int = Field(default=80, ge=1, le=300)


class ScheduleSwapItem(BaseModel):
    from_slot: str = Field(min_length=1, max_length=20)
    to_slot: str = Field(min_length=1, max_length=20)


class ScheduleSwapPayload(BaseModel):
    class_id: str = Field(min_length=1, max_length=40)
    swaps: list[ScheduleSwapItem] = Field(min_length=1, max_length=100)


def _meta() -> dict[str, Any]:
    return {
        "subjects": deepcopy(SUBJECTS),
        "curriculum": {str(grade): deepcopy(values) for grade, values in CURRICULUM.items()},
        "days": deepcopy(DAYS),
        "periods": deepcopy(PERIODS),
        "rules": deepcopy(RULE_DESCRIPTIONS),
        "fixed_lessons": {str(grade): fixed_lessons_for_grade(grade) for grade in range(1, 7)},
        "storage_backend": getattr(store, "backend", "local"),
    }


def _state_response() -> dict[str, Any]:
    return {"state": store.load(), "meta": _meta()}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response) -> dict[str, Any]:
    if not verify_credentials(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    token = create_token(payload.username)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=os.getenv("CLASSASSIGN_SECURE_COOKIE", "0") == "1",
    )
    return {"success": True, "username": payload.username}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME)
    return {"success": True}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    user = decode_token(token) if token else None
    if not user:
        return {"authenticated": False, "username": None}
    return {"authenticated": True, "username": user["sub"]}


@app.get("/api/state")
def get_state(_: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    return _state_response()


@app.put("/api/settings")
def update_settings(payload: SettingsPayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    try:
        store.update_settings(payload.school_name, payload.class_counts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _state_response()


@app.put("/api/classes/{class_id}")
def update_class(class_id: str, payload: ClassPayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    target = next((item for item in state["classes"] if item["id"] == class_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="班级不存在")
    target["name"] = payload.name.strip()
    state["schedule"] = None
    store.save(state)
    return _state_response()


def _validate_subject_ids(subject_ids: list[str]) -> list[str]:
    allowed = {subject["id"] for subject in SUBJECTS}
    unique = list(dict.fromkeys(subject_ids))
    invalid = [item for item in unique if item not in allowed]
    if invalid:
        raise HTTPException(status_code=422, detail=f"未知课程：{', '.join(invalid)}")
    return unique


def _validate_homeroom_class_id(
    state: dict[str, Any], homeroom_class_id: str | None, teacher_id: str | None = None
) -> str | None:
    class_id = (homeroom_class_id or "").strip() or None
    if not class_id:
        return None
    class_item = next((item for item in state["classes"] if item["id"] == class_id), None)
    if not class_item:
        raise HTTPException(status_code=422, detail="班主任班级不存在")
    conflict = next(
        (teacher for teacher in state["teachers"] if teacher["id"] != teacher_id and teacher.get("homeroom_class_id") == class_id),
        None,
    )
    if conflict:
        raise HTTPException(status_code=409, detail=f"{class_item['name']}的班主任已是{conflict['name']}")
    return class_id


def _validate_teaching_assignments(
    state: dict[str, Any],
    teaching_assignments: dict[str, list[str]],
) -> dict[str, list[str]]:
    if len(teaching_assignments) > 120:
        raise HTTPException(status_code=422, detail="一次最多配置120个班级")
    classes = {item["id"]: item for item in state["classes"]}
    normalized: dict[str, list[str]] = {}
    total = 0
    for class_id, subject_ids in teaching_assignments.items():
        class_item = classes.get(class_id)
        if not class_item:
            raise HTTPException(status_code=422, detail="任课班级不存在")
        unique_subjects = list(dict.fromkeys(subject_ids))
        total += len(unique_subjects)
        if total > 1000:
            raise HTTPException(status_code=422, detail="一次最多配置1000项任课关系")
        allowed_subjects = set(CURRICULUM[int(class_item["grade"])])
        invalid = [subject_id for subject_id in unique_subjects if subject_id not in allowed_subjects]
        if invalid:
            raise HTTPException(status_code=422, detail=f"{class_item['name']}没有课程：{', '.join(invalid)}")
        automatic = [subject_id for subject_id in unique_subjects if subject_id in {"reading", "meeting"}]
        if automatic:
            raise HTTPException(status_code=422, detail="阅读和班队会由语文教师、班主任自动设置")
        if unique_subjects:
            normalized[class_id] = unique_subjects
    return normalized


def _merge_teacher_subject_ids(
    subject_ids: list[str],
    teaching_assignments: dict[str, list[str]] | None,
) -> list[str]:
    selected = set(subject_ids)
    if teaching_assignments is not None:
        selected.update(
            subject_id
            for class_subjects in teaching_assignments.values()
            for subject_id in class_subjects
        )
    return [subject["id"] for subject in SUBJECTS if subject["id"] in selected]


def _apply_teacher_assignments(
    state: dict[str, Any],
    teacher_id: str,
    teaching_assignments: dict[str, list[str]],
) -> None:
    selected = {
        (class_id, subject_id)
        for class_id, subject_ids in teaching_assignments.items()
        for subject_id in subject_ids
    }
    for class_id, class_assignments in state["assignments"].items():
        for subject_id, assigned_teacher in class_assignments.items():
            if (
                assigned_teacher == teacher_id
                and subject_id not in {"reading", "meeting"}
                and (class_id, subject_id) not in selected
            ):
                class_assignments[subject_id] = None
    for class_id, subject_ids in teaching_assignments.items():
        class_assignments = state["assignments"].setdefault(class_id, {})
        for subject_id in subject_ids:
            class_assignments[subject_id] = teacher_id


@app.post("/api/teachers")
def create_teacher(payload: TeacherPayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    if any(item["name"] == payload.name.strip() for item in state["teachers"]):
        raise HTTPException(status_code=409, detail="教师姓名已存在")
    homeroom_class_id = _validate_homeroom_class_id(state, payload.homeroom_class_id)
    subject_ids = _validate_subject_ids(payload.subject_ids)
    teaching_assignments = (
        _validate_teaching_assignments(state, payload.teaching_assignments)
        if payload.teaching_assignments is not None
        else None
    )
    teacher_id = f"t_{uuid.uuid4().hex[:10]}"
    state["teachers"].append({
        "id": teacher_id,
        "name": payload.name.strip(),
        "subject_ids": _merge_teacher_subject_ids(subject_ids, teaching_assignments),
        "min_weekly_lessons": payload.min_weekly_lessons,
        "homeroom_class_id": homeroom_class_id,
    })
    if teaching_assignments is not None:
        _apply_teacher_assignments(state, teacher_id, teaching_assignments)
    apply_required_teacher_assignments(state)
    state["schedule"] = None
    store.save(state)
    return _state_response()


@app.get("/api/teachers/import-template.xlsx")
def download_teacher_import_template(_: dict[str, Any] = Depends(require_auth)) -> FileResponse:
    if not TEACHER_TEMPLATE_PATH.exists():
        raise HTTPException(status_code=500, detail="教师名单模板缺失")
    return FileResponse(
        TEACHER_TEMPLATE_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="教师名单导入模板.xlsx",
    )


@app.post("/api/teachers/import")
async def import_teachers(
    file: UploadFile = File(...),
    _: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=422, detail="请上传 .xlsx 格式的教师名单")
    try:
        content = await file.read(MAX_TEACHER_IMPORT_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_TEACHER_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="教师名单不能超过5MB")
    state = store.load()
    try:
        rows = parse_teacher_workbook(content)
        summary = merge_teacher_rows(state, rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.save(state)
    return {"import": summary, **_state_response()}


@app.put("/api/teachers/{teacher_id}")
def update_teacher(teacher_id: str, payload: TeacherPayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    target = next((item for item in state["teachers"] if item["id"] == teacher_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="教师不存在")
    if any(item["id"] != teacher_id and item["name"] == payload.name.strip() for item in state["teachers"]):
        raise HTTPException(status_code=409, detail="教师姓名已存在")
    subject_ids = _validate_subject_ids(payload.subject_ids)
    teaching_assignments = (
        _validate_teaching_assignments(state, payload.teaching_assignments)
        if payload.teaching_assignments is not None
        else None
    )
    target.update({
        "name": payload.name.strip(),
        "subject_ids": _merge_teacher_subject_ids(subject_ids, teaching_assignments),
        "min_weekly_lessons": payload.min_weekly_lessons,
        "homeroom_class_id": _validate_homeroom_class_id(state, payload.homeroom_class_id, teacher_id),
    })
    if teaching_assignments is not None:
        _apply_teacher_assignments(state, teacher_id, teaching_assignments)
    apply_required_teacher_assignments(state)
    state["schedule"] = None
    store.save(state)
    return _state_response()


@app.delete("/api/teachers/{teacher_id}")
def delete_teacher(teacher_id: str, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    before = len(state["teachers"])
    state["teachers"] = [item for item in state["teachers"] if item["id"] != teacher_id]
    if len(state["teachers"]) == before:
        raise HTTPException(status_code=404, detail="教师不存在")
    for class_assignments in state["assignments"].values():
        for subject_id, assigned_teacher in class_assignments.items():
            if assigned_teacher == teacher_id:
                class_assignments[subject_id] = None
    apply_required_teacher_assignments(state)
    state["schedule"] = None
    store.save(state)
    return _state_response()


def _normalized_class_assignments(
    state: dict[str, Any],
    class_id: str,
    assignments: dict[str, str | None],
) -> dict[str, str | None]:
    class_item = next((item for item in state["classes"] if item["id"] == class_id), None)
    if not class_item:
        raise HTTPException(status_code=404, detail="班级不存在")
    teacher_ids = {teacher["id"] for teacher in state["teachers"]}
    allowed_subjects = set(CURRICULUM[int(class_item["grade"])])
    normalized = dict(state["assignments"].get(class_id, {}))
    for subject_id, teacher_id in assignments.items():
        if subject_id not in allowed_subjects:
            raise HTTPException(status_code=422, detail="该年级没有此课程")
        if teacher_id and teacher_id not in teacher_ids:
            raise HTTPException(status_code=422, detail="教师不存在")
        normalized[subject_id] = teacher_id or None
    return {subject_id: normalized.get(subject_id) for subject_id in allowed_subjects}


@app.put("/api/assignments")
def update_assignment_batch(
    payload: AssignmentBatchPayload,
    _: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    if not payload.classes:
        raise HTTPException(status_code=422, detail="没有需要保存的任课配置")
    if len(payload.classes) > 120:
        raise HTTPException(status_code=422, detail="一次最多保存120个班级")

    state = store.load()
    normalized_by_class = {
        class_id: _normalized_class_assignments(state, class_id, assignments)
        for class_id, assignments in payload.classes.items()
    }
    for class_id, assignments in normalized_by_class.items():
        state["assignments"][class_id] = assignments
    apply_required_teacher_assignments(state)
    state["schedule"] = None
    store.save(state)
    return {"updated_classes": len(normalized_by_class), **_state_response()}


@app.put("/api/assignments/{class_id}")
def update_assignments(class_id: str, payload: AssignmentPayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    state["assignments"][class_id] = _normalized_class_assignments(state, class_id, payload.assignments)
    apply_required_teacher_assignments(state)
    state["schedule"] = None
    store.save(state)
    return _state_response()


@app.post("/api/schedule/generate")
def create_schedule(payload: GeneratePayload, _: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    state = store.load()
    result = generate_schedule(state, seed=payload.seed, attempts=payload.attempts)
    if result.get("success"):
        state["schedule"] = result
        store.save(state)
    return {"result": result, **_state_response()}


@app.put("/api/schedule/swaps")
def save_schedule_swaps(
    payload: ScheduleSwapPayload,
    _: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    state = store.load()
    try:
        apply_schedule_swaps(
            state,
            payload.class_id,
            [item.model_dump() for item in payload.swaps],
        )
    except ScheduleSwapError as exc:
        raise HTTPException(status_code=409, detail=exc.issues) from exc
    store.save(state)
    return {"updated_swaps": len(payload.swaps), **_state_response()}


@app.get("/api/export/schedule.pdf")
def export_schedule(
    grade: int | None = Query(default=None, ge=1, le=6),
    _: dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    state = store.load()
    try:
        output = build_class_schedule_pdf(state, grade=grade)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"{GRADE_NAMES[grade]}班级课程表.pdf" if grade else "全部班级课程表.pdf"
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/export/schedule.xlsx")
def export_schedule_xlsx(
    grade: int | None = Query(default=None, ge=1, le=6),
    _: dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    state = store.load()
    try:
        output = build_class_schedule_xlsx(state, grade=grade)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"{GRADE_NAMES[grade]}班级课程表.xlsx" if grade else "全部班级课程表.xlsx"
    return StreamingResponse(
        output,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _safe_export_filename(value: str, used: set[str]) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(".") or "教师"
    candidate = cleaned
    index = 2
    while candidate.lower() in used:
        candidate = f"{cleaned}-{index}"
        index += 1
    used.add(candidate.lower())
    return candidate


@app.get("/api/export/teachers.zip")
def export_teacher_schedules(
    grade: int | None = Query(default=None, ge=1, le=6),
    _: dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    state = store.load()
    schedule = state.get("schedule")
    if not schedule or not schedule.get("success"):
        raise HTTPException(status_code=409, detail="尚未生成可导出的课表")

    classes = state.get("classes", [])
    if grade is not None:
        scoped_class_ids = {item["id"] for item in classes if int(item["grade"]) == grade}
    else:
        scoped_class_ids = {item["id"] for item in classes}

    teacher_ids = {
        lesson["teacher_id"]
        for scoped_id in scoped_class_ids
        for lesson in schedule.get("lessons", {}).get(scoped_id, {}).values()
        if lesson.get("teacher_id")
    }
    teachers = [item for item in state.get("teachers", []) if item["id"] in teacher_ids]
    if not teachers:
        raise HTTPException(status_code=409, detail="所选范围内没有已排课教师")

    archive = BytesIO()
    used_names: set[str] = set()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
        for teacher in teachers:
            pdf = build_teacher_schedule_pdf(state, teacher["id"])
            file_stem = _safe_export_filename(f"{teacher['name']}课程表", used_names)
            zip_file.writestr(f"{file_stem}.pdf", pdf.getvalue())
    archive.seek(0)

    if grade is not None:
        filename = f"{GRADE_NAMES[grade]}相关教师课表.zip"
    else:
        filename = "全部教师课表.zip"
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/export/teacher.pdf")
def export_single_teacher_schedule(
    teacher_id: str = Query(min_length=1, max_length=40),
    _: dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    state = store.load()
    schedule = state.get("schedule")
    if not schedule or not schedule.get("success"):
        raise HTTPException(status_code=409, detail="尚未生成可导出的课表")
    teacher = next((item for item in state.get("teachers", []) if item["id"] == teacher_id), None)
    if not teacher:
        raise HTTPException(status_code=404, detail="教师不存在")
    try:
        pdf = build_teacher_schedule_pdf(state, teacher_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"{_safe_export_filename(teacher['name'], set())}课程表.pdf"
    return StreamingResponse(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/export/teachers.xlsx")
def export_teacher_schedules_xlsx(
    grade: int | None = Query(default=None, ge=1, le=6),
    teacher_id: str | None = Query(default=None, min_length=1, max_length=40),
    _: dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    if grade is not None and teacher_id is not None:
        raise HTTPException(status_code=422, detail="年级范围和指定教师不能同时选择")
    state = store.load()
    teacher = None
    if teacher_id:
        teacher = next((item for item in state.get("teachers", []) if item["id"] == teacher_id), None)
        if not teacher:
            raise HTTPException(status_code=404, detail="教师不存在")
    try:
        output = build_teacher_schedules_xlsx(state, grade=grade, teacher_id=teacher_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if teacher:
        filename = f"{_safe_export_filename(teacher['name'], set())}课程表.xlsx"
    elif grade is not None:
        filename = f"{GRADE_NAMES[grade]}相关教师课表.xlsx"
    else:
        filename = "全部教师课表.xlsx"
    return StreamingResponse(
        output,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/configuration.json")
def export_configuration(_: dict[str, Any] = Depends(require_auth)) -> Response:
    state = store.load()
    state["schedule"] = None
    import json

    return Response(
        json.dumps(state, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=classassign-config.json"},
    )
