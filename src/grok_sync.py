#!/usr/bin/env python3
"""grok-sync - Multi-account Grok/xAI OAuth switcher for OpenCode.

Mirrors the claude-sync UX:

  grok-sync --login <label>     Snapshot current OpenCode xAI session
  grok-sync --switch <label>    Activate a saved account in OpenCode
  grok-sync --list              List saved accounts
  grok-sync --status            Show active account + token health
  grok-sync --remove <label>    Remove a saved account
  grok-sync --rotate            Rotate to the next account
  grok-sync --sync              Re-write active account into OpenCode
  grok-sync --add <label>       Alias of --login (snapshot current)

Login flow:
  1. Run: opencode auth login   (choose xAI Grok OAuth)
  2. Complete browser / device-code login
  3. Run: grok-sync --login work
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared import (  # noqa: E402
    ACCOUNTS_FILE,
    OPENCODE_AUTH,
    acquire_lock,
    ensure_fresh_creds,
    ensure_opencode_matches_active,
    fetch_user,
    format_auth_error,
    is_token_expired,
    load_store,
    opencode_matches_account,
    read_opencode_xai,
    release_lock,
    save_store,
    write_opencode_xai,
)


def _fmt_remaining(expires_at: int | float | None) -> str:
    if expires_at is None:
        return "unknown expiry"
    exp = float(expires_at)
    if exp > 1_000_000_000_000:
        exp = exp / 1000.0
    rem = exp - time.time()
    if rem <= 0:
        return "EXPIRED"
    hours = int(rem // 3600)
    mins = int((rem % 3600) // 60)
    return f"{hours}h {mins}m remaining"


def _snapshot_current(label: str) -> None:
    if not label:
        print("Usage: grok-sync --login <label>", file=sys.stderr)
        sys.exit(1)

    creds = read_opencode_xai()
    if not creds or not creds.get("accessToken"):
        print("No OpenCode xAI OAuth session found.", file=sys.stderr)
        print(f"  1. Run: opencode auth login  (pick xAI Grok OAuth)", file=sys.stderr)
        print(f"  2. Complete the browser login", file=sys.stderr)
        print(f"  3. Re-run: grok-sync --login {label}", file=sys.stderr)
        sys.exit(1)

    fresh, reason = ensure_fresh_creds(creds)
    if not fresh:
        print(format_auth_error(reason, label=label), file=sys.stderr)
        sys.exit(1)

    email = None
    user = fetch_user(fresh["accessToken"])
    if user:
        email = user.get("email")

    if not acquire_lock():
        print("Lock timeout", file=sys.stderr)
        sys.exit(1)
    try:
        store = load_store()
        is_update = label in store["accounts"]
        store["accounts"][label] = {
            "type": "oauth",
            "accessToken": fresh["accessToken"],
            "refreshToken": fresh.get("refreshToken"),
            "expiresAt": fresh.get("expiresAt"),
            "email": email,
            "addedAt": store["accounts"].get(label, {}).get("addedAt")
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if not store.get("active"):
            store["active"] = label
        save_store(store)
    finally:
        release_lock()

    action = "Updated" if is_update else "Added"
    who = f" ({email})" if email else ""
    print(f"{action}: {label}{who}")
    print(f"Token: {_fmt_remaining(fresh.get('expiresAt'))}")
    print(f"Store: {ACCOUNTS_FILE}")
    print(f"Switch with: grok-sync --switch {label}")


def cmd_login(label: str) -> None:
    """Snapshot the current OpenCode xAI session as a named account."""
    print(
        "Snapshotting current OpenCode xAI session.\n"
        "If you need a different account first:\n"
        "  opencode auth login   # pick xAI Grok OAuth, finish browser flow\n",
        file=sys.stderr,
    )
    _snapshot_current(label)


def cmd_add(label: str) -> None:
    _snapshot_current(label)


def cmd_list() -> None:
    store = load_store()
    accounts = store.get("accounts", {})
    active = store.get("active")
    if not accounts:
        print("No accounts stored.")
        print("Add one: opencode auth login  then  grok-sync --login <label>")
        return
    print(f"Grok accounts  ({ACCOUNTS_FILE})")
    print("─" * 50)
    for label, acc in accounts.items():
        mark = "*" if label == active else " "
        email = acc.get("email") or ""
        rem = _fmt_remaining(acc.get("expiresAt"))
        print(f" {mark} {label:<16}  {rem:<20}  {email}")
    print()
    print("* = active")


def cmd_status() -> None:
    ensure_opencode_matches_active()

    store = load_store()
    active = store.get("active")
    accounts = store.get("accounts", {})
    if not active or active not in accounts:
        print("No active account. Use --list / --switch.", file=sys.stderr)
        sys.exit(1)

    acc = accounts[active]
    print(f"Active:  {active}")
    if acc.get("email"):
        print(f"Email:   {acc['email']}")
    print(f"Token:   {_fmt_remaining(acc.get('expiresAt'))}")
    print(f"Store:   {ACCOUNTS_FILE}")

    live = read_opencode_xai()
    if live and live.get("accessToken"):
        same = opencode_matches_account(live, acc)
        print(f"OpenCode xai: {'matches active' if same else 'DIFFERS from active'}")
        print(f"OpenCode tok: {_fmt_remaining(live.get('expiresAt'))}")
    else:
        print("OpenCode xai: not set")


def cmd_remove(label: str) -> None:
    if not label:
        print("Usage: grok-sync --remove <label>", file=sys.stderr)
        sys.exit(1)
    if not acquire_lock():
        print("Lock timeout", file=sys.stderr)
        sys.exit(1)
    try:
        store = load_store()
        if label not in store["accounts"]:
            print(f"Not found: {label}", file=sys.stderr)
            sys.exit(1)
        count = len(store["accounts"])
        if store.get("active") == label and count > 1:
            others = [k for k in store["accounts"] if k != label]
            store["active"] = others[0]
            store["rotationIndex"] = 0
            print(f"Active switched to: {store['active']}", file=sys.stderr)
        elif count == 1:
            store["active"] = None
            store["rotationIndex"] = 0
        del store["accounts"][label]
        save_store(store)
        print(f"Removed: {label}. {len(store['accounts'])} remaining.")
    finally:
        release_lock()


def do_sync() -> None:
    store = load_store()
    active = store.get("active")
    if not (active and active in store.get("accounts", {})):
        print("No active account to sync.", file=sys.stderr)
        return

    acc = dict(store["accounts"][active])
    fresh, reason = ensure_fresh_creds(acc)
    if not fresh:
        print(
            format_auth_error(reason, label=active, email=acc.get("email")),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        ensure_opencode_matches_active(quiet=True)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    store = load_store()
    acc = store["accounts"][active]
    print(
        f"{datetime.now().isoformat()} synced active={active} "
        f"({_fmt_remaining(acc.get('expiresAt'))})"
    )


def cmd_switch(label: str) -> None:
    if not label:
        print("Usage: grok-sync --switch <label>", file=sys.stderr)
        sys.exit(1)
    if not acquire_lock():
        print("Lock timeout", file=sys.stderr)
        sys.exit(1)
    try:
        store = load_store()
        if label not in store["accounts"]:
            print(f"Not found: {label}", file=sys.stderr)
            print("Use --login <label> after opencode auth login.", file=sys.stderr)
            sys.exit(1)
        store["active"] = label
        store["rotationIndex"] = list(store["accounts"].keys()).index(label)
        save_store(store)
        print(f"Switched to: {label}", file=sys.stderr)
    finally:
        release_lock()
    do_sync()


def cmd_rotate() -> None:
    if not acquire_lock():
        print("Lock timeout", file=sys.stderr)
        sys.exit(1)
    try:
        store = load_store()
        labels = list(store["accounts"].keys())
        if len(labels) < 2:
            print("Need 2+ accounts", file=sys.stderr)
            sys.exit(1)
        idx = (store.get("rotationIndex", 0) + 1) % len(labels)
        store["rotationIndex"] = idx
        store["active"] = labels[idx]
        save_store(store)
        print(
            f"Rotated to: {store['active']} ({idx + 1}/{len(labels)})",
            file=sys.stderr,
        )
    finally:
        release_lock()
    do_sync()


def cmd_sync() -> None:
    do_sync()


def print_help() -> None:
    print(
        """Usage: grok-sync [command]

  --login <label>    Snapshot current OpenCode xAI session as <label>
  --add <label>      Same as --login
  --switch <label>   Make <label> active and write it into OpenCode
  --list             List saved accounts
  --status           Show active account + OpenCode match
  --remove <label>   Remove a saved account
  --rotate           Rotate to the next account
  --sync             Re-sync active account into OpenCode (refresh if needed)
  --help             Show this help

Typical flow:
  opencode auth login          # pick xAI Grok OAuth, finish browser login
  grok-sync --login personal   # save as named account
  opencode auth login          # log into a second account
  grok-sync --login work
  grok-sync --switch work      # OpenCode now uses work
  gu                           # check SuperGrok usage
"""
    )


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print_help()
        return

    mode = args[0]
    label = args[1] if len(args) > 1 else ""

    match mode:
        case "--login":
            cmd_login(label)
        case "--add":
            cmd_add(label)
        case "--list":
            cmd_list()
        case "--status":
            cmd_status()
        case "--remove":
            cmd_remove(label)
        case "--switch":
            cmd_switch(label)
        case "--rotate":
            cmd_rotate()
        case "--sync":
            cmd_sync()
        case _:
            print(f"Unknown: {mode}", file=sys.stderr)
            print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
