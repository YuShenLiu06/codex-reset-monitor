"""Bounded single-EXE payload format; Windows' built-in LZMS, no packer runtime."""
import hashlib
from pathlib import Path, PurePosixPath
import struct

MAGIC=b'CRMPKG01'
FOOTER=struct.Struct('<QQQQ8s')
ENTRY=struct.Struct('<IQQQ32s')
MAX_FILE=128*1024*1024
MAX_TOTAL=1024*1024*1024


def safe_name(name):
    parts=name.split('/')
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    return (0<len(name.encode('utf-8'))<=512 and not PurePosixPath(name).is_absolute()
            and all(part and part not in ('.','..') and not part.endswith((' ','.'))
                    and not any(ord(c)<32 or c in ':\\<>"|?*' for c in part)
                    and part.split('.')[0].upper() not in reserved for part in parts))


def manifest(files):
    result=bytearray(MAGIC+struct.pack('<I',len(files)));seen=set();total=0
    for name,size,packed,offset,digest in files:
        if not safe_name(name) or name.casefold() in seen or not 0<size<=MAX_FILE or not 0<packed<=MAX_FILE:
            raise ValueError('Unsafe payload entry')
        total+=size;seen.add(name.casefold())
        raw=name.encode('utf-8');result+=ENTRY.pack(len(raw),size,packed,offset,bytes.fromhex(digest))+raw
    if total>MAX_TOTAL or not 1<=len(files)<=2048:raise ValueError('Payload limit')
    return bytes(result)


def parse_manifest(raw,payload_size):
    if len(raw)>2*1024*1024 or raw[:8]!=MAGIC:raise ValueError('Payload manifest')
    count=struct.unpack_from('<I',raw,8)[0];cursor=12;files=[];end=0
    for _ in range(count):
        length,size,packed,offset,digest=ENTRY.unpack_from(raw,cursor);cursor+=ENTRY.size
        name=raw[cursor:cursor+length].decode('utf-8');cursor+=length
        if offset!=end or offset+packed>payload_size:raise ValueError('Payload offsets')
        files.append((name,size,packed,offset,digest.hex()));end+=packed
    if cursor!=len(raw) or end!=payload_size or manifest(files)!=raw:raise ValueError('Payload layout')
    return files


def inspect_exe(path):
    with Path(path).open('rb') as source:
        source.seek(0,2);length=source.tell();source.seek(-FOOTER.size,2)
        offset,size,start,packed,magic=FOOTER.unpack(source.read())
        if magic!=MAGIC or start+packed!=offset or offset+size!=length-FOOTER.size or size>2*1024*1024:
            raise ValueError('Single EXE footer')
        source.seek(offset);raw=source.read(size)
    return {'manifest_sha256':hashlib.sha256(raw).hexdigest(),'payload_offset':start,
            'payload_bytes':packed,'files':parse_manifest(raw,packed)}


def compress(data):
    import ctypes
    from ctypes import wintypes
    dll=ctypes.WinDLL('cabinet',use_last_error=True)
    dll.CreateCompressor.argtypes=[wintypes.DWORD,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    dll.Compress.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    dll.CloseCompressor.argtypes=[ctypes.c_void_p]
    handle=ctypes.c_void_p();needed=ctypes.c_size_t()
    if not dll.CreateCompressor(5,None,ctypes.byref(handle)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        if dll.Compress(handle,data,len(data),None,0,ctypes.byref(needed)) or ctypes.get_last_error()!=122:
            raise ValueError('LZMS size query failed')
        output=ctypes.create_string_buffer(needed.value)
        if not dll.Compress(handle,data,len(data),output,len(output),ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return output.raw[:needed.value]
    finally:dll.CloseCompressor(handle)


def decompress(data,size):
    import ctypes
    from ctypes import wintypes
    dll=ctypes.WinDLL('cabinet',use_last_error=True);handle=ctypes.c_void_p();actual=ctypes.c_size_t()
    dll.CreateDecompressor.argtypes=[wintypes.DWORD,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    dll.Decompress.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    dll.CloseDecompressor.argtypes=[ctypes.c_void_p]
    if not dll.CreateDecompressor(5,None,ctypes.byref(handle)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        output=ctypes.create_string_buffer(size)
        if not dll.Decompress(handle,data,len(data),output,size,ctypes.byref(actual)) or actual.value!=size:
            raise ValueError('LZMS decompress failed')
        return output.raw
    finally:dll.CloseDecompressor(handle)


def build_exe(package,work,output,commit):
    import concurrent.futures
    import json
    import os
    import subprocess
    package=Path(package);work=Path(work);output=Path(output)
    root=Path(__file__).resolve().parents[1]
    paths=sorted(p for p in package.rglob('*') if p.is_file())
    def pack(path):
        raw=path.read_bytes()
        return path.relative_to(package).as_posix(),raw,compress(raw)
    payload=work/'payload.bin';files=[];offset=0
    with payload.open('wb') as target,concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for name,raw,packed in executor.map(pack,paths):
            files.append((name,len(raw),len(packed),offset,hashlib.sha256(raw).hexdigest()))
            target.write(packed);offset+=len(packed)
    metadata=manifest(files);identity=hashlib.sha256(metadata).hexdigest()
    (work/'payload_identity.h').write_text('#define EXPECTED_MANIFEST "'+identity+'"\n#define SOURCE_COMMIT "'+commit+'"\n')
    resource=work/'bootstrap.rc'
    resource.write_text('101 ICON "'+(root/'windows/app.ico').as_posix()+'"\n'
        '1 24 "'+(root/'windows/app.manifest').as_posix()+'"\n'
        '1 VERSIONINFO\nFILEVERSION 0,1,7,0\nPRODUCTVERSION 0,1,7,0\nFILEOS 0x40004\nFILETYPE 1\nBEGIN\n'
        'BLOCK "StringFileInfo" BEGIN BLOCK "040904b0" BEGIN\n'
        'VALUE "FileDescription", "Codex Reset Installer Preview"\nVALUE "FileVersion", "0.1.7-preview.1"\n'
        'VALUE "ProductName", "Codex Reset Installer"\nVALUE "ProductVersion", "0.1.7-preview.1"\n'
        'END END\nBLOCK "VarFileInfo" BEGIN VALUE "Translation", 0x409, 1200 END\nEND\n')
    vswhere=Path(os.environ['ProgramFiles(x86)'])/'Microsoft Visual Studio/Installer/vswhere.exe'
    installation=subprocess.check_output([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
    if not installation:raise ValueError('Standard runner C++ tools not found')
    environment=Path(installation)/'VC/Auxiliary/Build/vcvars64.bat'
    # Batch is a generated build script with fixed code and quoted local paths.
    native=work/'bootstrap.exe';res=work/'bootstrap.res'
    script=work/'compile-bootstrap.cmd'
    script.write_text('@echo off\ncall "'+str(environment)+'"\nif errorlevel 1 exit /b 1\n'
        'rc /nologo /fo "'+str(res)+'" "'+str(resource)+'"\nif errorlevel 1 exit /b 1\n'
        'cl /nologo /std:c++17 /utf-8 /EHsc /O2 /MT /W4 /D_WIN32_WINNT=0x0A00 /I "'+str(work)+'" '
        '"'+str(root/'windows/Bootstrap.cpp')+'" "'+str(res)+'" /Fo"'+str(work/'bootstrap.obj')+'" /Fe"'+str(native)+'" '
        '/link /SUBSYSTEM:WINDOWS /MANIFEST:NO /DYNAMICBASE /NXCOMPAT\nexit /b %errorlevel%\n')
    subprocess.run(['cmd.exe','/d','/c',str(script)],check=True)
    with output.open('wb') as target:
        target.write(native.read_bytes());start=target.tell()
        with payload.open('rb') as source:
            import shutil
            shutil.copyfileobj(source,target)
        metadata_start=target.tell();target.write(metadata)
        target.write(FOOTER.pack(metadata_start,len(metadata),start,offset,MAGIC))
    (work/'payload-manifest.json').write_text(json.dumps({'source_commit':commit,'manifest_sha256':identity,'files':files},indent=2))
    return inspect_exe(output)
