"""测试 install_error_handler 把各类异常统一包装成 ``{code, message, details}``。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from inalpha_shared.errors import NotFoundError, ValidationError
from inalpha_shared.middleware import install_error_handler, install_request_logging


def _build_app() -> FastAPI:
    app = FastAPI()
    install_request_logging(app)
    install_error_handler(app)

    class Body(BaseModel):
        x: int

    @app.get("/inalpha-error")
    def raise_inalpha() -> None:
        raise NotFoundError("strategy not found", details={"id": "abc"})

    @app.get("/business-validation")
    def raise_business_validation() -> None:
        raise ValidationError("size must be positive")

    @app.post("/schema-validation")
    def schema_validation(body: Body) -> dict[str, int]:
        return {"x": body.x}

    @app.get("/http-exception")
    def raise_http() -> None:
        raise HTTPException(status_code=503, detail="upstream down")

    @app.get("/unhandled")
    def raise_unhandled() -> None:
        raise RuntimeError("oops")

    return app


def test_inalpha_error_passes_through() -> None:
    client = TestClient(_build_app())
    r = client.get("/inalpha-error")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "strategy not found"
    assert body["details"] == {"id": "abc"}


def test_business_validation_error() -> None:
    client = TestClient(_build_app())
    r = client.get("/business-validation")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_request_schema_validation_error() -> None:
    client = TestClient(_build_app())
    r = client.post("/schema-validation", json={"x": "not-an-int"})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "request body validation failed"
    assert "errors" in body["details"]


def test_plain_http_exception_wrapped() -> None:
    client = TestClient(_build_app())
    r = client.get("/http-exception")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "HTTP_ERROR"
    assert body["message"] == "upstream down"


def test_unhandled_exception_becomes_500() -> None:
    # TestClient 默认 raise_server_exceptions=True 会把 RuntimeError 抛到客户端，
    # 这里关掉以验证 exception_handler 真的把它包装成了 500 JSON 响应
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/unhandled")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "INTERNAL_ERROR"


def test_trace_id_echoed_in_response_header() -> None:
    client = TestClient(_build_app())
    r = client.get("/business-validation", headers={"X-Trace-Id": "trace-abc"})
    assert r.headers["x-trace-id"] == "trace-abc"


def test_trace_id_auto_generated_when_missing() -> None:
    client = TestClient(_build_app())
    r = client.get("/business-validation")
    assert "x-trace-id" in r.headers
    assert len(r.headers["x-trace-id"]) == 36  # UUID
