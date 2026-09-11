from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_remote_middleware_never_shares_online_data_or_ports():
    config = yaml.safe_load((ROOT / 'deploy/compose.dev-middleware.yaml').read_text())
    assert config['name'] == 'chuanshen-local-dev'
    assert set(config['services']) == {'postgres', 'redis', 'rabbitmq', 'minio', 'opensearch', 'qdrant', 'falkordb'}
    for name, service in config['services'].items():
        assert service['restart'] == 'unless-stopped'
        assert service['healthcheck']
        assert all(port.startswith('127.0.0.1:2') for port in service['ports'])
        assert all(volume.startswith('./data/' + name + ':') for volume in service['volumes'])
    assert config['services']['postgres']['environment']['POSTGRES_DB'] == 'semantica_dev'
    assert config['services']['rabbitmq']['environment']['RABBITMQ_DEFAULT_VHOST'] == 'semantica_dev'


def test_tunnel_pins_host_and_has_no_broad_remote_access():
    script = (ROOT / 'scripts/development/tunnel.sh').read_text()
    assert 'StrictHostKeyChecking=yes' in script
    assert 'UserKnownHostsFile=' in script
    assert 'ExitOnForwardFailure=yes' in script
    assert 'ServerAliveCountMax=3' in script
    assert script.count('-L 0.0.0.0:') == 7
    assert 'root@' not in script
    policy = (ROOT / 'deploy/sshd-dev-tunnel.conf').read_text()
    assert 'Match User chuanshen-dev-tunnel' in policy
    assert 'AllowTcpForwarding local' in policy
    assert 'ForceCommand /usr/sbin/nologin' in policy
    assert 'PasswordAuthentication no' in policy


def test_local_override_disables_heavy_default_services_and_base_endpoints():
    config = (ROOT / 'compose.remote-dev.yaml').read_text()
    assert config.count('profiles: [local-middleware]') == 7
    assert 'profiles: [local-media]' in config
    assert config.count('environment: !reset {}') == 3
    assert 'depends_on: !override' in config
    assert '--concurrency=1' in config
    assert '--max-memory-per-child=524288' in config
    assert 'read_only: true' in config
    assert 'ports:' not in config  # Tunnel sockets are not published to the LAN.


def test_generated_credentials_cannot_enter_git_or_build_context():
    assert '.local-dev/' in (ROOT / '.gitignore').read_text()
    assert '.local-dev' in (ROOT / '.dockerignore').read_text()
    script = (ROOT / 'scripts/development/prepare_remote_env.mjs').read_text()
    assert "flag: 'wx', mode: 0o600" in script
    assert 'Refusing to replace existing' in script
    assert 'randomBytes(24)' in script
    assert 'ssh-keyscan' not in script


def test_restart_helper_does_not_delete_volumes_or_start_online_services():
    script = (ROOT / 'scripts/development/remote_dev.sh').read_text()
    assert 'compose.remote-dev.yaml' in script
    assert '--no-deps --wait' in script
    assert 'down' not in script
    assert 'rm ' not in script
    assert 'compose.production.yaml' not in script
