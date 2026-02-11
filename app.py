#!/usr/bin/env python3
"""AutoSync Web Dashboard — Flask application."""

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, redirect, render_template, request

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
# API: Config
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_config_get():
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
# API: Auto-Start (LaunchAgent)
# ---------------------------------------------------------------------------
@app.route("/api/autostart/status")
def api_autostart_status():
    try:
        import launchd_service
        return jsonify({"installed": launchd_service.is_installed()})
    except Exception as e:
        return jsonify({"installed": False, "error": str(e)})


@app.route("/api/autostart/enable", methods=["POST"])
def api_autostart_enable():
    try:
        import launchd_service
        ok = launchd_service.install()
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/autostart/disable", methods=["POST"])
def api_autostart_disable():
    try:
        import launchd_service
        ok = launchd_service.uninstall()
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
if __name__ == "__main__":
    logger.info("Starting AutoSync Web Dashboard on http://localhost:8050")
    app.run(host="localhost", port=8050, threaded=True, debug=False)
