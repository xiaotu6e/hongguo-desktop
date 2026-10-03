#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <wbemidl.h>
#include <tlhelp32.h>
#include <wrl/client.h>
#include <algorithm>
#include <string>
#include <vector>
#include <cwchar>

using Microsoft::WRL::ComPtr;
static std::wstring dataFolder, helperPath;
static unsigned restarts = 0;
static constexpr DWORD HEARTBEAT_TIMEOUT = 45000;
static constexpr wchar_t MUTEX_NAME[] = L"Local\\HongguoDesktopHelperGuardian";

static std::string Utf8(const std::wstring& value) {
    int n = WideCharToMultiByte(CP_UTF8, 0, value.data(), (int)value.size(), nullptr, 0, nullptr, nullptr);
    std::string result(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), (int)value.size(), result.data(), n, nullptr, nullptr);
    return result;
}
static std::string Json(const std::wstring& value) {
    std::string result = "\"";
    for (char c : Utf8(value)) {
        if (c == '\\' || c == '"') result += '\\';
        if ((unsigned char)c >= 32) result += c;
    }
    return result + "\"";
}
static std::string Timestamp() {
    SYSTEMTIME t{}; GetLocalTime(&t);
    char value[40];
    sprintf_s(value, "%04u-%02u-%02uT%02u:%02u:%02u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    return value;
}
static bool Write(const std::wstring& file, const std::string& data, bool append = false) {
    HANDLE h = CreateFileW(file.c_str(), append ? FILE_APPEND_DATA : GENERIC_WRITE,
                          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                          append ? OPEN_ALWAYS : CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    bool ok = WriteFile(h, data.data(), (DWORD)data.size(), &written, nullptr) && written == data.size();
    CloseHandle(h); return ok;
}
static void Log(const char* event, DWORD pid = 0, DWORD code = 0) {
    Write(dataFolder + L"\\guardian.log", "{\"at\":\"" + Timestamp() + "\",\"event\":\"" + event +
          "\",\"guardian_pid\":" + std::to_string(GetCurrentProcessId()) +
          ",\"helper_pid\":" + std::to_string(pid) + ",\"code\":" + std::to_string(code) + "}\n", true);
}
static void State(const char* state, DWORD pid, ULONGLONG age = 0, HANDLE process = nullptr) {
    BOOL inJob = FALSE, helperInJob = FALSE;
    IsProcessInJob(GetCurrentProcess(), nullptr, &inJob);
    if (process) IsProcessInJob(process, nullptr, &helperInJob);
    std::string data = "{\"updated_at\":\"" + Timestamp() + "\",\"state\":\"" + state +
        "\",\"guardian_pid\":" + std::to_string(GetCurrentProcessId()) +
        ",\"helper_pid\":" + std::to_string(pid) + ",\"restarts\":" + std::to_string(restarts) +
        ",\"heartbeat_age_ms\":" + std::to_string(age) + ",\"in_job\":" + (inJob ? "true" : "false") +
        ",\"helper_in_job\":" + (helperInJob ? "true" : "false") +
        ",\"helper_path\":" + Json(helperPath) + "}\n";
    std::wstring file = dataFolder + L"\\guardian-state.json", temp = file + L".tmp";
    if (Write(temp, data) && !MoveFileExW(temp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        Write(file, data); // This machine's encrypted folder can reject a same-folder rename.
}
static std::wstring ProcessPath(HANDLE process) {
    wchar_t path[32768]; DWORD length = _countof(path);
    return QueryFullProcessImageNameW(process, 0, path, &length) ? std::wstring(path, length) : L"";
}
static bool IsHelper(HANDLE process) {
    return _wcsicmp(ProcessPath(process).c_str(), helperPath.c_str()) == 0;
}
static HANDLE FindHelper(DWORD& pid) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return nullptr;
    PROCESSENTRY32W entry{}; entry.dwSize = sizeof(entry);
    HANDLE found = nullptr;
    if (Process32FirstW(snapshot, &entry)) do {
        if (_wcsicmp(entry.szExeFile, L"hongguo_desktop.exe")) continue;
        HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, FALSE, entry.th32ProcessID);
        if (h && IsHelper(h) && WaitForSingleObject(h, 0) == WAIT_TIMEOUT) { found = h; pid = entry.th32ProcessID; break; }
        if (h) CloseHandle(h);
    } while (Process32NextW(snapshot, &entry));
    CloseHandle(snapshot); return found;
}
static BOOL CALLBACK RestoreWindow(HWND hwnd, LPARAM parameter) {
    DWORD pid = 0; GetWindowThreadProcessId(hwnd, &pid);
    wchar_t title[128]; GetWindowTextW(hwnd, title, _countof(title));
    if (pid == (DWORD)parameter && wcscmp(title, L"红果桌面助手") == 0) {
        ShowWindowAsync(hwnd, SW_RESTORE); SetForegroundWindow(hwnd); return FALSE;
    }
    return TRUE;
}
static BOOL CALLBACK ClosePlayer(HWND hwnd, LPARAM) {
    wchar_t cls[256]; GetClassNameW(hwnd, cls, _countof(cls));
    if (wcscmp(cls, L"com.phoenix.read")) return TRUE;
    DWORD pid = 0; GetWindowThreadProcessId(hwnd, &pid);
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return TRUE;
    std::wstring path = ProcessPath(h); CloseHandle(h);
    size_t slash = path.find_last_of(L"\\/");
    if (_wcsicmp(path.substr(slash == std::wstring::npos ? 0 : slash + 1).c_str(), L"WsaClient.exe") == 0) {
        PostMessageW(hwnd, WM_CLOSE, 0, 0); Log("player_close_requested", pid);
    }
    return TRUE;
}
// Win32_Process.Create starts outside the caller's Job Object. Neither a
// detached CreateProcess nor ShellExecute guarantees that separation.
static HRESULT LaunchIndependent(const std::wstring& executable, const std::wstring& args) {
    HRESULT init = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    CoInitializeSecurity(nullptr, -1, nullptr, nullptr, RPC_C_AUTHN_LEVEL_DEFAULT,
                         RPC_C_IMP_LEVEL_IMPERSONATE, nullptr, EOAC_NONE, nullptr);
    ComPtr<IWbemLocator> locator; ComPtr<IWbemServices> services;
    ComPtr<IWbemClassObject> cls, signature, input, output;
    HRESULT hr = CoCreateInstance(CLSID_WbemLocator, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&locator));
    BSTR ns = SysAllocString(L"ROOT\\CIMV2"), className = SysAllocString(L"Win32_Process"), method = SysAllocString(L"Create");
    if (SUCCEEDED(hr)) hr = locator->ConnectServer(ns, nullptr, nullptr, nullptr, 0, nullptr, nullptr, &services);
    if (SUCCEEDED(hr)) hr = CoSetProxyBlanket(services.Get(), RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, nullptr,
                                             RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE, nullptr, EOAC_NONE);
    if (SUCCEEDED(hr)) hr = services->GetObject(className, 0, nullptr, &cls, nullptr);
    if (SUCCEEDED(hr)) hr = cls->GetMethod(method, 0, &signature, nullptr);
    if (SUCCEEDED(hr)) hr = signature->SpawnInstance(0, &input);
    VARIANT value{}; value.vt = VT_BSTR;
    value.bstrVal = SysAllocString((L"\"" + executable + L"\" " + args).c_str());
    if (SUCCEEDED(hr)) hr = input->Put(L"CommandLine", 0, &value, 0);
    VariantClear(&value); value.vt = VT_BSTR;
    value.bstrVal = SysAllocString(executable.substr(0, executable.find_last_of(L"\\/")).c_str());
    if (SUCCEEDED(hr)) hr = input->Put(L"CurrentDirectory", 0, &value, 0);
    VariantClear(&value);
    if (SUCCEEDED(hr)) hr = services->ExecMethod(className, method, 0, nullptr, input.Get(), &output, nullptr);
    if (SUCCEEDED(hr)) {
        hr = output->Get(L"ReturnValue", 0, &value, nullptr, nullptr);
        if (SUCCEEDED(hr) && value.ulVal != 0) hr = HRESULT_FROM_WIN32(value.ulVal);
        VariantClear(&value);
    }
    SysFreeString(ns); SysFreeString(className); SysFreeString(method);
    output.Reset(); input.Reset(); signature.Reset(); cls.Reset(); services.Reset(); locator.Reset();
    if (SUCCEEDED(init)) CoUninitialize();
    return hr;
}
static HANDLE LaunchHelper(DWORD& pid, bool recovery) {
    SetEnvironmentVariableW(L"HONGGUO_GUARDIAN_RECOVERY", recovery ? L"1" : L"0");
    SetEnvironmentVariableW(L"HONGGUO_GUARDIAN_PID", std::to_wstring(GetCurrentProcessId()).c_str());
    std::wstring command = L"\"" + helperPath + L"\"";
    std::wstring folder = helperPath.substr(0, helperPath.find_last_of(L"\\/"));
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = recovery ? SW_SHOWMINNOACTIVE : SW_SHOWNORMAL;
    PROCESS_INFORMATION info{};
    if (!CreateProcessW(helperPath.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_NEW_PROCESS_GROUP,
                        nullptr, folder.c_str(), &startup, &info)) { Log("launch_failed", 0, GetLastError()); return nullptr; }
    CloseHandle(info.hThread); pid = info.dwProcessId;
    Log(recovery ? "helper_recovered" : "helper_started", pid); return info.hProcess;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    wchar_t local[32768], self[32768];
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, _countof(local))) return 80;
    dataFolder = std::wstring(local) + L"\\HongguoDesktopHelper"; CreateDirectoryW(dataFolder.c_str(), nullptr);
    GetModuleFileNameW(nullptr, self, _countof(self));
    helperPath = std::wstring(self).substr(0, std::wstring(self).find_last_of(L"\\/")) + L"\\hongguo_desktop.exe";
    DWORD attach = 0; bool independent = false;
    int count = 0; wchar_t** args = CommandLineToArgvW(GetCommandLineW(), &count);
    std::wstring forwarded = L"--independent";
    for (int i = 1; args && i < count; ++i) {
        if (wcscmp(args[i], L"--independent") == 0) independent = true;
        else if (wcscmp(args[i], L"--attach") == 0 && i + 1 < count) attach = wcstoul(args[++i], nullptr, 10);
        else if (wcscmp(args[i], L"--helper") == 0 && i + 1 < count) helperPath = args[++i];
    }
    if (attach) forwarded += L" --attach " + std::to_wstring(attach);
    forwarded += L" --helper \"" + helperPath + L"\"";
    if (args) LocalFree(args);
    BOOL inJob = FALSE; IsProcessInJob(GetCurrentProcess(), nullptr, &inJob);
    if (inJob) {
        if (!independent) {
            HRESULT hr = LaunchIndependent(self, forwarded);
            Log(SUCCEEDED(hr) ? "detached_via_wmi" : "detach_failed", attach, (DWORD)hr);
            if (SUCCEEDED(hr)) return 0;
        }
        // Never claim to supervise while sharing the temporary caller's job.
        Log("independent_launch_required", attach); return 81;
    }
    HANDLE mutex = CreateMutexW(nullptr, FALSE, MUTEX_NAME);
    if (!mutex) { Log("mutex_failed", attach, GetLastError()); return 82; }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        DWORD pid = 0; HANDLE h = FindHelper(pid);
        if (h) { if (!attach) EnumWindows(RestoreWindow, pid); CloseHandle(h); }
        CloseHandle(mutex); return 0;
    }
    Log("guardian_started", attach);
    DWORD pid = attach;
    HANDLE process = attach ? OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, FALSE, attach) : nullptr;
    if (process && !IsHelper(process)) { CloseHandle(process); process = nullptr; }
    if (!process) process = FindHelper(pid);
    if (!process) process = LaunchHelper(pid, attach != 0);
    bool failed = process == nullptr;
    std::vector<ULONGLONG> failures;
    while (process) {
        HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, (L"Local\\HongguoDesktopHelperStop_" + std::to_wstring(pid)).c_str());
        HANDLE beat = CreateEventW(nullptr, FALSE, FALSE, (L"Local\\HongguoDesktopHelperBeat_" + std::to_wstring(pid)).c_str());
        if (!stop || !beat) { Log("events_failed", pid, GetLastError()); if (stop) CloseHandle(stop); if (beat) CloseHandle(beat); failed = true; break; }
        ULONGLONG heartbeat = GetTickCount64(), previous = heartbeat, lastState = 0;
        bool requested = false;
        for (;;) {
            HANDLE handles[] = {process, stop, beat};
            DWORD result = WaitForMultipleObjects(3, handles, FALSE, 1000);
            ULONGLONG now = GetTickCount64();
            if (now - previous > 5000) heartbeat = now; // Resume from sleep is not a hung helper.
            previous = now;
            if (result == WAIT_OBJECT_0) break;
            if (result == WAIT_OBJECT_0 + 1) {
                requested = true; Log("explicit_exit", pid); State("stopping", pid, 0, process);
                if (WaitForSingleObject(process, 15000) == WAIT_TIMEOUT) {
                    EnumWindows(ClosePlayer, 0);
                    Log("exit_cleanup_timeout", pid);
                    TerminateProcess(process, 0); WaitForSingleObject(process, 5000);
                }
                break;
            }
            if (result == WAIT_OBJECT_0 + 2) heartbeat = now;
            if (result == WAIT_FAILED) { Log("wait_failed", pid, GetLastError()); break; }
            if (now - lastState >= 3000) { State("watching", pid, now - heartbeat, process); lastState = now; }
            if (now - heartbeat > HEARTBEAT_TIMEOUT) {
                // Catch a stop requested just as the timeout expires.
                if (WaitForSingleObject(stop, 0) == WAIT_OBJECT_0) continue;
                Log("heartbeat_timeout", pid);
                TerminateProcess(process, 0xE0000001); WaitForSingleObject(process, 5000); break;
            }
        }
        requested = requested || WaitForSingleObject(stop, 0) == WAIT_OBJECT_0;
        DWORD code = 0; GetExitCodeProcess(process, &code);
        CloseHandle(stop); CloseHandle(beat); CloseHandle(process); process = nullptr;
        if (requested) { State("stopped", pid); Log("guardian_stopped_explicitly", pid, code); break; }
        Log("unexpected_exit", pid, code);
        CopyFileW((dataFolder + L"\\runtime-state.json").c_str(), (dataFolder + L"\\last-unexpected-exit.json").c_str(), FALSE);
        ULONGLONG now = GetTickCount64();
        failures.erase(std::remove_if(failures.begin(), failures.end(), [now](ULONGLONG t) { return now - t > 60000; }), failures.end());
        failures.push_back(now);
        if (failures.size() >= 3) {
            EnumWindows(ClosePlayer, 0); State("failed", pid); Log("recovery_failed_player_closed", pid);
            CloseHandle(mutex); mutex = nullptr; // Acknowledging an error must not block the next launch.
            MessageBoxW(nullptr, L"后台辅助连续启动失败，已请求关闭红果播放窗口。\n请重新打开桌面助手，或查看操作记录中的日志。",
                        L"红果桌面助手", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
            break;
        }
        ++restarts; State("recovering", pid); Sleep(1000);
        process = LaunchHelper(pid, true);
        if (!process) failed = true;
    }
    if (process) CloseHandle(process);
    if (failed) {
        EnumWindows(ClosePlayer, 0); State("failed", 0); Log("helper_start_failed");
        CloseHandle(mutex); mutex = nullptr;
        MessageBoxW(nullptr, L"后台管理程序未能启动，已请求关闭红果播放窗口。\n请保留完整的助手文件夹，并查看 guardian.log。", L"红果桌面助手", MB_OK | MB_ICONERROR);
    }
    if (mutex) CloseHandle(mutex); return 0;
}
