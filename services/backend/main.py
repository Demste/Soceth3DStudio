import contextlib
import hmac
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import control, routes
from .core import BASE, PORT, ROOT, init
from .jobs import worker
from .logging_config import configure
from .media import MESH_LIMIT

COOKIE = "soceth_session"
DIST = ROOT / "apps/desktop/dist"


@asynccontextmanager
async def lifecycle(app):
    app.state.token = init()
    configure()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop,), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(5)


app = FastAPI(title="Soceth 3D Studio", lifespan=lifecycle, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


def valid_origin(request):
    origin = request.headers.get("origin")
    return origin is None or origin in {BASE, f"http://localhost:{PORT}"}


def valid_token(request, token):
    try:
        return bool(token) and hmac.compare_digest(token.encode("utf-8"), request.app.state.token.encode("ascii"))
    except AttributeError:
        return False


def authorized(request):
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
    return valid_token(request, bearer) or valid_token(request, request.cookies.get(COOKIE))


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        if not valid_origin(request):
            return JSONResponse({"detail": "This API only accepts local Studio requests."}, status_code=403)
        if path not in {"/api/health", "/api/session"} and not authorized(request):
            return JSONResponse({"detail": "The local Studio session has expired."}, status_code=401)
        limit = MESH_LIMIT + 1024**2 if path.endswith("/files") else 2 * 1024**2
        length = request.headers.get("content-length")
        if path.endswith("/files") and request.method == "POST" and not length:
            return JSONResponse({"detail": "File uploads require a content length."}, status_code=411)
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse({"detail": "Request exceeds the upload limit."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; connect-src 'self' blob: " + BASE.replace("http:", "ws:") + "; object-src 'none'; frame-ancestors 'none'")
    return response


@app.exception_handler(ValueError)
async def invalid(_, error):
    return JSONResponse({"detail": str(error)}, status_code=422)


@app.get("/api/health")
def health():
    return {"service": "soceth", "protocol_version": 1}


@app.post("/api/session")
def session(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not valid_token(request, token):
        return JSONResponse({"detail": "Launch Studio from start.ps1 to authorize this session."}, status_code=401)
    response = Response(status_code=204)
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/api")
    return response


app.include_router(routes.router)
app.include_router(control.router)
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="desktop")

