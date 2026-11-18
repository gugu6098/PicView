# PicView

一个轻量的 Windows 图片查看器。界面跑在系统自带的 Edge（Chromium）内核里，所以打包出来只有一个 **8.6 MB** 的单文件 EXE，不需要 Python、不需要运行库，也没有常驻后台进程。

除了常规格式，它原生支持 **PSD** 和 **PDF**，并且能在资源管理器里给 PSD 文件显示真实内容缩略图（带蓝色 PS 外框和 PSD 角标）。

界面仿 2345 看图王：顶部工具栏 + 底部胶片条 + 状态栏 + 右键菜单 + 幻灯片。

## 功能

- **格式**：JPG / PNG / GIF / BMP / WebP / SVG / ICO / AVIF，PSD / PSB，PDF
- **PSD**：自写解码器，支持 8/16 位、灰度 / RGB / CMYK、RAW 与 PackBits RLE 压缩（含 PSB 的 4 字节行表）
- **PDF**：交给 Edge 内置阅读器渲染，支持多页翻页
- **浏览**：打开任意图片即扫描所在目录（含两层子目录，上限 3000 个文件），左侧目录树、中间缩略图网格、右侧大图预览，`←` `→` 连续翻页
- **资源管理器缩略图**：C++ 实现的 Shell 缩略图扩展，PSD 在文件夹里直接显示画面内容，统一正方形卡片 + 蓝色外框 + 右下角 PSD 角标
- **网络共享**：支持 `\\server\share\...` UNC 路径直接打开
- **交互**：滚轮缩放、拖拽平移、旋转、适应窗口、1:1、全屏、幻灯片（3 秒/张）、拖入文件夹递归加载
- **文件关联**：安装后可一键注册为图片默认打开方式

## 系统要求

- Windows 10 / 11（x64）
- 系统自带的 Microsoft Edge（Chromium 内核，Win10/11 默认自带）
- 注册 PSD 缩略图扩展需要管理员权限（只影响资源管理器缩略图，不影响看图）

## 使用

1. 下载 Release 里的 `PicView-Setup.exe`，双击运行，安装到 `%LOCALAPPDATA%\PicView\app\`
2. 若弹出 UAC 提权窗口（用于注册 PSD 缩略图扩展），选择「是」
3. 双击任意图片即可用它打开；未接管默认打开方式时，在「设置 → 默认应用」里搜 PicView 点一次「设为默认值」

> 程序本体是 onedir 布局（exe + `_internal`），启动时不需要自解压，双击图片几乎是瞬时打开。

常用快捷键：

| 按键 | 功能 |
|---|---|
| `←` `→` | 上一张 / 下一张 |
| `+` `-` | 放大 / 缩小 |
| `0` | 适应窗口 |
| `1` | 原始大小 |
| `R` / `Shift+R` | 顺时针 / 逆时针旋转 |
| `F` | 全屏 |
| `B` `T` | 目录树 / 缩略图条 |
| `Space` | 暂停 / 继续幻灯片 |
| `Ctrl+O` | 打开文件 |

## 从源码构建

### 1. 编译 PSD 缩略图扩展

需要一个 C++ 编译器。Windows 上最省事的是用 zig 交叉编译，不需要装 MSVC：

```bash
pip install ziglang
cd src/thumb
zig c++ -target x86_64-windows-gnu -shared -O2 \
    -o PsdThumb.dll psdthumb.cpp psdthumb.def -lole32 -lgdi32
```

### 2. 打包

```bash
pip install pyinstaller
cd src
# 2a. onedir 主程序（快，装好后双击图片近乎瞬时）
pyinstaller --onedir --noconsole --name PicView --icon assets/picview.ico \
    --add-data "<绝对路径>/src/PicView.html;." \
    --add-data "<绝对路径>/src/assets/picview.ico;." \
    --add-data "<绝对路径>/src/thumb/PsdThumb.dll;." \
    picview_launcher.py

# 2b. 把产物打成 payload.zip
python build/make_payload.py dist/PicView build/payload.zip

# 2c. 单文件安装器（分发给别人只需要这一个 exe）
pyinstaller --onefile --noconsole --name PicView-Setup --icon assets/picview.ico \
    --add-data "<绝对路径>/build/payload.zip;." \
    picview_setup.py
```

产物：`dist/PicView-Setup.exe`（单文件，给别人用）、`dist/PicView/`（onedir 布局）。

或者直接跑 `build/build.bat`，四步一次完成。

## 目录结构

```
src/
  PicView.html          界面 + PSD 解码 + 交互逻辑（单文件）
  picview_launcher.py   EXE 壳：安装、文件关联、目录扫描、拉起 Edge
  thumb/
    psdthumb.cpp        Shell 缩略图扩展（IThumbnailProvider）
    psdthumb.def        导出表
  assets/
    picview.ico         应用图标（9 种尺寸）
scripts/
  SetDefault.bat        打开「默认应用」设置页
  Uninstall.bat         卸载：还原文件关联、删除已安装文件
  RefreshThumbs.bat     清理并重建缩略图缓存
build/
  build.bat             一键编译 + 打包
```

## 实现说明

EXE 壳只做三件事：安装自己、写注册表、把当前目录的文件列表写成 `launch.js`，然后用 `--app` 模式拉起 Edge 并立刻退出。界面和皮都在 `PicView.html` 里，所以改界面不需要重新打包（改完 `%LOCALAPPDATA%\PicView\PicView.html` 即可）。

PSD 解码器是手写的，不依赖任何第三方库：跳过头、颜色模式、图像资源、图层三个段落后，逐通道逐行解 RAW 或 PackBits，边解边降采样，避免整图多份副本。

缩略图扩展是标准的 in-proc COM 组件，实现 `IInitializeWithStream` + `IThumbnailProvider`。两个坑值得记一下：

- Windows 11 只从 `HKLM` 读缩略图扩展，注册到 `HKCU` 完全不生效
- GDI 的 `StretchBlt` 输出 32bpp DIB 时会把 alpha 字节清零，必须自己写面积平均缩放，画完框和角标后还要手动补 alpha

## 已知限制

- 只读浏览，不含编辑、批处理、格式转换
- TIFF / HEIC / 相机 RAW 暂不支持（仅占位）
- Win11 有默认应用反劫持机制，程序无法静默接管 `.jpg` `.png` 等受保护格式，必须在设置页确认一次
- 依赖 Edge 作为渲染内核，没装 Chromium 版 Edge 的系统（如 Win7）无法运行

## License

MIT
