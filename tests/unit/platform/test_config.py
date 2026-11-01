"""单一配置 schema 测试（T011，A2）。

pydantic-settings 从 env 与 config.yaml 载入同一 schema；
单机多机同构（G10：配置只有一份 schema，差别只是值）。
"""

import pytest
from pydantic import ValidationError

from dataagent.platform.config import Settings, settings_from_yaml

CONFIG_YAML = """\
env: prod
log_level: DEBUG
batch_size: 64
postgres_url: postgresql+psycopg://app:secret@db:5432/dataagent
object_store_backend: s3
s3_endpoint: http://minio:9000
s3_bucket: artifacts
budget_max_cost: 50
"""


def test_yaml_and_env_load_same_schema(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DATAAGENT_CONFIG", str(cfg))

    from_yaml = Settings()
    assert isinstance(from_yaml, Settings)
    assert from_yaml.batch_size == 64
    assert from_yaml.env == "prod"
    assert from_yaml.object_store_backend == "s3"
    assert from_yaml.budget_max_cost == 50

    monkeypatch.setenv("DATAAGENT_BATCH_SIZE", "128")
    from_env = Settings()
    assert isinstance(from_env, Settings)
    assert from_env.batch_size == 128

    # 同一 schema：都是 Settings，仅值不同
    assert type(from_yaml) is type(from_env) is Settings


def test_settings_from_yaml_returns_settings(tmp_path: object) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    s = settings_from_yaml(cfg)
    assert isinstance(s, Settings)
    assert s.batch_size == 64


def test_env_overrides_yaml(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DATAAGENT_CONFIG", str(cfg))
    monkeypatch.setenv("DATAAGENT_BATCH_SIZE", "16")
    s = Settings()
    assert s.batch_size == 16  # env 优先于 config.yaml


def test_single_schema_g10(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """单机多机同构：只有一份 schema，差别只是值。"""
    cfg_a = tmp_path / "a.yaml"
    cfg_a.write_text("env: dev\nbatch_size: 32\n", encoding="utf-8")
    cfg_b = tmp_path / "b.yaml"
    cfg_b.write_text("env: prod\nbatch_size: 64\n", encoding="utf-8")

    monkeypatch.setenv("DATAAGENT_CONFIG", str(cfg_a))
    a = Settings()
    monkeypatch.setenv("DATAAGENT_CONFIG", str(cfg_b))
    b = Settings()

    assert type(a) is type(b) is Settings
    assert (a.env, a.batch_size) != (b.env, b.batch_size)


def test_defaults_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 env / 无 yaml 时使用默认值（schema 恒定）。"""
    monkeypatch.delenv("DATAAGENT_CONFIG", raising=False)
    s = Settings()
    assert s.batch_size == 32
    assert s.plan_max_model_calls == 8
    assert s.object_store_backend == "local"
    # 专用宿主端口：避开本机其他 Docker 项目（Langfuse 5432/6379 等）
    assert ":15432/" in s.postgres_url
    assert ":16379/" in s.redis_url


def test_unknown_yaml_key_rejected(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """单一 schema：config.yaml 出现未知键即拒绝。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("not_a_field: 1\n", encoding="utf-8")
    monkeypatch.setenv("DATAAGENT_CONFIG", str(cfg))
    with pytest.raises(ValidationError):
        Settings()
