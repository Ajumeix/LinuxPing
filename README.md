# LinuxPing

LinuxPing is a visual multi-ping monitor for Linux with ICMP/TCP checks, profiles, outage summary, and CSV export.

## Quick start

```bash
chmod +x *.sh linuxping.py
./install_dependencies.sh
./run_linuxping.sh
```

## Install as a Linux app with logo

This creates a normal app launcher so you can open LinuxPing from your Linux app menu.

```bash
chmod +x *.sh linuxping.py
./install_dependencies.sh
./install_app.sh
```

After installation, open your app menu and search for:

```text
LinuxPing
```

The launcher is installed only for your current user under:

```text
~/.local/share/applications/linuxping.desktop
```

The app icon is installed under:

```text
~/.local/share/icons/hicolor/scalable/apps/linuxping.svg
```

## GitHub download on another Linux laptop

```bash
git clone https://github.com/Ajumeix/LinuxPing.git
cd LinuxPing
chmod +x *.sh linuxping.py
./install_dependencies.sh
./install_app.sh
```

Then launch from the app menu by searching `LinuxPing`, or run directly:

```bash
./run_linuxping.sh
```

## Download ZIP from GitHub webpage

1. Open your GitHub LinuxPing repository.
2. Click **Code**.
3. Click **Download ZIP**.
4. Extract the ZIP.
5. Open terminal inside the extracted folder.
6. Run:

```bash
chmod +x *.sh linuxping.py
./install_dependencies.sh
./install_app.sh
```

## Required packages

- Python 3
- Tkinter
- ping utility
- Git, only needed if you use `git clone`

Supported package managers in the scripts:

- apt
- dnf
- yum
- pacman
- zypper

## Build a standalone executable with PyInstaller

Install PyInstaller:

```bash
python3 -m pip install --user pyinstaller
```

Build:

```bash
pyinstaller --onefile --name linuxping linuxping.py
```

Run the output:

```bash
./dist/linuxping
```

Note: A PyInstaller binary is best built on the same Linux distro/version family where you will run it.
