from fastapi import APIRouter, HTTPException, Request

from core.engine import EngineError
from server.schemas import SwitchModelRequest


router = APIRouter(prefix="/api/models", tags=["Models"])


@router.get("")
def list_models(request: Request):
    engine = request.app.state.engine
    models = engine.config.refresh_models()

    if engine.current_model not in models and models:
        engine.current_model = engine.config.inference.default_model

    return {
        "models": [
            {
                "name": name,
                "file": model.file,
                "role": model.role,
                "quantization": model.quantization,
                "context_size": model.context_size,
                "size_bytes": model.size_bytes,
                "available": model.available,
            }
            for name, model in models.items()
        ],
        "active_model": engine.current_model,
        "model_dir": str(engine.config.model_dir),
    }


@router.get("/status")
def model_status(request: Request):
    engine = request.app.state.engine
    return engine.get_status()


@router.post("/switch")
def switch_model(
    payload: SwitchModelRequest,
    request: Request
):
    engine = request.app.state.engine

    try:
        status = engine.switch_model(payload.model)
        return {
            "success": True,
            "active_model": payload.model,
            "status": status
        }

    except (EngineError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc
