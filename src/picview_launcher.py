# -*- coding: utf-8 -*-
"""PicView.exe - installer, file association and launcher shell."""
import os
import sys
import json
import base64
import shutil
import ctypes
import hashlib
import subprocess
import time
import winreg
from urllib.parse import quote, unquote

APP = 'PicView'
PROGID = 'PicView.Image'
PROTO = 'picview'
HKCU = winreg.HKEY_CURRENT_USER

IMG_EXTS = {'.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.gif', '.bmp',
            '.webp', '.avif', '.svg', '.ico', '.apng', '.jxl'}
PSD_EXTS = {'.psd', '.psb'}
PDF_EXTS = {'.pdf'}
OTHER_EXTS = {'.tif', '.tiff', '.heic', '.heif'}
ALL_EXTS = IMG_EXTS | PSD_EXTS | PDF_EXTS | OTHER_EXTS
ASSOC_EXTS = sorted(IMG_EXTS | PSD_EXTS | OTHER_EXTS)
CLASSES = 'Software\\Classes\\'
FILEEXTS = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\'
CAP = 'Software\\Clients\\PicView\\Capabilities'
HKLM = winreg.HKEY_LOCAL_MACHINE
THUMB_CLSID = '{7A3F2B14-9C4E-4D6A-8E15-3B2C7D9F4A01}'
THUMB_IID = '{E357FCCD-A995-4576-B01F-234630154E96}'
APPROVED = 'Software\\Microsoft\\Windows\\CurrentVersion\\Shell Extensions\\Approved'


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def thumb_dll_path():
    return os.path.join(appdir_bin(), 'PsdThumb.dll')


def thumb_installed():
    dll = thumb_dll_path()
    try:
        key = winreg.OpenKey(HKLM, CLASSES + 'CLSID\\' + THUMB_CLSID + '\\InprocServer32')
        try:
            value = winreg.QueryValue(key, '')
        finally:
            winreg.CloseKey(key)
        return os.path.normcase(value) == os.path.normcase(dll)
    except Exception:
        return False


def register_thumb():
    """Thumbnail handlers on Windows 11 are only honoured from HKLM."""
    dll = thumb_dll_path()
    if not os.path.isfile(dll):
        return False

    def sv(root, path, name, value):
        key = winreg.CreateKey(root, path)
        try:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        finally:
            winreg.CloseKey(key)

    base = CLASSES + 'CLSID\\' + THUMB_CLSID
    sv(HKLM, base, '', 'PicView PSD Thumbnail')
    sv(HKLM, base + '\\InprocServer32', '', dll)
    sv(HKLM, base + '\\InprocServer32', 'ThreadingModel', 'Apartment')
    for ext in ('.psd', '.psb'):
        sv(HKLM, CLASSES + ext + '\\ShellEx\\' + THUMB_IID, '', THUMB_CLSID)
    sv(HKLM, APPROVED, THUMB_CLSID, 'PicView PSD Thumbnail')

    # drop any stale per-user registration from earlier versions
    for ext in ('.psd', '.psb'):
        for k in (CLASSES + ext + '\\ShellEx\\' + THUMB_IID, CLASSES + PROGID + '\\ShellEx\\' + THUMB_IID):
            try:
                winreg.DeleteKey(HKCU, k)
            except Exception:
                pass

    notify_shell()
    return True


def unregister_thumb():
    def dk(root, path):
        try:
            winreg.DeleteKey(root, path)
        except Exception:
            pass

    for ext in ('.psd', '.psb'):
        dk(HKLM, CLASSES + ext + '\\ShellEx\\' + THUMB_IID)
        dk(HKCU, CLASSES + ext + '\\ShellEx\\' + THUMB_IID)
    dk(HKLM, CLASSES + 'CLSID\\' + THUMB_CLSID + '\\InprocServer32')
    dk(HKLM, CLASSES + 'CLSID\\' + THUMB_CLSID)
    for root in (HKLM, HKCU):
        try:
            key = winreg.OpenKey(root, APPROVED, 0, winreg.KEY_WRITE)
            try:
                winreg.DeleteValue(key, THUMB_CLSID)
            finally:
                winreg.CloseKey(key)
        except Exception:
            pass
    notify_shell()


def appdir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, APP)


def appdir_bin():
    """程序本体目录（onedir 安装后 exe 与 _internal 都在这里）"""
    return os.path.join(appdir(), 'app')


def bundle_root():
    """onedir 打包：exe 所在目录（含 _internal）；onefile：解压出的临时目录。"""
    if getattr(sys, 'frozen', False):
        exed = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.isdir(os.path.join(exed, '_internal')):
            return exed
        return getattr(sys, '_MEIPASS', exed)
    return os.path.dirname(os.path.abspath(__file__))


def res(name):
    root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, name)


def fileurl(path):
    p = path.replace('\\', '/')
    if p.startswith('//'):
        # UNC share  \\server\share\dir  ->  file://server/share/dir
        return 'file:' + quote(p, safe='/:%')
    if p.startswith('/'):
        return 'file://' + quote(p, safe='/:%')
    return 'file:///' + quote(p, safe='/:%')


def kind(name):
    e = os.path.splitext(name)[1].lower()
    if e in PSD_EXTS:
        return 'psd'
    if e in PDF_EXTS:
        return 'pdf'
    if e in OTHER_EXTS:
        return 'other'
    return 'img'


def find_edge():
    for p in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
        if os.path.isfile(p):
            return p
    return None


def msg(text):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, APP, 0x40)
    except Exception:
        pass


def set_default(path, value):
    winreg.SetValue(HKCU, path, winreg.REG_SZ, value)


def set_value(path, name, value):
    k = winreg.CreateKey(HKCU, path)
    try:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(k)


def delete_key(path):
    try:
        winreg.DeleteKey(HKCU, path)
        return True
    except Exception:
        return False


def notify_shell():
    # SHCNE_ASSOCCHANGED: make Explorer drop its cached file associations
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def register_proto(exe):
    """注册 picview: 协议，界面里“设为桌面背景”靠它回调本程序。"""
    base = 'Software\\Classes\\' + PROTO
    set_default(base, 'URL:PicView')
    set_value(base, 'URL Protocol', '')
    set_default(base + r'\DefaultIcon', exe + ',0')
    set_default(base + r'\shell\open\command', '"%s" --wall "%%1"' % exe)


def unregister_proto():
    for p in (r'\shell\open\command', r'\shell\open', r'\shell', ''):
        delete_key('Software\\Classes\\' + PROTO + p)


def _b64url(s):
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s).decode('utf-8', 'replace')


def set_wallpaper(uri):
    """picview:wall-<base64(file url)> -> 设为桌面背景"""
    s = str(uri or '').strip().strip('"')
    if 'wall-' in s:
        s = s.split('wall-', 1)[1]
    s = s.rstrip('/').strip()
    try:
        url = _b64url(s)
    except Exception:
        return False
    if not url.startswith('file:///'):
        return False
    path = unquote(url[8:]).replace('/', '\\')
    if not os.path.isfile(path):
        return False
    try:
        k = winreg.CreateKey(HKCU, 'Control Panel\\Desktop')
        try:
            winreg.SetValueEx(k, 'WallpaperStyle', 0, winreg.REG_SZ, '10')  # 填充
            winreg.SetValueEx(k, 'TileWallpaper', 0, winreg.REG_SZ, '0')
        finally:
            winreg.CloseKey(k)
    except Exception:
        pass
    try:
        return bool(ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, path, 3))
    except Exception:
        return False


def get_wallpaper():
    buf = ctypes.create_unicode_buffer(520)
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0073, 520, buf, 0)
    except Exception:
        return ''
    return buf.value


def copy_bundle(root, ad):
    """把整份程序复制到安装目录（跳过运行期数据）。"""
    skip = {'EdgeProfile', 'cache', 'launch.js', 'assoc_backup.json'}
    os.makedirs(ad, exist_ok=True)
    for name in os.listdir(root):
        if name in skip:
            continue
        s = os.path.join(root, name)
        t = os.path.join(ad, name)
        try:
            if os.path.isdir(s):
                shutil.rmtree(t, ignore_errors=True)
                shutil.copytree(s, t)
            else:
                shutil.copy2(s, t)
        except Exception:
            pass


def install():
    d = appdir()
    ad = appdir_bin()
    os.makedirs(d, exist_ok=True)
    os.makedirs(ad, exist_ok=True)

    # onedir 安装：直接从磁盘加载 exe 与 _internal，省掉 onefile
    # 每次启动都要解压 8.6MB 的固定开销。
    root = bundle_root()
    here = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, 'frozen', False) else '')
    if os.path.abspath(here).lower() != os.path.abspath(ad).lower():
        copy_bundle(root, ad)
    target_exe = os.path.join(ad, APP + '.exe')
    if not os.path.isfile(target_exe):
        target_exe = os.path.abspath(sys.executable)
    for n in ('PicView.html', 'picview.ico'):
        try:
            shutil.copy2(res(n), os.path.join(ad, n))
        except Exception:
            pass

    cmd = '"%s" "%%1"' % target_exe
    set_default('Software\\Classes\\' + PROGID, 'PicView 图片查看器')
    set_default('Software\\Classes\\' + PROGID + r'\DefaultIcon', target_exe + ',0')
    set_value('Software\\Classes\\' + PROGID + r'\shell\open', 'FriendlyAppName', APP)
    set_default('Software\\Classes\\' + PROGID + r'\shell\open\command', cmd)
    set_default(r'Software\Classes\Applications\PicView.exe\shell\open\command', cmd)
    set_default(r'Software\Classes\Applications\PicView.exe\DefaultIcon', target_exe + ',0')
    set_value(r'Software\Classes\Applications\PicView.exe', 'FriendlyAppName', APP)

    # picview: 协议（界面里的“设为桌面背景”）
    register_proto(target_exe)

    # Register as a real "Default apps" entry; Windows needs one user click to
    # confirm a default app (it silently reverts programmatic UserChoice writes).
    set_default('Software\\Clients\\PicView', 'PicView')
    set_default('Software\\Clients\\PicView\\shell\\open\\command', cmd)
    set_value(CAP, 'ApplicationName', 'PicView')
    set_value(CAP, 'ApplicationDescription', '轻量图片查看器 - PSD / PDF / 文件夹预览')
    set_value('Software\\RegisteredApplications', 'PicView', CAP)
    for e in ASSOC_EXTS + sorted(PDF_EXTS):
        set_value(CAP + '\\FileAssociations', e, PROGID)

    bp = os.path.join(d, 'assoc_backup.json')
    try:
        with open(bp, encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    backup = {}
    for e in ASSOC_EXTS + sorted(PDF_EXTS):
        sub = CLASSES + e
        try:
            k = winreg.CreateKey(HKCU, sub)
            try:
                old = winreg.QueryValue(k, '')
            except Exception:
                old = ''
            winreg.CloseKey(k)
        except Exception:
            old = ''
        uc = None
        try:
            k = winreg.OpenKey(HKCU, sub + r'\UserChoice')
            try:
                uc = winreg.QueryValueEx(k, 'ProgId')[0]
            except Exception:
                uc = ''
            winreg.CloseKey(k)
        except Exception:
            pass
        prev = existing.get(e, {})
        backup[e] = {'default': prev.get('default', old),
                     'userchoice': prev.get('userchoice', uc),
                     'explorer_userchoice': prev.get('explorer_userchoice'),
                     'explorer_uc_removed': prev.get('explorer_uc_removed', False)}

        # register as an "Open with" candidate
        try:
            k = winreg.CreateKey(HKCU, sub + r'\OpenWithProgids')
            try:
                winreg.SetValueEx(k, PROGID, 0, winreg.REG_NONE, b'')
            finally:
                winreg.CloseKey(k)
        except Exception:
            pass

        if e in PDF_EXTS:
            continue  # PDF: candidate only, do not take over the default

        # Explorer-level UserChoice outranks the Classes default on Win10/11
        uc_path = FILEEXTS + e + r'\UserChoice'
        try:
            k = winreg.OpenKey(HKCU, uc_path)
            try:
                ex_uc = winreg.QueryValueEx(k, 'ProgId')[0]
            except Exception:
                ex_uc = ''
            winreg.CloseKey(k)
            if not backup[e]['explorer_userchoice']:
                backup[e]['explorer_userchoice'] = ex_uc
            if delete_key(uc_path):
                backup[e]['explorer_uc_removed'] = True
        except Exception:
            pass

        set_default(sub, PROGID)
        if uc is not None:
            delete_key(sub + r'\UserChoice')

    try:
        with open(bp, 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    notify_shell()

    # deploy + register the PSD thumbnail handler (Windows 11 needs HKLM, so admin)
    try:
        shutil.copy2(res('PsdThumb.dll'), os.path.join(ad, 'PsdThumb.dll'))
    except Exception:
        pass
    if os.path.isfile(thumb_dll_path()) and not thumb_installed():
        if is_admin():
            try:
                register_thumb()
            except Exception:
                pass
        else:
            try:
                ctypes.windll.shell32.ShellExecuteW(
                    None, 'runas', os.path.join(ad, APP + '.exe'), '--regthumb', None, 0)
            except Exception:
                pass
    return d


def uninstall():
    d = appdir()
    backup = {}
    try:
        with open(os.path.join(d, 'assoc_backup.json'), encoding='utf-8') as f:
            backup = json.load(f)
    except Exception:
        pass
    for e, info in backup.items():
        sub = 'Software\\Classes\\' + e
        try:
            set_default(sub, info.get('default') or '')
        except Exception:
            pass
        try:
            k = winreg.OpenKey(HKCU, sub + r'\OpenWithProgids', 0, winreg.KEY_WRITE)
            try:
                winreg.DeleteValue(k, PROGID)
            finally:
                winreg.CloseKey(k)
        except Exception:
            pass
        uc = info.get('userchoice')
        if uc:
            set_value(sub + r'\UserChoice', 'ProgId', uc)
        ex_uc = info.get('explorer_userchoice')
        if ex_uc:
            set_value(FILEEXTS + e + r'\UserChoice', 'ProgId', ex_uc)
    delete_key(r'Software\Classes\Applications\PicView.exe\shell\open\command')
    delete_key(r'Software\Classes\Applications\PicView.exe\shell\open')
    delete_key(r'Software\Classes\Applications\PicView.exe\shell')
    delete_key(r'Software\Classes\Applications\PicView.exe')
    delete_key('Software\\Classes\\' + PROGID + r'\shell\open\command')
    delete_key('Software\\Classes\\' + PROGID + r'\shell\open')
    delete_key('Software\\Classes\\' + PROGID + r'\shell')
    delete_key('Software\\Classes\\' + PROGID + r'\DefaultIcon')
    delete_key('Software\\Classes\\' + PROGID)
    try:
        k = winreg.OpenKey(HKCU, 'Software\\RegisteredApplications', 0, winreg.KEY_WRITE)
        try:
            winreg.DeleteValue(k, 'PicView')
        finally:
            winreg.CloseKey(k)
    except Exception:
        pass
    for p in (CAP + '\\FileAssociations', CAP,
              'Software\\Clients\\PicView\\shell\\open\\command',
              'Software\\Clients\\PicView\\shell\\open',
              'Software\\Clients\\PicView\\shell', 'Software\\Clients\\PicView'):
        delete_key(p)
    try:
        unregister_thumb()
    except Exception:
        pass
    try:
        unregister_proto()
    except Exception:
        pass
    for n in ('PicView.html', 'picview.ico', 'PsdThumb.dll', 'launch.js',
              'assoc_backup.json', APP + '.exe'):
        try:
            os.remove(os.path.join(d, n))
        except Exception:
            pass
    for n in ('cache', 'EdgeProfile'):
        shutil.rmtree(os.path.join(d, n), ignore_errors=True)
    ad = appdir_bin()
    running_here = (getattr(sys, 'frozen', False) and
                    os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
                    == os.path.normcase(os.path.abspath(ad)))
    if running_here:
        # 正在运行的程序删不掉自己，交给系统在进程退出后清理
        try:
            subprocess.Popen('cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s"' % ad,
                             shell=True, creationflags=0x08000000)
        except Exception:
            pass
    else:
        shutil.rmtree(ad, ignore_errors=True)
    notify_shell()


def scan(folder, limit=3000):
    """扫描目录（深度 2 层）。用 os.scandir 的 DirEntry 缓存，
    枚举时已经带回文件大小，比 os.walk + getsize 少一次 stat。"""
    out = []
    folder = os.path.abspath(folder)
    stem = len(folder)

    def walk(path, depth):
        try:
            it = os.scandir(path)
        except OSError:
            return
        with it:
            try:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < 2 and not e.name.startswith('.'):
                                walk(e.path, depth + 1)
                            if len(out) >= limit:
                                return
                            continue
                    except OSError:
                        continue
                    ext = os.path.splitext(e.name)[1].lower()
                    if ext not in ALL_EXTS:
                        continue
                    try:
                        size = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    out.append({'name': e.name,
                                'rel': os.path.relpath(e.path, folder).replace('\\', '/'),
                                'path': e.path,
                                'url': fileurl(e.path),
                                'size': size,
                                'k': kind(e.name)})
                    if len(out) >= limit:
                        return
            except OSError:
                return

    walk(folder, 0)
    out.sort(key=lambda x: x['rel'].lower())
    return out


CACHE_MAX_FILE = 150 * 1024 * 1024
CACHE_MAX_TOTAL = 600 * 1024 * 1024


def localize_remote(files, d):
    """浏览器读不到网络共享(UNC)上的文件字节；
    PSD/PDF 需要读字节才能解码，因此先复制到本地缓存再把 url 指过去。
    增量策略：大小一致且缓存不比源文件旧就直接复用，只补新增/变更的文件。"""
    cache = os.path.join(d, 'cache')
    need = [f for f in files
            if f['k'] in ('psd', 'pdf') and str(f.get('path', '')).startswith('\\\\')]
    if not need:
        return
    os.makedirs(cache, exist_ok=True)
    total = 0
    keep = set()
    for f in need:
        if f['size'] > CACHE_MAX_FILE:
            continue
        name = (hashlib.md5(f['path'].encode('utf-8')).hexdigest()[:10]
                + '_' + f['name'])
        dst = os.path.join(cache, name)
        # 命中检查：文件存在且大小/时间对得上，跳过整份重拷
        try:
            st = os.stat(dst)
            src = os.stat(f['path'])
            if st.st_size == f['size'] and st.st_mtime >= src.st_mtime - 2:
                f['url'] = fileurl(dst)
                keep.add(name)
                total += f['size']
                continue
        except Exception:
            pass
        if total + f['size'] > CACHE_MAX_TOTAL:
            continue
        try:
            shutil.copy2(f['path'], dst)
            f['url'] = fileurl(dst)
            keep.add(name)
            total += f['size']
        except Exception:
            pass
    # 清掉本次清单之外的陈旧缓存
    try:
        for n in os.listdir(cache):
            if n not in keep:
                try:
                    os.remove(os.path.join(cache, n))
                except Exception:
                    pass
    except Exception:
        pass


def work_area():
    """屏幕可用区域（扣掉任务栏）。"""
    try:
        class RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                        ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            w, h = r.right - r.left, r.bottom - r.top
            if w > 320 and h > 240:
                return w, h
    except Exception:
        pass
    return 1440, 900


def edge_windows():
    u = ctypes.windll.user32
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, buf, 64)
        if buf.value == 'Chrome_WidgetWin_1' and u.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    u.EnumWindows(proto(cb), 0)
    return out


def maximize_new(pre, timeout=6.0):
    """把这次新开出来的窗口最大化。
    --app 窗口不记录最大化状态，Edge 每次都会按 --window-size 重新开一个普通窗口。"""
    u = ctypes.windll.user32
    t0 = time.time()
    while time.time() - t0 < timeout:
        for h in edge_windows():
            if h not in pre:
                u.ShowWindow(h, 3)     # SW_MAXIMIZE
                u.SetForegroundWindow(h)
                return True
        time.sleep(0.02)
    return False


def sync_assets(ad):
    """把随包的界面文件同步到安装目录：升级后不必重装，启动也不会变慢。"""
    for n in ('PicView.html', 'picview.ico'):
        try:
            s = res(n)
            t = os.path.join(ad, n)
            if not os.path.isfile(t) or os.path.getsize(t) != os.path.getsize(s):
                shutil.copy2(s, t)
        except Exception:
            pass


def open_view(target):
    d = appdir()
    ad = appdir_bin()
    html = os.path.join(ad, 'PicView.html')
    if not os.path.isfile(html):
        install()
        html = os.path.join(appdir_bin(), 'PicView.html')
    else:
        sync_assets(ad)
    data = None
    if target and os.path.isfile(target):
        target = os.path.abspath(target)
        folder = os.path.dirname(target)
        files = scan(folder)
        localize_remote(files, d)
        active = fileurl(target)
        base = os.path.basename(target)
        for f in files:
            if f['rel'] == base:
                active = f['url']
                break
        data = {'root': folder, 'active': active,
                'files': [{k: v for k, v in f.items() if k != 'path'} for f in files]}
    # 启动页：把本次的数据直接内联进模板，用唯一文件名落盘。
    # Edge 对 file:// 的缓存会忽略查询串，固定文件名会读到上一次的数据，
    # 表现为「打开图片却显示空界面」，所以每次换文件名。
    data_js = 'window.PICVIEW_LAUNCH=' + json.dumps(data, ensure_ascii=False) + ';'
    page = html
    try:
        with open(html, encoding='utf-8') as f:
            tpl = f.read()
        if '/*LAUNCH_DATA*/' in tpl:
            run = 'run-%d.html' % os.getpid()
            run_path = os.path.join(ad, run)
            with open(run_path, 'w', encoding='utf-8') as f:
                f.write(tpl.replace('/*LAUNCH_DATA*/', data_js))
            page = run_path
            for fn in os.listdir(ad):
                if fn.startswith('run-') and fn.endswith('.html') and fn != run:
                    try:
                        os.remove(os.path.join(ad, fn))
                    except Exception:
                        pass
        else:
            # 老界面兜底：仍然写 launch.js
            with open(os.path.join(ad, 'launch.js'), 'w', encoding='utf-8') as f:
                f.write(data_js)
    except Exception:
        pass

    edge = find_edge()
    url = fileurl(page)
    if edge:
        w, h = work_area()
        args = [edge,
                '--app=' + url,
                '--allow-file-access-from-files',
                '--user-data-dir=' + os.path.join(d, 'EdgeProfile'),
                '--window-size=%d,%d' % (w, h),
                '--window-position=0,0',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
                # --- 内存最小化（实测私有内存 203MB -> 130MB）---
                '--disable-gpu',                    # 关掉 GPU 进程，省 ~70MB
                '--enable-low-end-device-mode',     # 缩小光栅/图块缓存
                '--renderer-process-limit=1',
                '--process-per-site',
                '--js-flags=--max-old-space-size=256 --max-semi-space-size=2',
                '--disk-cache-size=4194304',
                '--disable-component-update',
                '--disable-domain-reliability',
                '--disable-client-side-phishing-detection',
                '--no-pings',
                '--metrics-recording-only',
                '--disable-breakpad',
                '--disable-crash-reporter',
                '--no-service-autorun']
        try:
            pre = set(edge_windows())
            subprocess.Popen(args, close_fds=True)
            maximize_new(pre)
        except Exception:
            os.startfile(html)
    else:
        os.startfile(html)


def is_installed():
    ad = appdir_bin()
    return (os.path.isfile(os.path.join(ad, 'PicView.html')) and
            os.path.isfile(os.path.join(ad, APP + '.exe')))


def main():
    a = sys.argv[1:]
    if '--wall' in a:
        i = a.index('--wall')
        uri = a[i + 1] if len(a) > i + 1 else ''
        try:
            set_wallpaper(uri)
        except Exception:
            pass
        return
    if '--getwall' in a:
        try:
            with open(os.path.join(appdir(), 'wall.txt'), 'w', encoding='utf-8') as f:
                f.write(get_wallpaper())
        except Exception:
            pass
        return
    if '--uninstall' in a:
        uninstall()
        msg('PicView 已卸载，文件关联已还原。')
        return
    if '--setdefault' in a:
        try:
            os.startfile('ms-settings:defaultapps?registeredAppUser=PicView')
        except Exception:
            msg('请打开：设置 → 应用 → 默认应用 → PicView → 设为默认值')
        return
    if '--regthumb' in a:
        try:
            register_thumb()
        except Exception:
            pass
        return
    if '--install-only' in a:
        try:
            install()
        except Exception as e:
            msg('安装失败：%s' % e)
        return
    target = next((x for x in a if not x.startswith('--') and os.path.isfile(x)), None)
    if '--install' in a or not is_installed():
        try:
            install()
        except Exception as e:
            msg('安装失败：%s' % e)
            return
    open_view(target)


if __name__ == '__main__':
    main()
