#!/bin/sh
set -e

if ! getent group amaru >/dev/null 2>&1; then
    groupadd --system amaru >/dev/null 2>&1 || true
fi

if ! getent passwd amaru >/dev/null 2>&1; then
    useradd \
        --system \
        --gid amaru \
        --home-dir /var/lib/amaru \
        --shell /sbin/nologin \
        --comment "Amaru service user" \
        amaru >/dev/null 2>&1 || true
fi

install -d -o amaru -g amaru /var/lib/amaru
