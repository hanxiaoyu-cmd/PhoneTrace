# Implementation interfaces

All modules use Python 3.11+. The desktop UI uses PySide6 and custom QPainter charts. Shared data types are defined in models.py; no HTTP server is required.

## Backend (`phonetrace/adb.py`, `phonetrace/collector.py`)

`AdbClient(executable: str | Path)` exposes `devices() -> list[Device]`, `packages(serial) -> list[str]`, `foreground_package(serial) -> str`, `layers(serial, package='') -> list[str]`, `device_info(serial) -> dict`, `pair(endpoint, code) -> str`, `connect(endpoint) -> str`, `shell(serial, command, timeout=8) -> str`. Use no host shell, bounded subprocesses, CREATE_NO_WINDOW. `AdbError` for actionable errors.

`Collector(client: AdbClient, config: SessionConfig)` exposes `sample() -> Sample`, `close()`. Real sampler; never invent unsupported metrics. SurfaceFlinger per-game layer, never global/display FPS substitution. Sample timestamps use ISO local timezone; RecordingWorker assigns elapsed time from the session monotonic clock. Use `frame_intervals_ms` only new deduplicated frame intervals; no recording before first poll baseline; reset safely on layer change/stale timestamps. Handle missing data, layer auto-selection or manual override. Battery power only verified unplugged discharging; raw current sign retained and use magnitude for positive discharge W; units explicit. Default caller samples every 0.5s. Slow frames means observed intervals >50ms (not vendor Jank). `device_info` must not expose unrelated identifiers like phone number/accounts.

## UI (`phonetrace/ui.py`, optional `phonetrace/charts.py`)

`MainWindow(data_dir: Path)` QMainWindow, controller attaches later. Signals: `refresh_requested()`, `device_changed(str)`, `detect_requested()`, `layers_requested()`, `start_requested(object)` [dict keys serial,package,layer,interval_s,title,duration_s,demo], `stop_requested()`, `pair_requested(str,str)`, `connect_requested(str)`, `history_requested()`, `session_open_requested(str)`, `open_folder_requested()`. Expose methods: `set_devices(list[Device])`, `set_packages(list[str])`, `set_package(str)`, `set_layers(list[str])`, `set_status(str, level='info')`, `set_busy(bool)`, `set_recording(bool, demo=False)`, `add_sample(Sample)`, `reset_charts()`, `set_summary(dict)`, `set_history(list[dict])`, `show_session(dict,list[Sample])`, `show_error(str)`. History dict keys: id,title,started_at,duration_s,sample_count,avg_fps,avg_power_w,path,demo,status. Controller handles reading history/session. The window emits `close_requested` while recording. Controller stops the worker, finishes persistence and then closes the window safely.

Chinese UI, polished navy / cyan desktop dashboard, clear no-phone empty state; device/package/layer selection; demo toggle conspicuous data not real; runtime curves FPS,power,battery temp,CPU; metric cards with unavailable dash; 0.5s/1s/2s sampling; session title; optional duration minutes; connection form pairing and connection ports separate; export already automatically saved. Avoid enabling conflicting actions during recording. No actual shell calls in UI. Default monitor responsive at 1200x820, scroll smaller screens. Charts support gaps, don't plot missing as zero. UI no file deletion.

## Recorder (`phonetrace/recorder.py`)

`SessionRecorder(root:Path, config:SessionConfig, device_info:dict)` creates collision-proof directory, properties `path:Path`, `id:str`; `append(sample:Sample)` durable streaming CSV + JSONL and observed frame intervals CSV (UTF-8 BOM CSV for Excel). `finish(status='completed', error='') -> dict` write summary.json, metadata.json; do not divide null or gaps into zero. Expose `list_sessions(root)->list[dict]`, `load_session(path)->tuple[dict,list[Sample]]`; `build_summary(samples, metadata=None)->dict`. Write a standalone local `report.html` upon finish using inline SVG charts with missing gaps; no network or external JS. Clearly demarcate demo. Weighted power uses valid adjacent endpoints only; total actual elapsed duration field. FPS summary prefer total observed interval count / observed duration; missing not zeros. Header units clear; power as whole-device battery discharge estimate; sample_count valid; frame coverage limitations noted. Retain incomplete/interrupted session data on disk errors. Test actual persistence, nulls, disconnection, arithmetic; no unit tests mirroring trivial functions.

## Entry points and shared modules

`main.py` starts the application; `models.py` defines shared records; `controller.py` coordinates background jobs and recording; `demo.py` provides explicitly marked simulated data; `paths.py` locates local resources and writable storage.

`build.ps1` and `PhoneTrace.spec` build the Windows portable directory. `scripts/package_release.py` creates the distributable archive and its SHA-256 checksum. Tests live in `tests/`; desktop smoke checks live in `scripts/`.
