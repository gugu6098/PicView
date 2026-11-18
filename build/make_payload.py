# -*- coding: utf-8 -*-
"""把 onedir 产物打成 payload.zip，供单文件安装器内置。

用法：python build/make_payload.py <onedir 目录> <输出 zip>
"""
import os
import sys
import zipfile


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'dist/PicView'
    out = sys.argv[2] if len(sys.argv) > 2 else 'build/payload.zip'
    if not os.path.isdir(src):
        print('missing dir:', src)
        return 1
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, src).replace(os.sep, '/'))
    print('%s  %.1f MB' % (out, os.path.getsize(out) / 1048576.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
