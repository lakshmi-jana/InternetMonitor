# Internet Connection Monitor

A Windows system-tray application that checks connectivity every 30 seconds,
estimates download and upload speed every hour, stores 90 days of history, and
serves a live dashboard.

It uses the installed Python 3.12 runtime and Python's standard library only.
There are no packages to install.

## Start

Double-click `run-monitor.bat`. The dashboard opens at
<http://127.0.0.1:8765>, and the application remains available from the system
tray. Right-click the tray icon to open the dashboard, run a test immediately,
or exit.

For diagnostics, run `run-console.bat` instead. Press Ctrl+C to stop it.

Only one instance can run at a time. Starting it again opens the existing
dashboard.

## What it measures

- Connectivity: DNS resolution plus three small HTTPS checks every 30 seconds
- Latency and jitter: application-level HTTPS response timing
- Download: four concurrent 5 MB HTTP transfers
- Upload: one generated 5 MB HTTP transfer
- History: SQLite records retained for 90 days

The speed values are HTTP throughput estimates, not ISP-certified line-rate
measurements. The default full test transfers about 25 MB, or roughly 18 GB per
30-day month when run hourly. Change the payload sizes or interval if needed.

## Configuration and data

On first run, the application creates:

- `%LOCALAPPDATA%\InternetMonitor\config.json`
- `%LOCALAPPDATA%\InternetMonitor\history.db`

Exit and restart the monitor after changing configuration. See
`config.example.json` for every setting. A failed full speed test is retried
after five minutes; tests never overlap.

The default endpoints are Cloudflare's public connectivity and speed-test
endpoints. They can be replaced with compatible endpoints you control.

## Dashboard access

By default, the local HTTP server binds only to `127.0.0.1`, so other devices
cannot connect.

To allow access from the home network:

1. Exit the monitor.
2. Set `dashboard.lan_enabled` to `true` in the generated `config.json`.
3. Restart the monitor and allow the private-network Windows Firewall prompt if
   one appears.
4. From another device, open
   `http://YOUR-PC-IP:8765/?token=ACCESS_TOKEN`, using the generated
   `dashboard.access_token` value.

Treat the token like a password. Do not forward the port on your router or
expose it to the public internet. LAN mode is plain HTTP and is intended only
for a trusted home network.

## Start automatically at sign-in

1. Press Win+R and enter `shell:startup`.
2. Create a shortcut in that folder targeting `run-monitor.bat`.
3. Set the shortcut's **Start in** directory to this project folder.

Remove the shortcut to disable automatic startup.

## Portable archive

`dist\InternetMonitor.zip` contains the runnable project. Rebuild it after
changes with:

```powershell
powershell -ExecutionPolicy Bypass -File .\package.ps1
```

The archive intentionally uses the Python 3.12 runtime already installed on the
computer; it does not install or bundle another runtime.

## Tests

From this directory:

```powershell
py -3.12 -m unittest discover -s tests -v
```

The integration tests use a local fake transfer server and do not consume
internet bandwidth.
