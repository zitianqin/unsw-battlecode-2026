# A Windows game host

A Windows PC can play referee games like `mm` and `ll` (see `remote.py`). It builds both bots itself, like
the Linux host, so its games are fair but may not match the Mac's move for move, and same-moves checks never
use it. Every call is `host_agent.py` run by uv, so the host needs SSH, uv and a C++20 compiler. The steps
below use an admin PowerShell unless they say otherwise.

**x86-64 only.** pycapnp 2.2.4, one of the pinned packages, has no Windows ARM64 wheel, so setting up the
toolchain would fail on an ARM PC.

## 1. OpenSSH Server

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
```

Keep the default SSH shell, `cmd.exe`, or switch it to PowerShell; both work. Don't make bash (Git Bash or
WSL) the default shell. With Git Bash, `uname` says `MINGW64_NT-...`, the referee takes the Windows route,
and bash mangles its `.\` paths, so the host fails its first check. WSL would be a separate Linux host: set it
up like `ll`.

The helper doesn't care which of cmd.exe and PowerShell runs, because nothing sent needs a shell. Every
command is one program, `.\.local\bin\uv.exe`, then plain arguments: letters, digits, `-`, `_`, `.` and `/`,
with the call's arguments sent as URL-safe base64. The one exception is the one-time install line. Its Python
code is in double quotes and has no spaces, `$`, `%`, backquotes or backslashes, so both shells pass it
through unchanged. Paths are relative to the folder an SSH session starts in, `%USERPROFILE%`.

## 2. Keys

Put the Mac's public key (`~/.ssh/id_ed25519.pub`) on the host:

- **Normal user** (recommended for a game account): `%USERPROFILE%\.ssh\authorized_keys`.
- **Member of Administrators**: sshd ignores the file above and reads
  `C:\ProgramData\ssh\administrators_authorized_keys`. That file must be writable only by Administrators and
  SYSTEM:

  ```powershell
  icacls.exe C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
  ```

## 3. Tailscale, and a firewall that only lets the tailnet in

Install Tailscale from https://tailscale.com/download/windows and sign in to the same tailnet as the Mac. In
its menu, turn on *Run unattended* so it stays connected with nobody logged in. Stop the PC sleeping on AC
power: `powercfg /change standby-timeout-ac 0`.

The OpenSSH install adds the firewall rule `OpenSSH-Server-In-TCP`, which accepts port 22 from anywhere.
Limit it to Tailscale's addresses:

```powershell
Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -RemoteAddress 100.64.0.0/10,fd7a:115c:a1e0::/48
```

## 4. uv

As the SSH user (not necessarily admin):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

This puts uv at `%USERPROFILE%\.local\bin\uv.exe`, which is the only place the referee looks for it on
Windows. uv downloads the pinned Python itself on first contact. That download (about 30 MB) can outlast the
first check's time limit; the host is then rested for a minute and the next check finds the download done. To
skip that wait, run `uv python install 3.14.5` once, using the version from `.venv/bin/python --version` on
the Mac.

## 5. A C++20 compiler: WinLibs MinGW-w64 g++

Download a WinLibs build from https://winlibs.com: GCC, UCRT runtime, POSIX threads, Win64, zip archive. The
variant without LLVM is enough. Unzip it to `C:\mingw64`, put its `bin` folder on the machine PATH, and
restart sshd so that new sessions see the change:

```powershell
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "Machine") + ";C:\mingw64\bin", "Machine")
Restart-Service sshd
```

Don't use `setx` for this: it truncates PATH at 1024 characters.

How the toolkit uses it:

- **Finding it.** The toolkit takes `CXX` if that is set, otherwise the first of `c++`, `g++`, `clang++` and
  `cl` on PATH. WinLibs provides `c++.exe` and `g++.exe`.
- **Compiling.** It runs `-std=c++20 -O2`. g++ names the output `bot.exe`, which the toolkit finds.
- **Running.** The bot needs MinGW's runtime DLLs (`libstdc++-6.dll`, `libgcc_s_seh-1.dll`,
  `libwinpthread-1.dll`). The engine starts bots without that PATH, so after each build the helper copies the DLLs
  the bot names from the compiler's folder next to its executable (`host_agent.bundle_runtime`).
- **Why not MSVC.** `cl` works only inside a Developer Command Prompt environment, which SSH sessions don't
  have.

Optional: Defender's real-time scan slows down venv creation and new executables. You can exclude the game
folder: `Add-MpPreference -ExclusionPath "$env:USERPROFILE\.bc-remote"`.

## 6. Check it from the Mac, then add it

Add a `Host wh` entry to `~/.ssh/config` with the PC's Tailscale name and user. Then check each piece:

```sh
ssh wh '.\.local\bin\uv.exe run --no-project python -c "import os;print(os.getcwd())"'  # C:\Users\<user>
ssh wh 'g++ --version'
```

The first check proves three things at once: sessions start in the home folder, uv is where the referee looks,
and uv can run a Python.

Add `{"name": "wh", "max": N}` to `~/.bc-loop/remote.json`. N is a safety ceiling, at most twice the PC's logical
CPUs: the referee fills the host's idle cores up to it (see `Host.measure` in `remote.py`).

What the first game does: it uploads `host_agent.py` to `.bc-remote\agent\`, creates the pinned toolchain in
`.bc-remote\toolchains\<key>` (a few minutes), and builds each bot in `.bc-remote\builds\`. If any step fails,
the game is replayed on the Mac and the host rests, as with every host. Its reason is in
`~/.bc-loop/remote-status.json`.

For the capacity check, the helper counts the busy share of all cores over a second, as on every host. Games stop as a process tree (`taskkill /T /F`).

## This team's Windows hosts (moved from loop/LOOP.md, 2026-09-28)

Both of sig's Windows PCs play in their WSL2 Ubuntu, as Linux hosts. His brother's PC `zz` plays as `zz-wsl`
(Ubuntu-24.04), marked `shared` so it gives games back when the PC is busy, with a 30 s `connect_timeout` in place
of sz's start-up task, which its non-admin account can't register; it is shut down by day, so it is `part_time`: an
outage in which it doesn't answer (`remote.UNREACHABLE`) alerts only past `health.PART_TIME_DOWN`, one in which it
answers and fails alerts as any other, and a down host costs nothing but its rests. His Spectre `sz` plays as
`sz-wsl`: `~/.ssh/config` reaches it through sz's own SSH with `sshd -i`, and sz's start-up task `bc-wsl-boot` (run
as sig with no login, S4U) keeps WSL running so a connection needn't wait out its 8 s start.
