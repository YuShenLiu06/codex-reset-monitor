"""Windows pipe worker. Console build is intentional; the WPF parent hides it."""
import os
from pathlib import Path
import sys
import threading


def main():
    # Windows defaults to a legacy codepage; both ends explicitly use UTF-8, no BOM.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream is None:raise RuntimeError('PIPE_STREAM_REQUIRED')
        stream.reconfigure(encoding='utf-8',errors='strict',newline='\n')
    arguments=sys.argv[1:]
    cancel_file=None;offline=None
    while arguments:
        name=arguments.pop(0)
        if name=='--cancel-file' and arguments:cancel_file=Path(arguments.pop(0))
        elif name=='--offline-e2e' and arguments:offline=arguments.pop(0)
        else:raise SystemExit('Unsupported worker argument')
    stopped=threading.Event();cancelled=threading.Event()
    if cancel_file:
        def watch():
            while not stopped.wait(0.05):
                if cancel_file.exists():cancelled.set();return
        threading.Thread(target=watch,daemon=True).start()
        # A file signal works for hidden Windows processes. Raise at a Python
        # boundary so cli's KeyboardInterrupt cleanup runs. The parent has a
        # bounded process-tree fallback for an OS/network call that cannot return.
        def trace(frame,event,arg):
            if cancelled.is_set():
                sys.settrace(None)
                raise KeyboardInterrupt()
            return trace
        sys.settrace(trace)
    try:
        from installer.gui_worker import GUI,progress_directory
        from installer.cli import main as install
        gui=GUI();directory=progress_directory()
        args=['--progress',str(directory/'state.local.json'),'--public-config',str(directory/'app.local.json')]
        if offline:
            from windows.offline_fixture import run
            code=run(offline,gui,args)
        else:code=install(args,ui=gui)
        gui.send({'type':'done','code':code})
        return code
    finally:
        sys.settrace(None);stopped.set()


if __name__=='__main__':raise SystemExit(main())
