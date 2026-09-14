#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include "MinHook.h"

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")

// Shared IPC memory struct
struct SharedData {
    double target_frametime_ms; // e.g. 16.6666 for 60fps
    double current_fps;
    double current_frametime_ms;
    int is_active;
};

SharedData* g_shared_data = nullptr;
HANDLE g_map_file = NULL;

typedef HRESULT(__stdcall* PresentFn)(IDXGISwapChain*, UINT, UINT);
PresentFn oPresent = nullptr;

LARGE_INTEGER g_frequency;
LARGE_INTEGER g_last_present_time;

// Precise hybrid spin-wait frame pacer
void PaceFrame(double target_ms) {
    if (target_ms <= 0.0) return;

    LARGE_INTEGER current_time;
    QueryPerformanceCounter(&current_time);

    double elapsed_ms = (double)(current_time.QuadPart - g_last_present_time.QuadPart) * 1000.0 / (double)g_frequency.QuadPart;
    double remaining_ms = target_ms - elapsed_ms;

    // Coarse sleep to yield CPU if more than 2ms remaining
    if (remaining_ms > 2.0) {
        timeBeginPeriod(1);
        Sleep((DWORD)(remaining_ms - 1.5));
        timeEndPeriod(1);
    }

    // High-resolution spin wait for the remainder
    while (true) {
        QueryPerformanceCounter(&current_time);
        elapsed_ms = (double)(current_time.QuadPart - g_last_present_time.QuadPart) * 1000.0 / (double)g_frequency.QuadPart;
        if (elapsed_ms >= target_ms) break;
        YieldProcessor();
    }

    if (g_shared_data) {
        g_shared_data->current_frametime_ms = elapsed_ms;
        g_shared_data->current_fps = (elapsed_ms > 0.0) ? (1000.0 / elapsed_ms) : 0.0;
    }

    g_last_present_time = current_time;
}

HRESULT __stdcall HookedPresent(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags) {
    if (g_shared_data && g_shared_data->is_active) {
        PaceFrame(g_shared_data->target_frametime_ms);
    }
    return oPresent(pSwapChain, SyncInterval, Flags);
}

DWORD WINAPI MainThread(LPVOID lpParam) {
    QueryPerformanceFrequency(&g_frequency);
    QueryPerformanceCounter(&g_last_present_time);

    // Setup Shared Memory for IPC
    g_map_file = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(SharedData), "Local\\FramepacerIPC");
    if (g_map_file) {
        g_shared_data = (SharedData*)MapViewOfFile(g_map_file, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(SharedData));
    }

    // Initialize MinHook
    if (MH_Initialize() != MH_OK) return 1;

    // Create a dummy swapchain to locate the IDXGISwapChain::Present vtable entry
    WNDCLASSEXA wc = { sizeof(WNDCLASSEX), CS_CLASSDC, DefWindowProcA, 0L, 0L, GetModuleHandle(NULL), NULL, NULL, NULL, NULL, "DX_DUMMY", NULL };
    RegisterClassExA(&wc);
    HWND hWnd = CreateWindowA("DX_DUMMY", NULL, WS_OVERLAPPEDWINDOW, 0, 0, 100, 100, NULL, NULL, wc.hInstance, NULL);

    D3D_FEATURE_LEVEL featureLevel;
    const D3D_FEATURE_LEVEL featureLevels[] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_0 };
    DXGI_SWAP_CHAIN_DESC sd = {};
    sd.BufferCount = 1;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hWnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;

    ID3D11Device* pDevice = nullptr;
    ID3D11DeviceContext* pContext = nullptr;
    IDXGISwapChain* pSwapChain = nullptr;

    if (SUCCEEDED(D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, featureLevels, 2, D3D11_SDK_VERSION, &sd, &pSwapChain, &pDevice, &featureLevel, &pContext))) {
        void** pVMT = *(void***)pSwapChain;
        // Present is index 8 on the IDXGISwapChain vtable
        MH_CreateHook(pVMT[8], (LPVOID)HookedPresent, (LPVOID*)&oPresent);
        MH_EnableHook(pVMT[8]);

        pSwapChain->Release();
        pDevice->Release();
        pContext->Release();
    }

    DestroyWindow(hWnd);
    UnregisterClassA("DX_DUMMY", wc.hInstance);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    if (ul_reason_for_call == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        CreateThread(NULL, 0, MainThread, NULL, 0, NULL);
    }
    return TRUE;
}
