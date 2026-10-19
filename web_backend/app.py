"""Starlette API: chỉ vận chuyển dữ liệu giữa Web UI và Python runtime."""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from web_runtime import WebFaceCheckRuntime


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web_ui"
runtime = WebFaceCheckRuntime(load_ai=os.getenv("FACECHECK_WEB_SKIP_AI") != "1")
DISPLAY_TOKEN = os.getenv("FACECHECK_DISPLAY_TOKEN", "")
ADMIN_TOKEN = os.getenv("FACECHECK_ADMIN_TOKEN", "")


def api_error(exc):
    status = 404 if isinstance(exc, KeyError) else 400
    return JSONResponse({"ok": False, "error": str(exc).strip("'")}, status_code=status)


async def health(_request):
    return JSONResponse({"ok": True, "service": "FaceCheck Web UI"})


async def state(_request):
    return JSONResponse(runtime.snapshot())




async def display_frame(_request):
    if not _display_authorized(_request):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    frame_id, frame = runtime.display_jpeg_frame()
    if not frame:
        return Response(status_code=204)
    state = runtime.display_snapshot()

    def header_value(key, default=""):
        return str(state.get(key, default)).replace("\r", " ").replace("\n", " ")

    return Response(
        frame,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Frame-Id": str(frame_id),
            "X-FaceCheck-Mode": header_value("mode", "idle"),
            "X-FaceCheck-State": header_value("state", "idle"),
            "X-FaceCheck-Title": header_value("title"),
            "X-FaceCheck-Name": header_value("name"),
            "X-FaceCheck-Employee": header_value("employee_id"),
            "X-FaceCheck-Fps": header_value("fps", 0),
            "X-FaceCheck-Brightness": header_value("brightness", 0),
            "X-FaceCheck-Sample-Count": header_value("sample_count", 0),
            "X-FaceCheck-Sample-Target": header_value("sample_target", 0),
        },
    )




async def display_health(request):
    if not _display_authorized(request):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    return JSONResponse({"ok": True, "service": "FaceCheck TFT display"})


def _display_authorized(request: Request):
    supplied = request.headers.get("x-facecheck-token", "")
    return bool(DISPLAY_TOKEN) and secrets.compare_digest(supplied, DISPLAY_TOKEN)


def _admin_authorized(request: Request):
    supplied = request.headers.get("x-facecheck-admin-token", "")
    return bool(ADMIN_TOKEN) and secrets.compare_digest(supplied, ADMIN_TOKEN)


def _admin_guard(request: Request):
    if not ADMIN_TOKEN:
        return JSONResponse(
            {"ok": False, "error": "FACECHECK_ADMIN_TOKEN chưa được cấu hình"},
            status_code=503,
        )
    if not _admin_authorized(request):
        return JSONResponse(
            {"ok": False, "error": "Mã quản trị không hợp lệ"},
            status_code=401,
        )
    return None


async def admin_auth(request: Request):
    rejected = _admin_guard(request)
    if rejected:
        return rejected
    return JSONResponse({"ok": True})


async def fingerprints(request: Request):
    rejected = _admin_guard(request)
    if rejected:
        return rejected
    return JSONResponse({"ok": True, **runtime.fingerprint_management()})


async def fingerprint_enroll(request: Request):
    rejected = _admin_guard(request)
    if rejected:
        return rejected
    try:
        payload = await request.json()
        return JSONResponse(
            {"ok": True, **runtime.enroll_fingerprint(payload)}, status_code=202
        )
    except Exception as exc:
        return api_error(exc)


async def fingerprint_delete(request: Request):
    rejected = _admin_guard(request)
    if rejected:
        return rejected
    try:
        fingerprint_id = int(request.path_params["fingerprint_id"])
        payload = await request.json()
        confirmation = str(payload.get("confirmation_id", "")).strip()
        if confirmation != str(fingerprint_id):
            return JSONResponse(
                {"ok": False, "error": "Phải nhập lại đúng ID vân tay để xác nhận"},
                status_code=400,
            )
        if runtime.door_service.fingerprint(fingerprint_id) is None:
            return JSONResponse(
                {"ok": False, "error": "ID vân tay không có trong danh sách quản lý"},
                status_code=404,
            )
        return JSONResponse(
            {"ok": True, **runtime.delete_fingerprint(fingerprint_id)}, status_code=202
        )
    except Exception as exc:
        return api_error(exc)


async def fingerprint_update(request: Request):
    rejected = _admin_guard(request)
    if rejected:
        return rejected
    try:
        fingerprint_id = int(request.path_params["fingerprint_id"])
        item = runtime.update_fingerprint(fingerprint_id, await request.json())
        return JSONResponse({"ok": True, "item": item})
    except Exception as exc:
        return api_error(exc)


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


async def enrollment_start(_request):
    try:
        return JSONResponse(runtime.start_enrollment_scan())
    except Exception as exc:
        return api_error(exc)


async def enrollment_activity(_request):
    return JSONResponse(runtime.note_enrollment_activity())


async def enrollment_finish(_request):
    try:
        return JSONResponse(runtime.finish_enrollment_session())
    except Exception as exc:
        return api_error(exc)


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
    return FileResponse(
        image,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


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


async def door_events(_request):
    return JSONResponse(runtime.door_events())


async def camera_stream(_request):
    async def frames():
        last_frame_id = -1
        try:
            while True:
                frame_id, frame = runtime.camera_preview_jpeg_frame()
                if frame and frame_id != last_frame_id:
                    last_frame_id = frame_id
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.01)
        except (asyncio.CancelledError, GeneratorExit):
            pass
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
    Route("/api/enrollment/start", enrollment_start, methods=["POST"]),
    Route("/api/enrollment/activity", enrollment_activity, methods=["POST"]),
    Route("/api/enrollment/finish", enrollment_finish, methods=["POST"]),
    Route("/api/employees", employees),
    Route("/api/employees/{employee_id:str}/image", employee_image),
    Route("/api/employees/{employee_id:str}", employee_update, methods=["PUT"]),
    Route("/api/employees/{employee_id:str}/toggle", employee_toggle, methods=["POST"]),
    Route("/api/employees/{employee_id:str}", employee_delete, methods=["DELETE"]),
    Route("/api/attendance", history),
    Route("/api/dashboard", dashboard),
    Route("/api/door/events", door_events),
    Route("/api/admin/auth", admin_auth, methods=["POST"]),
    Route("/api/admin/fingerprints", fingerprints),
    Route("/api/admin/fingerprints/enroll", fingerprint_enroll, methods=["POST"]),
    Route(
        "/api/admin/fingerprints/{fingerprint_id:int}",
        fingerprint_delete,
        methods=["DELETE"],
    ),
    Route(
        "/api/admin/fingerprints/{fingerprint_id:int}",
        fingerprint_update,
        methods=["PUT"],
    ),
    Route("/api/camera/stream", camera_stream),
    WebSocketRoute("/ws/state", state_socket),
    Mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web-ui"),
]

app = Starlette(routes=routes, lifespan=lifespan)

display_app = Starlette(routes=[
    Route("/health", display_health),
    Route("/frame.jpg", display_frame),
])
