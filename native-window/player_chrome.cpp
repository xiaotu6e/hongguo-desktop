#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cwchar>
#include "resize_geometry.h"

// WSA's own F11 mode hides its composition-based title bar. We keep the
// resulting borderless window at its content size, without copying video.
static HWND target = nullptr, grip = nullptr, feedButton = nullptr;
static DWORD targetPid = 0;
static HANDLE parentProcess = nullptr, stopEvent = nullptr, inputEvent = nullptr;
static bool ownsFullscreen = false, entering = false, syncing = false;
static bool hideTitlebar = true;
static RECT requested{}, lastGrip{};
static RECT dragWindow{};
static POINT dragPointer{};
static bool dragging = false;
static int resizeEdge = hongguo::None;
static RECT dragWork{};
static ULONGLONG transitionStarted = 0;
static int captionDip = 30;
static const wchar_t* CLASS_NAME = L"HongguoPlayerDragArea";
static int wheelRemainder = 0;
static ULONGLONG lastWheelInput = 0, lastWheelForward = 0;
static UINT_PTR wheelSequence = 0;
static UINT_PTR pendingWheel = 0;
static std::atomic<ULONGLONG> inputHeartbeat{0};
static std::atomic<UINT_PTR> hookGeneration{0}, hookWheelCalls{0};
static std::atomic<DWORD> hookError{0};
static const UINT TAKE_WHEEL = WM_APP + 21;
static const UINT INPUT_READY = WM_APP + 22;
static const UINT QUEUE_WHEEL = WM_APP + 23;
static const UINT SET_TITLEBAR = WM_APP + 24;
static const UINT INPUT_STATUS = WM_APP + 25;
static const UINT SET_FULLSCREEN_TARGET = WM_APP + 26;
static const UINT TAKE_FULLSCREEN = WM_APP + 27;
static const UINT QUEUE_FULLSCREEN = WM_APP + 28;
static const UINT SET_FULLSCREEN_PRESENTATION = WM_APP + 29;
static const UINT QUEUE_PAGE_CHANGE = WM_APP + 30;
static const UINT TAKE_PAGE_CHANGE = WM_APP + 31;
static const UINT FOCUS_FULLSCREEN_CLICK = WM_APP + 32;
static DWORD pendingPageChangeAt = 0;
static const ULONGLONG INPUT_LEASE_MS = 3000;
static const ULONGLONG FULLSCREEN_TARGET_MS = 3000;
static RECT fullscreenNormalized{};
static RECT fullscreenMaskNormalized{}, feedButtonLocal{}, feedMaskLocal{}, lastFeedWidget{};
static COLORREF feedBackground = RGB(0, 0, 0);
static const wchar_t* FEED_BUTTON_CLASS = L"HongguoFeedFullscreen";
static std::atomic<ULONGLONG> fullscreenTargetAt{0};
static std::atomic<UINT_PTR> fullscreenHitVersion{0};
static std::atomic<ULONGLONG> fullscreenTopLeft{0}, fullscreenBottomRight{0};
static DWORD pendingFullscreenAt = 0;
static int pendingFullscreenKind = 1;
static std::atomic<bool> fullscreenPassthrough{false};
static bool fullscreenPressed = false;
static bool fullscreenPressPassthrough = false;
static std::atomic<UINT_PTR> fullscreenPresses{0}, fullscreenQueued{0}, fullscreenFocusOk{0};
static POINT fullscreenPressPoint{};
static DWORD fullscreenPressAt = 0;
static bool SameTarget();

static bool SameTarget() {
    DWORD pid = 0;
    return IsWindow(target) && GetWindowThreadProcessId(target, &pid) && pid == targetPid;
}
static bool HasCaption() { return (GetWindowLongPtrW(target, GWL_STYLE) & WS_CAPTION) != 0; }
static int EdgeAt(POINT point) {
    RECT r{};
    if (!hideTitlebar || entering || !SameTarget() || HasCaption() || IsZoomed(target) ||
        !GetWindowRect(target, &r)) return hongguo::None;
    UINT dpi = GetDpiForWindow(target); if (!dpi) dpi = 96;
    return hongguo::ResizeEdge({r.left, r.top, r.right, r.bottom}, point.x, point.y,
                              MulDiv(6, dpi, 96), MulDiv(12, dpi, 96));
}
static void RememberSize() {
    RECT r{};
    if (!resizeEdge || !SameTarget() || !GetWindowRect(target, &r)) return;
    UINT dpi = GetDpiForWindow(target); if (!dpi) dpi = 96;
    const wchar_t* name = r.right-r.left > r.bottom-r.top ?
        L"HongguoLandscapeHeightDip" : L"HongguoPortraitHeightDip";
    SetPropW(target, name, reinterpret_cast<HANDLE>(static_cast<INT_PTR>(MulDiv(r.bottom-r.top, 96, dpi))));
}
static void CancelDrag() {
    if (dragging) RememberSize();
    dragging = false;
    resizeEdge = hongguo::None;
    if (GetCapture() == grip) ReleaseCapture();
}
static bool ContentRect(RECT& area) {
    if (!SameTarget() || !GetWindowRect(target, &area)) return false;
    if (HasCaption()) {
        RECT client{}; POINT origin{};
        if (!GetClientRect(target, &client) || !ClientToScreen(target, &origin)) return false;
        UINT dpi = GetDpiForWindow(target);
        area = {origin.x, origin.y + MulDiv(captionDip, dpi ? dpi : 96, 96),
                origin.x + client.right, origin.y + client.bottom};
    }
    return area.right > area.left && area.bottom > area.top;
}
static void SyncFeedButton() {
    RECT area{};
    ULONGLONG now = GetTickCount64(), observed = fullscreenTargetAt.load(), heartbeat = inputHeartbeat.load();
    if (!feedButton) return;
    if (!observed || now-observed > FULLSCREEN_TARGET_MS || !heartbeat || now-heartbeat > INPUT_LEASE_MS ||
        fullscreenMaskNormalized.right <= fullscreenMaskNormalized.left ||
        fullscreenMaskNormalized.bottom <= fullscreenMaskNormalized.top ||
        entering || !SameTarget() || IsIconic(target) || !IsWindowVisible(target) || !ContentRect(area)) {
        ShowWindow(feedButton, SW_HIDE); lastFeedWidget = {}; return;
    }
    const int width = area.right-area.left, height = area.bottom-area.top;
    auto mapped = [&](RECT rect) { return RECT{
        area.left+MulDiv(rect.left,width,65535), area.top+MulDiv(rect.top,height,65535),
        area.left+MulDiv(rect.right,width,65535), area.top+MulDiv(rect.bottom,height,65535)}; };
    RECT button = mapped(fullscreenNormalized), mask = mapped(fullscreenMaskNormalized);
    RECT widget{std::min(button.left,mask.left),std::min(button.top,mask.top),
                std::max(button.right,mask.right),std::max(button.bottom,mask.bottom)};
    RECT localButton{button.left-widget.left,button.top-widget.top,button.right-widget.left,button.bottom-widget.top};
    RECT localMask{mask.left-widget.left,mask.top-widget.top,mask.right-widget.left,mask.bottom-widget.top};
    bool changed = !EqualRect(&widget,&lastFeedWidget) || !EqualRect(&localButton,&feedButtonLocal) ||
                   !EqualRect(&localMask,&feedMaskLocal) || !IsWindowVisible(feedButton);
    if (changed) {
        POINT sample{mask.left-5,mask.top-5};
        HWND under = WindowFromPoint(sample);
        if (under == target || under == grip || IsChild(target,under)) {
            HDC screen = GetDC(nullptr);
            COLORREF color = GetPixel(screen,sample.x,sample.y);
            ReleaseDC(nullptr,screen);
            if (color != CLR_INVALID) feedBackground = color;
        }
        feedButtonLocal = localButton; feedMaskLocal = localMask;
        HRGN region = CreateRectRgn(localButton.left,localButton.top,localButton.right,localButton.bottom);
        HRGN old = CreateRectRgn(localMask.left,localMask.top,localMask.right,localMask.bottom);
        CombineRgn(region,region,old,RGN_OR); DeleteObject(old);
        if (!SetWindowRgn(feedButton,region,FALSE)) DeleteObject(region);
        lastFeedWidget = widget;
    }
    // Above the input grip (or WSA with a visible title), never globally topmost.
    HWND anchor = IsWindowVisible(grip) ? grip : target;
    if (changed || GetWindow(anchor,GW_HWNDPREV) != feedButton) {
        HWND above = GetWindow(anchor,GW_HWNDPREV);
        SetWindowPos(feedButton,above == feedButton ? nullptr : above,widget.left,widget.top,
                     widget.right-widget.left,widget.bottom-widget.top,
                     (above == feedButton ? SWP_NOZORDER : 0) | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW);
    }
    if (changed) InvalidateRect(feedButton,nullptr,FALSE);
}
static LRESULT CALLBACK FeedButtonProc(HWND window,UINT message,WPARAM wParam,LPARAM lParam) {
    if (message == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
    if (message == WM_ERASEBKGND) return 1;
    if (message == WM_PAINT) {
        PAINTSTRUCT paint{}; HDC dc = BeginPaint(window,&paint);
        RECT all{}; GetClientRect(window,&all);
        HBRUSH background = CreateSolidBrush(feedBackground); FillRect(dc,&all,background); DeleteObject(background);
        const int height = feedButtonLocal.bottom-feedButtonLocal.top;
        HBRUSH button = CreateSolidBrush(RGB(36,36,36));
        HGDIOBJ oldBrush = SelectObject(dc,button), oldPen = SelectObject(dc,GetStockObject(NULL_PEN));
        RoundRect(dc,feedButtonLocal.left,feedButtonLocal.top,feedButtonLocal.right,feedButtonLocal.bottom,
                  std::max(6,height/2),std::max(6,height/2));
        SelectObject(dc,oldBrush); SelectObject(dc,oldPen); DeleteObject(button);
        int fontHeight = std::max(6,MulDiv(height,18,38));
        HFONT font = CreateFontW(-fontHeight,0,0,0,FW_SEMIBOLD,FALSE,FALSE,FALSE,DEFAULT_CHARSET,
                                 OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Microsoft YaHei UI");
        HGDIOBJ oldFont = SelectObject(dc,font);
        SetBkMode(dc,TRANSPARENT); SetTextColor(dc,RGB(255,255,255));
        int iconWidth = MulDiv(height,16,38), gap = MulDiv(height,7,38);
        RECT text{}; DrawTextW(dc,L"\u5168\u5c4f\u89c2\u770b",4,&text,DT_CALCRECT|DT_SINGLELINE);
        int x = feedButtonLocal.left + (feedButtonLocal.right-feedButtonLocal.left-iconWidth-gap-text.right)/2;
        int y = feedButtonLocal.top+(height-MulDiv(height,17,38))/2;
        HPEN iconPen = CreatePen(PS_SOLID,std::max(1,height/25),RGB(255,255,255));
        oldPen = SelectObject(dc,iconPen); oldBrush = SelectObject(dc,GetStockObject(NULL_BRUSH));
        RoundRect(dc,x,y,x+MulDiv(height,9,38),y+MulDiv(height,17,38),3,3);
        MoveToEx(dc,x+MulDiv(height,12,38),y+MulDiv(height,5,38),nullptr);
        LineTo(dc,x+iconWidth,y+MulDiv(height,5,38)); LineTo(dc,x+iconWidth,y+MulDiv(height,14,38));
        LineTo(dc,x+MulDiv(height,12,38),y+MulDiv(height,14,38));
        SelectObject(dc,oldPen); SelectObject(dc,oldBrush); DeleteObject(iconPen);
        text = {x+iconWidth+gap,feedButtonLocal.top,feedButtonLocal.right,feedButtonLocal.bottom};
        DrawTextW(dc,L"\u5168\u5c4f\u89c2\u770b",4,&text,DT_LEFT|DT_VCENTER|DT_SINGLELINE);
        SelectObject(dc,oldFont); DeleteObject(font); EndPaint(window,&paint); return 0;
    }
    return DefWindowProcW(window,message,wParam,lParam);
}
static ULONGLONG PackPoint(LONG x, LONG y) {
    return (static_cast<ULONGLONG>(static_cast<DWORD>(x)) << 32) | static_cast<DWORD>(y);
}
static POINT UnpackPoint(ULONGLONG point) {
    return {static_cast<LONG>(point >> 32), static_cast<LONG>(point & 0xffffffff)};
}
static void UpdateFullscreenHitRect() {
    RECT area{}, hit{};
    if (fullscreenTargetAt.load() && ContentRect(area) && !entering && !IsIconic(target) && IsWindowVisible(target)) {
        int width = area.right-area.left, height = area.bottom-area.top;
        hit = {area.left + MulDiv(fullscreenNormalized.left, width, 65535),
               area.top + MulDiv(fullscreenNormalized.top, height, 65535),
               area.left + MulDiv(fullscreenNormalized.right, width, 65535),
               area.top + MulDiv(fullscreenNormalized.bottom, height, 65535)};
    }
    // The mouse-hook thread reads without waiting for the window/UI thread.
    ++fullscreenHitVersion;
    fullscreenTopLeft = PackPoint(hit.left, hit.top);
    fullscreenBottomRight = PackPoint(hit.right, hit.bottom);
    ++fullscreenHitVersion;
}
static bool FullscreenHit(POINT point) {
    ULONGLONG observed = fullscreenTargetAt.load();
    if (!observed || GetTickCount64()-observed > FULLSCREEN_TARGET_MS) return false;
    UINT_PTR version = fullscreenHitVersion.load();
    if (version & 1) return false;
    POINT first = UnpackPoint(fullscreenTopLeft.load()), last = UnpackPoint(fullscreenBottomRight.load());
    if (version != fullscreenHitVersion.load()) return false;
    RECT hit{first.x, first.y, last.x, last.y};
    return PtInRect(&hit, point) != FALSE;
}
static bool ClickModifiers() {
    return (GetAsyncKeyState(VK_CONTROL) & 0x8000) || (GetAsyncKeyState(VK_MENU) & 0x8000) ||
           (GetAsyncKeyState(VK_SHIFT) & 0x8000) || (GetAsyncKeyState(VK_LWIN) & 0x8000) ||
           (GetAsyncKeyState(VK_RWIN) & 0x8000);
}
static bool IsHongguoWindow(HWND window) {
    wchar_t title[128]{};
    GetWindowTextW(window, title, 128);
    if (wcscmp(title, L"\u7ea2\u679c\u514d\u8d39\u77ed\u5267") &&
        wcscmp(title, L"\u7ea2\u679c\u77ed\u5267")) return false;
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return false;
    wchar_t path[32768]{};
    DWORD length = 32768;
    bool ok = QueryFullProcessImageNameW(process, 0, path, &length) != FALSE;
    CloseHandle(process);
    const wchar_t* name = wcsrchr(path, L'\\');
    return ok && name && _wcsicmp(name + 1, L"WsaClient.exe") == 0;
}
static void ToggleFullscreen() {
    // Targeted messages, never keystrokes directed at the foreground app.
    PostMessageW(target, WM_KEYDOWN, VK_F11, 0x00570001);
    PostMessageW(target, WM_KEYUP, VK_F11, 0xC0570001);
}
static void Restore() {
    if (!ownsFullscreen) return;
    ownsFullscreen = entering = false;
    if (!SameTarget() || HasCaption()) return;
    RECT content{};
    GetWindowRect(target, &content);
    bool minimized = IsIconic(target) != FALSE;
    ToggleFullscreen();
    ULONGLONG deadline = GetTickCount64() + 1500;
    while (SameTarget() && !HasCaption() && GetTickCount64() < deadline) Sleep(10);
    if (!SameTarget() || !HasCaption() || minimized) return;
    // Keep the content where the user moved it; put the restored frame around it.
    RECT outer{}, client{};
    POINT origin{};
    GetWindowRect(target, &outer);
    GetClientRect(target, &client);
    ClientToScreen(target, &origin);
    UINT dpi = GetDpiForWindow(target);
    int top = origin.y-outer.top + MulDiv(captionDip, dpi ? dpi : 96, 96);
    int left = origin.x-outer.left;
    int right = outer.right-origin.x-client.right;
    int bottom = outer.bottom-origin.y-client.bottom;
    SetWindowPos(target, nullptr, content.left-left, content.top-top,
                 content.right-content.left+left+right, content.bottom-content.top+top+bottom,
                 SWP_NOZORDER | SWP_NOACTIVATE);
}
static void Enter() {
    RECT client{};
    POINT origin{};
    GetClientRect(target, &client);
    ClientToScreen(target, &origin);
    UINT dpi = GetDpiForWindow(target);
    requested = {origin.x, origin.y + MulDiv(captionDip, dpi ? dpi : 96, 96),
                 origin.x + client.right, origin.y + client.bottom};
    ownsFullscreen = entering = true;
    transitionStarted = GetTickCount64();
    ToggleFullscreen();
}
static void Sync() {
    if (syncing) return;
    syncing = true;
    if (!SameTarget() || WaitForSingleObject(parentProcess, 0) == WAIT_OBJECT_0 ||
        WaitForSingleObject(stopEvent, 0) == WAIT_OBJECT_0) {
        PostQuitMessage(0); syncing = false; return;
    }
    if (IsIconic(target) || !IsWindowVisible(target)) {
        CancelDrag();
        UpdateFullscreenHitRect();
        ShowWindow(grip, SW_HIDE); SyncFeedButton(); syncing = false; return;
    }
    if (!hideTitlebar) {
        CancelDrag();
        Restore();
        ShowWindow(grip, SW_HIDE);
        lastGrip = {};
        UpdateFullscreenHitRect();
        SyncFeedButton();
        syncing = false; return;
    }
    if (entering) {
        if (HasCaption()) {
            if (GetTickCount64()-transitionStarted > 2000) PostQuitMessage(7);
            syncing = false; return;
        }
        entering = false;
        SetWindowPos(target, nullptr, requested.left, requested.top,
                     requested.right-requested.left, requested.bottom-requested.top,
                     SWP_NOZORDER | SWP_NOACTIVATE);
    } else if (HasCaption()) {
        Enter(); syncing = false; return;
    }
    RECT area{};
    GetWindowRect(target, &area);
    UINT dpi = GetDpiForWindow(target);
    if (!dpi) dpi = 96;
    HWND aboveTarget = GetWindow(target, GW_HWNDPREV);
    if (!EqualRect(&area, &lastGrip) || !IsWindowVisible(grip) || aboveTarget != grip) {
        int width = area.right-area.left, height = area.bottom-area.top;
        int inset = std::min(MulDiv(72, dpi, 96), width/4);
        int border = MulDiv(6, dpi, 96), corner = MulDiv(12, dpi, 96);
        // Only a thin perimeter and the existing central move strip receive
        // input. The video and APP controls in the interior remain untouched.
        HRGN region = CreateRectRgn(0, 0, width, height);
        HRGN interior = CreateRectRgn(border, border, width-border, height-border);
        CombineRgn(region, region, interior, RGN_DIFF); DeleteObject(interior);
        HRGN move = CreateRectRgn(inset, 0, width-inset, MulDiv(18, dpi, 96));
        CombineRgn(region, region, move, RGN_OR); DeleteObject(move);
        for (int x : {0, width-corner}) for (int y : {0, height-corner}) {
            HRGN area = CreateRectRgn(x, y, x+corner, y+corner);
            CombineRgn(region, region, area, RGN_OR); DeleteObject(area);
        }
        if (!SetWindowRgn(grip, region, FALSE)) DeleteObject(region);
        // F11 reorders WSA above owned windows. Put the grip immediately above
        // WSA, never globally topmost over other applications.
        SetWindowPos(grip, aboveTarget == grip ? nullptr : aboveTarget,
                     area.left, area.top, width, height,
                     (aboveTarget == grip ? SWP_NOZORDER : 0) |
                     SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW);
        lastGrip = area;
    }
    UpdateFullscreenHitRect();
    SyncFeedButton();
    syncing = false;
}
static void CALLBACK LocationChanged(HWINEVENTHOOK, DWORD, HWND window, LONG object, LONG, DWORD, DWORD) {
    if (window == target && object == OBJID_WINDOW) Sync();
}
static bool WheelAt(POINT point, int delta) {
    ULONGLONG now = GetTickCount64();
    ULONGLONG heartbeat = inputHeartbeat.load();
    if (!heartbeat || now-heartbeat > INPUT_LEASE_MS || entering || !SameTarget() || IsIconic(target)) return false;
    HWND under = WindowFromPoint(point);
    if (under != grip && under != target && !IsChild(target, under)) return false;
    if (dragging || GetAsyncKeyState(VK_LBUTTON) & 0x8000) return true;
    if (GetAsyncKeyState(VK_CONTROL) & 0x8000 || GetAsyncKeyState(VK_MENU) & 0x8000) return false;
    RECT r{}; if (!ContentRect(r)) return false;
    if (!PtInRect(&r, point)) return false;
    if (now-lastWheelInput > 250 || (wheelRemainder < 0 && delta > 0) ||
        (wheelRemainder > 0 && delta < 0)) wheelRemainder = 0;
    lastWheelInput = now;
    if (now-lastWheelForward < 220) { wheelRemainder = 0; return true; }
    wheelRemainder += delta;
    if (wheelRemainder > -WHEEL_DELTA && wheelRemainder < WHEEL_DELTA) return true;
    UINT_PTR direction = wheelRemainder < 0 ? 1 : 2;
    wheelRemainder = 0;
    int x = std::clamp(MulDiv(point.x-r.left, 65535, r.right-r.left), 0, 65535);
    int y = std::clamp(MulDiv(point.y-r.top, 65535, r.bottom-r.top), 0, 65535);
    pendingWheel = (direction << 32) | (static_cast<UINT_PTR>(y) << 16) | x;
    lastWheelForward = now;
    SetPropW(grip, L"HongguoWheel", reinterpret_cast<HANDLE>(++wheelSequence));
    SetEvent(inputEvent);
    return true;
}
static LRESULT CALLBACK MouseInput(int code, WPARAM message, LPARAM data) {
    if (code == HC_ACTION && (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP)) {
        const auto* mouse = reinterpret_cast<const MSLLHOOKSTRUCT*>(data);
        if (message == WM_LBUTTONUP && fullscreenPressed) {
            fullscreenPressed = false;
            bool passed = fullscreenPressPassthrough;
            ULONGLONG heartbeat = inputHeartbeat.load();
            HWND under = WindowFromPoint(mouse->pt);
            if (heartbeat && GetTickCount64()-heartbeat < INPUT_LEASE_MS && !ClickModifiers() &&
                mouse->time-fullscreenPressAt < 800 &&
                abs(mouse->pt.x-fullscreenPressPoint.x) <= 8 && abs(mouse->pt.y-fullscreenPressPoint.y) <= 8 &&
                ((under == grip || under == target || IsChild(target, under)) && FullscreenHit(mouse->pt) ||
                 !passed && SameTarget() && GetAncestor(GetForegroundWindow(),GA_ROOT) == target))
                PostMessageW(grip, QUEUE_FULLSCREEN, mouse->time, passed ? 2 : 1);
            // Only this pair was intercepted. A canceled button click must not
            // inject a lone release or replay input onto a different page.
            if (!passed) return 1;
        }
        ULONGLONG heartbeat = inputHeartbeat.load();
        if (message == WM_LBUTTONDOWN && heartbeat && GetTickCount64()-heartbeat < INPUT_LEASE_MS &&
            !ClickModifiers() && FullscreenHit(mouse->pt)) {
            HWND under = WindowFromPoint(mouse->pt);
            if (under == target || under == feedButton || IsChild(target, under)) {
                fullscreenPressed = true;
                ++fullscreenPresses;
                fullscreenPressPassthrough = fullscreenPassthrough.load();
                fullscreenPressPoint = mouse->pt;
                fullscreenPressAt = mouse->time;
                // Initialized players handle their real click immediately.
                // Python only observes; it cannot reject or replay this pair.
                if (!fullscreenPressPassthrough) {
                    // Swallowing a cold click also suppresses Windows' normal
                    // activation. Do that small UI operation on the window
                    // thread, with the fresh physical gesture still checked.
                    PostMessageW(grip, FOCUS_FULLSCREEN_CLICK, mouse->time,
                                 MAKELPARAM(mouse->pt.x, mouse->pt.y));
                    return 1;
                }
            }
        }
    }
    if (code == HC_ACTION && message == WM_LBUTTONUP) {
        const auto* mouse = reinterpret_cast<const MSLLHOOKSTRUCT*>(data);
        ULONGLONG heartbeat = inputHeartbeat.load();
        if (heartbeat && GetTickCount64()-heartbeat < INPUT_LEASE_MS) {
            HWND under = WindowFromPoint(mouse->pt);
            if (under == target || IsChild(target,under))
                // Read-only notification: the original click passes to WSA.
                // The hook never waits for Python, Android, or a UI snapshot.
                PostMessageW(grip,QUEUE_PAGE_CHANGE,mouse->time,0);
        }
    }
    if (code == HC_ACTION && message == WM_MOUSEWHEEL) {
        ++hookWheelCalls;
        const auto* mouse = reinterpret_cast<const MSLLHOOKSTRUCT*>(data);
        ULONGLONG heartbeat = inputHeartbeat.load();
        if (heartbeat && GetTickCount64()-heartbeat < INPUT_LEASE_MS &&
            !(GetAsyncKeyState(VK_CONTROL)&0x8000) && !(GetAsyncKeyState(VK_MENU)&0x8000)) {
            HWND under = WindowFromPoint(mouse->pt);
            if (under == grip || under == target || IsChild(target, under)) {
                // Never run DWM, window activation, or Python/ADB work on this
                // hook thread. Blocking here stalls the desktop mouse itself.
                WPARAM packet = (static_cast<UINT_PTR>(mouse->time) << 16) | HIWORD(mouse->mouseData);
                if (PostMessageW(grip, QUEUE_WHEEL, packet, MAKELPARAM(mouse->pt.x, mouse->pt.y))) return 1;
            }
        }
    }
    return CallNextHookEx(nullptr, code, message, data);
}
static DWORD WINAPI InputThread(void* readyEvent) {
    HHOOK hook = SetWindowsHookExW(WH_MOUSE_LL, MouseInput, GetModuleHandleW(nullptr), 0);
    hookError = hook ? 0 : GetLastError();
    if (hook) ++hookGeneration;
    SetEvent(static_cast<HANDLE>(readyEvent));
    if (!hook) return 8;
    // Windows silently removes a low-level hook when its thread is starved.
    // Re-register on this dedicated message-pump thread; no UI/ADB work runs
    // here, and mouse input continues on the previous hook if registration fails.
    UINT_PTR refreshTimer = SetTimer(nullptr, 0, 5000, nullptr);
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (msg.message == WM_TIMER && msg.wParam == refreshTimer) {
            HHOOK replacement = SetWindowsHookExW(WH_MOUSE_LL, MouseInput, GetModuleHandleW(nullptr), 0);
            if (replacement) {
                UnhookWindowsHookEx(hook);
                hook = replacement;
                hookError = 0;
                ++hookGeneration;
            } else hookError = GetLastError();
        } else { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    if (refreshTimer) KillTimer(nullptr, refreshTimer);
    UnhookWindowsHookEx(hook);
    return 0;
}
static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case FOCUS_FULLSCREEN_CLICK: {
        POINT point{static_cast<short>(LOWORD(lParam)),static_cast<short>(HIWORD(lParam))}, cursor{};
        HWND under = WindowFromPoint(point);
        if (GetTickCount()-static_cast<DWORD>(wParam) < 200 && SameTarget() && !IsIconic(target) &&
            !ClickModifiers() && FullscreenHit(point) && GetCursorPos(&cursor) &&
            abs(cursor.x-point.x) <= 8 && abs(cursor.y-point.y) <= 8 &&
            (under == target || under == feedButton || IsChild(target,under))) {
            DWORD currentThread = GetCurrentThreadId();
            DWORD foregroundThread = GetWindowThreadProcessId(GetForegroundWindow(),nullptr);
            bool attached = foregroundThread && foregroundThread != currentThread &&
                            AttachThreadInput(currentThread,foregroundThread,TRUE);
            if (SetForegroundWindow(target)) ++fullscreenFocusOk;
            if (attached) AttachThreadInput(currentThread,foregroundThread,FALSE);
        }
        return 0;
    }
    case QUEUE_WHEEL: {
        if (GetTickCount()-static_cast<DWORD>(wParam >> 16) > 600) return 0;
        POINT point{static_cast<short>(LOWORD(lParam)), static_cast<short>(HIWORD(lParam))};
        WheelAt(point, static_cast<short>(LOWORD(wParam)));
        return 0;
    }
    // Retired crop protocol: reject even requests from an older Python helper.
    // The companion must never crop or replace the Android content surface.
    case WM_APP + 20: return 0;
    case TAKE_WHEEL: {
        UINT_PTR packet = GetTickCount64()-lastWheelForward < 600 ? pendingWheel : 0;
        pendingWheel = 0;
        if (packet && ((pendingFullscreenAt && GetTickCount()-pendingFullscreenAt < 600) ||
                       (pendingPageChangeAt && GetTickCount()-pendingPageChangeAt < 1000))) SetEvent(inputEvent);
        return static_cast<LRESULT>(packet);
    }
    case INPUT_READY:
        inputHeartbeat = wParam ? GetTickCount64() : 0;
        if (!wParam) { pendingWheel = wheelRemainder = 0; pendingFullscreenAt = pendingPageChangeAt = 0; }
        return 1;
    case SET_FULLSCREEN_TARGET:
        fullscreenPassthrough = (static_cast<UINT_PTR>(wParam) & (UINT_PTR{1} << 32)) != 0;
        fullscreenNormalized = {static_cast<LONG>(LOWORD(wParam)), static_cast<LONG>(HIWORD(wParam)),
                                static_cast<LONG>(LOWORD(lParam)), static_cast<LONG>(HIWORD(lParam))};
        fullscreenTargetAt = fullscreenNormalized.right > fullscreenNormalized.left &&
                             fullscreenNormalized.bottom > fullscreenNormalized.top ? GetTickCount64() : 0;
        if (!fullscreenTargetAt.load()) pendingFullscreenAt = 0;
        UpdateFullscreenHitRect();
        SyncFeedButton();
        return 1;
    case SET_FULLSCREEN_PRESENTATION:
        fullscreenMaskNormalized = {static_cast<LONG>(LOWORD(wParam)),static_cast<LONG>(HIWORD(wParam)),
                                    static_cast<LONG>(LOWORD(lParam)),static_cast<LONG>(HIWORD(lParam))};
        SyncFeedButton(); return 1;
    case QUEUE_FULLSCREEN:
        if (GetTickCount()-static_cast<DWORD>(wParam) > 600 || !SameTarget()) return 0;
        pendingFullscreenAt = static_cast<DWORD>(wParam);
        ++fullscreenQueued;
        pendingFullscreenKind = lParam == 2 ? 2 : 1;
        SetEvent(inputEvent);
        return 0;
    case QUEUE_PAGE_CHANGE:
        if (GetTickCount()-static_cast<DWORD>(wParam)>1000 || !SameTarget()) return 0;
        pendingPageChangeAt = static_cast<DWORD>(wParam);
        SetEvent(inputEvent); return 0;
    case TAKE_PAGE_CHANGE: {
        bool ready = pendingPageChangeAt && GetTickCount()-pendingPageChangeAt < 1000;
        pendingPageChangeAt = 0;
        if (ready && ((pendingFullscreenAt && GetTickCount()-pendingFullscreenAt < 600) ||
                      (pendingWheel && GetTickCount64()-lastWheelForward < 600))) SetEvent(inputEvent);
        return ready ? 1 : 0;
    }
    case TAKE_FULLSCREEN: {
        bool ready = pendingFullscreenAt && GetTickCount()-pendingFullscreenAt < 600;
        pendingFullscreenAt = 0;
        // Auto-reset events coalesce signals. Preserve a second fresh input
        // kind when this read consumed the shared wake-up notification.
        if (ready && ((pendingWheel && GetTickCount64()-lastWheelForward < 600) ||
                      (pendingPageChangeAt && GetTickCount()-pendingPageChangeAt < 1000))) SetEvent(inputEvent);
        return ready ? pendingFullscreenKind : 0;
    }
    case SET_TITLEBAR:
        hideTitlebar = wParam != 0;
        Sync();
        return 1;
    case INPUT_STATUS:
        switch (wParam) {
        case 0: return (inputHeartbeat.load() ? 1 : 0) | (hideTitlebar ? 2 : 0) |
                       (ownsFullscreen ? 4 : 0) | (HasCaption() ? 8 : 0);
        case 1: return inputHeartbeat.load() ? GetTickCount64()-inputHeartbeat.load() : -1;
        case 2: return hookGeneration.load();
        case 3: return hookWheelCalls.load();
        case 4: return wheelSequence;
        case 5: return hookError.load();
        case 6: return fullscreenPresses.load();
        case 7: return fullscreenQueued.load();
        case 8: return fullscreenFocusOk.load();
        }
        return 0;
    case WM_LBUTTONDOWN: {
        if (!SameTarget() || entering || HasCaption() || IsZoomed(target)) return 0;
        pendingWheel = wheelRemainder = 0;
        GetCursorPos(&dragPointer);
        GetWindowRect(target, &dragWindow);
        resizeEdge = EdgeAt(dragPointer);
        MONITORINFO monitor{sizeof(MONITORINFO)};
        if (!GetMonitorInfoW(MonitorFromWindow(target, MONITOR_DEFAULTTONEAREST), &monitor)) return 0;
        dragWork = monitor.rcWork;
        SetForegroundWindow(target);
        SetCapture(window);
        dragging = true;
        return 0;
    }
    case WM_MOUSEWHEEL: {
        POINT point{static_cast<short>(LOWORD(lParam)), static_cast<short>(HIWORD(lParam))};
        if (!WheelAt(point, static_cast<short>(HIWORD(wParam))) && SameTarget())
            PostMessageW(target, message, wParam, lParam);
        return 0;
    }
    case WM_MOUSEMOVE:
        if (dragging && SameTarget()) {
            POINT point{};
            GetCursorPos(&point);
            if (resizeEdge) {
                UINT dpi = GetDpiForWindow(target); if (!dpi) dpi = 96;
                auto r = hongguo::ResizeRect({dragWindow.left, dragWindow.top, dragWindow.right, dragWindow.bottom},
                    {dragWork.left, dragWork.top, dragWork.right, dragWork.bottom}, resizeEdge,
                    point.x-dragPointer.x, point.y-dragPointer.y, MulDiv(180, dpi, 96));
                SetWindowPos(target, nullptr, r.left, r.top, r.right-r.left, r.bottom-r.top,
                             SWP_NOZORDER | SWP_NOACTIVATE);
            } else {
                SetWindowPos(target, nullptr, dragWindow.left + point.x-dragPointer.x,
                             dragWindow.top + point.y-dragPointer.y, 0, 0,
                             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
            }
            Sync();
        }
        return 0;
    case WM_LBUTTONUP:
        if (dragging) RememberSize();
        if (dragging) ReleaseCapture();
        dragging = false;
        resizeEdge = hongguo::None;
        return 0;
    case WM_CAPTURECHANGED:
        if (dragging) RememberSize();
        dragging = false;
        resizeEdge = hongguo::None;
        return 0;
    case WM_MOUSEACTIVATE: return MA_NOACTIVATE;
    case WM_SETCURSOR: {
        POINT point{}; GetCursorPos(&point);
        int edge = dragging ? resizeEdge : EdgeAt(point);
        LPCWSTR cursor = IDC_SIZEALL;
        if (edge == hongguo::Left || edge == hongguo::Right) cursor = IDC_SIZEWE;
        else if (edge == hongguo::Top || edge == hongguo::Bottom) cursor = IDC_SIZENS;
        else if (edge == (hongguo::Left | hongguo::Top) || edge == (hongguo::Right | hongguo::Bottom)) cursor = IDC_SIZENWSE;
        else if (edge) cursor = IDC_SIZENESW;
        SetCursor(LoadCursorW(nullptr, cursor)); return TRUE;
    }
    case WM_TIMER: Sync(); return 0;
    case WM_CLOSE:
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(window, message, wParam, lParam);
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    int count = 0;
    LPWSTR* args = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!args || (count != 6 && count != 7)) { if (args) LocalFree(args); return 2; }
    DWORD parentPid = wcstoul(args[1], nullptr, 10);
    target = reinterpret_cast<HWND>(_wcstoui64(args[2], nullptr, 10));
    captionDip = _wtoi(args[3]);
    hideTitlebar = count == 6 || _wtoi(args[6]) != 0;
    if (!parentPid || captionDip < 16 || captionDip > 64 || !IsHongguoWindow(target)) {
        LocalFree(args); return 3;
    }
    parentProcess = OpenProcess(SYNCHRONIZE, FALSE, parentPid);
    stopEvent = OpenEventW(SYNCHRONIZE, FALSE, args[4]);
    inputEvent = OpenEventW(EVENT_MODIFY_STATE, FALSE, args[5]);
    LocalFree(args);
    if (!parentProcess || !stopEvent || !inputEvent) return 4;
    GetWindowThreadProcessId(target, &targetPid);
    wchar_t mutexName[128]{};
    swprintf_s(mutexName, L"Local\\HongguoChrome_%lu_%llu", targetPid, reinterpret_cast<UINT_PTR>(target));
    HANDLE mutex = CreateMutexW(nullptr, TRUE, mutexName);
    if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS) return 5;
    WNDCLASSW klass{};
    klass.lpfnWndProc = WindowProc;
    klass.hInstance = instance;
    klass.hCursor = LoadCursorW(nullptr, IDC_SIZEALL);
    klass.hbrBackground = reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    klass.lpszClassName = CLASS_NAME;
    if (!RegisterClassW(&klass)) return 6;
    grip = CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                          CLASS_NAME, L"", WS_POPUP, 0, 0, 1, 1, target, nullptr, instance, nullptr);
    if (!grip) return 6;
    SetLayeredWindowAttributes(grip, 0, 1, LWA_ALPHA);
    WNDCLASSW buttonClass{};
    buttonClass.lpfnWndProc = FeedButtonProc; buttonClass.hInstance = instance;
    buttonClass.hCursor = LoadCursorW(nullptr,IDC_HAND); buttonClass.lpszClassName = FEED_BUTTON_CLASS;
    if (!RegisterClassW(&buttonClass)) return 6;
    feedButton = CreateWindowExW(WS_EX_LAYERED|WS_EX_TRANSPARENT|WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE,
        FEED_BUTTON_CLASS,L"",WS_POPUP,0,0,1,1,target,nullptr,instance,nullptr);
    if (!feedButton) return 6;
    SetLayeredWindowAttributes(feedButton,0,255,LWA_ALPHA);
    HWINEVENTHOOK hook = SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE,
                                        nullptr, LocationChanged, targetPid, 0,
                                        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS);
    SetTimer(grip, 1, 100, nullptr);
    HANDLE inputReady = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!inputReady) return 8;
    DWORD inputThreadId = 0;
    HANDLE inputThread = CreateThread(nullptr, 0, InputThread, inputReady, 0, &inputThreadId);
    if (!inputThread) { CloseHandle(inputReady); return 8; }
    WaitForSingleObject(inputReady, 2000); CloseHandle(inputReady);
    if (WaitForSingleObject(inputThread, 0) == WAIT_OBJECT_0) { CloseHandle(inputThread); return 8; }
    Sync();
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message); DispatchMessageW(&message);
    }
    inputHeartbeat = 0;
    PostThreadMessageW(inputThreadId, WM_QUIT, 0, 0);
    WaitForSingleObject(inputThread, 2000); CloseHandle(inputThread);
    if (hook) UnhookWinEvent(hook);
    KillTimer(grip, 1); ShowWindow(grip, SW_HIDE);
    ShowWindow(feedButton,SW_HIDE); DestroyWindow(feedButton);
    Restore(); DestroyWindow(grip);
    CloseHandle(stopEvent); CloseHandle(parentProcess); CloseHandle(inputEvent);
    ReleaseMutex(mutex); CloseHandle(mutex);
    return static_cast<int>(message.wParam);
}
