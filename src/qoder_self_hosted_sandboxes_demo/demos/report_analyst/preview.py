# SPDX-License-Identifier: Apache-2.0
"""本机预览代理与只读浏览器画面。

演示目标：让浏览器直接看到云端页面和 Chromium 画面，访问令牌由本机代理添加。
执行流程：仅绑定回环地址，代理到指定分析服务；通过 CDP 只读截图观察 Browser Sandbox。
能力体现：预览后端运行在云端，页面更新来自真实文件；本机只承担鉴权代理与展示。
"""

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout


async def serve(config, port, status_provider=None):
    frame = {"data": b"", "error": "starting", "received": 0.0}
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout) as client:

        async def remote(request):
            if request.path == "/api/demo-status":
                status = (
                    await asyncio.to_thread(status_provider.snapshot)
                    if status_provider else
                    {"state": "unavailable", "message": "请通过项目 preview 命令启动会话状态检查。", "session_url": None, "phases": {}}
                )
                if status["state"] == "ready":
                    try:
                        async with client.get(
                            config["analysis_url"] + "/health",
                            headers={"e2b-traffic-access-token": config["analysis_token"]},
                            allow_redirects=False,
                            timeout=ClientTimeout(total=3),
                        ) as health:
                            if health.status != 200:
                                raise RuntimeError("Analysis service unavailable")
                    except Exception:
                        status.update(state="unavailable", message="分析 Sandbox 服务暂不可用，请检查实例是否到期。", session_url=None)
                status["browser_live"] = bool(frame["data"] and time.monotonic() - frame["received"] <= 3)
                return web.json_response(status, headers={"Cache-Control": "no-store"})
            if request.path == "/live/frame":
                if not frame["data"] or time.monotonic() - frame["received"] > 3:
                    return web.Response(status=503, text="Waiting for CDP frame")
                return web.Response(
                    body=frame["data"],
                    content_type="image/png",
                    headers={"Cache-Control": "no-store"},
                )
            path = request.rel_url.path_qs
            try:
                async with client.get(
                    config["analysis_url"] + path,
                    headers={"e2b-traffic-access-token": config["analysis_token"]},
                    allow_redirects=False,
                ) as response:
                    data = await response.read()
                    headers = {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() in {"content-type", "content-disposition", "cache-control"}
                    }
                    if not request.path.startswith("/files/") and request.path != "/api/download":
                        headers = {k: v for k, v in headers.items() if k.lower() != "content-disposition"}
                    return web.Response(status=response.status, body=data, headers=headers)
            except Exception:
                return web.json_response(
                    {"error": "Sandbox 服务暂不可用，请检查实例是否到期或重启预览。"},
                    status=503, headers={"Cache-Control": "no-store"},
                )

        async def observe():
            while True:
                try:
                    async with client.ws_connect(
                        config["cdp_url"],
                        headers={"X-Access-Token": config["browser_token"]},
                        heartbeat=20,
                    ) as ws:
                        seq = 0

                        async def rpc(method, params=None, session=None):
                            nonlocal seq
                            seq += 1
                            identifier = seq
                            payload = {
                                "id": identifier,
                                "method": method,
                                "params": params or {},
                            }
                            if session:
                                payload["sessionId"] = session
                            await ws.send_json(payload)
                            while True:
                                message = await ws.receive_json(timeout=15)
                                if message.get("id") == identifier:
                                    if "error" in message:
                                        raise RuntimeError(
                                            message["error"].get("message", "CDP error")
                                        )
                                    return message.get("result", {})

                        target = None
                        while target is None:
                            targets = (await rpc("Target.getTargets")).get(
                                "targetInfos", []
                            )
                            pages = [t for t in targets if t["type"] == "page"]
                            target = next(
                                (
                                    t
                                    for t in pages
                                    if t.get("url", "").startswith(
                                        config["analysis_url"]
                                    )
                                ),
                                pages[0] if pages else None,
                            )
                            if target is None:
                                await asyncio.sleep(0.5)
                        session = (
                            await rpc(
                                "Target.attachToTarget",
                                {"targetId": target["targetId"], "flatten": True},
                            )
                        )["sessionId"]
                        while True:
                            result = await rpc(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                                session,
                            )
                            frame["data"] = base64.b64decode(result["data"])
                            frame["received"] = time.monotonic()
                            frame["error"] = ""
                            await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    frame["data"] = b""
                    frame["error"] = type(exc).__name__
                    await asyncio.sleep(1)

        app = web.Application()
        app.router.add_get("/{tail:.*}", remote)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        task = asyncio.create_task(observe())
        print(f"Preview ready: http://127.0.0.1:{port}", flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            task.cancel()
            await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(json.loads(Path(args.config).read_text()), args.port))
