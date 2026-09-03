import base64

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wallet:wallet@localhost:5432/wallet"
    test_database_url: str = "postgresql+asyncpg://wallet:wallet@localhost:5432/wallet_test"

    jwt_secret: str = "CHANGE_ME_dev_only_secret_do_not_ship"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    payload_aes_key: str = "8pQ7yZ1kR4nV6bXcW2eT9uY0iO3aS5dF7gH8jK1lM4o="
    demo_mode: bool = True

    @field_validator("database_url", "jwt_secret")
    @classmethod
    def _must_not_be_blank(cls, v: str, info: ValidationInfo) -> str:
        """An env var set to "" overrides the default with an empty string.

        These two are typed `str`, so "" is structurally valid and would pass
        silently -- and an empty JWT_SECRET means every token is signed with an
        empty key, i.e. anyone can forge one. Unset is fine (the default
        applies); set-but-blank is a deployment mistake and must fail loudly.
        """
        if not v.strip():
            raise ValueError(f"{info.field_name.upper()} is set but empty")
        return v

    @field_validator("payload_aes_key")
    @classmethod
    def _must_be_aes256(cls, v: str) -> str:
        if len(base64.b64decode(v)) != 32:
            raise ValueError("PAYLOAD_AES_KEY must decode to exactly 32 bytes (AES-256)")
        return v

    @property
    def aes_key(self) -> bytes:
        return base64.b64decode(self.payload_aes_key)


settings = Settings()
