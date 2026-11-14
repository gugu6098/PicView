// PicView PSD thumbnail provider
// A minimal in-process Shell thumbnail handler for .psd / .psb files.
// Reads the flattened composite image from the PSD, downsamples it and
// hands Explorer a 32bpp DIB. No external dependencies, no GDI+.

#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#include <windows.h>
#include <objidl.h>
#include <vector>
#include <cstdint>
#include <cstring>
#include <new>

// {7A3F2B14-9C4E-4D6A-8E15-3B2C7D9F4A01}
static const CLSID CLSID_PsdThumb =
    {0x7a3f2b14, 0x9c4e, 0x4d6a, {0x8e, 0x15, 0x3b, 0x2c, 0x7d, 0x9f, 0x4a, 0x01}};

// IThumbnailProvider / IInitializeWithStream (kept local so the build does not
// depend on SDK header versions).
static const IID IID_IThumbProv =
    {0xe357fccd, 0xa995, 0x4576, {0xb0, 0x1f, 0x23, 0x46, 0x30, 0x15, 0x4e, 0x96}};
static const IID IID_IInitStream =
    {0xb824b49d, 0x22ac, 0x4161, {0xac, 0x8a, 0x99, 0x16, 0xe8, 0xfa, 0x3f, 0x7f}};

struct IThumbProv : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetThumbnail(UINT cx, HBITMAP *phbmp,
                                                  int *pAlpha) = 0;
};
struct IInitStream : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Initialize(IStream *stm, DWORD mode) = 0;
};

// ---------------------------------------------------------------- PSD decode

static bool readRow(const BYTE *b, size_t off, size_t len, uint16_t comp,
                    size_t step, uint32_t width, BYTE *out) {
    memset(out, 0, width);
    if (comp == 0) {
        for (uint32_t x = 0; x < width; x++) out[x] = b[off + (size_t)x * step];
        return true;
    }
    size_t s = off, end = off + len;
    uint32_t d = 0;
    while (s < end && d < width) {
        int8_t v = (int8_t)b[s++];
        if (v >= 0) {
            size_t cnt = (size_t)v + 1;
            for (size_t i = 0; i < cnt && d < width; i++, d++) {
                if (s >= end) return true;
                out[d] = b[s];
                s += step;
            }
        } else if (v != -128) {
            if (s >= end) return true;
            BYTE val = b[s];
            s += step;
            size_t cnt = (size_t)(1 - v);
            for (size_t i = 0; i < cnt && d < width; i++, d++) out[d] = val;
        }
    }
    return true;
}

// Decodes the flattened composite into BGRA (top-down).
static bool decodePsd(const std::vector<BYTE> &d, std::vector<BYTE> &out,
                      int &ow, int &oh) {
    const BYTE *b = d.data();
    const size_t n = d.size();
    if (n < 26 || memcmp(b, "8BPS", 4) != 0) return false;

    const auto u16 = [&](size_t o) -> uint32_t { return ((uint32_t)b[o] << 8) | b[o + 1]; };
    const auto u32 = [&](size_t o) -> uint32_t {
        return ((uint32_t)b[o] << 24) | ((uint32_t)b[o + 1] << 16) |
               ((uint32_t)b[o + 2] << 8) | b[o + 3];
    };

    const uint16_t ver = (uint16_t)u16(4);
    const uint32_t channels = u16(12);
    const uint32_t height = u32(14);
    const uint32_t width = u32(18);
    const uint16_t depth = (uint16_t)u16(22);
    const uint16_t mode = (uint16_t)u16(24);

    if (!width || !height || width > 30000 || height > 30000) return false;
    if (channels < 1 || channels > 8) return false;
    if (depth != 8 && depth != 16) return false;
    if (mode != 1 && mode != 3 && mode != 4) return false;

    size_t p = 26;
    for (int i = 0; i < 3; i++) {  // color mode data / image resources / layer info
        if (p + 4 > n) return false;
        const uint32_t len = u32(p);
        p += 4;
        if (len > n || p + len > n) return false;
        p += len;
    }
    if (p + 2 > n) return false;
    const uint16_t comp = (uint16_t)u16(p);
    p += 2;
    if (comp > 1) return false;

    const size_t step = depth == 8 ? 1 : 2;
    const size_t rowBytes = (size_t)width * step;

    std::vector<uint32_t> rowLens;
    if (comp == 1) {
        const size_t unit = (ver == 2) ? 4 : 2;
        const size_t cnt = (size_t)channels * height;
        if (p + cnt * unit > n) return false;
        rowLens.resize(cnt);
        for (size_t i = 0; i < cnt; i++) {
            rowLens[i] = (unit == 4) ? u32(p) : u16(p);
            p += unit;
        }
    }

    const int kMaxSide = 1024;
    double scale = 1.0;
    if ((int)width > kMaxSide || (int)height > kMaxSide) {
        const uint32_t big = width > height ? width : height;
        scale = (double)kMaxSide / (double)big;
    }
    int w = (int)(width * scale);
    int h = (int)(height * scale);
    if (w < 1) w = 1;
    if (h < 1) h = 1;

    std::vector<uint8_t> chan((size_t)w * h * channels, 0);
    std::vector<uint8_t> row((size_t)width, 0);

    size_t off = p;
    for (uint32_t c = 0; c < channels; c++) {
        int lastDy = -1;
        for (uint32_t y = 0; y < height; y++) {
            const size_t len = (comp == 0) ? rowBytes : rowLens[(size_t)c * height + y];
            if (off + len > n) return false;
            if (!readRow(b, off, len, comp, step, width, row.data())) return false;
            off += len;
            int dy = (int)(y * scale);
            if (dy > h - 1) dy = h - 1;
            if (dy != lastDy) {
                lastDy = dy;
                const size_t base = (size_t)dy * w * channels + c;
                for (int x = 0; x < w; x++) {
                    int sx = (int)(x / scale);
                    if (sx > (int)width - 1) sx = (int)width - 1;
                    chan[base + (size_t)x * channels] = row[sx];
                }
            }
        }
    }

    out.assign((size_t)w * h * 4, 0);
    for (size_t i = 0, cnt = (size_t)w * h; i < cnt; i++) {
        const size_t o = i * channels;
        BYTE *q = &out[i * 4];
        int R, G, B, A = 255;
        if (mode == 1) {
            R = G = B = chan[o];
            if (channels > 1) A = chan[o + 1];
        } else if (mode == 3) {
            R = chan[o];
            G = channels > 1 ? chan[o + 1] : R;
            B = channels > 2 ? chan[o + 2] : R;
            if (channels > 3) A = chan[o + 3];
        } else {  // CMYK
            const int C = chan[o];
            const int M = channels > 1 ? chan[o + 1] : 0;
            const int Y = channels > 2 ? chan[o + 2] : 0;
            const int K = channels > 3 ? chan[o + 3] : 0;
            R = 255 - (C + K > 255 ? 255 : C + K);
            G = 255 - (M + K > 255 ? 255 : M + K);
            B = 255 - (Y + K > 255 ? 255 : Y + K);
            if (channels > 4) A = chan[o + 4];
        }
        q[0] = (BYTE)B;
        q[1] = (BYTE)G;
        q[2] = (BYTE)R;
        q[3] = (BYTE)A;
    }
    ow = w;
    oh = h;
    return true;
}

// ---------------------------------------------------------------- bitmap out

// Photoshop brand palette (matches the Ps app tile).
static const COLORREF PS_BLUE = RGB(0x31, 0xA8, 0xFF);
static const COLORREF PS_DARK = RGB(0x00, 0x1E, 0x36);

// Force the alpha of a rectangle opaque. GDI never touches the alpha byte,
// so every drawn pixel needs this afterwards or it stays fully transparent.
static void alphaRect(BYTE *bits, int W, int H, int x0, int y0, int x1, int y1) {
    if (!bits) return;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > W - 1) x1 = W - 1;
    if (y1 > H - 1) y1 = H - 1;
    for (int y = y0; y <= y1; y++) {
        BYTE *p = bits + ((size_t)y * W + x0) * 4;
        for (int x = x0; x <= x1; x++, p += 4) p[3] = 255;
    }
}

// Make every pixel that carries the given solid colour opaque (frame strokes).
static void alphaByColor(BYTE *bits, int W, int H, COLORREF c) {
    if (!bits) return;
    const BYTE r = GetRValue(c), g = GetGValue(c), b = GetBValue(c);
    const size_t n = (size_t)W * H;
    for (size_t i = 0; i < n; i++) {
        BYTE *p = bits + i * 4;
        if (p[0] == b && p[1] == g && p[2] == r && p[3] != 255) p[3] = 255;
    }
}

// Area-average downscale that keeps a correct alpha channel. GDI's StretchBlt
// zeroes the alpha byte on 32bpp DIBs, which leaves the artwork fully transparent.
static void blitScaled(const BYTE *src, int sw, int sh, BYTE *dst, int dw, int dh,
                       int dstStride, int ox, int oy) {
    for (int y = 0; y < dh; y++) {
        int sy0 = (int)((long long)y * sh / dh);
        int sy1 = (int)((long long)(y + 1) * sh / dh);
        if (sy1 <= sy0) sy1 = sy0 + 1;
        if (sy1 > sh) sy1 = sh;
        for (int x = 0; x < dw; x++) {
            int sx0 = (int)((long long)x * sw / dw);
            int sx1 = (int)((long long)(x + 1) * sw / dw);
            if (sx1 <= sx0) sx1 = sx0 + 1;
            if (sx1 > sw) sx1 = sw;

            unsigned long sa = 0, sr = 0, sg = 0, sb = 0, cnt = 0;
            for (int yy = sy0; yy < sy1; yy++) {
                const BYTE *p = src + ((size_t)yy * sw + sx0) * 4;
                for (int xx = sx0; xx < sx1; xx++, p += 4) {
                    const unsigned a = p[3];
                    sa += a;
                    sr += (unsigned)p[2] * a;
                    sg += (unsigned)p[1] * a;
                    sb += (unsigned)p[0] * a;
                    cnt++;
                }
            }
            BYTE *q = dst + (size_t)(oy + y) * dstStride + (size_t)(ox + x) * 4;
            if (sa && cnt) {
                q[0] = (BYTE)(sb / sa);
                q[1] = (BYTE)(sg / sa);
                q[2] = (BYTE)(sr / sa);
                q[3] = (BYTE)(sa / cnt);
            } else {
                q[0] = q[1] = q[2] = q[3] = 0;
            }
        }
    }
}

// Artwork + blue Ps frame + bottom-right "Ps" badge.
static HBITMAP makeThumbBitmap(const std::vector<BYTE> &bgra, int w, int h, UINT cx) {
    HDC screen = GetDC(nullptr);
    if (!screen) return nullptr;

    int total = (int)cx;
    if (total < 8) total = 8;

    int bw = total / 34;  // frame stroke width (thick, PS-tile style)
    if (bw < 1) bw = 1;

    int m = bw + 4 + total / 80;  // stroke + breathing gap before the artwork
    const int mMax = total / 7;
    if (m > mMax) m = mMax;
    if (m < 2) m = 2;

    int avail = total - 2 * m;
    if (avail < 6) {  // too small to frame: plain artwork
        avail = total;
        m = 0;
        bw = 0;
    }

    double sc = (double)avail / (double)(w > h ? w : h);
    if (sc > 1.0) sc = 1.0;
    int tw = (int)(w * sc + 0.5);
    int th = (int)(h * sc + 0.5);
    if (tw < 1) tw = 1;
    if (th < 1) th = 1;

    // Fixed square canvas: every PSD tile gets the same footprint, the same
    // frame and the same badge size no matter how wide or tall the artwork is.
    const int W = total;
    const int H = total;
    const int ox = (W - tw) / 2;
    const int oy = (H - th) / 2;

    BITMAPINFO bi;
    memset(&bi, 0, sizeof(bi));
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = W;
    bi.bmiHeader.biHeight = -H;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    void *dbits = nullptr;
    HBITMAP dst = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &dbits, nullptr, 0);
    if (!dst) {
        ReleaseDC(nullptr, screen);
        return nullptr;
    }

    BYTE *px = (BYTE *)dbits;
    blitScaled(bgra.data(), w, h, px, tw, th, W * 4, ox, oy);

    HDC dc = CreateCompatibleDC(screen);
    HGDIOBJ o2 = SelectObject(dc, dst);

    if (bw > 0) {
        int r = total / 16;
        if (r < 3) r = 3;
        HPEN pen = CreatePen(PS_SOLID, bw, PS_BLUE);
        HGDIOBJ op = SelectObject(dc, pen);
        HGDIOBJ ob = SelectObject(dc, GetStockObject(NULL_BRUSH));
        const int in = bw / 2;
        RoundRect(dc, in, in, W - in, H - in, r * 2, r * 2);
        SelectObject(dc, ob);
        SelectObject(dc, op);
        DeleteObject(pen);
    }

    // Bottom-right "PSD" pill: height is a fixed fraction of the tile, width
    // follows the measured label so the three letters always fit.
    int bh = (int)(total * 0.22);
    const int limH = (int)((W < H ? W : H) * 0.45);
    if (bh > limH) bh = limH;
    if (bh > 48) bh = 48;

    if (bh >= 10) {
        int fh = (bh * 66) / 100;
        if (fh < 6) fh = 6;
        HFONT fn = CreateFontW(-fh, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE,
                               DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                               ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_SWISS, L"Segoe UI");
        HGDIOBJ ofn = SelectObject(dc, fn);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, PS_BLUE);

        SIZE sz;
        memset(&sz, 0, sizeof(sz));
        GetTextExtentPoint32W(dc, L"PSD", 3, &sz);

        const int padx = bh >= 18 ? bh / 5 : 2;
        int bwd = sz.cx + padx * 2;
        const int minW = (bh * 17) / 10;
        if (bwd < minW) bwd = minW;

        const int gap = bw > 0 ? bw + m : 2;
        const int bx1 = W - 1 - gap;
        const int by1 = H - 1 - gap;
        int bx0 = bx1 - bwd + 1;
        const int by0 = by1 - bh + 1;
        if (bx0 < 1) bx0 = 1;
        int cr = (bh * 26) / 100;
        if (cr < 2) cr = 2;

        HBRUSH bb = CreateSolidBrush(PS_DARK);
        HGDIOBJ obb = SelectObject(dc, bb);
        HGDIOBJ obn = SelectObject(dc, GetStockObject(NULL_PEN));
        RoundRect(dc, bx0, by0, bx1 + 1, by1 + 1, cr, cr);
        SelectObject(dc, obn);
        SelectObject(dc, obb);
        DeleteObject(bb);

        int ebw = bw > 1 ? bw : 1;
        if (ebw > 2) ebw = 2;
        HPEN pen2 = CreatePen(PS_SOLID, ebw, PS_BLUE);
        HGDIOBJ op2 = SelectObject(dc, pen2);
        HGDIOBJ ob2 = SelectObject(dc, GetStockObject(NULL_BRUSH));
        RoundRect(dc, bx0, by0, bx1 + 1, by1 + 1, cr, cr);
        SelectObject(dc, ob2);
        SelectObject(dc, op2);
        DeleteObject(pen2);

        if (bh >= 14) {
            int tx = bx0 + (bwd - sz.cx) / 2;
            int ty = by0 + (bh - sz.cy) / 2;
            TextOutW(dc, tx, ty, L"PSD", 3);
        }

        SelectObject(dc, ofn);
        DeleteObject(fn);
        alphaRect(px, W, H, bx0, by0, bx1, by1);
    }

    if (bw > 0) alphaByColor(px, W, H, PS_BLUE);

    SelectObject(dc, o2);
    DeleteDC(dc);
    ReleaseDC(nullptr, screen);
    return dst;
}

// ---------------------------------------------------------------- COM object

class PsdThumb : public IThumbProv, public IInitStream {
  public:
    PsdThumb() : ref_(1) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IThumbProv)) {
            *ppv = static_cast<IThumbProv *>(this);
        } else if (IsEqualIID(riid, IID_IInitStream)) {
            *ppv = static_cast<IInitStream *>(this);
        } else {
            *ppv = nullptr;
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)InterlockedIncrement(&ref_); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG n = InterlockedDecrement(&ref_);
        if (n == 0) delete this;
        return (ULONG)n;
    }

    HRESULT STDMETHODCALLTYPE Initialize(IStream *stm, DWORD) override {
        if (!stm) return E_POINTER;
        try {
            STATSTG st;
            memset(&st, 0, sizeof(st));
            HRESULT hr = stm->Stat(&st, STATFLAG_NONAME);
            if (FAILED(hr)) return hr;
            const ULONGLONG size = st.cbSize.QuadPart;
            if (size < 26 || size > (512ull << 20)) return E_FAIL;
            data_.resize((size_t)size);
            LARGE_INTEGER zero;
            zero.QuadPart = 0;
            stm->Seek(zero, STREAM_SEEK_SET, nullptr);
            ULONG got = 0;
            hr = stm->Read(data_.data(), (ULONG)size, &got);
            if (FAILED(hr) || got < 26) return E_FAIL;
            data_.resize(got);
            return S_OK;
        } catch (...) {
            return E_FAIL;
        }
    }

    HRESULT STDMETHODCALLTYPE GetThumbnail(UINT cx, HBITMAP *phbmp, int *pAlpha) override {
        if (!phbmp) return E_POINTER;
        *phbmp = nullptr;
        if (pAlpha) *pAlpha = 2;  // WTSAT_ARGB
        if (cx == 0 || data_.empty()) return E_FAIL;
        if (cx > 1024) cx = 1024;
        try {
            std::vector<BYTE> bgra;
            int w = 0, h = 0;
            if (!decodePsd(data_, bgra, w, h)) return E_FAIL;
            HBITMAP bmp = makeThumbBitmap(bgra, w, h, cx);
            if (!bmp) return E_OUTOFMEMORY;
            *phbmp = bmp;
            return S_OK;
        } catch (...) {
            return E_FAIL;
        }
    }

  private:
    LONG ref_;
    std::vector<BYTE> data_;
};

class PsdThumbFactory : public IClassFactory {
  public:
    PsdThumbFactory() : ref_(1) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IClassFactory)) {
            *ppv = static_cast<IClassFactory *>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)InterlockedIncrement(&ref_); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG n = InterlockedDecrement(&ref_);
        if (n == 0) delete this;
        return (ULONG)n;
    }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown *outer, REFIID riid, void **ppv) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        if (!ppv) return E_POINTER;
        PsdThumb *obj = new (std::nothrow) PsdThumb();
        if (!obj) return E_OUTOFMEMORY;
        HRESULT hr = obj->QueryInterface(riid, ppv);
        obj->Release();
        return hr;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL) override { return S_OK; }

  private:
    LONG ref_;
};

// ---------------------------------------------------------------- exports

extern "C" __declspec(dllexport) HRESULT __stdcall DllGetClassObject(REFCLSID rclsid,
                                                                    REFIID riid,
                                                                    void **ppv) {
    if (!ppv) return E_POINTER;
    if (!IsEqualCLSID(rclsid, CLSID_PsdThumb)) return CLASS_E_CLASSNOTAVAILABLE;
    PsdThumbFactory *f = new (std::nothrow) PsdThumbFactory();
    if (!f) return E_OUTOFMEMORY;
    HRESULT hr = f->QueryInterface(riid, ppv);
    f->Release();
    return hr;
}

extern "C" __declspec(dllexport) HRESULT __stdcall DllCanUnloadNow() {
    return S_FALSE;  // stay resident; avoids dangling pointers inside Explorer
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(inst);
    return TRUE;
}
