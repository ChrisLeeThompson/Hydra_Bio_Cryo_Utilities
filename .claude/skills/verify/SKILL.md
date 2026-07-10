---
name: verify
description: How to launch and drive this app to verify changes at runtime (PySide6/QML GUI, simulation mode, programmatic UI driving).
---

# Verifying Hydra Bio Cryo Utilities

## Launch

```bash
python hydra_bio_cryo_utilities.py --simulation
```

- Python: `C:\Program Files\Enthought\Python\envs\AutoScript\python.exe` (on PATH as `python`), PySide6 6.7.1.
- `--simulation` skips the microscope connection; `appController.isConnected` becomes true and all pages enable. The status bar shows "Simulation mode".
- No test suite / typecheck matters here — the surface is the GUI.

## Driving the GUI programmatically

Write a driver script that wires the app exactly like `main()` in
`hydra_bio_cryo_utilities.py` (QGuiApplication, `AppController(force_simulation=True)`,
`qmlRegisterSingletonInstance` for MicroscopeBounds, `QQmlApplicationEngine`,
`addImportPath(qml_resources)`, context property `appController`, load `main.qml`,
`QTimer.singleShot(0, app_controller.initialize)`), then drive it with QTimer-chained steps
before `app.exec()`:

- **Find QML items**: walk `window.contentItem()` recursively via `.childItems()`;
  match by `item.metaObject().className()` (e.g. contains `"CheckBox"`) or
  `item.property("text")`. QML `id`s are not queryable at runtime.
- **Click like a user**: `QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)`
  with `pos = item.mapToScene(center)` as a `QPoint`. Goes through the real event pipeline.
- **Navigate pages**: click the visible sidebar entry whose `text` matches the page name
  (page-title Labels with the same text exist but are not visible until current).
- **Screenshots**: `window.grabWindow().save(path)` — works headed; save to scratchpad
  and Read them.
- **Simulate a drag-resize**: `window.setWidth(...)` — the responsive compact Binding
  reads width/height regardless of source.
- Give each step ~600 ms (`QTimer.singleShot`) to let geometry/layout settle, and add a
  watchdog `QTimer` that force-exits so a hang never blocks the session.

## Gotchas

- Window-state assertions (`window.visibility()`) can flake one run in a while
  (a maximize→windowed transition once read `Minimized` spuriously, unreproducible).
  Re-run before treating as a real failure; poll over ~1 s rather than sampling once.
- The window opens visibly on the desktop for the duration of the run (a few seconds).
- Compact-mode geometry constants live in `qml_resources/Config/AppConfig.qml`
  (`compactWindow*`, `compactBreakpoint*`, `mainWindow*`); the compact flag is
  `UiState.compact`, driven only by the size Binding in `main.qml`.
