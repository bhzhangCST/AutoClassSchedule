from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import PROJECT_ROOT
from .constants import CURRICULUM, GRADE_NAMES

DATA_DIR = Path(os.getenv("CLASSASSIGN_DATA_DIR", PROJECT_ROOT / "data"))
STATE_FILE = DATA_DIR / "app_state.json"
DEFAULT_CLASS_COUNT = 6


class StorageError(RuntimeError):
    pass


def _default_classes(class_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    counts = class_counts or {str(grade): DEFAULT_CLASS_COUNT for grade in range(1, 7)}
    classes: list[dict[str, Any]] = []
    for grade in range(1, 7):
        for number in range(1, int(counts.get(str(grade), DEFAULT_CLASS_COUNT)) + 1):
            classes.append({
                "id": f"g{grade}c{number}",
                "grade": grade,
                "name": f"{GRADE_NAMES[grade]}{number}班",
            })
    return classes


def default_state() -> dict[str, Any]:
    classes = _default_classes()
    return {
        "schema_version": 2,
        "school_name": "高唐县民族实验小学",
        "class_counts": {str(grade): DEFAULT_CLASS_COUNT for grade in range(1, 7)},
        "classes": classes,
        "teachers": [],
        "assignments": {
            item["id"]: {subject_id: None for subject_id in CURRICULUM[item["grade"]]}
            for item in classes
        },
        "schedule": None,
    }


class SettingsStoreMixin:
    backend = "unknown"

    def load(self) -> dict[str, Any]:
        raise NotImplementedError

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def update_settings(self, school_name: str, class_counts: dict[str, int]) -> dict[str, Any]:
        with self._lock:
            state = self.load()
            normalized: dict[str, int] = {}
            for grade in range(1, 7):
                count = int(class_counts.get(str(grade), DEFAULT_CLASS_COUNT))
                if count < 1 or count > 20:
                    raise ValueError(f"{GRADE_NAMES[grade]}班级数必须在1到20之间")
                normalized[str(grade)] = count

            old_classes = {item["id"]: item for item in state.get("classes", [])}
            old_assignments = state.get("assignments", {})
            classes = _default_classes(normalized)
            for item in classes:
                if item["id"] in old_classes:
                    item["name"] = old_classes[item["id"]].get("name", item["name"])

            assignments = {}
            for item in classes:
                prior = old_assignments.get(item["id"], {})
                assignments[item["id"]] = {
                    subject_id: prior.get(subject_id)
                    for subject_id in CURRICULUM[item["grade"]]
                }

            state.update({
                "school_name": school_name.strip() or "未命名学校",
                "class_counts": normalized,
                "classes": classes,
                "assignments": assignments,
                "schedule": None,
            })
            return self.save(state)


class StateStore(SettingsStoreMixin):
    backend = "local"

    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(default_state())

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = default_state()
                self._write(state)
            return deepcopy(state)

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._write(state)
            return deepcopy(state)


class UpstashStateStore(SettingsStoreMixin):
    """Persist the complete single-school state under one Upstash Redis key."""

    backend = "upstash"

    def __init__(self, rest_url: str, token: str, key: str = "classassign:state:v1") -> None:
        self.rest_url = rest_url.rstrip("/")
        self.token = token
        self.key = key
        self._lock = threading.RLock()

    def _execute(self, command: str, *arguments: Any) -> Any:
        body = json.dumps([command, *arguments], ensure_ascii=False).encode("utf-8")
        request = Request(
            self.rest_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise StorageError(f"云端数据存储请求失败（HTTP {exc.code}）") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise StorageError("暂时无法连接云端数据存储，请稍后重试") from exc
        if "error" in payload:
            raise StorageError(f"云端数据存储拒绝了请求：{payload['error']}")
        return payload.get("result")

    def load(self) -> dict[str, Any]:
        with self._lock:
            raw_state = self._execute("GET", self.key)
            if raw_state is None:
                state = default_state()
                self.save(state)
                return deepcopy(state)
            try:
                state = json.loads(raw_state)
            except (TypeError, json.JSONDecodeError) as exc:
                raise StorageError("云端课表数据格式无效，为避免覆盖已停止读取") from exc
            if not isinstance(state, dict):
                raise StorageError("云端课表数据格式无效，为避免覆盖已停止读取")
            return deepcopy(state)

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            result = self._execute("SET", self.key, serialized)
            if result != "OK":
                raise StorageError("云端数据保存未成功，请稍后重试")
            return deepcopy(state)


def create_state_store() -> SettingsStoreMixin:
    rest_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if bool(rest_url) != bool(token):
        raise RuntimeError("UPSTASH_REDIS_REST_URL 与 UPSTASH_REDIS_REST_TOKEN 必须同时配置")
    if rest_url and token:
        key = os.getenv("CLASSASSIGN_STATE_KEY", "classassign:state:v1").strip()
        return UpstashStateStore(rest_url, token, key=key)
    return StateStore()


store = create_state_store()
