"""Shared helpers for grok-sync and gu."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

HOME = Path.home()
ACCOUNTS_DIR = HOME / ".config" / "opencode-grok-auth-sync"
ACCOUNTS_FILE = ACCOUNTS_DIR / "accounts.json"
ACCOUNTS_LOCK = ACCOUNTS_DIR / "accounts.lock"
OPENCODE_AUTH = HOME / ".local" / "share" / "opencode" / "auth.json"
PROFILES_DIR = HOME / ".config" / "ai-usage-monitors" / "profiles"

# Public Grok-CLI / OpenCode SuperGrok OAuth client (no secret).
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
BILLING_WEEKLY_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
BILLING_MONTHLY_URL = "https://cli-chat-proxy.grok.com/v1/billing"
USER_URL = "https://cli-chat-proxy.grok.com/v1/user"
API_TIMEOUT_S = 12


def bar(pct: float | None, width: int = 20) -> str:
    if pct is None:
        return DIM + "░" * width + RESET
    filled = round(min(100.0, max(0.0, pct)) / 100 * width)
    return GREEN + "█" * filled + DIM + "░" * (width - filled) + RESET


def pct_color(pct: float | None) -> str:
    if pct is None:
        return DIM
    if pct >= 90:
        return RED
    if pct >= 70:
        return YELLOW
    return GREEN


def clamp_pct(v: float | int | None) -> float | None:
    if v is None:
        return None
    return min(100.0, max(0.0, float(v)))


def time_until_iso(reset_at: str | None) -> str:
    if not reset_at:
        return "?"
    try:
        cleaned = reset_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = dt - now
        if diff.total_seconds() <= 0:
            return "reset now"
        minutes = int(diff.total_seconds() // 60)
        hours, mins = divmod(minutes, 60)
        days = hours // 24
        hours = hours % 24
        if days > 0:
            return f"{days}d{hours}h"
        return f"{hours}h{mins:02d}m"
    except (TypeError, ValueError):
        return "?"


def curl_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | None = None,
    form: bool = False,
) -> dict[str, Any] | None:
    args = [
        "curl",
        "-s",
        "--max-time",
        str(API_TIMEOUT_S),
        "-X",
        method,
        url,
    ]
    if headers:
        for key, value in headers.items():
            args += ["-H", f"{key}: {value}"]
    if data is not None:
        if form:
            args += ["-H", "Content-Type: application/x-www-form-urlencoded", "-d", data]
        else:
            args += ["-H", "Content-Type: application/json", "-d", data]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=API_TIMEOUT_S + 3,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            parsed = json.loads(result.stdout.strip())
            if isinstance(parsed, dict):
                return parsed
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    return None


def load_store() -> dict[str, Any]:
    if ACCOUNTS_FILE.exists():
        try:
            data = json.loads(ACCOUNTS_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("accounts", {})
                data.setdefault("active", None)
                data.setdefault("rotationIndex", 0)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"accounts": {}, "active": None, "rotationIndex": 0}


def save_store(store: dict[str, Any]) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(ACCOUNTS_DIR, 0o700)
    tmp = ACCOUNTS_FILE.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(store, indent=2))
    os.chmod(tmp, 0o600)
    tmp.rename(ACCOUNTS_FILE)


def acquire_lock() -> bool:
    for _ in range(300):
        try:
            ACCOUNTS_LOCK.mkdir(exist_ok=False)
            return True
        except FileExistsError:
            time.sleep(0.1)
    return False


def release_lock() -> None:
    try:
        ACCOUNTS_LOCK.rmdir()
    except OSError:
        pass


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.rename(path)


def read_opencode_xai() -> dict[str, Any] | None:
    if not OPENCODE_AUTH.exists():
        return None
    try:
        auth = json.loads(OPENCODE_AUTH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    xai = auth.get("xai")
    if not isinstance(xai, dict):
        return None
    access = xai.get("access") or xai.get("accessToken")
    if not access:
        return None
    return {
        "type": xai.get("type", "oauth"),
        "accessToken": access,
        "refreshToken": xai.get("refresh") or xai.get("refreshToken"),
        "expiresAt": xai.get("expires") or xai.get("expiresAt"),
        "source": "opencode",
    }


def write_opencode_xai(access: str, refresh: str | None, expires_at: int | None) -> None:
    if not OPENCODE_AUTH.exists():
        raise FileNotFoundError(
            f"OpenCode auth.json not found at {OPENCODE_AUTH}. Run OpenCode once first."
        )
    auth = json.loads(OPENCODE_AUTH.read_text())
    auth["xai"] = {
        "type": "oauth",
        "access": access,
        "refresh": refresh,
        "expires": expires_at,
    }
    write_json_atomic(OPENCODE_AUTH, auth)


def opencode_matches_account(live: dict[str, Any] | None, acc: dict[str, Any] | None) -> bool:
    if not live or not acc:
        return False
    live_tok = live.get("accessToken") or ""
    acc_tok = acc.get("accessToken") or ""
    if live_tok and acc_tok and live_tok == acc_tok:
        return True
    live_ref = live.get("refreshToken") or ""
    acc_ref = acc.get("refreshToken") or ""
    return bool(live_ref and acc_ref and live_ref == acc_ref)


def ensure_opencode_matches_active(*, quiet: bool = False) -> str | None:
    store = load_store()
    active = store.get("active")
    accounts = store.get("accounts", {})
    if not active or active not in accounts:
        return None

    acc = dict(accounts[active])
    fresh, reason = ensure_fresh_creds(acc)
    if not fresh:
        if not quiet:
            print(
                format_auth_error(reason, label=active, email=acc.get("email")),
                file=sys.stderr,
            )
            healthy = [
                name
                for name, other in accounts.items()
                if name != active and other.get("accessToken")
            ]
            if healthy:
                print(
                    f"\nSwitch to a working account:\n  grok-sync --switch {healthy[0]}",
                    file=sys.stderr,
                )
        return active

    if (
        fresh.get("accessToken") != acc.get("accessToken")
        or fresh.get("expiresAt") != acc.get("expiresAt")
        or fresh.get("refreshToken") != acc.get("refreshToken")
    ):
        if acquire_lock():
            try:
                store = load_store()
                if active in store.get("accounts", {}):
                    store["accounts"][active].update(
                        {
                            "accessToken": fresh["accessToken"],
                            "refreshToken": fresh.get("refreshToken"),
                            "expiresAt": fresh.get("expiresAt"),
                            "updatedAt": datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        }
                    )
                    save_store(store)
            finally:
                release_lock()

    live = read_opencode_xai()
    if opencode_matches_account(live, fresh):
        return active

    try:
        write_opencode_xai(
            fresh["accessToken"],
            fresh.get("refreshToken"),
            fresh.get("expiresAt"),
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        if not quiet:
            print(f"Warning: could not sync OpenCode xai to '{active}': {exc}", file=sys.stderr)
        return active

    if not quiet:
        print(
            f"Synced OpenCode xai → {active} (was out of date)",
            file=sys.stderr,
        )
    return active


def is_token_expired(expires_at: int | float | None, skew_seconds: int = 60) -> bool:
    if expires_at is None:
        return False
    # Support both ms and seconds timestamps.
    exp = float(expires_at)
    if exp > 1_000_000_000_000:
        exp = exp / 1000.0
    return exp <= time.time() + skew_seconds


def refresh_access_token(
    refresh_token: str,
) -> tuple[dict[str, Any] | None, str | None]:
    body = (
        f"grant_type=refresh_token"
        f"&refresh_token={refresh_token}"
        f"&client_id={CLIENT_ID}"
    )
    parsed = curl_json(TOKEN_URL, method="POST", data=body, form=True)
    if not parsed:
        return None, "network"
    if parsed.get("access_token"):
        expires_in = int(parsed.get("expires_in") or 3600)
        return (
            {
                "accessToken": parsed["access_token"],
                "refreshToken": parsed.get("refresh_token") or refresh_token,
                "expiresAt": int(time.time() * 1000) + expires_in * 1000,
            },
            None,
        )
    err = str(parsed.get("error") or "")
    desc = str(parsed.get("error_description") or "")
    blob = f"{err} {desc}".lower()
    if "revok" in blob or err == "invalid_grant":
        return None, "revoked"
    if err:
        return None, "refresh_failed"
    return None, "refresh_failed"


def ensure_fresh_creds(
    creds: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if not creds.get("accessToken"):
        return None, "missing"
    if not is_token_expired(creds.get("expiresAt")):
        return creds, None
    refresh = creds.get("refreshToken")
    if not refresh:
        return None, "expired_no_refresh"
    refreshed, reason = refresh_access_token(refresh)
    if not refreshed:
        return None, reason or "refresh_failed"
    return {**creds, **refreshed}, None


def relogin_hint(label: str | None = None, email: str | None = None) -> str:
    who = f" ({email})" if email else ""
    name = label or "<name>"
    return (
        f"Re-login{who}:\n"
        f"  1. opencode auth login          # xAI Grok OAuth\n"
        f"  2. grok-sync --login {name}\n"
        f"  3. gu {name}"
    )


def format_auth_error(
    reason: str | None,
    *,
    label: str | None = None,
    email: str | None = None,
) -> str:
    name = f" '{label}'" if label else ""
    match reason:
        case "revoked":
            head = f"Session expired for{name}: refresh token was revoked by xAI."
        case "expired_no_refresh":
            head = f"Session expired for{name}: no refresh token saved."
        case "missing":
            head = f"No credentials found for{name}."
        case "network":
            head = f"Could not refresh{name}: network error talking to auth.x.ai."
        case "refresh_failed":
            head = f"Session expired for{name}: token refresh failed."
        case _:
            head = f"Credentials unusable for{name}."
    return f"{head}\n{relogin_hint(label, email)}"


def fetch_user(access_token: str) -> dict[str, Any] | None:
    return curl_json(
        USER_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )


def fetch_weekly_billing(access_token: str) -> dict[str, Any] | None:
    return curl_json(
        BILLING_WEEKLY_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )


def fetch_monthly_billing(access_token: str) -> dict[str, Any] | None:
    return curl_json(
        BILLING_MONTHLY_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )


def profile_dir(name: str) -> Path:
    return PROFILES_DIR / name


def save_profile(name: str, data: dict[str, Any]) -> None:
    d = profile_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    to_save = {
        **data,
        "savedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (d / "grok.json").write_text(json.dumps(to_save, indent=2))


def load_profile(name: str) -> dict[str, Any] | None:
    path = profile_dir(name) / "grok.json"
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def list_profiles() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    names: list[str] = []
    for entry in sorted(PROFILES_DIR.iterdir()):
        if entry.is_dir() and (entry / "grok.json").exists():
            names.append(entry.name)
    return names


def profile_cache_file(name: str | None = None) -> Path:
    if name:
        return profile_dir(name) / ".grok-cache.json"
    return HOME / ".config" / "opencode-grok-auth-sync" / ".usage-cache.json"
