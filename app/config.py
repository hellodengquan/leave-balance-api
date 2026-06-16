from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./leave_balance.db"
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "员工假期余额管理API"

    class Config:
        case_sensitive = True


settings = Settings()
