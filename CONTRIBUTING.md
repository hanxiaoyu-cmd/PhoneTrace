# 参与 PhoneTrace

欢迎提交问题反馈、手机兼容性结果、文档改进和代码。项目优先关注三件事：第一次使用就能操作、记录包含解释数据所需的信息、每个指标都有明确来源与计算口径。

## 本地开发

推荐 Windows x64 与 Python 3.11 或 3.12。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe main.py --demo --data-dir test-output/demo
```

自动化测试使用受控数据和模拟设备，不需要手机、ADB、Root 或账号。演示模式可检查界面和保存流程，数据会标明为模拟。

连接真机或制作便携包时，按 [README](README.md) 准备 Google 官方 Android Platform Tools。构建命令为 `powershell -ExecutionPolicy Bypass -File .\build.ps1 -OutputRoot dist/v0.1.1`；若输出目录已存在，换一个新目录，构建不会覆盖旧程序或记录。请保留第三方许可证和源码。运行或分享自行构建的版本前，应验证独立启动、保存与历史回看。项目中的截图烟测脚本可能依赖本机字体等条件，因此基础 CI 不执行这些脚本。

## 代码位置

| 位置 | 职责 |
| --- | --- |
| `phonetrace/adb.py`、`collector.py` | 设备通信、指标读取和帧时间解析 |
| `phonetrace/recorder.py` | 本地持久化、汇总与 HTML 报告 |
| `phonetrace/controller.py` | 后台任务和记录生命周期 |
| `phonetrace/ui.py`、`charts.py` | 桌面界面和曲线 |
| `tests/` | 解析、统计、记录和工作流回归测试 |

模块约定见 [INTERFACES.md](INTERFACES.md)，现有验证范围见 [VALIDATION.md](VALIDATION.md)。

## 数据口径

- 新增或调整指标时，说明数据来源、单位、计算方式、采样频率和不可用条件。涉及 Android 行为时，尽可能附官方文档或 AOSP 源码依据。
- 保留缺失值和失败原因；缺失数据不能填成零或自动替换为模拟数据。
- 屏幕刷新率不能作为游戏 FPS。电池放电估计、整机耗电和游戏自身耗电应明确区分，不猜测双电芯倍率。
- 调整帧解析或统计时，补充能复现问题的少量脱敏测试数据，覆盖修正行为；避免只重复实现逻辑的测试。
- 说明验证是模拟测试还是真机测试，以及机型、系统、游戏和测试条件。模拟测试通过不代表真机兼容，也不构成精度校准。

## 提交修改

1. 先查看已有 Issues，避免重复；较大改动可先说明使用场景与方案。
2. 从主分支建立功能分支，每次修改尽量解决一个清晰的问题。
3. 运行 `python -m pytest tests -q`（或上面的虚拟环境命令）。修改界面时，额外手动检查演示记录、停止保存及历史回看。
4. Pull Request 写明原问题、修改后的行为、验证方法及仍未验证的范围。无需为了简单文案修改添加测试。

GitHub Actions 在 Windows 上使用 Python 3.11、3.12 运行已有测试；它不连接真实手机，不发布版本，不上传个人记录。CI 通过说明这些自动化用例通过，不能单独证明手机适配或测量精度。

## 反馈与隐私

使用 Issue 中的中文模板，提供软件版本、电脑系统、手机型号与系统、游戏、连接方式、复现步骤和界面提示。若需日志，仅分享必要片段，并删除序列号、IMEI、IP 地址、无线配对码、账号、个人路径和通知内容；截图也请遮挡个人信息。无需上传完整手机日志或记录目录。

项目自身源码按 [MIT License](LICENSE) 开源；第三方组件适用各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
