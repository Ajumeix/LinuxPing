#!/usr/bin/env bash
set -e

echo "Installing LinuxPing Linux dependencies..."
if command -v apt >/dev/null 2>&1; then
  sudo apt update
  sudo apt install -y python3 python3-tk iputils-ping git
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-tkinter iputils git
elif command -v yum >/dev/null 2>&1; then
  sudo yum install -y python3 python3-tkinter iputils git
elif command -v pacman >/dev/null 2>&1; then
  sudo pacman -Sy --needed python tk iputils git
elif command -v zypper >/dev/null 2>&1; then
  sudo zypper install -y python3 python3-tk iputils git
else
  echo "Unsupported package manager. Install Python 3, Tkinter, ping, and git manually."
  exit 1
fi

echo "Done. Run with: ./run_linuxping.sh"
