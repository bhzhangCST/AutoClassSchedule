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

from app import main
from app.store import StateStore, StorageError, UpstashStateStore


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-only"})
    assert response.status_code == 200


def _teacher_import_file(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "教师名单"
    sheet.append(["教师姓名", "期望最低周课时", "可任科目"])
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_login_state_generate_and_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
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
    assert exported.headers["content-type"] == "application/pdf"
    full_pdf = PdfReader(BytesIO(exported.content))
    assert len(full_pdf.pages) == 36
    assert "高唐县民族实验小学一年级1班课程表" in (full_pdf.pages[0].extract_text() or "")

    grade_export = client.get("/api/export/schedule.pdf?grade=3")
    assert grade_export.status_code == 200
    assert len(PdfReader(BytesIO(grade_export.content)).pages) == 6
    assert client.get("/api/export/schedule.pdf?grade=7").status_code == 422


def test_frontend_assets_are_versioned_and_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "/assets/styles.css?v=20260822-5" in index_response.text
    assert "/assets/app.js?v=20260822-5" in index_response.text
    assert 'value="teacher">指定教师' in index_response.text
    assert "teacher-export-class" not in index_response.text
    assert "/api/export/schedule.pdf" in index_response.text
    assert "no-store" in index_response.headers["cache-control"]

    script_response = client.get("/assets/app.js?v=20260822-5")
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


def test_teacher_template_and_batch_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)
    _login(client)

    template = client.get("/api/teachers/import-template.xlsx")
    assert template.status_code == 200
    template_workbook = load_workbook(BytesIO(template.content))
    assert template_workbook.sheetnames == ["教师名单", "填写说明"]
    assert [template_workbook["教师名单"].cell(1, column).value for column in range(1, 4)] == [
        "教师姓名",
        "期望最低周课时",
        "可任科目",
    ]
    assert all(
        cell.fill.fill_type is None
        for sheet in template_workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )

    created = client.post("/api/teachers", json={"name": "王老师", "subject_ids": ["english"], "min_weekly_lessons": 8})
    assert created.status_code == 200
    template_sheet = template_workbook["教师名单"]
    for row_index, values in enumerate((
        ["王老师", 12, "语文、数学"],
        ["王老师", 10, "体育、音乐"],
        ["李老师", 0, "科学，信息科技"],
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
        "details": [],
    }
    teachers = {item["name"]: item for item in imported.json()["state"]["teachers"]}
    assert set(teachers) == {"王老师", "王老师（2）", "李老师"}
    assert teachers["王老师"]["subject_ids"] == ["chinese", "math"]
    assert teachers["王老师"]["min_weekly_lessons"] == 12
    assert teachers["王老师（2）"]["subject_ids"] == ["pe", "music"]

    imported_again = client.post(
        "/api/teachers/import",
        files={"file": ("teachers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported_again.status_code == 200
    assert imported_again.json()["import"]["created"] == 0
    assert imported_again.json()["import"]["updated"] == 3
    assert len(imported_again.json()["state"]["teachers"]) == 3

    bad_content = _teacher_import_file([["错误教师", 12, "不存在的科目"]])
    rejected = client.post("/api/teachers/import", files={"file": ("bad.xlsx", bad_content)})
    assert rejected.status_code == 422
    assert len(client.get("/api/state").json()["state"]["teachers"]) == 3


def test_teacher_schedule_export_by_grade_or_teacher(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "store", StateStore(tmp_path / "state.json"))
    client = TestClient(main.app)
    _login(client)
    teacher = client.post(
        "/api/teachers",
        json={"name": "三年级数学教师", "subject_ids": ["math"], "min_weekly_lessons": 0},
    ).json()["state"]["teachers"][0]
    assigned = client.put("/api/assignments/g3c1", json={"assignments": {"math": teacher["id"]}})
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
    assert single_export.headers["content-type"] == "application/pdf"
    assert single_export.headers["content-disposition"].endswith(".pdf")
    single_pdf = PdfReader(BytesIO(single_export.content))
    assert len(single_pdf.pages) == 1
    assert "高唐县民族实验小学三年级数学教师课程表" in (single_pdf.pages[0].extract_text() or "")

    assert client.get("/api/export/teachers.zip?grade=1").status_code == 409
    assert client.get("/api/export/teacher.pdf?teacher_id=missing").status_code == 404


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
    assert json.loads(remote["test-state"])["schema_version"] == 2

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
