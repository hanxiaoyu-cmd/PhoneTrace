# 本地开发与构建

日常使用请下载 [Windows 便携版](https://github.com/hanxiaoyu-cmd/PhoneTrace/releases)，无需安装 Python。连接手机的步骤见 [快速上手](quick-start.md)。

## 运行要求

- **电脑**：Windows x64，Windows 10 1809 或更新版本、Windows 11，依据 [Qt 6.11 支持范围](https://doc.qt.io/qt-6/windows.html)。目前仅在开发用 Windows 11 电脑验证过。
- **源码环境**：推荐 Python 3.11 或 3.12；依赖版本见仓库的 `requirements.txt` 与 `requirements-dev.txt`。
- **手机**：可开启 ADB 调试的 Android 设备；Android 11+ 可使用系统无线调试配对。可用指标取决于系统权限，不代表已适配所有机型。

## 从源码运行

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

连接真机前，将 Google 官方 [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) 解压到 `tools/platform-tools`；源码运行也可通过 `ANDROID_SDK_ROOT` 或 `PATH` 提供 ADB。构建便携版需要 `tools/platform-tools` 内的 `adb.exe`、配套 DLL、`NOTICE.txt` 和 `source.properties`。

不接手机时，可用演示模式检查界面、曲线和保存流程。演示数据会标明为模拟，并可存到独立目录：

```powershell
.\.venv\Scripts\python.exe main.py --demo --demo-seconds 10 --data-dir test-output/demo
```

## 验证与打包

```powershell
# 自动化测试：不需要手机、ADB、Root 或账号
.\.venv\Scripts\python.exe -m pytest -q

# 构建 Windows 便携程序
.\build.ps1 -OutputRoot dist/v0.1.1

# 生成完整 ZIP 便携包与 SHA-256 校验文件
.\.venv\Scripts\python.exe scripts/package_release.py --dist-root dist/v0.1.1
```

如果 PowerShell 执行策略阻止脚本，可将构建命令替换为：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -OutputRoot dist/v0.1.1
```

输出程序为 `dist/v0.1.1/PhoneTrace/PhoneTrace.exe`，ZIP 和校验文件位于同一输出根目录。请保留整个 `PhoneTrace` 程序文件夹。

**构建会拒绝覆盖已有程序目录**，以保护正在运行的软件和用户记录。再次构建请使用项目内的新输出目录，并让 `--dist-root` 与 `-OutputRoot` 保持一致。不要删除旧目录来绕过保护。

便携包包含运行库、ADB、第三方许可证，以及 `source` 目录中的源码和构建资源。分享自行构建的版本前，请验证独立启动、保存与历史回看；具体许可见 [第三方说明](../THIRD_PARTY.md)。

## 开发约定

代码位置、贡献流程和指标要求见 [贡献指南](../CONTRIBUTING.md)，模块约定见 [INTERFACES.md](../INTERFACES.md)。自动化测试与模拟演示不代表真机兼容或精度校准，现有验证范围见 [VALIDATION.md](../VALIDATION.md)。
