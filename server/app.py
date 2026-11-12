from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.engine import Engine

from server.routes import (
    chat,
    conversations,
    files,
    models,
    system
)


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[PAIN] Initializing engine...")
    engine = Engine()
    app.state.engine = engine
    print("[PAIN] Engine ready.")

    yield

    print("[PAIN] Shutting down...")
    engine.close()
    print("[PAIN] Shutdown complete.")


app = FastAPI(
    title="PAIN API",
    description="Personal AI Network",
    version="0.1.0",
    lifespan=lifespan
)


app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(files.router)
app.include_router(models.router)
app.include_router(system.router)


app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static"
)


@app.get("/api/health")
def health():
    return {
        "status": "online",
        "name": "PAIN",
        "version": "0.1.0"
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")