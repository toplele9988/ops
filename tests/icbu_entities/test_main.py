from entities_fixtures import receiver


def test_cli_initializes_separate_entity_db_and_only_binds_temporary_port(tmp_path, monkeypatch):
    captured = []
    class TestServer(receiver.Server):
        def __init__(self, address, *args, **kwargs):
            super().__init__(('127.0.0.1', 0), *args, **kwargs)
            assert self.server_address[1] != 8791
            captured.append(self)
        def serve_forever(self, **kwargs):
            assert self.entities.stats()['total_entities'] == 0
    monkeypatch.setattr(receiver, 'Server', TestServer)
    monkeypatch.setattr(receiver.live, 'POLICY_PATH', '/fictional-unused', raising=False)
    monkeypatch.setattr(receiver.live, 'load_policy', lambda *args: None, raising=False)
    monkeypatch.setattr(receiver.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(receiver.os, 'umask', lambda _: None)
    token = tmp_path / 'test-token'
    token.write_text('fictional-instance-only-' + 'x' * 32)
    token.chmod(0o600)
    assert receiver.main(['--bind', '127.0.0.1', '--port', '18888', '--token-file', str(token),
                          '--db', str(tmp_path / 'inbound.sqlite3'),
                          '--entities-db', str(tmp_path / 'entities.sqlite3'),
                          '--attachments-db', str(tmp_path / 'attachments.sqlite3'),
                          '--attachments-root', str(tmp_path / 'files')]) == 0
    assert captured[0].socket.fileno() == -1
    assert captured[0].entities.path == tmp_path / 'entities.sqlite3'
