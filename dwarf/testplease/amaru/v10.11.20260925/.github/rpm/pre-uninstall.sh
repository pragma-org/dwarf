#!/bin/sh
set -e

if [ "${1:-0}" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now amaru.service >/dev/null 2>&1 || true
fi
