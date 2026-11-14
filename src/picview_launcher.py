# -*- coding: utf-8 -*-
"""PicView.exe - installer, file association and launcher shell."""
import os
import sys
import json
import shutil
import ctypes
import hashlib
import subprocess
import winreg
from urllib.parse import quote

APP = 'PicView'
PROGID = 'PicView.Image'
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
    return os.path.join(appdir(), 'PsdThumb.dll')


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


def install():
    d = appdir()
    os.makedirs(d, exist_ok=True)

    # copy self + html to a stable install location
    target_exe = os.path.join(d, APP + '.exe')
    if os.path.abspath(sys.executable).lower() != target_exe.lower():
        try:
            shutil.copy2(sys.executable, target_exe)
        except Exception:
            target_exe = os.path.abspath(sys.executable)
    shutil.copy2(res('PicView.html'), os.path.join(d, 'PicView.html'))
    try:
        shutil.copy2(res('picview.ico'), os.path.join(d, 'picview.ico'))
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
        shutil.copy2(res('PsdThumb.dll'), os.path.join(d, 'PsdThumb.dll'))
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
                    None, 'runas', os.path.join(d, APP + '.exe'), '--regthumb', None, 0)
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
    for n in ('PicView.html', 'picview.ico', 'PsdThumb.dll', 'launch.js', 'assoc_backup.json'):
        try:
            os.remove(os.path.join(d, n))
        except Exception:
            pass
    notify_shell()


def scan(folder, limit=3000):
    out = []
    folder = os.path.abspath(folder)
    for dp, dns, fns in os.walk(folder):
        if dp[len(folder):].count(os.sep) >= 2:
            dns[:] = []
            continue
        dns[:] = [x for x in dns if not x.startswith('.')]
        for fn in fns:
            e = os.path.splitext(fn)[1].lower()
            if e not in ALL_EXTS:
                continue
            full = os.path.join(dp, fn)
            try:
                size = os.path.getsize(full)
            except Exception:
                continue
            out.append({'name': fn,
                        'rel': os.path.relpath(full, folder).replace('\\', '/'),
                        'path': full,
                        'url': fileurl(full),
                        'size': size,
                        'k': kind(fn)})
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x['rel'].lower())
    return out


CACHE_MAX_FILE = 150 * 1024 * 1024
CACHE_MAX_TOTAL = 600 * 1024 * 1024


def localize_remote(files, d):
    """浏览器读不到网络共享(UNC)上的文件字节；
    PSD/PDF 需要读字节才能解码，因此先复制到本地缓存再把 url 指过去。"""
    cache = os.path.join(d, 'cache')
    need = [f for f in files
            if f['k'] in ('psd', 'pdf') and str(f.get('path', '')).startswith('\\\\')]
    if not need:
        return
    shutil.rmtree(cache, ignore_errors=True)
    total = 0
    for f in need:
        if f['size'] > CACHE_MAX_FILE or total + f['size'] > CACHE_MAX_TOTAL:
            continue
        try:
            os.makedirs(cache, exist_ok=True)
            dst = os.path.join(cache,
                               hashlib.md5(f['path'].encode('utf-8')).hexdigest()[:10]
                               + '_' + f['name'])
            shutil.copy2(f['path'], dst)
            f['url'] = fileurl(dst)
            total += f['size']
        except Exception:
            pass


def open_view(target):
    d = appdir()
    html = os.path.join(d, 'PicView.html')
    if not os.path.isfile(html):
        install()
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
    try:
        with open(os.path.join(d, 'launch.js'), 'w', encoding='utf-8') as f:
            f.write('window.PICVIEW_LAUNCH=' + json.dumps(data, ensure_ascii=False) + ';')
    except Exception:
        pass

    edge = find_edge()
    url = fileurl(html)
    if edge:
        args = [edge,
                '--app=' + url,
                '--allow-file-access-from-files',
                '--user-data-dir=' + os.path.join(d, 'EdgeProfile'),
                '--window-size=1440,900',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync']
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception:
            os.startfile(html)
    else:
        os.startfile(html)


def is_installed():
    d = appdir()
    return (os.path.isfile(os.path.join(d, 'PicView.html')) and
            os.path.isfile(os.path.join(d, APP + '.exe')))


def main():
    a = sys.argv[1:]
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
