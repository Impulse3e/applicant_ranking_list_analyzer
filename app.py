from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from ranking import DataSourceError, RankingIndex


BASE_DIR = Path(__file__).resolve().parent


class SearchRateLimiter:
    """Small in-memory limiter that never retains raw client addresses."""

    def __init__(self, secret: bytes, limit: int = 20, window_seconds: int = 60):
        self._secret = secret
        self._limit = limit
        self._window_seconds = window_seconds
        self._requests: dict[bytes, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _client_key(self, address: str) -> bytes:
        return hmac.new(
            self._secret, address.encode(), hashlib.sha256
        ).digest()

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        oldest_allowed = now - self._window_seconds
        key = self._client_key(address)

        with self._lock:
            attempts = self._requests[key]
            while attempts and attempts[0] <= oldest_allowed:
                attempts.popleft()
            if len(attempts) >= self._limit:
                return False
            attempts.append(now)
            return True


def _secret_from_config(app: Flask) -> bytes:
    configured = app.config.get("HMAC_SECRET")
    if configured is None:
        configured = os.environ.get("APP_HMAC_SECRET")
    if configured is None:
        return secrets.token_bytes(32)
    if isinstance(configured, str):
        configured = configured.encode()
    if not isinstance(configured, bytes):
        raise TypeError("HMAC_SECRET must be bytes or string.")
    if len(configured) < 32:
        raise ValueError("APP_HMAC_SECRET must contain at least 32 bytes.")
    return configured


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATA_DIRECTORY=BASE_DIR / "data",
        MAX_CONTENT_LENGTH=4 * 1024,
        SEARCH_RATE_LIMIT=20,
        SEARCH_RATE_WINDOW=60,
    )
    if config:
        app.config.update(config)

    app.json.ensure_ascii = False
    secret = _secret_from_config(app)
    index = RankingIndex.from_directory(
        Path(app.config["DATA_DIRECTORY"]),
        secret=secret,
        campaign="2026",
    )
    limiter = SearchRateLimiter(
        secret=secret,
        limit=int(app.config["SEARCH_RATE_LIMIT"]),
        window_seconds=int(app.config["SEARCH_RATE_WINDOW"]),
    )
    app.extensions["ranking_index"] = index
    app.extensions["search_rate_limiter"] = limiter

    @app.after_request
    def apply_security_headers(response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index_page():
        return render_template(
            "index.html",
            source_count=index.source_count,
            entry_count=index.entry_count,
        )

    @app.post("/api/search")
    def search():
        client_address = request.remote_addr or "unknown"
        if not limiter.allow(client_address):
            return (
                jsonify(
                    {
                        "error": (
                            "Слишком много запросов. Повторите попытку через минуту."
                        )
                    }
                ),
                429,
            )

        if not request.is_json:
            return jsonify({"error": "Ожидается JSON-запрос."}), 415

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "applicant_code" not in payload:
            return jsonify({"error": "Укажите код поступающего."}), 400

        try:
            result = index.public_result(payload["applicant_code"])
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(result)

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "sources": index.source_count,
                "entries": index.entry_count,
            }
        )

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify({"error": "Запрос превышает допустимый размер."}), 413

    @app.errorhandler(DataSourceError)
    def data_source_error(_error):
        return jsonify({"error": "Ошибка загрузки источников данных."}), 503

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
