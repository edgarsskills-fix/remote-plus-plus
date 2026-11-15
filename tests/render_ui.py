"""Isolated GTK previews; never reads or changes the real remote host."""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--theme', choices=['dark', 'light'], default='dark')
parser.add_argument('--pages', nargs='+', choices=['overview', 'setup', 'security', 'diagnostics'])
options = parser.parse_args()
options.output.mkdir(parents=True, exist_ok=True)
os.environ['REMOTEPLUS_THEME'] = options.theme
from app import App
from gi.repository import GLib

with tempfile.TemporaryDirectory(prefix='remote-ui-') as tmp:
    library = Path(tmp) / 'capture.so'
    flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gtk4'], text=True).split()
    subprocess.run(['cc', '-shared', '-fPIC', str(ROOT / 'tests/capture-widget.c'), '-o', str(library), *flags], check=True)
    capture = ctypes.CDLL(str(library)).capture_widget
    capture.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    capture.restype = ctypes.c_int
    pointer = ctypes.pythonapi.PyCapsule_GetPointer
    pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    pointer.restype = ctypes.c_void_p
    app = App(preview=True)
    steps = [('overview', 'ready', 1080, 820), ('setup', 'new', 1080, 1000),
             ('security', 'ready', 1080, 820), ('diagnostics', 'ready', 1080, 820),
             ('overview', 'ready', 540, 900), ('setup', 'new', 540, 1080)]
    if options.pages:
        steps = [step for step in steps if step[0] in options.pages]
    failures = []
    started = False

    def next_step():
        if not steps:
            app.quit()
            return False
        page, state, width, height = steps.pop(0)
        os.environ['REMOTEPLUS_PREVIEW_STATE'] = state
        app.window.refresh()
        app.window.select_page(page)
        if page == 'diagnostics':
            app.window.load_diagnostics()
        app.window.set_default_size(width, height)
        app.window.present()

        def shot(attempt=0):
            try:
                path = options.output / f'{page}-{width}-{options.theme}.png'
                ok = capture(pointer(app.window.__gpointer__, None), str(path).encode())
                if not ok and attempt < 5:
                    GLib.timeout_add(300, lambda: shot(attempt + 1))
                    return False
                assert ok, 'No rendered frame'
                # Client-side decorations reserve a few pixels for shadow.
                assert abs(app.window.get_width() - width) <= 20, f'Unexpected width: {app.window.get_width()} instead of {width}'
                print('SCREENSHOT', path, flush=True)
                GLib.timeout_add(200, next_step)
            except Exception:
                failures.append(traceback.format_exc())
                app.quit()
            return False
        GLib.timeout_add(650, shot)
        return False

    def activate(_):
        global started
        if not started:
            started = True
            GLib.timeout_add(500, next_step)

    app.connect_after('activate', activate)
    app.run(['remote-preview'])
    if failures:
        raise SystemExit('\n'.join(failures))
