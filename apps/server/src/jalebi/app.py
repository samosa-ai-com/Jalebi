"""Flask application factory and CLI entrypoint."""

import os

from flask import Flask, Response, jsonify


def create_app() -> Flask:
    """Create and configure the Jalebi Flask application."""
    app = Flask(__name__)

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    return app


def main() -> None:
    """Run the development server, bound to localhost only."""
    host = os.environ.get("JALEBI_HOST", "127.0.0.1")
    port = int(os.environ.get("JALEBI_PORT", "3456"))
    create_app().run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
