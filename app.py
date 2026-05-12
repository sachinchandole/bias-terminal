"""
Market Bias Terminal — Flask app.

Routes:
    GET  /                        → dashboard HTML
    GET  /api/health              → diagnostic
    GET  /api/snapshot/<symbol>   → full data snapshot (NIFTY or BANKNIFTY)
    GET  /api/option-chain/<sym>  → option chain only
    GET  /api/vix                 → India VIX
    GET  /api/fii-dii             → FII/DII cash
    GET  /api/participants        → participant-wise OI

Deploy on Render free tier (see README.md).
"""

import logging
import os
from datetime import datetime

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

import nse_fetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")

app = Flask(__name__, static_folder="static")
CORS(app)  # allow any origin — public API


@app.route("/")
def root():
    return send_from_directory("static", "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "nse_available": nse_fetcher.NSE_AVAILABLE,
        "time_utc": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/api/snapshot/<symbol>")
def snapshot(symbol):
    try:
        data = nse_fetcher.snapshot(symbol)
        return jsonify(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("snapshot failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/option-chain/<symbol>")
def option_chain(symbol):
    try:
        return jsonify(nse_fetcher.fetch_option_chain_summary(symbol))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("option chain failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/vix")
def vix():
    try:
        return jsonify(nse_fetcher.fetch_india_vix())
    except Exception as exc:
        log.exception("vix failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/fii-dii")
def fii_dii():
    try:
        return jsonify(nse_fetcher.fetch_fii_dii_cash())
    except Exception as exc:
        log.exception("fii/dii failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/participants")
def participants():
    try:
        return jsonify(nse_fetcher.fetch_participant_oi())
    except Exception as exc:
        log.exception("participants failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
