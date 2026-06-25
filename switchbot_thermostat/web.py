"""Mobile web UI + JSON API, served in-process by the control-loop daemon.

All hardware access goes through the shared :class:`Controller`, whose BLE lock
serialises it against the control loop — so the web handlers never touch the
Bluetooth adapter directly or concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from . import runtime
from .config import Config
from .controller import Controller
from .runtime import Overrides

_LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web"

# Frontend files served at the site root (sw.js must be at root for PWA scope).
_STATIC_FILES = {
    "app.js": "application/javascript",
    "style.css": "text/css",
    "manifest.webmanifest": "application/manifest+json",
    "sw.js": "application/javascript",
    "icon.svg": "image/svg+xml",
    "apple-touch-icon.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
}


def create_app(controller: Controller, config: Config) -> web.Application:
    app = web.Application()
    token = config.web.auth_token

    @web.middleware
    async def auth_mw(request: web.Request, handler):
        if token and request.path.startswith("/api/"):
            sent = request.headers.get("X-Auth-Token") or request.query.get("token")
            if sent != token:
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    app.middlewares.append(auth_mw)

    async def status(request: web.Request) -> web.Response:
        return web.json_response(controller.get_status())

    async def refresh(request: web.Request) -> web.Response:
        return web.json_response(await controller.refresh())

    async def set_target(request: web.Request) -> web.Response:
        data = await request.json()
        ov = Overrides.load(config.overrides_file)
        try:
            runtime.set_value(ov, "target", str(data["value"]))
        except (KeyError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        ov.save(config.overrides_file)
        return web.json_response(controller.get_status())

    async def set_mode(request: web.Request) -> web.Response:
        data = await request.json()
        ov = Overrides.load(config.overrides_file)
        try:
            runtime.set_value(ov, "action", str(data["action"]))
        except (KeyError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        ov.save(config.overrides_file)
        return web.json_response(controller.get_status())

    async def set_pause(request: web.Request) -> web.Response:
        data = await request.json()
        ov = Overrides.load(config.overrides_file)
        ov.paused = bool(data.get("paused", False))
        ov.save(config.overrides_file)
        return web.json_response(controller.get_status())

    async def set_output(request: web.Request) -> web.Response:
        data = await request.json()
        result = await controller.apply_output(bool(data["on"]), force=bool(data.get("force", False)))
        return web.json_response({**controller.get_status(), "action_result": result})

    async def correct_state(request: web.Request) -> web.Response:
        data = await request.json()
        controller.correct_state(bool(data["on"]))
        return web.json_response(controller.get_status())

    async def set_timer(request: web.Request) -> web.Response:
        data = await request.json()
        try:
            if data.get("clear"):
                controller.clear_timer()
            elif "minutes" in data:
                minutes = float(data["minutes"])
                if minutes <= 0:
                    raise ValueError("minutes must be positive")
                controller.set_timer_in(minutes)
            elif "at" in data:
                hour, minute = (int(x) for x in str(data["at"]).split(":"))
                controller.set_timer_at(hour, minute)
            else:
                return web.json_response({"error": "specify minutes, at, or clear"}, status=400)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": f"invalid timer: {exc}"}, status=400)
        return web.json_response(controller.get_status())

    app.router.add_get("/api/status", status)
    app.router.add_post("/api/refresh", refresh)
    app.router.add_post("/api/target", set_target)
    app.router.add_post("/api/mode", set_mode)
    app.router.add_post("/api/pause", set_pause)
    app.router.add_post("/api/output", set_output)
    app.router.add_post("/api/state", correct_state)
    app.router.add_post("/api/timer", set_timer)

    # Always revalidate the app shell so a redeploy is picked up immediately
    # (FileResponse adds ETag/Last-Modified, so unchanged files still 304).
    no_cache = {"Cache-Control": "no-cache"}

    async def index(request: web.Request) -> web.Response:
        return web.FileResponse(STATIC_DIR / "index.html", headers=dict(no_cache))

    app.router.add_get("/", index)
    for fname, content_type in _STATIC_FILES.items():
        def _make(name: str, ctype: str):
            async def _serve(request: web.Request) -> web.Response:
                return web.FileResponse(
                    STATIC_DIR / name, headers={"Content-Type": ctype, **no_cache}
                )
            return _serve
        app.router.add_get(f"/{fname}", _make(fname, content_type))

    return app


async def run_web(controller: Controller, config: Config) -> None:
    """Start the web server and serve until cancelled."""
    app = create_app(controller, config)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.web.host, config.web.port)
    await site.start()
    _LOGGER.info("Web UI listening on http://%s:%d", config.web.host, config.web.port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
