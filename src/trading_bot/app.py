from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from trading_bot.bootstrap.container import AppContainer
from trading_bot.domain.enums import ServiceStatus


def create_app(container: AppContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await container.startup()
        try:
            yield
        finally:
            await container.shutdown()

    app = FastAPI(title=container.config.runtime.service_name, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return (await container.health_checker.check_health()).model_dump(mode="json")

    @app.get("/ready")
    async def ready():
        report = await container.health_checker.check_readiness()
        status_code = 200 if report.status == ServiceStatus.OK else 503
        return JSONResponse(status_code=status_code, content=report.model_dump(mode="json"))

    @app.get("/metrics")
    async def metrics():
        return Response(content=container.metrics.render(), media_type=container.metrics.content_type)

    return app
