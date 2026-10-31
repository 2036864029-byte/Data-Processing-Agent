"""单一配置 schema（pydantic-settings）。

G10：单机多机同构——配置只有一份 schema，差别只是值。
env 优先于 config.yaml，config.yaml 优先于默认值。
"""

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

CONFIG_YAML_DEFAULT = "config.yaml"
CONFIG_ENV_NAME = "DATAAGENT_CONFIG"


class YamlConfigSource(PydanticBaseSettingsSource):
    """从 config.yaml 读取值的 settings 源（路径由 DATAAGENT_CONFIG 指定，默认 config.yaml）。"""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._path = Path(os.environ.get(CONFIG_ENV_NAME, CONFIG_YAML_DEFAULT))

    def _read(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        data = self._read()
        if field_name in data:
            return data[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        # 原样返回全部 yaml 键：未知键由 extra="forbid" 拒绝（单一 schema 强约束）
        return self._read()


class Settings(BaseSettings):
    """DataAgent 单一配置 schema。字段名 == config.yaml 键名 == DATAAGENT_<字段名> 环境变量。"""

    model_config = SettingsConfigDict(
        env_prefix="DATAAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    env: str = "dev"
    log_level: str = "INFO"

    # 存储与队列（A4 落点）
    postgres_url: str = "postgresql+psycopg://dataagent:dataagent@localhost:5432/dataagent"
    redis_url: str = "redis://localhost:6379/0"
    object_store_backend: Literal["local", "s3"] = "local"
    local_store_root: Path = Path("var/objects")
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "dataagent"

    # 批与并发（宪法 IV / research D5）
    batch_size: int = 32
    worker_concurrency: int = 4

    # 预算默认值（data-model.md Task.budget 三层）
    budget_max_cost: int = 100
    budget_max_attempts: int = 50
    budget_max_duration_s: float = 3600.0

    # Plan 守卫（data-model.md Plan.guards）
    plan_max_model_calls: int = 8
    plan_max_replans: int = 3
    plan_max_wall_time_s: int = 3600

    # API / 交付
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 优先级：显式构造 > env > .env > config.yaml > 默认值
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls),
        )


def settings_from_yaml(path: str | Path) -> Settings:
    """从指定 config.yaml 载入设置（env 与显式参数仍优先）。

    通过临时设置 DATAAGENT_CONFIG 指向该文件，载入后还原。
    """
    previous = os.environ.get(CONFIG_ENV_NAME)
    os.environ[CONFIG_ENV_NAME] = str(path)
    try:
        return Settings()
    finally:
        if previous is None:
            os.environ.pop(CONFIG_ENV_NAME, None)
        else:
            os.environ[CONFIG_ENV_NAME] = previous
