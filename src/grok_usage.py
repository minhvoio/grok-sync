#!/usr/bin/env python3
"""gu - Show SuperGrok / xAI subscription usage with colored terminal bars.

Reads OAuth credentials from:
  1. Named profile: ~/.config/ai-usage-monitors/profiles/<name>/grok.json
  2. grok-sync account store: ~/.config/opencode-grok-auth-sync/accounts.json
  3. Live OpenCode session: ~/.local/share/opencode/auth.json  (provider: xai)

Calls:
  GET https://cli-chat-proxy.grok.com/v1/billing?format=credits  (weekly %)
  GET https://cli-chat-proxy.grok.com/v1/billing                 (monthly credits)

Usage:
  gu                 # active / live OpenCode xAI account
  gu work            # named profile or grok-sync account
  gu all             # every saved account
  gu save work       # snapshot current OpenCode tokens as profile
  gu list
  gu --json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared import (  # noqa: E402
    BOLD,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    bar,
    clamp_pct,
    ensure_fresh_creds,
    ensure_opencode_matches_active,
    fetch_monthly_billing,
    fetch_user,
    fetch_weekly_billing,
    format_auth_error,
    is_token_expired,
    list_profiles,
    load_profile,
    load_store,
    pct_color,
    profile_cache_file,
    read_opencode_xai,
    save_profile,
    save_store,
    time_until_iso,
    write_opencode_xai,
)

CACHE_TTL_MS = 90_000


def resolve_creds(name: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if name:
        profile = load_profile(name)
        if profile and profile.get("accessToken"):
            return profile, name
        store = load_store()
        acc = store.get("accounts", {}).get(name)
        if acc and acc.get("accessToken"):
            return acc, name
        return None, name

    ensure_opencode_matches_active()

    store = load_store()
    active = store.get("active")
    if active and active in store.get("accounts", {}):
        acc = store["accounts"][active]
        if acc.get("accessToken"):
            return acc, active

    live = read_opencode_xai()
    if live:
        return live, None
    return None, None


def persist_refreshed(label: str | None, creds: dict[str, Any]) -> None:
    if not label:
        # Keep OpenCode in sync if this is the live session.
        try:
            write_opencode_xai(
                creds["accessToken"],
                creds.get("refreshToken"),
                creds.get("expiresAt"),
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        return

    # Prefer updating grok-sync store if the label lives there.
    store = load_store()
    if label in store.get("accounts", {}):
        store["accounts"][label].update(
            {
                "accessToken": creds["accessToken"],
                "refreshToken": creds.get("refreshToken"),
                "expiresAt": creds.get("expiresAt"),
                "updatedAt": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        save_store(store)
        return

    if load_profile(label) is not None:
        save_profile(
            label,
            {
                "accessToken": creds["accessToken"],
                "refreshToken": creds.get("refreshToken"),
                "expiresAt": creds.get("expiresAt"),
            },
        )


def parse_usage(
    weekly_raw: dict[str, Any] | None,
    monthly_raw: dict[str, Any] | None,
    user_raw: dict[str, Any] | None,
) -> dict[str, Any]:
    weekly_cfg = (weekly_raw or {}).get("config") or {}
    monthly_cfg = (monthly_raw or {}).get("config") or {}
    period = weekly_cfg.get("currentPeriod") or {}

    weekly_pct = clamp_pct(weekly_cfg.get("creditUsagePercent"))
    product_usage = []
    for item in weekly_cfg.get("productUsage") or []:
        if not isinstance(item, dict):
            continue
        product_usage.append(
            {
                "product": item.get("product"),
                "usagePercent": clamp_pct(item.get("usagePercent")),
            }
        )

    monthly_limit = None
    monthly_used = None
    if isinstance(monthly_cfg.get("monthlyLimit"), dict):
        monthly_limit = monthly_cfg["monthlyLimit"].get("val")
    if isinstance(monthly_cfg.get("used"), dict):
        monthly_used = monthly_cfg["used"].get("val")
    # Alternate shapes seen in the wild
    if monthly_used is None and isinstance((monthly_raw or {}).get("usage"), dict):
        monthly_used = (monthly_raw or {}).get("usage", {}).get("creditUsage")

    monthly_pct = None
    if monthly_limit not in (None, 0) and monthly_used is not None:
        monthly_pct = clamp_pct(100.0 * float(monthly_used) / float(monthly_limit))

    email = None
    if user_raw:
        email = user_raw.get("email")

    return {
        "weeklyPercent": weekly_pct,
        "weeklyPeriodStart": period.get("start")
        or weekly_cfg.get("billingPeriodStart"),
        "weeklyPeriodEnd": period.get("end") or weekly_cfg.get("billingPeriodEnd"),
        "weeklyPeriodType": period.get("type"),
        "productUsage": product_usage,
        "onDemandUsed": (weekly_cfg.get("onDemandUsed") or {}).get("val"),
        "onDemandCap": (weekly_cfg.get("onDemandCap") or {}).get("val"),
        "prepaidBalance": (weekly_cfg.get("prepaidBalance") or {}).get("val"),
        "isUnifiedBillingUser": weekly_cfg.get("isUnifiedBillingUser"),
        "monthlyLimit": monthly_limit,
        "monthlyUsed": monthly_used,
        "monthlyPercent": monthly_pct,
        "monthlyPeriodStart": monthly_cfg.get("billingPeriodStart"),
        "monthlyPeriodEnd": monthly_cfg.get("billingPeriodEnd"),
        "email": email,
    }


def read_cache(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def is_cache_valid(cache: dict[str, Any] | None, token_prefix: str | None) -> bool:
    if not cache or cache.get("error") or not cache.get("data"):
        return False
    if (
        token_prefix
        and cache.get("tokenPrefix")
        and cache["tokenPrefix"] != token_prefix
    ):
        return False
    age_ms = datetime.now(timezone.utc).timestamp() * 1000 - cache.get("timestamp", 0)
    return age_ms < CACHE_TTL_MS


def write_cache(path: Path, data: dict[str, Any], token_prefix: str | None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "data": data,
            "error": False,
            "source": "xai",
            "tokenPrefix": token_prefix,
        }
        path.write_text(json.dumps(entry, indent=2))
    except OSError:
        pass


def format_limits(
    data: dict[str, Any],
    account_label: str | None = None,
    *,
    is_current: bool = False,
) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if account_label:
        current_tag = f" {GREEN}*{RESET}" if is_current else ""
        label_str = f"  ({BOLD}{account_label}{RESET}{current_tag})"
    else:
        label_str = ""
    email = data.get("email")
    email_str = f"  {DIM}{email}{RESET}" if email else ""

    print()
    print(f"  {BOLD}Grok Usage{RESET}{label_str}{email_str}  -  {now_str}")
    print(f"  {'─' * 55}")

    weekly = data.get("weeklyPercent")
    if weekly is not None:
        reset = time_until_iso(data.get("weeklyPeriodEnd"))
        color = pct_color(weekly)
        print(
            f"  {BOLD}Weekly{RESET}     {bar(weekly, 20)}  "
            f"{color}{weekly:>5.1f}%{RESET}  resets in {color}{reset}{RESET}"
        )

    monthly_pct = data.get("monthlyPercent")
    monthly_used = data.get("monthlyUsed")
    monthly_limit = data.get("monthlyLimit")
    if monthly_pct is not None:
        color = pct_color(monthly_pct)
        used_s = f"{monthly_used}" if monthly_used is not None else "?"
        limit_s = f"{monthly_limit}" if monthly_limit is not None else "?"
        print(
            f"  {BOLD}Monthly{RESET}    {bar(monthly_pct, 20)}  "
            f"{color}{monthly_pct:>5.1f}%{RESET}  {DIM}{used_s} / {limit_s}{RESET}"
        )
    elif monthly_used is not None or monthly_limit is not None:
        used_s = f"{monthly_used}" if monthly_used is not None else "?"
        limit_s = f"{monthly_limit}" if monthly_limit is not None else "?"
        print(f"  {BOLD}Monthly{RESET}    {DIM}{used_s} / {limit_s} credits{RESET}")

    for item in data.get("productUsage") or []:
        product = item.get("product") or "Product"
        pct = item.get("usagePercent")
        if pct is None:
            continue
        color = pct_color(pct)
        label = str(product)[:9].ljust(9)
        print(
            f"  {BOLD}{label}{RESET}  {bar(pct, 20)}  "
            f"{color}{pct:>5.1f}%{RESET}"
        )

    on_demand = data.get("onDemandUsed")
    on_cap = data.get("onDemandCap")
    prepaid = data.get("prepaidBalance")
    extras: list[str] = []
    if on_demand is not None and (on_demand or on_cap):
        extras.append(f"on-demand used {on_demand}" + (f" / cap {on_cap}" if on_cap else ""))
    if prepaid:
        extras.append(f"prepaid {prepaid}")
    if extras:
        print(f"  {BOLD}Extra{RESET}      {DIM}{'; · '.join(extras)}{RESET}")

    print()


def fetch_and_parse(access_token: str) -> dict[str, Any] | None:
    weekly = fetch_weekly_billing(access_token)
    monthly = fetch_monthly_billing(access_token)
    user = fetch_user(access_token)
    if not weekly and not monthly:
        return None
    return parse_usage(weekly, monthly, user)


def print_expired_block(
    label: str | None,
    creds: dict[str, Any],
    reason: str | None,
    *,
    is_current: bool = False,
) -> None:
    email = creds.get("email")
    name = label or "unknown"
    star = f" {GREEN}*{RESET}" if is_current else ""
    print()
    print(f"  {BOLD}Grok Usage{RESET}  ({BOLD}{name}{RESET}{star})")
    print(f"  {'─' * 55}")
    print(f"  {RED}Session expired{RESET}  {DIM}({reason or 'refresh_failed'}){RESET}")
    if email:
        print(f"  {DIM}{email}{RESET}")
    print(f"  {YELLOW}Fix:{RESET}")
    print(f"    opencode auth login")
    print(f"    grok-sync --login {name}")
    print(f"    gu {name}")
    print()


def usage_for_creds(
    creds: dict[str, Any],
    label: str | None,
    *,
    as_json: bool,
    use_cache: bool = True,
    is_current: bool = False,
    quiet_errors: bool = False,
) -> dict[str, Any] | None:
    fresh, reason = ensure_fresh_creds(creds)
    if not fresh:
        if as_json:
            print(
                json.dumps(
                    {
                        "error": reason or "refresh_failed",
                        "label": label,
                        "email": creds.get("email"),
                        "isCurrent": is_current,
                    }
                )
            )
            return None
        if quiet_errors:
            print_expired_block(label, creds, reason, is_current=is_current)
        else:
            print(
                format_auth_error(reason, label=label, email=creds.get("email")),
                file=sys.stderr,
            )
        return None

    if (
        fresh.get("accessToken") != creds.get("accessToken")
        or fresh.get("expiresAt") != creds.get("expiresAt")
    ):
        persist_refreshed(label, fresh)

    token_prefix = (fresh.get("accessToken") or "")[:16]
    cache_path = profile_cache_file(label)
    if use_cache:
        cache = read_cache(cache_path)
        if is_cache_valid(cache, token_prefix) and cache:
            data = cache["data"]
            if as_json:
                print(json.dumps(data))
            else:
                format_limits(data, account_label=label, is_current=is_current)
            return data

    data = fetch_and_parse(fresh["accessToken"])
    if not data:
        cache = read_cache(cache_path)
        if cache and cache.get("data"):
            sys.stderr.write("[stale] ")
            data = cache["data"]
            if as_json:
                print(json.dumps(data))
            else:
                format_limits(data, account_label=label, is_current=is_current)
            return data
        print("Error: Could not reach Grok billing API.", file=sys.stderr)
        return None

    write_cache(cache_path, data, token_prefix)
    if as_json:
        print(json.dumps(data))
    else:
        format_limits(data, account_label=label, is_current=is_current)
    return data


def cmd_save(name: str) -> None:
    creds = read_opencode_xai()
    if not creds or not creds.get("accessToken"):
        print("Error: No OpenCode xAI credentials found.", file=sys.stderr)
        print("Run: opencode auth login  (pick xAI Grok OAuth)", file=sys.stderr)
        sys.exit(1)
    fresh, reason = ensure_fresh_creds(creds)
    if not fresh:
        print(format_auth_error(reason, label=name), file=sys.stderr)
        sys.exit(1)
    user = fetch_user(fresh["accessToken"])
    save_profile(
        name,
        {
            "accessToken": fresh["accessToken"],
            "refreshToken": fresh.get("refreshToken"),
            "expiresAt": fresh.get("expiresAt"),
            "email": (user or {}).get("email"),
        },
    )
    print(f"Saved Grok credentials to profile '{name}'.")
    print(f"Use `gu {name}` to check usage.")


def _token_status(creds: dict[str, Any]) -> str:
    if not creds.get("accessToken"):
        return "no token"
    if is_token_expired(creds.get("expiresAt")):
        if not creds.get("refreshToken"):
            return "EXPIRED"
        return "expired (refresh?)"
    exp = creds.get("expiresAt")
    if exp is None:
        return "ok"
    e = float(exp)
    if e > 1_000_000_000_000:
        e = e / 1000.0
    rem = e - __import__("time").time()
    if rem <= 0:
        return "EXPIRED"
    hours = int(rem // 3600)
    mins = int((rem % 3600) // 60)
    return f"{hours}h {mins}m"


def cmd_list() -> None:
    profiles = list_profiles()
    store = load_store()
    accounts = store.get("accounts", {})
    active = store.get("active")

    if not profiles and not accounts:
        print("No saved Grok profiles or grok-sync accounts.")
        print("  gu save <name>          snapshot current OpenCode session")
        print("  grok-sync --login <n>   multi-account switcher store")
        return

    if accounts:
        print("grok-sync accounts:")
        for name, acc in accounts.items():
            mark = f" {GREEN}*{RESET}" if name == active else ""
            status = _token_status(acc)
            bad = status == "no token" or "expired" in status.lower()
            color = RED if bad else DIM
            email = acc.get("email") or ""
            print(f"  {name}{mark}  {color}{status}{RESET}  {DIM}{email}{RESET}")
    if profiles:
        print("gu profiles:")
        for name in profiles:
            print(f"  {name}")


def cmd_all(as_json: bool) -> None:
    ensure_opencode_matches_active(quiet=True)

    names: list[str] = []
    store = load_store()
    active = store.get("active")
    for name in store.get("accounts", {}):
        if name not in names:
            names.append(name)
    for name in list_profiles():
        if name not in names:
            names.append(name)

    if not names:
        print("No saved Grok accounts/profiles.")
        print("Save one: opencode auth login && grok-sync --login <name>")
        return

    results: dict[str, Any] = {}
    failed = 0
    for name in names:
        creds, label = resolve_creds(name)
        if not creds:
            if as_json:
                results[name] = {"error": "missing", "isCurrent": name == active}
            else:
                print_expired_block(
                    name, {}, "missing", is_current=bool(active and name == active)
                )
            failed += 1
            continue
        is_current = bool(active and name == active)
        if as_json:
            fresh, reason = ensure_fresh_creds(creds)
            if not fresh:
                results[name] = {
                    "error": reason or "refresh_failed",
                    "email": creds.get("email"),
                    "isCurrent": is_current,
                }
                failed += 1
                continue
            data = fetch_and_parse(fresh["accessToken"])
            if data:
                results[name] = {**data, "isCurrent": is_current}
            else:
                results[name] = {
                    "error": "fetch_failed",
                    "email": creds.get("email"),
                    "isCurrent": is_current,
                }
                failed += 1
        else:
            out = usage_for_creds(
                creds,
                label,
                as_json=False,
                is_current=is_current,
                quiet_errors=True,
            )
            if out is None:
                failed += 1

    if as_json:
        print(json.dumps(results))
    elif failed and not as_json:
        print(
            f"  {DIM}{failed} account(s) need re-login "
            f"(opencode auth login && grok-sync --login <name>){RESET}"
        )
        print()


def main() -> None:
    args = sys.argv[1:]
    as_json = "--json" in args
    no_cache = "--no-cache" in args
    positional = [a for a in args if not a.startswith("--")]

    if positional and positional[0] == "save":
        if len(positional) < 2:
            print("Usage: gu save <profile-name>", file=sys.stderr)
            sys.exit(1)
        return cmd_save(positional[1])

    if positional and positional[0] == "list":
        return cmd_list()

    if positional and positional[0] == "all":
        return cmd_all(as_json)

    profile_name = positional[0] if positional else None
    creds, label = resolve_creds(profile_name)
    if not creds or not creds.get("accessToken"):
        if profile_name:
            print(
                format_auth_error("missing", label=profile_name),
                file=sys.stderr,
            )
        else:
            print("Error: No Grok/xAI credentials found.", file=sys.stderr)
            print("  opencode auth login          # pick xAI Grok OAuth", file=sys.stderr)
            print("  grok-sync --login <label>    # multi-account", file=sys.stderr)
        sys.exit(1)

    result = usage_for_creds(
        creds, label, as_json=as_json, use_cache=not no_cache
    )
    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
