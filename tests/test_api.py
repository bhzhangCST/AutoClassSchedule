from __future__ import annotations

import json
import os
from io import BytesIO
from zipfile import ZipFile

os.environ["CLASSASSIGN_USERNAME"] = "test-admin"
os.environ["CLASSASSIGN_PASSWORD"] = "test-password-only"
os.environ["CLASSASSIGN_SECRET"] = "test-session-secret-that-is-longer-than-32-characters"

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader

from app.assignment_rules import apply_required_teacher_assignments
from app import main
from app.store import StateStore, StorageError, UpstashStateStore


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-only"})
    assert response.status_code == 200


def _teacher_import_file(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "教师名单"
    sheet.append(["教师姓名", "期望最低周课时", "是否班主任", "班主任班级", "任课配置"])
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


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


def _page_contains_image(page) -> bool:
    resources = page.get("/Resources")
    if not resources:
        return False
    xobjects = resources.get_object().get("/XObject")
    if not xobjects:
        return False
    return any(item.get_object().get("/Subtype") == "/Image" for item in xobjects.get_object().values())


def test_login_state_generate_and_export(tmp_path, monkeypatch) -> None:
    local_store = StateStore(tmp_path / "state.json")
    configured_state = local_store.load()
    _configure_required_teachers(configured_state)
    local_store.save(configured_state)
    monkeypatch.setattr(main, "store", local_store)
    client = TestClient(main.app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/state").status_code == 401

    bad = client.post("/api/auth/login", json={"username": "test-admin", "password": "wrong"})
    assert bad.status_code == 401
    login = client.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-only"})
    assert login.status_code == 200

    state = client.get("/api/state")
    assert state.status_code == 200
    assert len(state.json()["state"]["classes"]) == 36
    assert set(state.json()["state"]["class_counts"].values()) == {6}

    generated = client.post("/api/schedule/generate", json={"seed": 202508, "attempts": 12})
    assert generated.status_code == 200
    assert generated.json()["result"]["success"]

    exported = client.get("/api/export/schedule.pdf")
    assert exported.status_code == 200
    assert exported.content.startswith(b"%PDF")
    assert len(exported.content) < 2_000_000
    assert exported.headers["content-type"] == "application/pdf"
    full_pdf = PdfReader(BytesIO(exported.content))
    assert len(full_pdf.pages) == 36
    first_page_text = full_pdf.pages[0].extract_text() or ""
    assert "高唐县民族实验小学一年级1班课程表" in first_page_text
    assert "一年级1班班主任" in first_page_text
    assert float(full_pdf.pages[0].mediabox.height) > float(full_pdf.pages[0].mediabox.width)
    assert _page_contains_image(full_pdf.pages[0])

    grade_export = client.get("/api/export/schedule.pdf?grade=3")
    assert grade_export.status_code == 200
    assert len(PdfReader(BytesIO(grade_export.content)).pages) == 6
    assert client.get("/api/export/schedule.pdf?grade=7").status_code == 422

    grade_xlsx = client.get("/api/export/schedule.xlsx?grade=3")
    assert grade_xlsx.status_code == 200
    assert grade_xlsx.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    class_workbook = load_workbook(BytesIO(grade_xlsx.content))
    assert len(class_workbook.sheetnames) == 6
    first_class_sheet = class_workbook[class_workbook.sheetnames[0]]
    assert first_class_sheet["A1"].value == "高唐县民族实验小学三年级1班课程表"
    assert first_class_sheet.page_setup.orientation == "portrait"
    assert "A1:G1" in {str(item) for item in first_class_sheet.merged_cells.ranges}
    assert all(cell.fill.fill_type is None for row in first_class_sheet.iter_rows() for cell in row)


def test_frontend_assets_are_versioned_and_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "/assets/styles.css?v=20260822-9" in index_response.text
    assert "/assets/app.js?v=20260822-9" in index_response.text
    assert 'value="teacher">指定教师' in index_response.text
    assert "teacher-export-class" not in index_response.text
    assert "批量导出课表" in index_response.text
    assert "导出教师课表" in index_response.text
    assert 'name="class-export-format"' in index_response.text
    assert 'name="teacher-export-format"' in index_response.text
    assert 'id="schedule-edit-button"' in index_response.text
    assert 'data-view="assignments"' not in index_response.text
    assert "教师与任课" in index_response.text
    assert '<img class="print-logo" src="/static/logo.jpg"' in index_response.text
    assert "no-store" in index_response.headers["cache-control"]

    script_response = client.get("/assets/app.js?v=20260822-9")
    assert script_response.status_code == 200
    assert "no-store" in script_response.headers["cache-control"]


def test_batch_assignments_are_saved_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)
    _login(client)

    state = client.get("/api/state").json()["state"]
    first_class, second_class = state["classes"][:2]
    created = client.post(
        "/api/teachers",
        json={"name": "批量保存测试教师", "subject_ids": ["math"], "min_weekly_lessons": 0},
    )
    teacher_id = created.json()["state"]["teachers"][0]["id"]

    saved = client.put(
        "/api/assignments",
        json={
            "classes": {
                first_class["id"]: {"math": teacher_id},
                second_class["id"]: {"math": teacher_id},
            }
        },
    )
    assert saved.status_code == 200
    assert saved.json()["updated_classes"] == 2
    assert saved.json()["state"]["assignments"][first_class["id"]]["math"] == teacher_id
    assert saved.json()["state"]["assignments"][second_class["id"]]["math"] == teacher_id

    rejected = client.put(
        "/api/assignments",
        json={
            "classes": {
                first_class["id"]: {"math": None},
                "missing-class": {"math": None},
            }
        },
    )
    assert rejected.status_code == 404
    unchanged = client.get("/api/state").json()["state"]
    assert unchanged["assignments"][first_class["id"]]["math"] == teacher_id


def test_homeroom_and_chinese_teacher_drive_fixed_courses(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)
    _login(client)

    created = client.post(
        "/api/teachers",
        json={
            "name": "一班语文班主任",
            "subject_ids": ["chinese"],
            "min_weekly_lessons": 0,
            "homeroom_class_id": "g1c1",
        },
    )
    assert created.status_code == 200
    teacher_id = created.json()["state"]["teachers"][0]["id"]
    assert created.json()["state"]["assignments"]["g1c1"]["meeting"] == teacher_id

    assigned = client.put("/api/assignments/g1c1", json={"assignments": {"chinese": teacher_id}})
    assert assigned.status_code == 200
    assert assigned.json()["state"]["assignments"]["g1c1"]["reading"] == teacher_id

    conflict = client.post(
        "/api/teachers",
        json={
            "name": "冲突班主任",
            "subject_ids": [],
            "min_weekly_lessons": 0,
            "homeroom_class_id": "g1c1",
        },
    )
    assert conflict.status_code == 409


def test_teacher_template_and_batch_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)
    _login(client)

    template = client.get("/api/teachers/import-template.xlsx")
    assert template.status_code == 200
    template_workbook = load_workbook(BytesIO(template.content))
    assert template_workbook.sheetnames == ["教师名单", "填写说明"]
    assert [template_workbook["教师名单"].cell(1, column).value for column in range(1, 6)] == [
        "教师姓名\n（必填）",
        "期望最低周课时\n（0—35整数，0表示不检查）",
        "是否班主任\n（填“是”或“否”）",
        "班主任班级\n（完整班名或1.1，不支持范围）",
        "任课配置\n（班级/范围：科目；如一年级1-7班：语文）",
    ]
    assert all(
        cell.fill.fill_type is None
        for sheet in template_workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    assert template_workbook["教师名单"]["A2"].border.right.style == "thin"
    assert template_workbook["教师名单"]["E2"].border.right.style == "thin"
    assert {str(item.sqref) for item in template_workbook["教师名单"].data_validations.dataValidation} == {
        "B2:B1001",
        "C2:C1001",
    }

    created = client.post("/api/teachers", json={"name": "王老师", "subject_ids": ["english"], "min_weekly_lessons": 8})
    assert created.status_code == 200
    template_sheet = template_workbook["教师名单"]
    for row_index, values in enumerate((
        ["王老师", 12, "是", "1.1", "一年级1-2班：语文、数学"],
        ["王老师", 10, "否", "", "1.1：体育"],
        ["李老师", 0, "否", "", ""],
    ), start=2):
        for column_index, value in enumerate(values, start=1):
            template_sheet.cell(row_index, column_index, value)
    template_output = BytesIO()
    template_workbook.save(template_output)
    content = template_output.getvalue()
    imported = client.post(
        "/api/teachers/import",
        files={"file": ("teachers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["import"] | {"details": []} == {
        "created": 2,
        "updated": 1,
        "renamed": 1,
        "total": 3,
        "assigned": 5,
        "homerooms": 1,
        "details": [],
    }
    teachers = {item["name"]: item for item in imported.json()["state"]["teachers"]}
    assert set(teachers) == {"王老师", "王老师（2）", "李老师"}
    assert teachers["王老师"]["subject_ids"] == ["chinese", "math"]
    assert teachers["王老师"]["min_weekly_lessons"] == 12
    assert teachers["王老师（2）"]["subject_ids"] == ["pe"]
    assert teachers["李老师"]["subject_ids"] == []
    assert teachers["王老师"]["homeroom_class_id"] == "g1c1"
    imported_state = imported.json()["state"]
    assert imported_state["assignments"]["g1c1"]["chinese"] == teachers["王老师"]["id"]
    assert imported_state["assignments"]["g1c1"]["reading"] == teachers["王老师"]["id"]
    assert imported_state["assignments"]["g1c1"]["meeting"] == teachers["王老师"]["id"]
    assert imported_state["assignments"]["g1c1"]["pe"] == teachers["王老师（2）"]["id"]
    assert imported_state["assignments"]["g1c2"]["chinese"] == teachers["王老师"]["id"]
    assert imported_state["assignments"]["g1c2"]["math"] == teachers["王老师"]["id"]

    imported_again = client.post(
        "/api/teachers/import",
        files={"file": ("teachers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported_again.status_code == 200
    assert imported_again.json()["import"]["created"] == 0
    assert imported_again.json()["import"]["updated"] == 3
    assert len(imported_again.json()["state"]["teachers"]) == 3

    bad_content = _teacher_import_file([["错误教师", 12, "否", "", "一年级1班：不存在的科目"]])
    rejected = client.post("/api/teachers/import", files={"file": ("bad.xlsx", bad_content)})
    assert rejected.status_code == 422
    assert len(client.get("/api/state").json()["state"]["teachers"]) == 3

    bad_range = _teacher_import_file([["范围错误教师", 12, "否", "", "一年级1-7班：语文"]])
    rejected_range = client.post("/api/teachers/import", files={"file": ("bad-range.xlsx", bad_range)})
    assert rejected_range.status_code == 422
    assert "一年级7班" in rejected_range.json()["detail"]


def test_teacher_schedule_export_by_grade_or_teacher(tmp_path, monkeypatch) -> None:
    local_store = StateStore(tmp_path / "state.json")
    scoped_state = local_store.load()
    scoped_state["classes"] = [item for item in scoped_state["classes"] if item["id"] == "g3c1"]
    scoped_state["assignments"] = {"g3c1": scoped_state["assignments"]["g3c1"]}
    local_store.save(scoped_state)
    monkeypatch.setattr(main, "store", local_store)
    client = TestClient(main.app)
    _login(client)
    teacher = client.post(
        "/api/teachers",
        json={"name": "三年级数学教师", "subject_ids": ["chinese", "math"], "min_weekly_lessons": 0, "homeroom_class_id": "g3c1"},
    ).json()["state"]["teachers"][0]
    assigned = client.put("/api/assignments/g3c1", json={"assignments": {"chinese": teacher["id"], "math": teacher["id"]}})
    assert assigned.status_code == 200
    generated = client.post("/api/schedule/generate", json={"seed": 818, "attempts": 40})
    assert generated.status_code == 200
    assert generated.json()["result"]["success"]

    for url in (
        "/api/export/teachers.zip",
        "/api/export/teachers.zip?grade=3",
    ):
        exported = client.get(url)
        assert exported.status_code == 200, exported.text
        with ZipFile(BytesIO(exported.content)) as archive:
            names = archive.namelist()
            assert names == ["三年级数学教师课程表.pdf"]
            teacher_pdf = archive.read(names[0])
            assert teacher_pdf.startswith(b"%PDF")
            assert len(PdfReader(BytesIO(teacher_pdf)).pages) == 1

    single_export = client.get(f"/api/export/teacher.pdf?teacher_id={teacher['id']}")
    assert single_export.status_code == 200
    assert single_export.content.startswith(b"%PDF")
    assert len(single_export.content) < 500_000
    assert single_export.headers["content-type"] == "application/pdf"
    assert single_export.headers["content-disposition"].endswith(".pdf")
    single_pdf = PdfReader(BytesIO(single_export.content))
    assert len(single_pdf.pages) == 1
    assert "高唐县民族实验小学三年级数学教师课程表" in (single_pdf.pages[0].extract_text() or "")
    assert float(single_pdf.pages[0].mediabox.height) > float(single_pdf.pages[0].mediabox.width)
    assert _page_contains_image(single_pdf.pages[0])

    for url in (
        "/api/export/teachers.xlsx",
        "/api/export/teachers.xlsx?grade=3",
        f"/api/export/teachers.xlsx?teacher_id={teacher['id']}",
    ):
        exported_xlsx = client.get(url)
        assert exported_xlsx.status_code == 200, exported_xlsx.text
        teacher_workbook = load_workbook(BytesIO(exported_xlsx.content))
        assert teacher_workbook.sheetnames == ["三年级数学教师"]
        teacher_sheet = teacher_workbook.active
        assert teacher_sheet["A1"].value == "高唐县民族实验小学三年级数学教师课程表"
        assert teacher_sheet.page_setup.orientation == "portrait"
        assert all(cell.fill.fill_type is None for row in teacher_sheet.iter_rows() for cell in row)

    assert client.get("/api/export/teachers.zip?grade=1").status_code == 409
    assert client.get("/api/export/teacher.pdf?teacher_id=missing").status_code == 404
    assert client.get("/api/export/teachers.xlsx?teacher_id=missing").status_code == 404


def test_schedule_swaps_are_atomic_and_keep_teacher_views_consistent(tmp_path, monkeypatch) -> None:
    local_store = StateStore(tmp_path / "state.json")
    scoped_state = local_store.load()
    scoped_state["classes"] = [item for item in scoped_state["classes"] if item["id"] in {"g3c1", "g3c2"}]
    scoped_state["assignments"] = {
        class_id: scoped_state["assignments"][class_id]
        for class_id in ("g3c1", "g3c2")
    }
    local_store.save(scoped_state)
    monkeypatch.setattr(main, "store", local_store)
    client = TestClient(main.app)
    _login(client)

    generated = client.post("/api/schedule/generate", json={"seed": 20260822, "attempts": 20})
    assert generated.status_code == 200
    assert generated.json()["result"]["success"]

    state = local_store.load()
    first_subject = state["schedule"]["lessons"]["g3c1"]["mon-am3"]["subject_id"]
    second_subject = state["schedule"]["lessons"]["g3c2"]["tue-am3"]["subject_id"]
    state["teachers"].append({
        "id": "t_manual",
        "name": "手动调整测试教师",
        "subject_ids": list(dict.fromkeys([first_subject, second_subject])),
        "min_weekly_lessons": 0,
        "homeroom_class_id": None,
    })
    state["schedule"]["lessons"]["g3c1"]["mon-am3"]["teacher_id"] = "t_manual"
    state["schedule"]["lessons"]["g3c2"]["tue-am3"]["teacher_id"] = "t_manual"
    local_store.save(state)
    before_lessons = local_store.load()["schedule"]["lessons"]

    fixed_rejected = client.put(
        "/api/schedule/swaps",
        json={"class_id": "g3c1", "swaps": [{"from_slot": "mon-pm3", "to_slot": "mon-am3"}]},
    )
    assert fixed_rejected.status_code == 409
    assert "固定课位" in "；".join(fixed_rejected.json()["detail"])
    assert local_store.load()["schedule"]["lessons"] == before_lessons

    conflict = client.put(
        "/api/schedule/swaps",
        json={"class_id": "g3c1", "swaps": [{"from_slot": "mon-am3", "to_slot": "tue-am3"}]},
    )
    assert conflict.status_code == 409
    assert "手动调整测试教师" in "；".join(conflict.json()["detail"])
    assert local_store.load()["schedule"]["lessons"] == before_lessons

    saved = client.put(
        "/api/schedule/swaps",
        json={
            "class_id": "g3c1",
            "swaps": [
                {"from_slot": "mon-am3", "to_slot": "wed-am3"},
                {"from_slot": "wed-am3", "to_slot": "fri-am3"},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    schedule = saved.json()["state"]["schedule"]
    assert saved.json()["updated_swaps"] == 2
    assert schedule["manual_swap_count"] == 2
    assert schedule["edited_at"]
    assert schedule["lessons"]["g3c1"]["fri-am3"]["teacher_id"] == "t_manual"
    assert schedule["lessons"]["g3c1"]["fri-am3"]["subject_id"] == first_subject
    assert schedule["lessons"]["g3c1"]["mon-am3"]["teacher_id"] is None

    teacher_xlsx = client.get("/api/export/teachers.xlsx?teacher_id=t_manual")
    assert teacher_xlsx.status_code == 200
    teacher_sheet = load_workbook(BytesIO(teacher_xlsx.content)).active
    assert str(teacher_sheet["G5"].value).startswith("三年级1班\n")


def test_upstash_store_initializes_and_persists(monkeypatch) -> None:
    remote: dict[str, str] = {}
    cloud_store = UpstashStateStore("https://example.upstash.io", "test-token", key="test-state")

    def fake_execute(command: str, *arguments: object) -> object:
        if command == "GET":
            return remote.get(str(arguments[0]))
        if command == "SET":
            remote[str(arguments[0])] = str(arguments[1])
            return "OK"
        raise AssertionError(command)

    monkeypatch.setattr(cloud_store, "_execute", fake_execute)
    initial = cloud_store.load()
    assert initial["school_name"] == "高唐县民族实验小学"
    assert json.loads(remote["test-state"])["schema_version"] == 3

    initial["school_name"] = "云端测试学校"
    cloud_store.save(initial)
    assert cloud_store.load()["school_name"] == "云端测试学校"

    def failed_execute(command: str, *arguments: object) -> object:
        raise StorageError("云端测试连接失败")

    monkeypatch.setattr(cloud_store, "_execute", failed_execute)
    monkeypatch.setattr(main, "store", cloud_store)
    client = TestClient(main.app)
    _login(client)
    unavailable = client.get("/api/state")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "云端测试连接失败"
