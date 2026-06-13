#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$APP_DIR/linuxping.py"

need_cmd() { command -v "$1" >/dev/null 2>&1; }

install_deps() {
  echo "LinuxPing needs Python 3, Tkinter, and ping."
  if need_cmd apt; then
    sudo apt update
    sudo apt install -y python3 python3-tk iputils-ping
  elif need_cmd dnf; then
    sudo dnf install -y python3 python3-tkinter iputils
  elif need_cmd yum; then
    sudo yum install -y python3 python3-tkinter iputils
  elif need_cmd pacman; then
    sudo pacman -Sy --needed python tk iputils
  elif need_cmd zypper; then
    sudo zypper install -y python3 python3-tk iputils
  else
    echo "Unsupported package manager. Install Python 3, Tkinter, and ping manually."
    exit 1
  fi
}

if ! need_cmd python3; then
  install_deps
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  install_deps
fi

if ! need_cmd ping; then
  install_deps
fi

echo "Starting LinuxPing..."
# ICMP ping may require privileges on some Linux setups. If normal run fails, run with sudo.
python3 "$APP" || {
  echo "Normal run failed. Retrying with sudo..."
  sudo python3 "$APP"
}
