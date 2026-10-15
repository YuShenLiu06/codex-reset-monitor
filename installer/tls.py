"""Native OS certificate verification for the self-contained desktop workers."""
import json
import sys


def configure_native_trust():
    # Source CLI/Actions keep their existing standard-library TLS behavior.
    if not getattr(sys,'frozen',False):return 'platform-default'
    try:
        import truststore
        truststore.inject_into_ssl()
        import ssl
        context=ssl.create_default_context()
        if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:
            raise RuntimeError('TLS verification required')
    except Exception as exc:
        from installer.github_api import SetupError
        raise SetupError('TLS_TRUST_UNAVAILABLE','system certificate trust could not initialize',
            diagnostics={'phase':'tls_initialization','category':'trust_setup','exception_type':type(exc).__name__}) from None
    return 'native-system'


def bootstrap_tls():
    """Emit a safe IPC failure even if TLS cannot initialize before the CLI."""
    try:configure_native_trust()
    except Exception:
        print(json.dumps({'type':'notice','stage':'stopped','title':'连接组件未能启动',
            'text':'无法启用系统证书校验。请重新下载完整测试包。问题标识：TLS_TRUST_UNAVAILABLE',
            'links':[],'user_code':''},ensure_ascii=False),flush=True)
        print(json.dumps({'type':'done','code':2}),flush=True)
        return False
    return True


def public_tls_check():
    """Fixed public GETs only; never create a device session or use credentials."""
    import ssl
    import urllib.request
    from monitor import NoRedirect
    context=ssl.create_default_context()
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    results=[]
    for host in ('github.com','api.github.com'):
        request=urllib.request.Request('https://'+host+'/',headers={'User-Agent':'CodexResetInstaller-TLS-check'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))
        with opener.open(request,timeout=15) as response:
            if response.status!=200:raise RuntimeError('PUBLIC_TLS_CHECK_FAILED')
            results.append({'host':host,'http_status':response.status})
    print(json.dumps({'native_system_trust':True,'certificate_verification':True,'hostname_verification':True,
        'public_gets':results,'oauth_requests':0}),flush=True)
    return 0
