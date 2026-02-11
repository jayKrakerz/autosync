#!/usr/bin/env python3
"""AutoSync Web Dashboard — Flask application."""

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, redirect, render_template, request

import admin_pin
import config as cfg
from config import save_user_config
from log_handler import sse_handler
from onedrive_api import get_api_base, validate_share_link
from state_db import load_state
from sync_manager import SyncManager

# ---------------------------------------------------------------------------
# Logging setup — attach SSE handler to root logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
root_logger = logging.getLogger()
root_logger.addHandler(sse_handler)

logger = logging.getLogger("app")

# ---------------------------------------------------------------------------
# Flask app & sync manager
# ---------------------------------------------------------------------------
app = Flask(__name__)
manager = SyncManager()


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: Status
# ---------------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    return jsonify(manager.get_status())


# ---------------------------------------------------------------------------
# Admin PIN helpers
# ---------------------------------------------------------------------------
def _require_admin():
    """Check X-Admin-Token header. Returns error response or None if OK."""
    if not admin_pin.is_pin_set():
        return jsonify({"ok": False, "error": "PIN not set"}), 401
    token = request.headers.get("X-Admin-Token", "")
    if not admin_pin.validate_session(token):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return None


# ---------------------------------------------------------------------------
# API: Admin PIN endpoints
# ---------------------------------------------------------------------------
@app.route("/api/admin/status")
def api_admin_status():
    return jsonify({"pin_set": admin_pin.is_pin_set()})


@app.route("/api/admin/set-pin", methods=["POST"])
def api_admin_set_pin():
    data = request.get_json(force=True)
    pin = data.get("pin", "")
    if len(pin) < 4:
        return jsonify({"ok": False, "error": "PIN must be at least 4 characters"}), 400

    if admin_pin.is_pin_set():
        # Changing PIN — require existing session
        err = _require_admin()
        if err:
            return err

    admin_pin.set_pin(pin)
    token = admin_pin.generate_session_token()
    return jsonify({"ok": True, "token": token})


@app.route("/api/admin/verify", methods=["POST"])
def api_admin_verify():
    data = request.get_json(force=True)
    pin = data.get("pin", "")
    if not admin_pin.verify_pin(pin):
        return jsonify({"ok": False, "error": "Incorrect PIN"}), 403
    token = admin_pin.generate_session_token()
    return jsonify({"ok": True, "token": token})


@app.route("/api/admin/lock", methods=["POST"])
def api_admin_lock():
    token = request.headers.get("X-Admin-Token", "")
    admin_pin.clear_session(token)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API: Config
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_config_get():
    err = _require_admin()
    if err:
        return err
    return jsonify({
        "share_link": cfg.SHARE_LINK,
        "local_folder": cfg.LOCAL_FOLDER,
        "poll_interval": cfg.POLL_INTERVAL,
        "client_id": cfg.CLIENT_ID,
        "tenant_id": cfg.TENANT_ID,
        "ignore_patterns": cfg.IGNORE_PATTERNS,
        "sync_folders": cfg.SYNC_FOLDERS,
        "exclude_folders": cfg.EXCLUDE_FOLDERS,
        "notifications_enabled": cfg.NOTIFICATIONS_ENABLED,
        "max_workers": cfg.MAX_WORKERS,
        "webhook_enabled": cfg.WEBHOOK_ENABLED,
        "webhook_url": cfg.WEBHOOK_URL,
    })


@app.route("/api/config", methods=["POST"])
def api_config_set():
    err = _require_admin()
    if err:
        return err
    if manager.running:
        return jsonify({"ok": False, "error": "Stop sync before changing config"}), 409

    data = request.get_json(force=True)
    updates = {}

    if "share_link" in data:
        updates["share_link"] = data["share_link"].strip()
    if "local_folder" in data:
        updates["local_folder"] = data["local_folder"].strip()
    if "client_id" in data:
        updates["client_id"] = data["client_id"].strip()
    if "tenant_id" in data:
        updates["tenant_id"] = data["tenant_id"].strip()
    if "poll_interval" in data:
        try:
            updates["poll_interval"] = int(data["poll_interval"])
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid poll interval"}), 400
    if "ignore_patterns" in data:
        patterns = data["ignore_patterns"]
        if isinstance(patterns, str):
            patterns = [p.strip() for p in patterns.split("\n") if p.strip()]
        updates["ignore_patterns"] = patterns
    if "sync_folders" in data:
        folders = data["sync_folders"]
        if isinstance(folders, str):
            folders = [f.strip() for f in folders.split("\n") if f.strip()]
        updates["sync_folders"] = folders
    if "exclude_folders" in data:
        folders = data["exclude_folders"]
        if isinstance(folders, str):
            folders = [f.strip() for f in folders.split("\n") if f.strip()]
        updates["exclude_folders"] = folders
    if "notifications_enabled" in data:
        updates["notifications_enabled"] = bool(data["notifications_enabled"])
    if "max_workers" in data:
        try:
            updates["max_workers"] = max(1, int(data["max_workers"]))
        except (ValueError, TypeError):
            pass
    if "webhook_enabled" in data:
        updates["webhook_enabled"] = bool(data["webhook_enabled"])
    if "webhook_url" in data:
        updates["webhook_url"] = data["webhook_url"].strip()

    if updates:
        save_user_config(updates)
        cfg.reload_config()

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API: Files
# ---------------------------------------------------------------------------
@app.route("/api/files")
def api_files():
    try:
        state = load_state(cfg.STATE_DB_PATH)
    except Exception as e:
        return jsonify({"files": [], "error": str(e)})

    files = []
    for path, entry in sorted(state.get("files", {}).items()):
        files.append({
            "path": path,
            "size": entry.get("size", 0),
            "local_mtime": entry.get("local_mtime", ""),
            "remote_mtime": entry.get("remote_mtime", ""),
            "synced_at": entry.get("synced_at", ""),
        })
    return jsonify({"files": files})


# ---------------------------------------------------------------------------
# API: Conflicts
# ---------------------------------------------------------------------------
@app.route("/api/conflicts")
def api_conflicts():
    conflicts = []
    local = cfg.LOCAL_FOLDER
    if not os.path.isdir(local):
        return jsonify({"conflicts": []})

    for root, _dirs, filenames in os.walk(local):
        for fname in filenames:
            if cfg.CONFLICT_SUFFIX in fname:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, local).replace(os.sep, "/")
                try:
                    stat = os.stat(full_path)
                    conflicts.append({
                        "path": rel_path,
                        "original": _guess_original(fname),
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    })
                except OSError:
                    pass

    return jsonify({"conflicts": conflicts})


def _guess_original(fname):
    """Try to reconstruct the original filename from a conflict filename."""
    idx = fname.find(cfg.CONFLICT_SUFFIX)
    if idx == -1:
        return fname
    base = fname[:idx]
    rest = fname[idx + len(cfg.CONFLICT_SUFFIX):]
    dot = rest.rfind(".")
    ext = rest[dot:] if dot != -1 else ""
    return base + ext


# ---------------------------------------------------------------------------
# API: Sync controls
# ---------------------------------------------------------------------------
@app.route("/api/sync/start", methods=["POST"])
def api_sync_start():
    result = manager.start()
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


@app.route("/api/sync/stop", methods=["POST"])
def api_sync_stop():
    result = manager.stop()
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


@app.route("/api/sync/trigger", methods=["POST"])
def api_sync_trigger():
    result = manager.trigger_sync()
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


# ---------------------------------------------------------------------------
# API: Validate share link
# ---------------------------------------------------------------------------
@app.route("/api/validate-link")
def api_validate_link():
    link = request.args.get("url", "").strip()
    if not link:
        return jsonify({"valid": False, "error": "No URL provided"})
    try:
        api_base = get_api_base(link)
        valid = validate_share_link(api_base)
        return jsonify({"valid": valid})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: Authentication (OAuth)
# ---------------------------------------------------------------------------
try:
    import auth
except ImportError:
    auth = None


@app.route("/auth/login")
def auth_login():
    if auth is None:
        return jsonify({"ok": False, "error": "msal not installed"}), 500
    # Verify admin token from query param (browser redirect can't send headers)
    token = request.args.get("admin_token", "")
    if admin_pin.is_pin_set() and not admin_pin.validate_session(token):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    redirect_uri = request.url_root.rstrip("/") + "/auth/callback"
    url = auth.get_auth_url(redirect_uri)
    if url is None:
        return jsonify({"ok": False, "error": "Client ID not configured"}), 400
    return redirect(url)


@app.route("/auth/callback")
def auth_callback():
    if auth is None:
        return "msal not installed", 500
    result = auth.complete_auth(dict(request.args))
    if result is None:
        return "Authentication failed. Close this window and try again.", 400
    return redirect("/")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    if auth is None:
        return jsonify({"ok": False, "error": "msal not installed"}), 500
    err = _require_admin()
    if err:
        return err
    auth.logout()
    return jsonify({"ok": True})


@app.route("/api/auth/status")
def api_auth_status():
    if auth is None:
        return jsonify({"authenticated": False, "user": None, "available": False})
    return jsonify({
        "authenticated": auth.is_authenticated(),
        "user": auth.get_user_info(),
        "available": True,
    })


# ---------------------------------------------------------------------------
# API: SSE log stream
# ---------------------------------------------------------------------------
@app.route("/api/logs/stream")
def api_logs_stream():
    def generate():
        q = sse_handler.subscribe()
        try:
            while True:
                try:
                    entry = q.get(timeout=30)
                    data = json.dumps(entry)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            sse_handler.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# API: Sync History
# ---------------------------------------------------------------------------
@app.route("/api/history")
def api_history():
    try:
        import sync_history
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        entries = sync_history.get_history(limit=limit, offset=offset)
        return jsonify({"history": entries})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})


# ---------------------------------------------------------------------------
# API: Health
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    try:
        import health_monitor
        token_expiry = None
        if auth is not None:
            try:
                token_expiry = auth.get_token_expiry()
            except Exception:
                pass
        health = health_monitor.get_health(token_expires_in=token_expiry)
        return jsonify(health)
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------------------------------------------------------------------------
# API: Auto-Start (LaunchAgent on macOS, Startup folder on Windows)
# ---------------------------------------------------------------------------
import platform as _platform

def _get_autostart_module():
    if _platform.system() == "Windows":
        import win_service
        return win_service
    else:
        import launchd_service
        return launchd_service


@app.route("/api/autostart/status")
def api_autostart_status():
    try:
        svc = _get_autostart_module()
        return jsonify({"installed": svc.is_installed()})
    except Exception as e:
        return jsonify({"installed": False, "error": str(e)})


@app.route("/api/autostart/enable", methods=["POST"])
def api_autostart_enable():
    try:
        svc = _get_autostart_module()
        ok = svc.install()
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/autostart/disable", methods=["POST"])
def api_autostart_disable():
    try:
        svc = _get_autostart_module()
        ok = svc.uninstall()
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Webhook Notifications
# ---------------------------------------------------------------------------
@app.route("/api/webhook/notify", methods=["POST"])
def api_webhook_notify():
    """Receive Graph API webhook notifications."""
    # Handle validation request
    validation_token = request.args.get("validationToken")
    if validation_token:
        return Response(validation_token, mimetype="text/plain")

    try:
        import webhook_manager
        data = request.get_json(force=True)
        changed = webhook_manager.handle_notification(data)
        if changed and manager.running:
            manager.trigger_sync()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("Webhook notification error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _server_is_running(port=8050):
    """Check if the server is already listening on the given port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _start_background_server():
    """Launch the server as a detached background process."""
    import subprocess, sys
    subprocess.Popen(
        [sys.executable, __file__, "--no-gui"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait for server to be ready
    import time
    for _ in range(30):
        if _server_is_running():
            return True
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gui", action="store_true", help="Run as headless background server")
    args = parser.parse_args()

    if args.no_gui:
        logger.info("Starting AutoSync server on http://localhost:8050")
        app.run(host="localhost", port=8050, threaded=True, debug=False)
    else:
        # Ensure background server is running
        if not _server_is_running():
            logger.info("Starting background server...")
            if not _start_background_server():
                logger.error("Failed to start background server")
                exit(1)
        else:
            logger.info("Server already running on port 8050")

        # Open native window (just a viewer — server lives independently)
        try:
            import webview
            logger.info("Opening AutoSync window")
            webview.create_window(
                "AutoSync — RiskArena",
                "http://localhost:8050",
                width=1100,
                height=750,
                min_size=(800, 500),
            )
            webview.start()
        except ImportError:
            import webbrowser
            logger.info("pywebview not installed, opening in browser")
            webbrowser.open("http://localhost:8050")
