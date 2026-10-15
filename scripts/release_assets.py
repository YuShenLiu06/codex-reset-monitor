"""Verify same-source platform assets before assembling a shared preview release."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile
from scripts.verify_macos_release import CREDENTIAL, FORBIDDEN, inspect_archive, validate_public
from installer.core import expected_manifest

ROOT=Path(__file__).resolve().parents[1]

MAC_ZIP='codex-reset-installer-macos-arm64-preview.zip'
WINDOWS_ZIP='codex-reset-installer-windows-x64-preview.zip'
WINDOWS_EXE='codex-reset-installer-windows-x64-preview.exe'
WINDOWS_EVIDENCE='WINDOWS-UI-EVIDENCE.zip'
WINDOWS_ROOT='Codex 提醒设置 Windows 测试版'
MAC_CHECKS={'archive-allowlist-and-credential-scan','sha256','ditto-unpack','deep-signature-verification',
            'arm64-executable-modes','bundled-gh-version','network-denied-frozen-worker-initialization-cancel',
            'original-logo-icns-and-actual-application-dock-icon-load'}


def check_deployment_report(report,sha):
    cases=report.get('cases',[])
    required={'paired','fork-disabled','missing','corrupt','unreviewed','workflow-unavailable'}
    if (report.get('deployment_commit')!=sha or report.get('oauth_requests')!=0 or report.get('http_requests')!=0
        or report.get('writes')!=0 or len(cases)!=len(required) or {v.get('case') for v in cases}!=required
        or any(v.get('passed') is not True for v in cases)):
        raise ValueError('Final deployment pairing evidence missing or mismatched')


def check_tls_report(report):
    if any(report.get(key) is not True for key in ('native_system_trust','certificate_verification','hostname_verification')):
        raise ValueError('Native strict TLS evidence required')
    responses=report.get('public_gets',[])
    if report.get('oauth_requests')!=0 or len(responses)!=2 or {v.get('host') for v in responses}!={'github.com','api.github.com'}:
        raise ValueError('Fixed unauthenticated HTTPS evidence required')
    if any(v.get('http_status')!=200 and not (v.get('host')=='api.github.com' and v.get('http_status')==403 and v.get('rate_limited') is True) for v in responses):
        raise ValueError('Unexpected public HTTPS result')


def check_trusted_manifests(mac,windows,sha):
    with zipfile.ZipFile(mac) as bundle:
        mac_info=json.loads(bundle.read('Codex 提醒设置.app/Contents/Resources/BUILD-INFO.json'))
        reviewed=json.loads(bundle.read('Codex 提醒设置.app/Contents/Resources/worker/_internal/installer/trusted_files.json'))
    with zipfile.ZipFile(windows) as bundle:
        windows_info=json.loads(bundle.read(WINDOWS_ROOT+'/BUILD-INFO.json'))
        windows_reviewed=json.loads(bundle.read(WINDOWS_ROOT+'/worker/_internal/installer/trusted_files.json'))
    if (mac_info.get('deployment_manifest_commit')!=sha or windows_info.get('deployment_manifest_commit')!=sha
        or reviewed!=windows_reviewed or reviewed!=expected_manifest(ROOT)):
        raise ValueError('Both final platform manifests must match this reviewed release source')
    return hashlib.sha256(json.dumps(reviewed,sort_keys=True).encode()).hexdigest()


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def check_sum(directory,archive,sums):
    value=digest(directory/archive)
    if (directory/sums).read_text(encoding='utf-8-sig').strip()!=value+'  '+archive:
        raise ValueError('Platform archive checksum mismatch: '+archive)
    return value


def check_windows_report(report,sha,archive_sha):
    if report.get('source_commit')!=sha or report.get('archive_sha256')!=archive_sha:
        raise ValueError('Windows report belongs to another source/archive')
    tests=report.get('tests',[])
    if len(tests)!=4 or {item.get('mode') for item in tests}!={'startup','full','cancel','resume'}:
        raise ValueError('Windows startup/full/cancel/resume evidence required')
    if any(item.get('passed') is not True or item.get('forced') is not False for item in tests):
        raise ValueError('All Windows modes must pass without forced termination')
    by_mode={item['mode']:item for item in tests}
    for mode in ('full','resume'):
        if by_mode[mode].get('doneCode')!=0 or by_mode[mode].get('finished') is not True:
            raise ValueError('Windows completion evidence missing')
    if by_mode['resume'].get('sawResume') is not True or by_mode['cancel'].get('finished') is not False:
        raise ValueError('Windows cancel/resume evidence missing')


def inspect_windows(archive,sha,public,logo_sha):
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist())!=len(set(bundle.namelist())):raise ValueError('Duplicate Windows ZIP paths')
        for item in bundle.infolist():
            path=PurePosixPath(item.filename)
            if (not path.parts or path.parts[0]!=WINDOWS_ROOT or path.is_absolute() or '..' in path.parts
                or any(':' in part or '\\' in part for part in path.parts) or stat.S_ISLNK(item.external_attr>>16)):
                raise ValueError('Unsafe Windows ZIP path')
            if ({part.lower() for part in path.parts}&FORBIDDEN or path.name.lower().endswith(('.pem','.key','.local.json','.pdb','.py','.spec','.log'))
                or path.name.lower() in ('config.json','app.local.json','state.local.json')):
                raise ValueError('Private/source file in Windows ZIP')
            data=bundle.read(item)
            if CREDENTIAL.search(data):raise ValueError('Credential-shaped data in Windows ZIP')
            if path.name=='public_app.json':
                expected=WINDOWS_ROOT+'/worker/_internal/installer/public_app.json'
                if path.as_posix()!=expected or validate_public(json.loads(data))!=public:
                    raise ValueError('Unreviewed Windows public App config')
        info=json.loads(bundle.read(WINDOWS_ROOT+'/BUILD-INFO.json'))
        if (info.get('source_commit')!=sha or info.get('architecture')!='x64' or info.get('public_app')!=public
            or info.get('logo_sha256')!=logo_sha):raise ValueError('Windows package identity mismatch')
        if hashlib.sha256(bundle.read(WINDOWS_ROOT+'/ProjectLogo.png')).hexdigest()!=logo_sha:
            raise ValueError('Windows original logo mismatch')
        if validate_public(json.loads(bundle.read(WINDOWS_ROOT+'/worker/_internal/installer/public_app.json')))!=public:
            raise ValueError('Windows bundled App config missing')
    return info


def inspect_windows_exe(executable,archive,report):
    from scripts.windows_payload import inspect_exe
    payload=inspect_exe(executable)
    if report.get('asset')!=WINDOWS_EXE or report.get('manifest_sha256')!=payload['manifest_sha256']:
        raise ValueError('Single EXE identity mismatch')
    required={'embedded-files-match-scanned-package','normal-no-argument-launch-and-close','empty-working-directory','Chinese-and-space-paths',
        'clean-PATH-no-Python-gh-dotnet','first-and-repeat-launch','cache-corruption-repair','prepare-cancel-cleanup-retry',
        'stale-preparation-cleanup','old-ZIP-to-EXE-progress-resume','previous-version-cache-retained','config-and-progress-retained','concurrent-launch',
        'embedded-manifest-corruption-rejected','reparse-point-rejected','bundled-gh'}
    if set(report.get('single_exe_checks',[]))!=required or not report.get('comparison'):
        raise ValueError('Single EXE acceptance evidence missing')
    with zipfile.ZipFile(archive) as bundle:
        expected={name.removeprefix(WINDOWS_ROOT+'/'):bundle.read(name) for name in bundle.namelist() if not name.endswith('/')}
    if set(expected)!={entry[0] for entry in payload['files']}:raise ValueError('EXE/ZIP file list mismatch')
    for name,size,packed,offset,checksum in payload['files']:
        if len(expected[name])!=size or hashlib.sha256(expected[name]).hexdigest()!=checksum:
            raise ValueError('EXE/ZIP file mismatch: '+name)


def assemble(mac,windows,output,sha,public,logo_sha,run_url):
    mac=Path(mac);windows=Path(windows);output=Path(output)
    mac_sha=check_sum(mac,MAC_ZIP,'SHA256SUMS')
    windows_sha=check_sum(windows,WINDOWS_EXE,'WINDOWS-SHA256SUMS')
    mac_report=json.loads((mac/'BUILD-CHECKS.json').read_text())
    if (mac_report.get('source_commit')!=sha or mac_report.get('public_app')!=public
        or mac_report.get('public_app_bundled') is not True or set(mac_report.get('checks',[]))!=MAC_CHECKS
        or mac_report.get('logo_sha256')!=logo_sha):raise ValueError('Mac checks belong to another source/configuration')
    inspect_archive(mac/MAC_ZIP,sha,public)
    windows_report=json.loads((windows/'WINDOWS-BUILD-CHECKS.json').read_text(encoding='utf-8-sig'))
    check_windows_report(windows_report,sha,windows_sha)
    inspect_windows(windows/WINDOWS_ZIP,sha,public,logo_sha)
    inspect_windows_exe(windows/WINDOWS_EXE,windows/WINDOWS_ZIP,windows_report)
    trusted_sha=check_trusted_manifests(mac/MAC_ZIP,windows/WINDOWS_ZIP,sha)
    mac_tls=json.loads((mac/'PUBLIC-TLS-CHECK.json').read_text())
    mac_pairing=json.loads((mac/'DEPLOYMENT-PAIR-CHECKS.json').read_text())
    check_tls_report(mac_tls);check_tls_report(windows_report.get('public_tls_check',{}))
    check_deployment_report(mac_pairing,sha);check_deployment_report(windows_report.get('deployment_pairing',{}),sha)
    output.mkdir(parents=True,exist_ok=False)
    copies=[(mac/MAC_ZIP,MAC_ZIP),(windows/WINDOWS_EXE,WINDOWS_EXE),
            (mac/'SHA256SUMS','MACOS-SHA256SUMS'),(windows/'WINDOWS-SHA256SUMS','WINDOWS-SHA256SUMS'),
            (mac/'BUILD-CHECKS.json','BUILD-CHECKS.json'),(windows/'WINDOWS-BUILD-CHECKS.json','WINDOWS-BUILD-CHECKS.json'),
            (mac/'PUBLIC-TLS-CHECK.json','MACOS-PUBLIC-TLS-CHECK.json'),(mac/'DEPLOYMENT-PAIR-CHECKS.json','MACOS-DEPLOYMENT-PAIR-CHECKS.json')]
    for source,name in copies:shutil.copyfile(source,output/name)
    # Only known synthetic UI captures: durable evidence, never personal account screenshots.
    screenshots=('startup/preparing.png','startup/welcome.png','full/device.png','full/final.png','cancel/final.png','resume/final.png')
    with zipfile.ZipFile(output/WINDOWS_EVIDENCE,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in screenshots:
            path=windows/'windows-evidence'/name;data=path.read_bytes()
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>5*1024*1024:
                raise ValueError('Actual offline PNG capture required: '+name)
            bundle.writestr(name,data)
    (output/'SHA256SUMS').write_text(mac_sha+'  '+MAC_ZIP+'\n'+windows_sha+'  '+WINDOWS_EXE+'\n')
    manifest={'source_commit':sha,'deployment_manifest_commit':sha,'trusted_manifest_sha256':trusted_sha,'run_url':run_url,'public_app':public,'logo_sha256':logo_sha,
              'platforms':{'macos-arm64':{'archive':MAC_ZIP,'sha256':mac_sha,'report':'BUILD-CHECKS.json'},
                           'windows-x64':{'archive':WINDOWS_EXE,'sha256':windows_sha,'report':'WINDOWS-BUILD-CHECKS.json'}},
              'assets':{path.name:digest(path) for path in sorted(output.iterdir())},
              'limits':['macOS not notarized; Windows unsigned','offline fixtures do not prove live multi-account installation']}
    (output/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return [output/name for name in sorted(path.name for path in output.iterdir())]
