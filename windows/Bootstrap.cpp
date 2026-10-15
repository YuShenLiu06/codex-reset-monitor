// A native /MT launcher: only Windows system DLLs, visible before preparing files.
#define UNICODE
#define _UNICODE
#define NOMINMAX
#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <bcrypt.h>
#include <compressapi.h>
#include <gdiplus.h>
#include <filesystem>
#include <fstream>
#include <vector>
#include <string>
#include <set>
#include <array>
#include <thread>
#include <atomic>
#include <algorithm>
#include <stdexcept>
#include <cstdint>
#include <cwctype>
#include "payload_identity.h"
#pragma comment(lib,"comctl32.lib")
#pragma comment(lib,"bcrypt.lib")
#pragma comment(lib,"cabinet.lib")
#pragma comment(lib,"shell32.lib")
#pragma comment(lib,"gdiplus.lib")
namespace fs=std::filesystem;
using Bytes=std::vector<unsigned char>;
using Hash=std::array<unsigned char,32>;
constexpr UINT PROGRESS=WM_APP+1, READY=WM_APP+2, FAILED=WM_APP+3, ENDED=WM_APP+4;
HWND windowHandle,label,bar,button; HINSTANCE instance;
std::atomic<bool> cancelled=false; std::thread work; fs::path cache,staging,testOutput;
std::wstring childArgs,testAction; bool offline=false,closing=false,reused=false,repaired=false;
ULONGLONG began=GetTickCount64(),visibleMs=0; std::string failure;
struct Handle { HANDLE value=INVALID_HANDLE_VALUE; ~Handle(){if(value!=INVALID_HANDLE_VALUE && value)CloseHandle(value);} };
void require(bool ok){if(!ok)throw std::runtime_error("PACKAGE_PREPARE_FAILED");}
void checkCancel(){if(cancelled)throw std::runtime_error("PREPARE_CANCELLED");}
Hash hashData(const unsigned char* data,size_t size){
    BCRYPT_ALG_HANDLE algorithm=nullptr; BCRYPT_HASH_HANDLE hash=nullptr; Hash result{};
    require(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)==0);
    auto status=BCryptCreateHash(algorithm,&hash,nullptr,0,nullptr,0,0);
    if(status==0)status=BCryptHashData(hash,const_cast<PUCHAR>(data),static_cast<ULONG>(size),0);
    if(status==0)status=BCryptFinishHash(hash,result.data(),32,0);
    if(hash)BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(algorithm,0);require(status==0);return result;
}
std::string hex(const Hash& value){std::string out;for(auto b:value){out+="0123456789abcdef"[b>>4];out+="0123456789abcdef"[b&15];}return out;}
Bytes readAt(HANDLE file,uint64_t offset,size_t size){
    LARGE_INTEGER position;position.QuadPart=offset;require(SetFilePointerEx(file,position,nullptr,FILE_BEGIN));
    Bytes data(size);DWORD read=0;require(size<0x80000000 && ReadFile(file,data.data(),static_cast<DWORD>(size),&read,nullptr) && read==size);return data;
}
uint64_t number(const Bytes& data,size_t& pos,size_t size){require(pos+size<=data.size());uint64_t n=0;for(size_t i=0;i<size;i++)n|=uint64_t(data[pos++])<<(i*8);return n;}
std::wstring wide(const std::string& value){int count=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,value.data(),static_cast<int>(value.size()),nullptr,0);require(count>0);std::wstring out(count,0);require(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,value.data(),static_cast<int>(value.size()),out.data(),count)==count);return out;}
bool safeName(const std::wstring& name){
    if(name.empty()||name.size()>512||name.front()==L'/')return false;
    size_t start=0;
    while(start<name.size()){
        size_t end=name.find(L'/',start);if(end==std::wstring::npos)end=name.size();auto part=name.substr(start,end-start);
        if(part.empty()||part==L"."||part==L".."||part.back()==L'.'||part.back()==L' ')return false;
        for(auto c:part)if(c<32||std::wstring(L":\\<>\"|?*").find(c)!=std::wstring::npos)return false;
        auto base=part.substr(0,part.find(L'.'));for(auto& c:base)c=towupper(c);
        if(base==L"CON"||base==L"PRN"||base==L"AUX"||base==L"NUL"||(base.size()==4&&(base.substr(0,3)==L"COM"||base.substr(0,3)==L"LPT")&&base[3]>=L'1'&&base[3]<=L'9'))return false;
        if(end==name.size())return true;start=end+1;
    }return false;
}
void noLinks(const fs::path& path){
    fs::path current;
    for(const auto& part:path){current/=part;DWORD attr=GetFileAttributesW(current.c_str());if(attr!=INVALID_FILE_ATTRIBUTES)require(!(attr&FILE_ATTRIBUTE_REPARSE_POINT));}
}
void makeDirectory(const fs::path& path){noLinks(path);fs::create_directories(path);noLinks(path);}
void removeTree(const fs::path& path){if(!fs::exists(path))return;noLinks(path);for(auto& item:fs::recursive_directory_iterator(path))require(!(GetFileAttributesW(item.path().c_str())&FILE_ATTRIBUTE_REPARSE_POINT));fs::remove_all(path);}
struct Entry {fs::path name;uint64_t size,packed,offset;Hash hash;};
std::vector<Entry> load(HANDLE source,uint64_t& payload){
    LARGE_INTEGER length;require(GetFileSizeEx(source,&length)&&length.QuadPart>40);auto footer=readAt(source,length.QuadPart-40,40);size_t pos=0;
    auto offset=number(footer,pos,8),size=number(footer,pos,8);payload=number(footer,pos,8);auto packed=number(footer,pos,8);
    require(std::string(footer.begin()+32,footer.end())=="CRMPKG01"&&payload<offset&&packed==offset-payload&&size<=2097152&&offset+size==uint64_t(length.QuadPart-40));
    auto data=readAt(source,offset,static_cast<size_t>(size));require(hex(hashData(data.data(),data.size()))==EXPECTED_MANIFEST);
    require(data.size()>=12 && std::string(data.begin(),data.begin()+8)=="CRMPKG01");pos=8;auto count=number(data,pos,4);require(count>0&&count<=2048);
    std::vector<Entry> entries;std::set<std::wstring> seen;uint64_t end=0,total=0;
    for(uint64_t i=0;i<count;i++){
        auto bytes=number(data,pos,4);Entry entry;entry.size=number(data,pos,8);entry.packed=number(data,pos,8);entry.offset=number(data,pos,8);
        require(bytes>0&&bytes<=512&&pos+32+bytes<=data.size()&&entry.size>0&&entry.size<=134217728&&entry.packed>0&&entry.packed<=134217728&&entry.offset==end&&entry.packed<=packed-end);
        std::copy_n(data.begin()+pos,32,entry.hash.begin());pos+=32;auto name=wide(std::string(data.begin()+pos,data.begin()+pos+bytes));pos+=bytes;require(safeName(name));
        auto lower=name;for(auto& c:lower)c=towlower(c);require(seen.insert(lower).second);entry.name=name;entries.push_back(entry);end+=entry.packed;total+=entry.size;require(total<=1073741824);
    }
    require(pos==data.size()&&end==packed);return entries;
}
bool validCache(const std::vector<Entry>& entries){
    if(!fs::is_directory(cache))return false;noLinks(cache);size_t count=0;
    for(auto& item:fs::recursive_directory_iterator(cache)){require(!(GetFileAttributesW(item.path().c_str())&FILE_ATTRIBUTE_REPARSE_POINT));if(item.is_regular_file())count++;}
    if(count!=entries.size())return false;
    for(const auto& entry:entries){
        checkCancel();auto path=cache/entry.name;noLinks(path);if(!fs::is_regular_file(path)||fs::file_size(path)!=entry.size)return false;
        Handle file;file.value=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT,nullptr);if(file.value==INVALID_HANDLE_VALUE)return false;
        auto bytes=readAt(file.value,0,static_cast<size_t>(entry.size));if(hashData(bytes.data(),bytes.size())!=entry.hash)return false;
    }return true;
}
void screenshot(const fs::path& path){
    RECT rect;GetClientRect(windowHandle,&rect);HDC source=GetDC(windowHandle),target=CreateCompatibleDC(source);HBITMAP bitmap=CreateCompatibleBitmap(source,rect.right,rect.bottom);auto old=SelectObject(target,bitmap);
    BitBlt(target,0,0,rect.right,rect.bottom,source,0,0,SRCCOPY);Gdiplus::Bitmap image(bitmap,nullptr);
    CLSID png={0x557cf406,0x1a04,0x11d3,{0x9a,0x73,0,0,0xf8,0x1e,0xf3,0x2e}};image.Save(path.c_str(),&png,nullptr);
    SelectObject(target,old);DeleteObject(bitmap);DeleteDC(target);ReleaseDC(windowHandle,source);
}
void report(const std::string& status){
    if(testOutput.empty())return;fs::create_directories(testOutput);std::ofstream output(testOutput/L"BOOTSTRAP.json");
    output<<"{\"status\":\""<<status<<"\",\"reused\":"<<(reused?"true":"false")<<",\"repaired\":"<<(repaired?"true":"false")<<",\"visible_ms\":"<<visibleMs<<",\"elapsed_ms\":"<<GetTickCount64()-began<<",\"source_commit\":\""<<SOURCE_COMMIT<<"\"}";
}
void prepare(){
    try{
        wchar_t exe[32768];require(GetModuleFileNameW(nullptr,exe,32768)>0);Handle source;source.value=CreateFileW(exe,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);require(source.value!=INVALID_HANDLE_VALUE);
        uint64_t payload=0;auto entries=load(source.value,payload);
        wchar_t local[32768];DWORD count=GetEnvironmentVariableW(L"LOCALAPPDATA",local,32768);require(count>0&&count<32768);
        auto packages=fs::path(local)/L"Codex Reset Installer"/L"packages";makeDirectory(packages);
        cache=packages/wide(std::string(SOURCE_COMMIT).substr(0,12)+"-"+std::string(EXPECTED_MANIFEST).substr(0,16));staging=cache;staging+=L".preparing";
        // With the app-wide mutex held, a leftover staging directory is an interrupted preparation.
        removeTree(staging);reused=validCache(entries);
        if(!reused){
            repaired=fs::exists(cache);removeTree(cache);makeDirectory(staging);
            DECOMPRESSOR_HANDLE decompressor=nullptr;require(CreateDecompressor(COMPRESS_ALGORITHM_LZMS,nullptr,&decompressor));
            try{
                size_t index=0;
                for(const auto& entry:entries){
                    checkCancel();PostMessageW(windowHandle,PROGRESS,static_cast<WPARAM>(100*index++/entries.size()),0);
                    auto packed=readAt(source.value,payload+entry.offset,static_cast<size_t>(entry.packed));Bytes bytes(static_cast<size_t>(entry.size));SIZE_T actual=0;
                    require(Decompress(decompressor,packed.data(),packed.size(),bytes.data(),bytes.size(),&actual)&&actual==entry.size&&hashData(bytes.data(),bytes.size())==entry.hash);checkCancel();
                    auto path=staging/entry.name;makeDirectory(path.parent_path());Handle target;target.value=CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);require(target.value!=INVALID_HANDLE_VALUE);DWORD written=0;require(WriteFile(target.value,bytes.data(),static_cast<DWORD>(bytes.size()),&written,nullptr)&&written==bytes.size());
                    if(testAction==L"cancel"&&index==1)cancelled=true;
                }
            }catch(...){CloseDecompressor(decompressor);throw;}CloseDecompressor(decompressor);
            checkCancel();fs::rename(staging,cache);
        }
        checkCancel();PostMessageW(windowHandle,READY,0,0);
    }catch(const std::exception& error){failure=error.what();try{if(!staging.empty())removeTree(staging);}catch(...){}PostMessageW(windowHandle,FAILED,0,0);}
}
std::wstring quote(const std::wstring& value){std::wstring out=L"\"";size_t slashes=0;for(wchar_t c:value){if(c==L'\\')slashes++;else{out.append(slashes*(c==L'"'?2:1),L'\\');slashes=0;if(c==L'"')out+=L'\\';out+=c;}}out.append(slashes*2,L'\\');return out+L'"';}
void beginWork(){cancelled=false;EnableWindow(button,TRUE);SetWindowTextW(button,L"取消");SetWindowTextW(label,L"正在准备本地组件…\n首次打开需要解压；完成后会自动进入设置。\n再次打开会检查并使用已准备的文件。");work=std::thread(prepare);}
LRESULT CALLBACK procedure(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    switch(msg){
    case WM_CLOSE:case WM_COMMAND:
        if(msg==WM_COMMAND&&LOWORD(wp)!=1)break;
        if(work.joinable()){cancelled=true;EnableWindow(button,FALSE);SetWindowTextW(label,L"正在取消并清理本次准备…");}
        else if(!failure.empty()&&msg==WM_COMMAND){failure.clear();beginWork();}
        else DestroyWindow(hwnd);return 0;
    case PROGRESS:SendMessageW(bar,PBM_SETPOS,wp,0);return 0;
    case READY:{
        work.join();if(cancelled){report("cancelled");DestroyWindow(hwnd);return 0;}report("ready");
        if(testAction==L"prepare"){DestroyWindow(hwnd);return 0;}
        auto exe=cache/L"CodexResetInstaller.exe";std::wstring command=quote(exe.wstring())+childArgs;STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION process{};
        if(!CreateProcessW(exe.c_str(),command.data(),nullptr,nullptr,FALSE,0,nullptr,cache.c_str(),&startup,&process)){failure="UI_START_FAILED";PostMessageW(hwnd,FAILED,0,0);return 0;}
        CloseHandle(process.hThread);ShowWindow(hwnd,SW_HIDE);closing=true;
        work=std::thread([process](){WaitForSingleObject(process.hProcess,INFINITE);DWORD exit=1;GetExitCodeProcess(process.hProcess,&exit);CloseHandle(process.hProcess);PostMessageW(windowHandle,ENDED,exit,0);});return 0;
    }
    case FAILED:
        if(work.joinable())work.join();report(cancelled?"cancelled":"failed");
        if(cancelled){DestroyWindow(hwnd);return 0;}
        SetWindowTextW(label,L"准备没有完成。请检查可用磁盘空间后点击重试。\n如果仍失败，请重新下载此 EXE 并反馈。\n问题标识：PACKAGE_PREPARE_FAILED\n原有设置进度仍保留。");SetWindowTextW(button,L"重试");EnableWindow(button,TRUE);
        if(offline){PostQuitMessage(1);}return 0;
    case ENDED:if(work.joinable())work.join();PostQuitMessage(static_cast<int>(wp));return 0;
    case WM_DESTROY:PostQuitMessage(cancelled?130:0);return 0;
    }return DefWindowProcW(hwnd,msg,wp,lp);
}
int WINAPI wWinMain(HINSTANCE module,HINSTANCE,LPWSTR,int){
    instance=module;SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32);SetProcessDPIAware();
    int argc;auto argv=CommandLineToArgvW(GetCommandLineW(),&argc);if(!argv)return 2;
    for(int i=1;i<argc;i++){
        std::wstring arg=argv[i];
        if((arg==L"--offline-ui-test"||arg==L"--test-output"||arg==L"--bootstrap-test")&&i+1<argc){auto value=std::wstring(argv[++i]);
            if(arg==L"--bootstrap-test"){testAction=value;offline=true;}
            else{childArgs+=L" "+quote(arg)+L" "+quote(value);if(arg==L"--test-output")testOutput=fs::absolute(value);else offline=true;}
        }else {LocalFree(argv);return 2;}
    }LocalFree(argv);
    if(!testAction.empty()&&(testOutput.empty()||(testAction!=L"prepare"&&testAction!=L"cancel")))return 2;
    Handle mutex;mutex.value=CreateMutexW(nullptr,TRUE,L"Local\\CodexResetInstallerLauncher");bool duplicate=GetLastError()==ERROR_ALREADY_EXISTS;require(mutex.value!=nullptr);
    Handle appMutex;appMutex.value=OpenMutexW(SYNCHRONIZE|MUTEX_MODIFY_STATE,FALSE,L"Local\\CodexResetInstaller");
    if(appMutex.value){DWORD wait=WaitForSingleObject(appMutex.value,0);if(wait==WAIT_TIMEOUT)duplicate=true;else if(wait==WAIT_OBJECT_0||wait==WAIT_ABANDONED)ReleaseMutex(appMutex.value);}
    if(duplicate){
        if(offline)report("already-open");else{auto existing=FindWindowW(nullptr,L"Codex 提醒设置 · Windows 测试版");if(!existing)existing=FindWindowW(L"CodexResetPrepare",nullptr);if(existing){ShowWindow(existing,SW_RESTORE);SetForegroundWindow(existing);}MessageBoxW(nullptr,L"设置窗口已经打开，请回到原来的窗口继续。",L"Codex 提醒",MB_OK|MB_ICONINFORMATION);}return 2;
    }
    Gdiplus::GdiplusStartupInput gdiplusInput;ULONG_PTR gdiplus;Gdiplus::GdiplusStartup(&gdiplus,&gdiplusInput,nullptr);
    INITCOMMONCONTROLSEX controls{sizeof(controls),ICC_PROGRESS_CLASS};InitCommonControlsEx(&controls);
    WNDCLASSW cls{};cls.lpfnWndProc=procedure;cls.hInstance=module;cls.lpszClassName=L"CodexResetPrepare";cls.hbrBackground=reinterpret_cast<HBRUSH>(COLOR_WINDOW+1);cls.hCursor=LoadCursorW(nullptr,IDC_ARROW);cls.hIcon=LoadIconW(module,MAKEINTRESOURCEW(101));RegisterClassW(&cls);
    windowHandle=CreateWindowExW(0,cls.lpszClassName,L"Codex 提醒 · 正在准备",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU, (GetSystemMetrics(SM_CXSCREEN)-600)/2,(GetSystemMetrics(SM_CYSCREEN)-300)/2,600,300,nullptr,nullptr,module,nullptr);
    label=CreateWindowW(L"STATIC",L"",WS_CHILD|WS_VISIBLE,28,25,530,125,windowHandle,nullptr,module,nullptr);
    bar=CreateWindowW(PROGRESS_CLASSW,L"",WS_CHILD|WS_VISIBLE,28,158,530,18,windowHandle,nullptr,module,nullptr);
    button=CreateWindowW(L"BUTTON",L"取消",WS_CHILD|WS_VISIBLE|BS_PUSHBUTTON,438,199,120,34,windowHandle,reinterpret_cast<HMENU>(1),module,nullptr);
    auto font=CreateFontW(-18,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");SendMessageW(label,WM_SETFONT,reinterpret_cast<WPARAM>(font),TRUE);SendMessageW(button,WM_SETFONT,reinterpret_cast<WPARAM>(font),TRUE);
    ShowWindow(windowHandle,SW_SHOW);UpdateWindow(windowHandle);visibleMs=GetTickCount64()-began;beginWork();
    if(!testOutput.empty()){fs::create_directories(testOutput);UpdateWindow(label);screenshot(testOutput/L"preparing.png");report("visible");}
    MSG message{};while(GetMessageW(&message,nullptr,0,0)>0){TranslateMessage(&message);DispatchMessageW(&message);}if(work.joinable()){cancelled=true;work.join();}
    DeleteObject(font);Gdiplus::GdiplusShutdown(gdiplus);ReleaseMutex(mutex.value);return static_cast<int>(message.wParam);
}
