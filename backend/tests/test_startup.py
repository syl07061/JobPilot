import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('jobpilot_startup', ROOT/'scripts/start_jobpilot.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def settings(**overrides):
    return SimpleNamespace(**{'backend_port':8200, 'chrome_cdp_url':'http://127.0.0.1:9222', **overrides})


def forbid(*args, **kwargs):
    raise AssertionError('Unexpected mutation or process start')


def test_reuse_running_services_without_new_processes_or_browser_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, 'listening', lambda _: True)
    monkeypatch.setattr(startup, 'chrome_info', lambda _: {'Browser':'Chrome/1'})
    monkeypatch.setattr(startup, 'healthy_backend', lambda _: True)
    monkeypatch.setattr(startup, 'healthy_frontend', lambda _: True)
    launcher = startup.Launcher(settings(), tmp_path)
    monkeypatch.setattr(launcher, 'spawn', forbid)
    monkeypatch.setattr(startup, 'open_pages', forbid)
    assert launcher.start(no_open=True)
    assert launcher.owned == []
    launcher.close()


def test_foreign_port_is_rejected_before_starting_any_process(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, 'listening', lambda _: True)
    monkeypatch.setattr(startup, 'chrome_info', lambda _: {'Browser':'Chrome/1'})
    monkeypatch.setattr(startup, 'healthy_backend', lambda _: False)
    launcher = startup.Launcher(settings(), tmp_path)
    monkeypatch.setattr(launcher, 'spawn', forbid)
    with pytest.raises(startup.StartupError, match='8200'):
        launcher.start()
    assert launcher.owned == []


def test_check_only_never_starts_missing_services(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, 'chrome_info', lambda _: None)
    monkeypatch.setattr(startup, 'healthy_backend', lambda _: False)
    monkeypatch.setattr(startup, 'healthy_frontend', lambda _: False)
    monkeypatch.setattr(startup, 'open_pages', forbid)
    launcher = startup.Launcher(settings(), tmp_path)
    monkeypatch.setattr(launcher, 'spawn', forbid)
    assert launcher.start(check_only=True) is False


@pytest.mark.parametrize('value', ['https://example.com:9222', 'http://user:secret@127.0.0.1:9222', 'http://127.0.0.1:9222/?token=x'])
def test_nonlocal_or_credential_cdp_config_is_rejected(value, tmp_path):
    with pytest.raises(startup.StartupError):
        startup.Launcher(settings(chrome_cdp_url=value), tmp_path)


def test_same_ports_are_rejected(tmp_path):
    with pytest.raises(startup.StartupError):
        startup.Launcher(settings(backend_port=5173), tmp_path)


def test_existing_boss_and_ui_tabs_are_kept(monkeypatch):
    monkeypatch.setattr(startup, 'chrome_info', lambda _: {'Browser':'Chrome/1'})
    monkeypatch.setattr(startup, 'get_json', lambda _: [
        {'type':'page','url':'https://www.zhipin.com/web/geek/jobs?query=fixture'},
        {'type':'page','url':'http://127.0.0.1:5173/'}])
    import websockets.sync.client
    monkeypatch.setattr(websockets.sync.client, 'connect', forbid)
    startup.open_pages('http://127.0.0.1:9222', 'http://127.0.0.1:5173')


def test_only_missing_homepage_is_created_no_navigation_or_collection(monkeypatch):
    monkeypatch.setattr(startup, 'chrome_info', lambda _: {'webSocketDebuggerUrl':'ws://127.0.0.1:9222/devtools/browser/test'})
    monkeypatch.setattr(startup, 'get_json', lambda _: [{'type':'page','url':'http://127.0.0.1:5173/'}])
    commands = []
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def send(self, text): commands.append(json.loads(text))
        def recv(self, timeout): return json.dumps({'id':commands[-1]['id'],'result':{'targetId':'fixture'}})
    import websockets.sync.client
    monkeypatch.setattr(websockets.sync.client, 'connect', lambda *args, **kwargs: Channel())
    startup.open_pages('http://127.0.0.1:9222', 'http://127.0.0.1:5173')
    assert commands == [{'id':1,'method':'Target.createTarget','params':{'url':'https://www.zhipin.com/'}}]


def test_cleanup_terminates_only_owned_live_processes(tmp_path):
    calls = []
    class Process:
        def __init__(self, name, exited=False): self.name, self.exited = name, exited
        def poll(self): return 0 if self.exited else None
        def terminate(self): calls.append(('terminate',self.name)); self.exited=True
        def wait(self, timeout): calls.append(('wait',self.name))
    launcher = startup.Launcher(settings(), tmp_path)
    launcher.owned=[('backend', Process('backend')), ('frontend', Process('frontend')), ('old',Process('old',True))]
    launcher.close()
    assert calls == [('terminate','frontend'),('wait','frontend'),('terminate','backend'),('wait','backend')]
