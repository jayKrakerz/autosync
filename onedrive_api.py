import base64
import logging
import os
import time

import requests

from config import (
    GRAPH_API_BASE,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    SIMPLE_UPLOAD_MAX,
    UPLOAD_CHUNK_SIZE,
)

logger = logging.getLogger(__name__)


def encode_sharing_url(share_url):
    """Convert a OneDrive sharing URL to a Graph API sharing token."""
    encoded = base64.b64encode(share_url.encode("utf-8")).decode("utf-8")
    encoded = encoded.rstrip("=").replace("/", "_").replace("+", "-")
    return "u!" + encoded


def get_api_base(share_url):
    """Return the Graph API base path for a shared link."""
    token = encode_sharing_url(share_url)
    return f"{GRAPH_API_BASE}/shares/{token}"


_drive_base_cache = {}


def _resolve_drive_base(api_base):
    """Resolve a shares-based URL to a drives-based URL for path operations."""
    if api_base in _drive_base_cache:
        return _drive_base_cache[api_base]

    url = f"{api_base}/driveItem"
    resp = _request_with_retry("GET", url)
    if resp.status_code == 200:
        data = resp.json()
        drive_id = data.get("parentReference", {}).get("driveId")
        item_id = data.get("id")
        if drive_id and item_id:
            drive_base = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}"
            _drive_base_cache[api_base] = drive_base
            logger.info("Resolved share to drive base: %s", drive_base)
            return drive_base
    logger.warning("Could not resolve drive base for shares endpoint")
    return None


def _item_url(api_base, remote_path, suffix=""):
    """Build a Graph API URL for a file within the shared folder."""
    drive_base = _resolve_drive_base(api_base)
    encoded_path = ":/" + _encode_path(remote_path) + ":"
    if drive_base:
        return f"{drive_base}{encoded_path}{suffix}"
    return f"{api_base}/driveItem{encoded_path}{suffix}"


def validate_share_link(api_base):
    """Test the share link by requesting the root driveItem."""
    url = f"{api_base}/driveItem"
    resp = _request_with_retry("GET", url)
    if resp.status_code == 200:
        data = resp.json()
        logger.info("Connected to shared folder: %s", data.get("name", "unknown"))
        return True
    logger.error("Share link validation failed: %s %s", resp.status_code, resp.text[:200])
    return False


def list_remote_files(api_base, path="/"):
    """Recursively list all files under the shared folder.

    Returns a list of dicts: {name, size, lastModifiedDateTime, path, remote_hash}
    """
    files = []
    _list_recursive(api_base, path, files)
    return files


def _list_recursive(api_base, path, files):
    """Walk through the remote folder tree."""
    if path == "/":
        drive_base = _resolve_drive_base(api_base)
        url = f"{drive_base}/children" if drive_base else f"{api_base}/driveItem/children"
    else:
        url = _item_url(api_base, path.lstrip("/"), "/children")

    while url:
        resp = _request_with_retry("GET", url)
        if resp.status_code != 200:
            logger.error("Failed to list %s: %s %s", path, resp.status_code, resp.text[:200])
            return

        data = resp.json()
        for item in data.get("value", []):
            if path == "/":
                item_path = item["name"]
            else:
                item_path = path.lstrip("/") + "/" + item["name"]

            if "folder" in item:
                _list_recursive(api_base, "/" + item_path, files)
            elif "file" in item:
                # Extract hash from Graph API response
                hashes = item.get("file", {}).get("hashes", {})
                remote_hash = (
                    hashes.get("sha256Hash")
                    or hashes.get("quickXorHash")
                    or ""
                )
                files.append({
                    "name": item["name"],
                    "size": item.get("size", 0),
                    "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
                    "path": item_path,
                    "remote_hash": remote_hash,
                })

        url = data.get("@odata.nextLink")


def list_remote_changes(api_base, delta_link=None):
    """Use Graph API delta to get incremental changes.

    Returns (changes_list, new_delta_link).
    Each change: {path, name, size, lastModifiedDateTime, remote_hash, deleted}
    """
    drive_base = _resolve_drive_base(api_base)
    if not drive_base:
        return [], None

    if delta_link:
        url = delta_link
    else:
        url = f"{drive_base}/delta"

    changes = []
    new_delta_link = None

    while url:
        resp = _request_with_retry("GET", url)
        if resp.status_code != 200:
            logger.error("Delta query failed: %s %s", resp.status_code, resp.text[:200])
            return [], None

        data = resp.json()
        for item in data.get("value", []):
            is_deleted = "deleted" in item
            is_file = "file" in item
            is_folder = "folder" in item

            # Build path from parentReference
            parent_ref = item.get("parentReference", {})
            parent_path = parent_ref.get("path", "")
            # parentReference.path looks like /drives/{id}/items/{id}:/folder/subfolder
            # We need just the relative path after the ":"
            if ":" in parent_path:
                parent_rel = parent_path.split(":", 1)[1].lstrip("/")
            else:
                parent_rel = ""

            item_name = item.get("name", "")
            if parent_rel:
                item_path = f"{parent_rel}/{item_name}"
            else:
                item_path = item_name

            if is_deleted or is_file:
                hashes = item.get("file", {}).get("hashes", {}) if is_file else {}
                remote_hash = hashes.get("sha256Hash") or hashes.get("quickXorHash") or ""
                changes.append({
                    "path": item_path,
                    "name": item_name,
                    "size": item.get("size", 0),
                    "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
                    "remote_hash": remote_hash,
                    "deleted": is_deleted,
                    "is_folder": is_folder,
                })

        # Follow pagination or get the final delta link
        url = data.get("@odata.nextLink")
        if not url:
            new_delta_link = data.get("@odata.deltaLink")

    return changes, new_delta_link


def download_file(api_base, remote_path, local_path, progress_cb=None):
    """Download a single file from OneDrive to a local path."""
    url = _item_url(api_base, remote_path, "/content")

    resp = _request_with_retry("GET", url, stream=True)
    if resp.status_code != 200:
        logger.error("Download failed for %s: %s %s", remote_path, resp.status_code, resp.text[:200])
        return False

    total = int(resp.headers.get("Content-Length", 0))
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    bytes_done = 0
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            bytes_done += len(chunk)
            if progress_cb and total:
                progress_cb(bytes_done, total)

    logger.info("Downloaded: %s", remote_path)
    return True


def upload_file(api_base, remote_path, local_path, progress_cb=None):
    """Upload a local file to OneDrive.

    Returns the remote item metadata dict on success, or None on failure.
    """
    file_size = os.path.getsize(local_path)

    if file_size <= SIMPLE_UPLOAD_MAX:
        return _simple_upload(api_base, remote_path, local_path, progress_cb)
    else:
        return _chunked_upload(api_base, remote_path, local_path, file_size, progress_cb)


def _simple_upload(api_base, remote_path, local_path, progress_cb=None):
    """Upload a small file (<4MB) using PUT to /content."""
    url = _item_url(api_base, remote_path, "/content")

    with open(local_path, "rb") as f:
        data = f.read()

    if progress_cb:
        progress_cb(0, len(data))

    resp = _request_with_retry("PUT", url, data=data,
                               headers={"Content-Type": "application/octet-stream"})
    if resp.status_code in (200, 201):
        if progress_cb:
            progress_cb(len(data), len(data))
        logger.info("Uploaded (simple): %s", remote_path)
        return resp.json()

    logger.error("Simple upload failed for %s: %s %s", remote_path, resp.status_code, resp.text[:200])
    return None


def _chunked_upload(api_base, remote_path, local_path, file_size, progress_cb=None):
    """Upload a large file using an upload session with chunked transfers."""
    url = _item_url(api_base, remote_path, "/createUploadSession")

    resp = _request_with_retry("POST", url, json={
        "item": {"@microsoft.graph.conflictBehavior": "replace"}
    })
    if resp.status_code not in (200, 201):
        logger.error("Failed to create upload session for %s: %s %s",
                      remote_path, resp.status_code, resp.text[:200])
        return None

    upload_url = resp.json()["uploadUrl"]

    with open(local_path, "rb") as f:
        offset = 0
        while offset < file_size:
            chunk_end = min(offset + UPLOAD_CHUNK_SIZE, file_size) - 1
            chunk_data = f.read(UPLOAD_CHUNK_SIZE)
            content_range = f"bytes {offset}-{chunk_end}/{file_size}"

            chunk_resp = _request_with_retry(
                "PUT", upload_url,
                data=chunk_data,
                headers={
                    "Content-Range": content_range,
                    "Content-Length": str(len(chunk_data)),
                },
            )

            if chunk_resp.status_code in (200, 201):
                if progress_cb:
                    progress_cb(file_size, file_size)
                logger.info("Uploaded (chunked): %s", remote_path)
                return chunk_resp.json()
            elif chunk_resp.status_code == 202:
                offset = chunk_end + 1
                if progress_cb:
                    progress_cb(offset, file_size)
            else:
                logger.error("Chunked upload failed for %s at offset %d: %s %s",
                              remote_path, offset, chunk_resp.status_code, chunk_resp.text[:200])
                _request_with_retry("DELETE", upload_url)
                return None

    return None


def delete_remote(api_base, remote_path):
    """Delete a file or folder on OneDrive."""
    url = _item_url(api_base, remote_path)

    resp = _request_with_retry("DELETE", url)
    if resp.status_code in (200, 204):
        logger.info("Deleted remote: %s", remote_path)
        return True
    elif resp.status_code == 404:
        logger.warning("Remote file already gone: %s", remote_path)
        return True

    logger.error("Delete failed for %s: %s %s", remote_path, resp.status_code, resp.text[:200])
    return False


def _encode_path(path):
    """URL-encode path segments for Graph API, preserving slashes."""
    return "/".join(requests.utils.quote(seg, safe="") for seg in path.split("/"))


def _request_with_retry(method, url, **kwargs):
    """Make an HTTP request with exponential backoff retry on failures.

    Includes 401 token refresh retry and health monitoring.
    """
    # Inject OAuth token if available
    try:
        import auth
        token = auth.get_access_token()
        if token:
            headers = kwargs.get("headers") or {}
            headers.setdefault("Authorization", f"Bearer {token}")
            kwargs["headers"] = headers
    except Exception:
        pass

    _did_401_retry = False

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=60, **kwargs)

            # Record for health monitoring
            try:
                import health_monitor
                health_monitor.record_api_call(resp.status_code)
            except Exception:
                pass

            # Handle 401 — try force-refreshing the token once
            if resp.status_code == 401 and not _did_401_retry:
                _did_401_retry = True
                try:
                    import auth
                    new_token = auth.get_access_token(force_refresh=True)
                    if new_token:
                        headers = kwargs.get("headers") or {}
                        headers["Authorization"] = f"Bearer {new_token}"
                        kwargs["headers"] = headers
                        logger.info("Token refreshed on 401, retrying...")
                        continue
                except Exception:
                    pass

            # Don't retry client errors (except 429 Too Many Requests)
            if resp.status_code < 500 and resp.status_code not in (429,):
                return resp
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", RETRY_BACKOFF_BASE * (2 ** attempt)))
                logger.warning("Rate limited, retrying in %ds...", retry_after)
                time.sleep(retry_after)
                continue
        except requests.RequestException as e:
            # Record connection errors as 0
            try:
                import health_monitor
                health_monitor.record_api_call(0)
            except Exception:
                pass

            if attempt == MAX_RETRIES:
                logger.error("Request failed after %d retries: %s %s — %s", MAX_RETRIES, method, url, e)
                raise
            logger.warning("Request error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)

        if attempt < MAX_RETRIES:
            sleep_time = RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.info("Retrying in %ds (attempt %d/%d)...", sleep_time, attempt + 2, MAX_RETRIES + 1)
            time.sleep(sleep_time)

    return resp
