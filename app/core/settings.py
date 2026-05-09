@field_validator("database_url", mode="before")
@classmethod
def fix_db_url(cls, v: str) -> str:
    # Railway usa postgres:// o postgresql://, asyncpg vuole postgresql+asyncpg://
    if v.startswith("postgres://"):
        v = v.replace("postgres://", "postgresql+asyncpg://", 1)
    elif v.startswith("postgresql://") and "+asyncpg" not in v:
        v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
    return v
