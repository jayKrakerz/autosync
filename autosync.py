#!/usr/bin/env python3
"""Bi-directional OneDrive sync tool.

Keeps a local folder and a OneDrive shared folder in sync.
Local changes are pushed immediately via file watcher;
remote changes are pulled every POLL_INTERVAL seconds via polling.
"""
import logging
import os
import signal
import sys
import time

import config as cfg
from config import save_user_config
from onedrive_api import get_api_base, validate_share_link
from state_db import load_state, save_state
from sync_engine import full_sync
from file_watcher import start_watcher, stop_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autosync")


def _prompt_setup():
    """Interactive first-run setup: prompt for share link and optional settings."""
    print("\n=== AutoSync Setup ===\n")

    share_link = input("Enter your OneDrive shared link (with Edit permissions): ").strip()
    if not share_link:
        print("No share link provided. Exiting.")
        sys.exit(1)

    local_folder = input(f"Local sync folder [{cfg.LOCAL_FOLDER}]: ").strip()
    if not local_folder:
        local_folder = cfg.LOCAL_FOLDER

    poll_str = input(f"Poll interval in seconds [{cfg.POLL_INTERVAL}]: ").strip()
    poll_interval = int(poll_str) if poll_str else cfg.POLL_INTERVAL

    # Save to user_config.json so it persists across runs
    save_user_config({
        "share_link": share_link,
        "local_folder": local_folder,
        "poll_interval": poll_interval,
    })

    # Update runtime config
    cfg.SHARE_LINK = share_link
    cfg.LOCAL_FOLDER = local_folder
    cfg.POLL_INTERVAL = poll_interval

    print(f"\nConfiguration saved to {cfg.USER_CONFIG_PATH}")
    print(f"  Share link:   {share_link[:60]}...")
    print(f"  Local folder: {local_folder}")
    print(f"  Poll interval: {poll_interval}s\n")


def main():
    # 1. Validate configuration — prompt if no share link
    if not cfg.SHARE_LINK:
        _prompt_setup()

    logger.info("Local folder: %s", cfg.LOCAL_FOLDER)
    logger.info("Poll interval: %ds", cfg.POLL_INTERVAL)
    logger.info("State DB: %s", cfg.STATE_DB_PATH)

    # 2. Resolve API base from share link
    api_base = get_api_base(cfg.SHARE_LINK)

    # 3. Validate share link
    logger.info("Validating share link...")
    if not validate_share_link(api_base):
        logger.error("Could not access the shared folder. Check your share link.")
        sys.exit(1)

    # 4. Create local folder if needed
    os.makedirs(cfg.LOCAL_FOLDER, exist_ok=True)

    # 5. Load or create state DB
    state = load_state(cfg.STATE_DB_PATH)
    save_state(state, cfg.STATE_DB_PATH)

    # 6. Run initial full sync
    logger.info("Running initial full sync...")
    full_sync(api_base)

    # 7. Start file watcher (background thread)
    observer = start_watcher(api_base)

    # 8. Handle graceful shutdown
    shutdown = False

    def signal_handler(signum, frame):
        nonlocal shutdown
        shutdown = True
        logger.info("Shutdown signal received, stopping...")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 9. Polling loop
    logger.info("Sync running. Press Ctrl+C to stop.")
    try:
        while not shutdown:
            # Sleep in small increments to allow responsive shutdown
            for _ in range(cfg.POLL_INTERVAL):
                if shutdown:
                    break
                time.sleep(1)

            if not shutdown:
                try:
                    full_sync(api_base)
                except Exception as e:
                    logger.error("Full sync failed: %s", e)
    finally:
        stop_watcher(observer)
        logger.info("Autosync stopped.")


if __name__ == "__main__":
    main()
