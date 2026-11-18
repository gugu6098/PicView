# -*- coding: utf-8 -*-
"""PicView 安装器（单文件）：解压内置程序包到安装目录，再交给主程序注册文件关联。

主程序是 onedir 布局，启动时不再自解压，双击图片几乎是瞬时打开。
"""
import ctypes
import os
import subprocess
import sys
import zipfile

APP = 'PicView'


def app_bin():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, APP, 'app')


def main():
    root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    payload = os.path.join(root, 'payload.zip')
    dst = app_bin()
    if not os.path.isfile(payload):
        ctypes.windll.user32.MessageBoxW(0, '安装包损坏：缺少程序文件。', APP, 0x10)
        return 1
    os.makedirs(dst, exist_ok=True)
    with zipfile.ZipFile(payload) as z:
        z.extractall(dst)
    exe = os.path.join(dst, APP + '.exe')
    try:
        subprocess.run([exe, '--install-only'], timeout=120)
    except Exception:
        pass
    ctypes.windll.user32.MessageBoxW(
        0,
        'PicView 已安装。\n\n现在可以直接双击图片使用；'
        '如需设为默认打开方式，请运行安装目录下的 SetDefault.bat，'
        '或在 设置 → 默认应用 中选择 PicView。',
        APP, 0x40)
    return 0


if __name__ == '__main__':
    sys.exit(main())
