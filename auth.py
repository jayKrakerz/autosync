"""Microsoft OAuth authentication via MSAL (Auth Code Flow with PKCE)."""

import json
import logging
import os

import msal

import config as cfg

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))

SCOPES = ["Files.ReadWrite.All", "Sites.ReadWrite.All"]

# Module-level state for the in-progress auth flow
_auth_flow = None


def _get_cache():
    """Load the MSAL serializable token cache from disk."""
    cache = msal.SerializableTokenCache()
    cache_path = getattr(cfg, "TOKEN_CACHE_PATH", os.path.join(_DIR, ".token_cache.json"))
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache.deserialize(f.read())
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not load token cache from %s", cache_path)
    return cache


def _save_cache(cache):
    """Persist the MSAL token cache to disk."""
    if cache.has_state_changed:
        cache_path = getattr(cfg, "TOKEN_CACHE_PATH", os.path.join(_DIR, ".token_cache.json"))
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


def _get_app(cache=None):
    """Create an MSAL PublicClientApplication."""
    client_id = getattr(cfg, "CLIENT_ID", "")
    tenant_id = getattr(cfg, "TENANT_ID", "common")
    if not client_id:
        return None
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.PublicClientApplication(
        client_id,
        authority=authority,
        token_cache=cache,
    )


def get_auth_url(redirect_uri):
    """Initiate the auth code flow and return the authorization URL.

    The caller should redirect the user's browser to this URL.
    """
    global _auth_flow
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return None
    _auth_flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri)
    if "auth_uri" not in _auth_flow:
        logger.error("Failed to build auth URL: %s", _auth_flow)
        return None
    return _auth_flow["auth_uri"]


def complete_auth(auth_response):
    """Exchange the authorization code for tokens.

    Args:
        auth_response: The query parameters from the callback URL (dict).

    Returns:
        The token result dict on success, or None on failure.
    """
    global _auth_flow
    if _auth_flow is None:
        logger.error("No auth flow in progress")
        return None
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return None
    result = app.acquire_token_by_auth_code_flow(_auth_flow, auth_response)
    _auth_flow = None
    if "access_token" in result:
        _save_cache(cache)
        logger.info("OAuth authentication successful")
        return result
    logger.error("OAuth token acquisition failed: %s", result.get("error_description", result))
    return None


def get_access_token(force_refresh=False):
    """Get a valid access token silently (from cache / refresh token).

    Args:
        force_refresh: If True, forces token refresh even if cached token is valid.

    Returns the token string, or None if not authenticated.
    """
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return None
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(
        SCOPES, account=accounts[0], force_refresh=force_refresh
    )
    if result and "access_token" in result:
        _save_cache(cache)
        return result["access_token"]
    return None


def get_token_expiry():
    """Return seconds until token expires, or None if unavailable."""
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return None
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if result and "access_token" in result:
        _save_cache(cache)
        # MSAL includes expires_in (seconds) in the result
        return result.get("expires_in")
    return None


def is_authenticated():
    """Check whether we have a cached account (tokens may still be expired)."""
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return False
    return len(app.get_accounts()) > 0


def get_user_info():
    """Return basic info about the signed-in user, or None."""
    cache = _get_cache()
    app = _get_app(cache)
    if app is None:
        return None
    accounts = app.get_accounts()
    if not accounts:
        return None
    acct = accounts[0]
    return {
        "username": acct.get("username", ""),
        "name": acct.get("name", acct.get("username", "")),
    }


def logout():
    """Clear all cached tokens."""
    cache_path = getattr(cfg, "TOKEN_CACHE_PATH", os.path.join(_DIR, ".token_cache.json"))
    if os.path.exists(cache_path):
        os.remove(cache_path)
        logger.info("Token cache removed")
