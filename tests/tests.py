from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

BACKUP_DIR = Path.cwd() / "backups"


def import_file(path: Path) -> Any:
    """
    Import a module from a file path, returning its contents.
    """
    loader = SourceFileLoader(path.name, str(path))
    spec = spec_from_loader(path.name, loader)
    assert spec is not None
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def normalize_container_name(container_name: str) -> str:
    return container_name.replace("_", "-")


# HACK: The filename isn't compatible with `import foo` syntax
db_auto_backup = import_file(Path.cwd() / "db-auto-backup.py")


def test_backup_runs(run_backup: Callable) -> None:
    exit_code, out = run_backup({})
    assert exit_code == 0, out
    assert BACKUP_DIR.is_dir()
    assert sorted(normalize_container_name(f.name) for f in BACKUP_DIR.iterdir()) == [
        "docker-db-auto-backup-mariadb-1.sql",
        "docker-db-auto-backup-mysql-1.sql",
        "docker-db-auto-backup-psql-1.sql",
        "docker-db-auto-backup-redis-1.rdb",
    ]
    for backup_file in BACKUP_DIR.iterdir():
        assert backup_file.stat().st_size > 50
        assert (backup_file.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    "algorithm,extension",
    [("gzip", ".gz"), ("lzma", ".xz"), ("xz", ".xz"), ("bz2", ".bz2"), ("plain", "")],
)
def test_backup_runs_compressed(
    run_backup: Callable, algorithm: str, extension: str
) -> None:
    exit_code, out = run_backup({"COMPRESSION": algorithm})
    assert exit_code == 0, out
    assert BACKUP_DIR.is_dir()
    assert sorted(normalize_container_name(f.name) for f in BACKUP_DIR.iterdir()) == [
        f"docker-db-auto-backup-mariadb-1.sql{extension}",
        f"docker-db-auto-backup-mysql-1.sql{extension}",
        f"docker-db-auto-backup-psql-1.sql{extension}",
        f"docker-db-auto-backup-redis-1.rdb{extension}",
    ]
    for backup_file in BACKUP_DIR.iterdir():
        assert (backup_file.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    "algorithm,extension",
    [("gzip", ".gz"), ("lzma", ".xz"), ("xz", ".xz"), ("bz2", ".bz2"), ("plain", "")],
)
def test_compressed_file_extension(algorithm: str, extension: str) -> None:
    assert db_auto_backup.get_compressed_file_extension(algorithm) == extension


def test_success_hook_url(monkeypatch: Any) -> None:
    monkeypatch.setenv("SUCCESS_HOOK_URL", "https://example.com")
    assert db_auto_backup.get_success_hook_url() == "https://example.com"


def test_healthchecks_success_hook_url(monkeypatch: Any) -> None:
    monkeypatch.setenv("HEALTHCHECKS_ID", "1234")
    assert db_auto_backup.get_success_hook_url() == "https://hc-ping.com/1234"


def test_healthchecks_success_hook_url_custom_host(monkeypatch: Any) -> None:
    monkeypatch.setenv("HEALTHCHECKS_ID", "1234")
    monkeypatch.setenv("HEALTHCHECKS_HOST", "my-healthchecks.com")
    assert db_auto_backup.get_success_hook_url() == "https://my-healthchecks.com/1234"


def test_uptime_kuma_success_hook_url(monkeypatch: Any) -> None:
    monkeypatch.setenv("UPTIME_KUMA_URL", "https://uptime-kuma.com")
    assert db_auto_backup.get_success_hook_url() == "https://uptime-kuma.com"


@pytest.mark.parametrize(
    "tag,name",
    [
        ("postgres:14-alpine", "postgres"),
        ("docker.io/postgres:14-alpine", "postgres"),
        ("ghcr.io/realorangeone/db-auto-backup:latest", "realorangeone/db-auto-backup"),
        ("theorangeone/db-auto-backup:latest:latest", "theorangeone/db-auto-backup"),
        ("lscr.io/linuxserver/mariadb:latest", "linuxserver/mariadb"),
        ("docker.io/library/postgres:14-alpine", "postgres"),
        ("library/postgres:14-alpine", "postgres"),
        ("pgautoupgrade/pgautoupgrade:15-alpine", "pgautoupgrade/pgautoupgrade"),
    ],
)
def test_get_container_names(tag: str, name: str) -> None:
    container = MagicMock()
    container.image.tags = [tag]
    assert db_auto_backup.get_container_names(container) == {name}


@pytest.mark.parametrize(
    "container_name,name",
    [
        ("postgres", "postgres"),
        ("mysql", "mysql"),
        ("mariadb", "mysql"),
        ("linuxserver/mariadb", "mysql"),
        ("tensorchord/pgvecto-rs", "postgres"),
        ("nextcloud/aio-postgresql", "postgres"),
        ("redis", "redis"),
        ("pgautoupgrade/pgautoupgrade", "postgres"),
    ],
)
def test_get_backup_provider(container_name: str, name: str) -> None:
    provider = db_auto_backup.get_backup_provider([container_name])

    assert provider is not None
    assert provider.name == name


def test_custom_backup_provider_patterns(monkeypatch: Any) -> None:
    # Save original backup providers
    original_providers = db_auto_backup.BACKUP_PROVIDERS.copy()

    try:
        # Set custom patterns environment variable
        monkeypatch.setenv(
            "CUSTOM_BACKUP_PROVIDER_POSTGRES_PATTERNS",
            "immich-app/postgres,custom-postgres",
        )

        # Create a copy of the original providers
        test_providers = original_providers.copy()
        db_auto_backup.BACKUP_PROVIDERS = test_providers

        # Run the code that processes environment variables
        for env_var, value in {
            "CUSTOM_BACKUP_PROVIDER_POSTGRES_PATTERNS": "immich-app/postgres,custom-postgres"
        }.items():
            if env_var.startswith("CUSTOM_BACKUP_PROVIDER_") and env_var.endswith(
                "_PATTERNS"
            ):
                provider_name = (
                    env_var.replace("CUSTOM_BACKUP_PROVIDER_", "")
                    .replace("_PATTERNS", "")
                    .lower()
                )
                custom_patterns = [
                    pattern.strip() for pattern in value.split(",") if pattern.strip()
                ]

                for provider in db_auto_backup.BACKUP_PROVIDERS:
                    if provider.name.lower() == provider_name:
                        index = db_auto_backup.BACKUP_PROVIDERS.index(provider)
                        db_auto_backup.BACKUP_PROVIDERS[
                            index
                        ] = db_auto_backup.BackupProvider(
                            name=provider.name,
                            patterns=provider.patterns + custom_patterns,
                            backup_method=provider.backup_method,
                            file_extension=provider.file_extension,
                        )
                        break

        # Test with the new custom pattern
        provider = db_auto_backup.get_backup_provider(["immich-app/postgres"])
        assert provider is not None
        assert provider.name == "postgres"

        provider = db_auto_backup.get_backup_provider(["custom-postgres"])
        assert provider is not None
        assert provider.name == "postgres"
    finally:
        # Restore original backup providers
        db_auto_backup.BACKUP_PROVIDERS = original_providers
