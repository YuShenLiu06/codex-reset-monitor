"""Frozen worker entry, including a no-network first-page smoke check for CI."""
import sys

if sys.argv[1:]==['--smoke-test']:
    import io
    import json
    from pathlib import Path
    import tempfile
    from reset_events import identity
    assert identity("bundled-check").startswith("reset:")
    from installer.cli import main as install, manifest
    from installer.gui_worker import GUI
    from installer.resources import read_bundled
    public=read_bundled('public_app.json')
    if public is not None:
        from installer.registration import public_config
        value=json.loads(public)
        assert set(value)=={'version','client_id','app_slug'} and value['version']==1
        assert value==public_config(value['client_id'],value['app_slug'])
    assert '.github/workflows/release.yml' in manifest()
    output=io.StringIO()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        code=install(['--progress',str(root/'state.local.json'),'--public-config',str(root/'app.local.json')],
                     ui=GUI(io.StringIO('{"id":1,"value":"cancel"}\n'),output))
        messages=[json.loads(line) for line in output.getvalue().splitlines()]
        expected='准备连接 GitHub' if public is None else '设置放在哪里？'
        assert code==2 and any(m.get('title')==expected for m in messages)
        assert not list(root.iterdir()), 'Smoke check wrote installation state'
    print('Frozen worker: bundled manifest, reviewed public configuration and safe cancellation passed')
else:
    from installer.gui_worker import main
    raise SystemExit(main())
