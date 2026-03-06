import config

try:
    import psycopg2
except Exception as e:  # pragma: no cover
    raise RuntimeError("reset_db.py requires psycopg2. Install via: pip install -r requirements.txt") from e


TABLES = [
    # Drop children first (or just use CASCADE everywhere).
    "User_Portfolio_Follows",
    "Portfolio_Positions",
    "Portfolio_Comments",
    "Portfolio_Transactions",
    "User_Combinations",
    "User_Stocks",
    "Value_Comments",
    "Raw_Statuses",
    "Target_users",
    "High_quality_users",
    "users",
    "Stocks",
    "System_Meta",
]


def main():
    dsn = (
        f"host={config.PG_HOST} port={config.PG_PORT} dbname={config.PG_DBNAME} "
        f"user={config.PG_USER} password={config.PG_PASSWORD} connect_timeout=10"
    )

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                for t in TABLES:
                    # Don't quote identifiers: our DDL uses unquoted names, which Postgres folds to lower-case.
                    cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE;")
                for ddl in config.SQL_CREATE_TABLES:
                    cur.execute(ddl)
        print("✅ 数据库已重置并重建完成。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
