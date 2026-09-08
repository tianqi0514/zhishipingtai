from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_server_deployment_is_fail_closed_and_uses_pinned_minio() -> None:
    base = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    production = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    script = (ROOT / "scripts/deploy_server.sh").read_text(encoding="utf-8")

    assert "minio/minio@sha256:" in base
    assert "ENVIRONMENT: production" in production
    for variable in (
        "APP_SECRET_KEY",
        "BOOTSTRAP_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "RABBITMQ_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ):
        assert f"${{{variable}:?" in production
        assert variable in script
    assert "compose.production.yaml" in script
    assert "down -v" not in script
    assert "set -x" not in script


def test_production_image_is_built_from_committed_semantica_source() -> None:
    production = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile.production").read_text(encoding="utf-8")
    ignore = (ROOT / "Dockerfile.production.dockerignore").read_text(encoding="utf-8")

    assert "context: .." in production
    assert "dockerfile: semantica-enterprise/Dockerfile.production" in production
    assert "FROM semantica-local" not in dockerfile
    assert "python:3.13-slim-bookworm@sha256:" in dockerfile
    assert "HOME=/app" in dockerfile
    assert '"torch==2.13.0+cpu" "torchvision==0.28.0+cpu"' in dockerfile
    assert dockerfile.index('"torch==2.13.0+cpu"') < dockerfile.index("pip install --constraint /tmp/production-constraints.txt .")
    assert "production-py313-linux-x86_64.txt" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "COPY semantica/semantica ./semantica" in dockerfile
    assert "COPY semantica-enterprise/apps ./apps" in dockerfile
    assert "COPY semantica-enterprise/scripts ./scripts" in dockerfile
    assert "COPY semantica-enterprise/demo ./demo" in dockerfile
    assert "!semantica/semantica/**" in ignore
    assert "!semantica-enterprise/constraints/**" in ignore
    assert "!semantica-enterprise/scripts/**" in ignore
    assert "!semantica-enterprise/demo/**" in ignore


def test_production_build_mirrors_are_explicit_and_apply_to_api_and_asr() -> None:
    production = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    asr_dockerfile = (ROOT / "Dockerfile.asr").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert production.count("PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}") == 2
    assert production.count(
        "PYTORCH_CPU_INDEX_URL: ${PYTORCH_CPU_INDEX_URL:-https://download.pytorch.org/whl/cpu}"
    ) == 2
    assert "ARG PIP_INDEX_URL=https://pypi.org/simple" in asr_dockerfile
    assert "ARG PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu" in asr_dockerfile
    assert 'pip install --index-url "${PYTORCH_CPU_INDEX_URL}"' in asr_dockerfile
    assert "PIP_INDEX_URL=https://pypi.org/simple" in env_example
    assert "PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu" in env_example


def test_local_development_image_contains_demo_preflight_assets() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY scripts /app/scripts" in dockerfile
    assert "COPY demo /app/demo" in dockerfile
    assert "scripts" not in ignore
    assert "demo" not in ignore


def test_manual_deployment_docs_do_not_rotate_existing_file_secrets() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")

    for content in (readme, deployment):
        assert "! -e deploy/secrets/kimi_api_key" in content
        assert "! -s deploy/secrets/agent_service_secret" in content
        assert "openssl rand -hex 32 > deploy/secrets/agent_service_secret" in content
    assert "cd ..\nexport BOOTSTRAP_ADMIN_PASSWORD" not in readme


def test_server_deployment_closes_the_demo_compose_and_health_loop() -> None:
    script = (ROOT / "scripts/deploy_server.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT.parent / "scripts/deploy.sh").read_text(encoding="utf-8")
    demo = (ROOT / "compose.guolian-demo.yaml").read_text(encoding="utf-8")
    preflight = (ROOT / "scripts/demo/preflight_guolian_demo.sh").read_text(encoding="utf-8")

    assert "GUOLIAN_DEMO_ENABLED" in script
    assert "compose.guolian-demo.yaml" in script
    assert "--profile demo" in script
    assert "--wait-timeout" in script
    assert "dotenv/URL 安全字符" in script
    assert "! -s deploy/secrets/agent_service_secret" in script
    assert ". ./.env" not in script
    assert "demo-stop" in script
    assert '"${base_compose[@]}" up -d --no-deps --force-recreate --wait' in script
    assert "立即撤销演示私网主机白名单" in (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "不得静默轮换" in bootstrap
    assert "不会修改已有对象存储凭据" in bootstrap
    assert "rm -f guolian-demo-postgres guolian-demo-mysql source-fixture" in bootstrap
    assert "未创建任何配置文件" in bootstrap
    assert bootstrap.index("未创建任何配置文件") < bootstrap.index('cp "$ENV_EXAMPLE" "$ENV_FILE"')
    assert "长度至少需要 12 个字符" in script
    assert "长度至少需要 12 个字符" in bootstrap
    assert "is_placeholder_value" in script
    assert "is_placeholder_value" in bootstrap
    assert "GUOLIAN_DEMO_POSTGRES_PUBLISHED_PORT" in demo
    assert "GUOLIAN_DEMO_MYSQL_PUBLISHED_PORT" in demo
    assert "GUOLIAN_DEMO_POSTGRES_DATABASE" in demo
    assert "GUOLIAN_DEMO_MYSQL_DATABASE" in demo
    assert "restart: unless-stopped" in demo
    allowlist_lines = [
        line.strip() for line in demo.splitlines()
        if line.strip().startswith("SOURCE_PRIVATE_HOST_ALLOWLIST:")
    ]
    assert len(allowlist_lines) == 2
    for line in allowlist_lines:
        allowed_hosts = line.split(":", 1)[1].strip().split(",")
        assert set(allowed_hosts) == {
            "source-fixture", "minio", "guolian-demo-postgres", "guolian-demo-mysql",
        }
        assert "postgres" not in allowed_hosts
        assert "structured-postgres" not in allowed_hosts
        assert "structured-mysql" not in allowed_hosts
    assert "docker compose exec -T" in preflight
    assert "/app/scripts/demo/verify_guolian_demo.py" in preflight
    assert "compose.production.yaml" in bootstrap
    assert "compose.guolian-demo.yaml" in bootstrap
    assert "GUOLIAN_DEMO_ENABLED" in bootstrap


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_server_deployment_script_runs_required_variable_validation(tmp_path: Path) -> None:
    project = tmp_path / "semantica-enterprise"
    scripts = project / "scripts"
    secrets = project / "deploy" / "secrets"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    secrets.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(ROOT / "scripts/deploy_server.sh", scripts / "deploy_server.sh")
    (secrets / "agent_service_secret").write_text("fixture-agent-secret", encoding="utf-8")
    (secrets / "kimi_api_key").write_text("", encoding="utf-8")
    _write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "APP_SECRET_KEY": "a" * 32,
            "BOOTSTRAP_ADMIN_PASSWORD": "b" * 16,
            "POSTGRES_PASSWORD": "c" * 24,
            "RABBITMQ_PASSWORD": "d" * 24,
            "MINIO_ROOT_USER": "demo-admin",
            "MINIO_ROOT_PASSWORD": "e" * 24,
        }
    )
    result = subprocess.run(
        [str(scripts / "deploy_server.sh"), "config"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "bad substitution" not in result.stderr.lower()


def test_server_deployment_rejects_shipped_middleware_placeholder(tmp_path: Path) -> None:
    project = tmp_path / "semantica-enterprise"
    scripts = project / "scripts"
    secrets = project / "deploy" / "secrets"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    secrets.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(ROOT / "scripts/deploy_server.sh", scripts / "deploy_server.sh")
    (secrets / "agent_service_secret").write_text("fixture-agent-secret", encoding="utf-8")
    (secrets / "kimi_api_key").write_text("", encoding="utf-8")
    _write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "APP_SECRET_KEY": "a" * 32,
            "BOOTSTRAP_ADMIN_PASSWORD": "b" * 16,
            "POSTGRES_PASSWORD": "replace-with-a-strong-password",
            "RABBITMQ_PASSWORD": "d" * 24,
            "MINIO_ROOT_USER": "demo-admin",
            "MINIO_ROOT_PASSWORD": "e" * 24,
        }
    )
    result = subprocess.run(
        [str(scripts / "deploy_server.sh"), "config"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "示例/开发值" in result.stderr


def test_root_bootstrap_rejects_invalid_admin_before_creating_env(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    project = repo / "semantica-enterprise"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    project.mkdir()
    fake_bin.mkdir()
    shutil.copy2(ROOT.parent / "scripts/deploy.sh", scripts / "deploy.sh")
    (project / ".env.example").write_text("APP_SECRET_KEY=replace-with-a-long-random-secret\n", encoding="utf-8")
    _write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "openssl", "#!/usr/bin/env bash\nexit 0\n")

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "BOOTSTRAP_ADMIN_PASSWORD": "invalid@password",
            "SKIP_BUILD": "1",
        }
    )
    result = subprocess.run(
        [str(scripts / "deploy.sh")],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "dotenv/URL" in result.stderr
    assert not (project / ".env").exists()
