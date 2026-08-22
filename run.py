from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("CLASSASSIGN_HOST", "127.0.0.1"),
        port=int(os.getenv("CLASSASSIGN_PORT", "8765")),
        reload=False,
    )

