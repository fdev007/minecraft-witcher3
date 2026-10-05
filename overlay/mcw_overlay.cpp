// mcw_overlay: the hidden Minecraft client's hand, held item and HUD on top of The Witcher 3, entirely on the GPU.
//
// Adapted from cs2-gothic/overlay/gpu_overlay.cpp (same author, same project tree). Windows.Graphics.Capture grabs the
// Minecraft window (mcclient: the world is not drawn, the background is a flat key colour) into a D3D11 texture, a pixel
// shader keys the background out and despills, and the result is presented through a DirectComposition swap chain
// (premultiplied alpha) on a click-through, non-activating topmost window over the Witcher's client area.
//
// Differences from gpu_overlay: window titles and key colour come from the command line; the default key is magenta,
// because Minecraft's own HUD is green in places (the XP bar would key out); the Minecraft window is kept right below
// the Witcher in z-order, at its position and client size, so the capture maps 1:1 onto the Witcher's pixels and nothing
// ever activates it (the Witcher pauses as soon as it loses focus).
//
//   mcw_overlay.exe [--src "MCW Client"] [--host "The Witcher 3"] [--key magenta|green] [--mode 0|1] [--no-manage]
//                   [--wait 120]
// stdin:  "mode 1" / "mode 0" (show / hide), "quit"
// stdout: "ready <w>x<h>", stats every 5 s, "lost" when a window goes away
#include <windows.h>
#include <unknwn.h>
#include <inspectable.h>
#include <d3d11.h>
#include <dxgi1_3.h>
#include <dcomp.h>
#include <dwmapi.h>
#include <d3dcompiler.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cwchar>
#include <string>
#include <thread>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dcomp.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "windowsapp.lib")

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;

// keyMode 0 = magenta (255,0,255): Minecraft draws its HUD over the flat key with plain alpha blending, so a pixel is
// C = a*F + (1-a)*K. With K = magenta the key's share is d = min(R,B) - G (exact for neutral F, which most of the HUD
// is): alpha = 1 - d, premultiplied colour = C - d*K. Translucent hotbar slots and chat backgrounds come out as the
// same dark glass they are in Minecraft instead of pink. Strongly purple pixels lose some alpha - a fair price.
// keyMode 1 = green (0,255,0), the gpu_overlay threshold key: d = G - max(R,B), d >= 160/255 fully transparent,
// d <= 60/255 fully opaque, despill removes the key's share from what stays half-transparent.
static const char* kShader = R"(
Texture2D tex : register(t0);
SamplerState smp : register(s0);
cbuffer Params : register(b0) { float4 uvRect; float4 key; };   // uvRect: xy offset, zw scale; key.x: 0 magenta, 1 green
struct V { float4 pos : SV_Position; float2 uv : TEXCOORD0; };
V vs(uint id : SV_VertexID) {
    V o;
    float2 p = float2((id << 1) & 2, id & 2);
    o.uv = p;
    o.pos = float4(p * float2(2, -2) + float2(-1, 1), 0, 1);
    return o;
}
float4 ps(V i) : SV_Target {
    float3 c = tex.Sample(smp, uvRect.xy + i.uv * uvRect.zw).rgb;
    if (key.x < 0.5) {
        float d = saturate(min(c.r, c.b) - c.g);             // share of magenta behind the pixel
        return float4(max(c.r - d, 0), c.g, max(c.b - d, 0), 1 - d);   // already premultiplied
    }
    float m = max(c.r, c.b);
    float d = c.g - m;                                       // green dominance
    float a = saturate((160.0 / 255.0 - d) / (100.0 / 255.0));
    c.g = min(c.g, m);                                       // despill
    return float4(c * a, a);                                 // premultiplied
}
)";

static std::atomic<bool> g_mode{true};
static std::atomic<bool> g_quit{false};

static std::wstring trim(std::wstring s) {
    while (!s.empty() && iswspace(s.back())) s.pop_back();
    return s;
}

struct FindCtx { const wchar_t* title; HWND found; };
static HWND findWindow(const wchar_t* title) {
    FindCtx ctx{title, nullptr};
    EnumWindows([](HWND h, LPARAM lp) -> BOOL {
        auto* c = reinterpret_cast<FindCtx*>(lp);
        wchar_t buf[256];
        GetWindowTextW(h, buf, 256);
        if (IsWindowVisible(h) && trim(buf) == c->title) { c->found = h; return FALSE; }
        return TRUE;
    }, reinterpret_cast<LPARAM>(&ctx));
    return ctx.found;
}

static RECT clientScreenRect(HWND h) {
    RECT rc{};
    GetClientRect(h, &rc);
    POINT tl{0, 0};
    ClientToScreen(h, &tl);
    return RECT{tl.x, tl.y, tl.x + rc.right, tl.y + rc.bottom};
}

// The Minecraft window: never activated, no taskbar button, right below the Witcher, its client area exactly over the
// Witcher's. mcclient creates it undecorated, so the window rectangle is the client rectangle; if it is not, the outer
// rectangle is grown by the difference.
static void placeSource(HWND src, HWND host) {
    LONG_PTR ex = GetWindowLongPtrW(src, GWL_EXSTYLE);
    LONG_PTR want = (ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~static_cast<LONG_PTR>(WS_EX_APPWINDOW);
    if (want != ex) SetWindowLongPtrW(src, GWL_EXSTYLE, want);
    RECT hc = clientScreenRect(host), sc = clientScreenRect(src), sw{};
    GetWindowRect(src, &sw);
    int w = hc.right - hc.left, h = hc.bottom - hc.top;
    int l = hc.left - (sc.left - sw.left), t = hc.top - (sc.top - sw.top);
    int ow = w + (sw.right - sw.left) - (sc.right - sc.left), oh = h + (sw.bottom - sw.top) - (sc.bottom - sc.top);
    // z-order every time: the shell may raise it; SWP_NOACTIVATE everywhere: the Witcher pauses without focus
    SetWindowPos(src, host, l, t, ow, oh, SWP_NOACTIVATE | SWP_NOOWNERZORDER);
}

static LRESULT CALLBACK wndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_NCHITTEST) return HTTRANSPARENT;
    return DefWindowProcW(h, m, w, l);
}

int wmain(int argc, wchar_t** argv) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    init_apartment(apartment_type::multi_threaded);

    std::wstring srcTitle = L"MCW Client", hostTitle = L"The Witcher 3";
    float keyMode = 0;
    bool manage = true;
    int waitS = 120;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        bool more = i + 1 < argc;
        if (a == L"--src" && more) srcTitle = argv[++i];
        else if (a == L"--host" && more) hostTitle = argv[++i];
        else if (a == L"--key" && more) { std::wstring k = argv[++i]; keyMode = k == L"green" ? 1.0f : 0.0f; }
        else if (a == L"--mode" && more) g_mode = wcscmp(argv[++i], L"0") != 0;
        else if (a == L"--wait" && more) waitS = _wtoi(argv[++i]);
        else if (a == L"--no-manage") manage = false;
        else { wprintf(L"unknown argument %ls\n", a.c_str()); return 2; }
    }

    std::thread([] {
        char line[128];
        while (fgets(line, sizeof line, stdin)) {
            std::string s(line);
            if (s.rfind("mode 1", 0) == 0) g_mode = true;
            else if (s.rfind("mode 0", 0) == 0) g_mode = false;
            else if (s.rfind("quit", 0) == 0) break;
        }
        g_quit = true;
    }).detach();

    // both windows may still be starting (Minecraft takes ~20 s)
    HWND mc = nullptr, host = nullptr;
    auto tWait = std::chrono::steady_clock::now();
    while (!g_quit) {
        mc = findWindow(srcTitle.c_str());
        host = findWindow(hostTitle.c_str());
        if (mc && host) break;
        if (std::chrono::steady_clock::now() - tWait > std::chrono::seconds(waitS)) {
            wprintf(L"windows not found (%ls=%p, %ls=%p)\n", srcTitle.c_str(), mc, hostTitle.c_str(), host);
            return 1;
        }
        Sleep(250);
    }
    if (g_quit) return 0;
    if (manage) placeSource(mc, host);

    // D3D11 device shared by capture and rendering
    com_ptr<ID3D11Device> dev;
    com_ptr<ID3D11DeviceContext> ctx;
    check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                    nullptr, 0, D3D11_SDK_VERSION, dev.put(), nullptr, ctx.put()));
    auto dxgiDev = dev.as<IDXGIDevice>();
    com_ptr<::IInspectable> insp;
    check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgiDev.get(), insp.put()));
    auto rtDev = insp.as<IDirect3DDevice>();

    // capture Minecraft
    auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
    GraphicsCaptureItem item{nullptr};
    check_hresult(interop->CreateForWindow(mc, guid_of<GraphicsCaptureItem>(), put_abi(item)));
    auto itemSize = item.Size();
    auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(rtDev, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, itemSize);
    auto session = pool.CreateCaptureSession(item);
    session.IsCursorCaptureEnabled(false);
    try { session.IsBorderRequired(false); } catch (...) {}
    session.StartCapture();

    // overlay window: click-through, never activated, composition-only (no redirection bitmap)
    WNDCLASSEXW wc{sizeof wc};
    wc.lpfnWndProc = wndProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"McwGpuOverlay";
    RegisterClassExW(&wc);
    RECT hr = clientScreenRect(host);
    int ow = hr.right - hr.left, oh = hr.bottom - hr.top;
    HWND wnd = CreateWindowExW(WS_EX_NOREDIRECTIONBITMAP | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT |
                               WS_EX_LAYERED | WS_EX_NOACTIVATE,
                               wc.lpszClassName, L"mcw overlay", WS_POPUP, hr.left, hr.top, ow, oh,
                               nullptr, nullptr, wc.hInstance, nullptr);
    SetLayeredWindowAttributes(wnd, 0, 255, LWA_ALPHA);

    com_ptr<IDXGIFactory2> factory;
    check_hresult(CreateDXGIFactory2(0, IID_PPV_ARGS(factory.put())));
    DXGI_SWAP_CHAIN_DESC1 sd{};
    sd.Width = ow; sd.Height = oh;
    sd.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 2;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;
    sd.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
    com_ptr<IDXGISwapChain1> swap;
    check_hresult(factory->CreateSwapChainForComposition(dev.get(), &sd, nullptr, swap.put()));

    com_ptr<IDCompositionDevice> dcomp;
    check_hresult(DCompositionCreateDevice(dxgiDev.get(), IID_PPV_ARGS(dcomp.put())));
    com_ptr<IDCompositionTarget> target;
    check_hresult(dcomp->CreateTargetForHwnd(wnd, TRUE, target.put()));
    com_ptr<IDCompositionVisual> visual;
    check_hresult(dcomp->CreateVisual(visual.put()));
    check_hresult(visual->SetContent(swap.get()));
    check_hresult(target->SetRoot(visual.get()));
    check_hresult(dcomp->Commit());

    // pipeline
    com_ptr<ID3DBlob> vsb, psb, err;
    if (FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "vs", "vs_5_0", 0, 0, vsb.put(), err.put())) ||
        FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "ps", "ps_5_0", 0, 0, psb.put(), err.put()))) {
        printf("shader: %s\n", err ? (const char*)err->GetBufferPointer() : "?");
        return 1;
    }
    com_ptr<ID3D11VertexShader> vs;
    com_ptr<ID3D11PixelShader> ps;
    check_hresult(dev->CreateVertexShader(vsb->GetBufferPointer(), vsb->GetBufferSize(), nullptr, vs.put()));
    check_hresult(dev->CreatePixelShader(psb->GetBufferPointer(), psb->GetBufferSize(), nullptr, ps.put()));
    // point sampling: the capture is pixel-for-pixel the overlay, linear would only smear the HUD
    D3D11_SAMPLER_DESC smpd{};
    smpd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    smpd.AddressU = smpd.AddressV = smpd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    com_ptr<ID3D11SamplerState> smp;
    check_hresult(dev->CreateSamplerState(&smpd, smp.put()));
    D3D11_BUFFER_DESC cbd{32, D3D11_USAGE_DYNAMIC, D3D11_BIND_CONSTANT_BUFFER, D3D11_CPU_ACCESS_WRITE};
    com_ptr<ID3D11Buffer> cb;
    check_hresult(dev->CreateBuffer(&cbd, nullptr, cb.put()));

    com_ptr<ID3D11Texture2D> frameTex;
    com_ptr<ID3D11ShaderResourceView> frameSrv;
    com_ptr<ID3D11RenderTargetView> rtv;
    auto makeRtv = [&] {
        rtv = nullptr;
        com_ptr<ID3D11Texture2D> bb;
        check_hresult(swap->GetBuffer(0, IID_PPV_ARGS(bb.put())));
        check_hresult(dev->CreateRenderTargetView(bb.get(), nullptr, rtv.put()));
    };
    makeRtv();

    bool shown = false, haveFrame = false;
    int captured = 0, presented = 0;
    auto tStat = std::chrono::steady_clock::now();
    auto tPlace = tStat;
    printf("ready %dx%d\n", ow, oh);
    fflush(stdout);

    while (!g_quit) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) DispatchMessageW(&msg);
        if (!IsWindow(mc) || !IsWindow(host)) {
            printf("lost (%s window closed)\n", IsWindow(mc) ? "host" : "source");
            fflush(stdout);
            break;
        }

        auto now = std::chrono::steady_clock::now();
        if (manage && now - tPlace >= std::chrono::milliseconds(500)) {
            tPlace = now;
            placeSource(mc, host);
        }

        // newest captured frame -> our texture
        for (auto f = pool.TryGetNextFrame(); f; f = pool.TryGetNextFrame()) {
            auto cs = f.ContentSize();
            if (cs.Width != itemSize.Width || cs.Height != itemSize.Height) {
                itemSize = cs;
                pool.Recreate(rtDev, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, itemSize);
                frameTex = nullptr;
                haveFrame = false;
                continue;
            }
            auto access = f.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            com_ptr<ID3D11Texture2D> src;
            check_hresult(access->GetInterface(guid_of<ID3D11Texture2D>(), src.put_void()));
            if (!frameTex) {
                D3D11_TEXTURE2D_DESC td;
                src->GetDesc(&td);
                td.Usage = D3D11_USAGE_DEFAULT;
                td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
                td.CPUAccessFlags = 0;
                td.MiscFlags = 0;
                check_hresult(dev->CreateTexture2D(&td, nullptr, frameTex.put()));
                frameSrv = nullptr;
                check_hresult(dev->CreateShaderResourceView(frameTex.get(), nullptr, frameSrv.put()));
            }
            ctx->CopyResource(frameTex.get(), src.get());
            haveFrame = true;
            ++captured;
        }

        bool want = g_mode && GetForegroundWindow() == host && haveFrame;
        if (want) {
            RECT r = clientScreenRect(host);
            int w = r.right - r.left, h = r.bottom - r.top;
            if (w != ow || h != oh) {
                ow = w; oh = h;
                rtv = nullptr;
                check_hresult(swap->ResizeBuffers(2, ow, oh, DXGI_FORMAT_B8G8R8A8_UNORM, 0));
                makeRtv();
            }
            SetWindowPos(wnd, HWND_TOPMOST, r.left, r.top, ow, oh, SWP_NOACTIVATE | (shown ? 0 : SWP_SHOWWINDOW));
            shown = true;

            // crop: Minecraft's client area inside the captured window image
            RECT frame{}, cli = clientScreenRect(mc);
            DwmGetWindowAttribute(mc, DWMWA_EXTENDED_FRAME_BOUNDS, &frame, sizeof frame);
            float fw = (float)itemSize.Width, fh = (float)itemSize.Height;
            float params[8] = {(cli.left - frame.left) / fw, (cli.top - frame.top) / fh,
                               (cli.right - cli.left) / fw, (cli.bottom - cli.top) / fh, keyMode, 0, 0, 0};
            D3D11_MAPPED_SUBRESOURCE ms;
            if (SUCCEEDED(ctx->Map(cb.get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) {
                memcpy(ms.pData, params, sizeof params);
                ctx->Unmap(cb.get(), 0);
            }
            D3D11_VIEWPORT vp{0, 0, (float)ow, (float)oh, 0, 1};
            ID3D11RenderTargetView* rt = rtv.get();
            ctx->OMSetRenderTargets(1, &rt, nullptr);
            ctx->RSSetViewports(1, &vp);
            ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            ctx->VSSetShader(vs.get(), nullptr, 0);
            ctx->PSSetShader(ps.get(), nullptr, 0);
            ID3D11ShaderResourceView* srv = frameSrv.get();
            ctx->PSSetShaderResources(0, 1, &srv);
            ID3D11SamplerState* s = smp.get();
            ctx->PSSetSamplers(0, 1, &s);
            ID3D11Buffer* b = cb.get();
            ctx->PSSetConstantBuffers(0, 1, &b);
            ctx->Draw(3, 0);
            swap->Present(1, 0);
            ++presented;
        } else {
            if (shown) { ShowWindow(wnd, SW_HIDE); shown = false; }
            Sleep(5);
        }

        double el = std::chrono::duration<double>(now - tStat).count();
        if (el >= 5) {
            RECT mr = clientScreenRect(mc);
            printf("captured %.1f fps, presented %.1f fps, %s, source %ldx%ld\n", captured / el, presented / el,
                   shown ? "visible" : "hidden", mr.right - mr.left, mr.bottom - mr.top);
            fflush(stdout);
            captured = presented = 0;
            tStat = now;
        }
    }
    session.Close();
    pool.Close();
    DestroyWindow(wnd);
    return 0;
}
