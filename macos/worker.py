"""Frozen worker entry, including a no-network first-page smoke check for CI."""
import sys

if sys.argv[1:]==['--smoke-test']:
    import io
    import json
    from pathlib import Path
    import tempfile
    from installer.cli import main as install, manifest
    from installer.gui_worker import GUI
    from installer.resources import read_bundled
    assert read_bundled('public_app.json') is None, 'Public preview must not bundle App configuration'
    assert '.github/workflows/release.yml' in manifest()
    output=io.StringIO()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        code=install(['--progress',str(root/'state.local.json'),'--public-config',str(root/'app.local.json')],
                     ui=GUI(io.StringIO('{"id":1,"value":"cancel"}\n'),output))
        messages=[json.loads(line) for line in output.getvalue().splitlines()]
        assert code==2 and any(m.get('title')=='准备连接 GitHub' for m in messages)
        assert not list(root.iterdir()), 'Smoke check wrote installation state'
    print('Frozen worker: bundled manifest, missing-App initialization and safe cancellation passed')
else:
    from installer.gui_worker import main
    raise SystemExit(main())
