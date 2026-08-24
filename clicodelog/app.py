from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import PACKAGE_DIR
from .routes import router

app = FastAPI(title="CLI Code Log", version=__version__)

# There is deliberately no CORSMiddleware here. The SPA is served from this same
# origin and never needed it, while `allow_origins=["*"]` with
# `allow_credentials=True` made Starlette reflect any requesting page's Origin —
# so any website open in the browser could read every transcript from
# 127.0.0.1:6126 and delete bookmarks through it.


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/js/vendor/"):
        # Vendored libraries are immutable for a given release.
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path.startswith("/static") or path in ("/", "/view"):
        # `no-cache` still revalidates on every load, preserving the
        # edit-and-refresh loop, but allows a 304 instead of a full re-download.
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _render(request: Request, name: str):
    # FastAPI/Starlette changed TemplateResponse to accept `request` first.
    # Support both call styles so fresh installs and older environments work.
    try:
        return templates.TemplateResponse(request=request, name=name)
    except TypeError:
        return templates.TemplateResponse(name, {"request": request})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _render(request, "index.html")


@app.get("/view", response_class=HTMLResponse)
async def view(request: Request):
    return _render(request, "view.html")


app.include_router(router)
