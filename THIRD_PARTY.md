# Third-party components

PhoneTrace uses the following components. Its original Python source and build scripts are supplied in `source/` alongside the application.

- **Python 3.11** — Python Software Foundation License. https://www.python.org/
- **PySide6 / Shiboken6 / Qt 6.11.2** — Qt for Python, LGPLv3/GPLv3 or commercial licenses as applicable to the supplied modules. PhoneTrace imports QtCore, QtGui and QtWidgets; the portable runtime also includes QtNetwork, QtOpenGL, QtSvg and supporting platform/image/TLS plugins. All Qt libraries remain dynamically loaded DLLs. Unused Qt PDF, QML, Quick and Virtual Keyboard modules are excluded. License texts are in `licenses/`. Sources and licensing information: https://doc.qt.io/qtforpython-6/licenses.html and https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/
- **Android SDK Platform Tools 37.0.1 (ADB)** — obtained directly from https://dl.google.com/android/repository/platform-tools-latest-windows.zip . The supplied `tools/platform-tools/NOTICE.txt` contains notices for included components. https://developer.android.com/tools/releases/platform-tools
- **PyInstaller 6.22.3** — GPL with exception for distributing bundled applications. Build-time dependency. https://pyinstaller.org/

The portable directory layout intentionally keeps dynamic libraries as separate files. Sources, build scripts, and pinned requirements permit rebuilding the application using compatible replacement runtime libraries.

This application is not affiliated with Xiaomi, Google, Tencent, Qt, or any game publisher.
