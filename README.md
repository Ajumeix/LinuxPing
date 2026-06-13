# LinuxPing for Linux

Visual Multi Ping for Linux with ICMP/TCP checks, profiles, outage summary, and CSV export.

## Quick start

```bash
chmod +x run_linuxping.sh install_dependencies.sh linuxping.py
./run_linuxping.sh
```

If dependencies are missing, run:

```bash
./install_dependencies.sh
./run_linuxping.sh
```

## Required packages

- Python 3
- Tkinter
- ping utility

Supported package managers in the scripts:

- apt
- dnf
- yum
- pacman
- zypper

## GitHub download on another Linux laptop

```bash
git clone https://github.com/YOUR_USERNAME/linuxping.git
cd linuxping
chmod +x *.sh linuxping.py
./install_dependencies.sh
./run_linuxping.sh
```

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
