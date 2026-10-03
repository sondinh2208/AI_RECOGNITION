"""Starlette API: chỉ vận chuyển dữ liệu giữa Web UI và Python runtime."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from web_runtime import WebFaceCheckRuntime


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web_ui"
runtime = WebFaceCheckRuntime(load_ai=os.getenv("FACECHECK_WEB_SKIP_AI") != "1")


def api_error(exc):
    status = 404 if isinstance(exc, KeyError) else 400
    return JSONResponse({"ok": False, "error": str(exc).strip("'")}, status_code=status)


async def health(_request):
    return JSONResponse({"ok": True, "service": "FaceCheck Web UI"})


async def state(_request):
    return JSONResponse(runtime.snapshot())


async def switch_mode(request: Request):
    try:
        data = await request.json()
        return JSONResponse(runtime.switch_mode(str(data.get("mode", ""))))
    except Exception as exc:
        return api_error(exc)


async def retry(_request):
    return JSONResponse(runtime.retry_recognition())


async def recognition_test(request: Request):
    try:
        filename = unquote(request.headers.get("x-filename", "test.jpg"))
        return JSONResponse(
            runtime.test_recognition_image(
                await request.body(), suffix=Path(filename).suffix,
                display_name=Path(filename).name,
            ),
            status_code=202,
        )
    except Exception as exc:
        return api_error(exc)


async def enrollment_save(request: Request):
    try:
        return JSONResponse(runtime.save_enrollment(await request.json()), status_code=202)
    except Exception as exc:
        return api_error(exc)


async def enrollment_reset(_request):
    return JSONResponse(runtime.reset_enrollment())


async def employees(request: Request):
    params = request.query_params
    try:
        result = runtime.list_employees(
            query=params.get("query", ""),
            page=int(params.get("page", "1")),
            page_size=int(params.get("page_size", "10")),
        )
        return JSONResponse(result)
    except Exception as exc:
        return api_error(exc)


async def employee_image(request: Request):
    image = runtime.employee_image(request.path_params["employee_id"])
    if image is None:
        return JSONResponse({"error": "Không tìm thấy ảnh"}, status_code=404)
    return FileResponse(image)


async def employee_update(request: Request):
    try:
        result = runtime.update_employee(
            request.path_params["employee_id"], await request.json()
        )
        return JSONResponse({"ok": True, "employee": result})
    except Exception as exc:
        return api_error(exc)


async def employee_toggle(request: Request):
    try:
        return JSONResponse({"ok": True, **runtime.toggle_employee(request.path_params["employee_id"])})
    except Exception as exc:
        return api_error(exc)


async def employee_delete(request: Request):
    try:
        runtime.delete_employee(request.path_params["employee_id"])
        return JSONResponse({"ok": True})
    except Exception as exc:
        return api_error(exc)


async def history(request: Request):
    params = request.query_params
    try:
        return JSONResponse(runtime.history_page(
            page=int(params.get("page", "1")),
            page_size=int(params.get("page_size", "10")),
            status=params.get("status", ""), query=params.get("query", ""),
        ))
    except Exception as exc:
        return api_error(exc)


async def dashboard(_request):
    return JSONResponse(runtime.dashboard())


async def camera_stream(_request):
    async def frames():
        while True:
            frame = runtime.camera_jpeg()
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0.05)
    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def state_socket(websocket):
    await websocket.accept()
    try:
        while True:
            # Telemetry and the automatic enrollment countdown change inside the
            # existing camera workers without emitting UI events. A lightweight
            # snapshot keeps those values live without rebuilding any page.
            await websocket.send_json(runtime.snapshot())
            await asyncio.sleep(0.35)
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, runtime.start)
    try:
        yield
    finally:
        runtime.shutdown()


routes = [
    Route("/api/health", health),
    Route("/api/state", state),
    Route("/api/mode", switch_mode, methods=["POST"]),
    Route("/api/recognition/retry", retry, methods=["POST"]),
    Route("/api/recognition/test-image", recognition_test, methods=["POST"]),
    Route("/api/enrollment/save", enrollment_save, methods=["POST"]),
    Route("/api/enrollment/reset", enrollment_reset, methods=["POST"]),
    Route("/api/employees", employees),
    Route("/api/employees/{employee_id:str}/image", employee_image),
    Route("/api/employees/{employee_id:str}", employee_update, methods=["PUT"]),
    Route("/api/employees/{employee_id:str}/toggle", employee_toggle, methods=["POST"]),
    Route("/api/employees/{employee_id:str}", employee_delete, methods=["DELETE"]),
    Route("/api/attendance", history),
    Route("/api/dashboard", dashboard),
    Route("/api/camera/stream", camera_stream),
    WebSocketRoute("/ws/state", state_socket),
    Mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web-ui"),
]

app = Starlette(routes=routes, lifespan=lifespan)
