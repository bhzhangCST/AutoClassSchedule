from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Local secrets live in .env (ignored by Git). Render injects the same names as
# environment variables, which take precedence because override is disabled.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def required_setting(name: str, *, min_length: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < min_length:
        raise RuntimeError(
            f"缺少或未正确设置环境变量 {name}。"
            "请复制 .env.example 为 .env 后填写，或在部署平台中配置该变量。"
        )
    return value
