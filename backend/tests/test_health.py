import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


def test_health_contract():
    """基础入口独立可用，无需启动采集器、数据库或 AI 服务。"""
    async def request_health():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
