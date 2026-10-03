#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string>

static LRESULT CALLBACK Player(HWND hwnd, UINT message, WPARAM w, LPARAM l) {
    if (message == WM_CLOSE) { DestroyWindow(hwnd); return 0; }
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(hwnd, message, w, l);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    wchar_t exe[32768]; GetModuleFileNameW(nullptr, exe, _countof(exe));
    std::wstring path(exe), folder = path.substr(0, path.find_last_of(L"\\/"));
    std::wstring name = path.substr(path.find_last_of(L"\\/") + 1);
    if (_wcsicmp(name.c_str(), L"WsaClient.exe") == 0) {
        WNDCLASSW cls{}; cls.lpfnWndProc = Player; cls.hInstance = instance; cls.lpszClassName = L"com.phoenix.read";
        RegisterClassW(&cls);
        CreateWindowW(cls.lpszClassName, L"Lifecycle verification fixture", WS_OVERLAPPEDWINDOW,
                      0, 0, 100, 100, nullptr, nullptr, instance, nullptr);
        MSG msg{}; while (GetMessageW(&msg, nullptr, 0, 0) > 0) DispatchMessageW(&msg);
        return 0;
    }
    if (GetFileAttributesW((folder + L"\\repeat-crash").c_str()) != INVALID_FILE_ATTRIBUTES) return 88;
    HANDLE marker = CreateFileW((folder + L"\\started-once").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    bool first = marker != INVALID_HANDLE_VALUE;
    if (first) CloseHandle(marker);
    std::wstring pid = std::to_wstring(GetCurrentProcessId());
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, (L"Local\\HongguoDesktopHelperStop_" + pid).c_str());
    HANDLE beat = CreateEventW(nullptr, FALSE, FALSE, (L"Local\\HongguoDesktopHelperBeat_" + pid).c_str());
    SetEvent(beat);
    while (WaitForSingleObject(stop, 200) == WAIT_TIMEOUT) if (!first) SetEvent(beat);
    CloseHandle(stop); CloseHandle(beat); return 0;
}
