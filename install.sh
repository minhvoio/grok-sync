#!/bin/bash
set -e

BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo ""
echo -e "${CYAN}${BOLD}  grok-sync${RESET}${DIM} - grok-sync + gu${RESET}"
echo -e "${DIM}  Multi-account Grok OAuth switcher + SuperGrok usage monitor${RESET}"
echo -e "${DIM}  ─────────────────────────────────────────────────────────${RESET}"
echo ""

if ! command -v node &>/dev/null; then
  echo -e "${RED}  ✗ Node.js not found.${RESET} Install it: https://nodejs.org"
  exit 1
fi

NODE_MAJOR=$(node -v | cut -d. -f1 | tr -d 'v')
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo -e "${RED}  ✗ Node.js >= 18 required.${RESET} Found: $(node -v)"
  exit 1
fi

if ! command -v npm &>/dev/null; then
  echo -e "${RED}  ✗ npm not found.${RESET}"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo -e "${RED}  ✗ Python 3 not found.${RESET}"
  exit 1
fi

if ! command -v curl &>/dev/null; then
  echo -e "${RED}  ✗ curl not found.${RESET}"
  exit 1
fi

echo -e "${GREEN}  ✓${RESET} Node.js $(node -v)"
echo -e "${GREEN}  ✓${RESET} npm $(npm -v)"
echo -e "${GREEN}  ✓${RESET} Python $(python3 --version | awk '{print $2}')"
echo -e "${GREEN}  ✓${RESET} curl"

NPM_PREFIX=$(npm config get prefix 2>/dev/null || echo "")

echo ""
echo -e "${BOLD}  Installing grok-sync...${RESET}"

# Prefer local install when run from a checkout; otherwise install from GitHub.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/package.json" ] && [ -f "$SCRIPT_DIR/bin/gu.mjs" ]; then
  npm install -g "$SCRIPT_DIR" 2>/dev/null || {
    echo -e "${RED}  ✗ Local install failed.${RESET}"
    exit 1
  }
else
  npm install -g github:minhvoio/grok-sync 2>/dev/null || {
    echo -e "${RED}  ✗ Installation failed.${RESET}"
    echo -e "${DIM}  Try: npm install -g github:minhvoio/grok-sync${RESET}"
    exit 1
  }
fi

if [ -L "$NPM_PREFIX/bin/grok-sync" ] || [ -f "$NPM_PREFIX/bin/grok-sync" ]; then
  echo -e "${GREEN}  ✓${RESET} grok-sync → $NPM_PREFIX/bin/grok-sync"
else
  echo -e "${YELLOW}  ⚠${RESET} grok-sync not found at $NPM_PREFIX/bin/grok-sync"
fi

if [ -L "$NPM_PREFIX/bin/gu" ] || [ -f "$NPM_PREFIX/bin/gu" ]; then
  echo -e "${GREEN}  ✓${RESET} gu        → $NPM_PREFIX/bin/gu"
else
  echo -e "${YELLOW}  ⚠${RESET} gu not found at $NPM_PREFIX/bin/gu"
fi

case ":$PATH:" in
  *":$NPM_PREFIX/bin:"*) ;;
  *) echo ""
     echo -e "${YELLOW}  ⚠${RESET} $NPM_PREFIX/bin is not in PATH. Add it so grok-sync/gu can be found." ;;
esac

echo ""
echo -e "${DIM}  ─────────────────────────────────────────────────────────${RESET}"
echo -e "${GREEN}${BOLD}  Done.${RESET} Run:"
echo ""
echo -e "    ${CYAN}opencode auth login${RESET}     # pick xAI Grok OAuth"
echo -e "    ${CYAN}grok-sync --login work${RESET}  # save current session as 'work'"
echo -e "    ${CYAN}grok-sync --switch work${RESET} # activate an account in OpenCode"
echo -e "    ${CYAN}gu${RESET}                      # SuperGrok weekly + monthly usage"
echo -e "    ${CYAN}gu --json${RESET}               # machine-readable"
echo ""
