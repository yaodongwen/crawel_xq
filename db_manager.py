import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import config

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "PostgreSQL backend requires psycopg2. Install dependencies via: pip install -r requirements.txt"
    ) from e


class DBManager:
    def __init__(self):
        # DSN format matches psycopg2.connect(**config) in test_db.py.
        # Keep timeouts short to fail fast when host/pg_hba/password is wrong.
        self._dsn = (
            f"host={config.PG_HOST} port={config.PG_PORT} dbname={config.PG_DBNAME} "
            f"user={config.PG_USER} password={config.PG_PASSWORD} connect_timeout=10"
        )

        self._verify_connection()

        minc = int(getattr(config, "PG_POOL_MIN", 1))
        maxc = int(getattr(config, "PG_POOL_MAX", 10))
        if minc < 1:
            minc = 1
        if maxc < minc:
            maxc = minc
        self._pool = pool.ThreadedConnectionPool(minc, maxc, self._dsn)

        self.init_tables()

    def _verify_connection(self):
        """Fail fast with a clear error message before pool initialization."""
        safe = f"{config.PG_USER}@{config.PG_HOST}:{config.PG_PORT}/{config.PG_DBNAME}"
        try:
            conn = psycopg2.connect(self._dsn)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(f"PostgreSQL connection failed: {safe} ({e})") from e

    @contextmanager
    def _get_conn(self):
        conn = None
        try:
            conn = self._pool.getconn()
            yield conn
        finally:
            try:
                if conn is not None:
                    self._pool.putconn(conn)
            except Exception:
                pass

    def init_tables(self):
        try:
            with self._get_conn() as conn:
                with conn:
                    with conn.cursor() as cur:
                        for ddl in config.SQL_CREATE_TABLES:
                            cur.execute(ddl)
        except Exception as e:
            raise RuntimeError(f"[DB Init Error] {e}") from e

    def execute_many_safe(self, sql, data, retries=3):
        if not data:
            return
        for attempt in range(int(retries)):
            try:
                with self._get_conn() as conn:
                    with conn:
                        with conn.cursor() as cur:
                            cur.executemany(sql, data)
                return
            except (psycopg2.errors.DeadlockDetected, psycopg2.errors.SerializationFailure):
                if attempt >= retries - 1:
                    raise
                time.sleep(0.5 * (attempt + 1))

    def execute_one_safe(self, sql, params=(), retries=3):
        for attempt in range(int(retries)):
            try:
                with self._get_conn() as conn:
                    with conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, params)
                return
            except (psycopg2.errors.DeadlockDetected, psycopg2.errors.SerializationFailure):
                if attempt >= retries - 1:
                    raise
                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                raise RuntimeError(f"DB execute failed: {e}\nSQL: {sql}") from e

    def get_existing_user_ids(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT User_Id FROM users")
                return {row[0] for row in cur.fetchall()}

    def get_existing_target_ids(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT User_Id FROM Target_users")
                return {row[0] for row in cur.fetchall()}

    def upsert_stocks(self, rows):
        """Insert/update stock dimension rows.

        rows: list[(symbol, name, market)]
        """
        if not rows:
            return
        # Deduplicate by symbol to reduce DB churn.
        dedup = {}
        for symbol, name, market in rows:
            sym = (str(symbol).strip() if symbol is not None else "")
            if not sym:
                continue
            dedup[sym] = (sym, (name or None), (market or None))
        payload = list(dedup.values())
        if not payload:
            return
        self.execute_many_safe(
            """
            INSERT INTO Stocks (Stock_Symbol, Stock_Name, Market)
            VALUES (%s,%s,%s)
            ON CONFLICT (Stock_Symbol) DO UPDATE SET
                Stock_Name = COALESCE(Stocks.Stock_Name, EXCLUDED.Stock_Name),
                Market = COALESCE(Stocks.Market, EXCLUDED.Market)
            """,
            payload,
        )

    def get_stock_id_map(self, symbols):
        if not symbols:
            return {}
        uniq = []
        seen = set()
        for s in symbols:
            sym = (str(s).strip() if s is not None else "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            uniq.append(sym)
        if not uniq:
            return {}
        placeholders = ",".join(["%s"] * len(uniq))
        sql = f"SELECT Stock_Symbol, Stock_Id FROM Stocks WHERE Stock_Symbol IN ({placeholders})"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(uniq))
                return {row[0]: row[1] for row in cur.fetchall()}

    def get_portfolio_last_crawled(self, symbol):
        if not symbol:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT Portfolio_Last_Crawled FROM User_Combinations WHERE Symbol = %s",
                    (symbol,),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    def should_skip_portfolio(self, symbol, cache_hours):
        last = self.get_portfolio_last_crawled(symbol)
        if not last:
            return False, None
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False, last
        cutoff = datetime.now() - timedelta(hours=cache_hours)
        return last_dt >= cutoff, last

    def get_comb_ids_by_symbols(self, symbols):
        if not symbols:
            return {}
        placeholders = ",".join(["%s"] * len(symbols))
        sql = f"SELECT Comb_Id, Symbol FROM User_Combinations WHERE Symbol IN ({placeholders})"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(symbols))
                return {row[1]: row[0] for row in cur.fetchall()}

    def get_pending_tasks(self, table_name, limit=None):
        cutoff = (datetime.now() - timedelta(days=config.CACHE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

        sql = f"SELECT * FROM {table_name} WHERE Last_Updated IS NULL OR Last_Updated < %s"
        params = [cutoff]
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))

        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchall()

    def update_task_status(self, user_id, table_name):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.execute_one_safe(
            f"UPDATE {table_name} SET Last_Updated = %s WHERE User_Id = %s",
            (now, user_id),
        )

    def get_unanalyzed_raw_data(self, limit=50):
        sql = "SELECT * FROM Raw_Statuses WHERE Is_Analyzed = 0 LIMIT %s"
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (int(limit),))
                return cur.fetchall()

    def mark_raw_as_analyzed(self, status_id, status_code=1):
        self.execute_one_safe(
            "UPDATE Raw_Statuses SET Is_Analyzed = %s WHERE Status_Id = %s",
            (status_code, status_id),
        )

    def get_unanalyzed_count(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM Raw_Statuses WHERE Is_Analyzed = 0")
                return cur.fetchone()[0]

    def get_user_comments_last_crawled(self, user_id):
        if not user_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT Value FROM System_Meta WHERE Key = %s",
                    (f"COMMENTS_LAST_CRAWLED_{user_id}",),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    def set_user_comments_last_crawled(self, user_id, ts_str):
        if not user_id or not ts_str:
            return
        self.execute_one_safe(
            """
            INSERT INTO System_Meta (Key, Value)
            VALUES (%s, %s)
            ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value
            """,
            (f"COMMENTS_LAST_CRAWLED_{user_id}", ts_str),
        )

    def get_target_count(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM Target_users")
                return cur.fetchone()[0]

    def get_next_source_user(self):
        sql = """
            SELECT User_Id, User_Name FROM High_quality_users
            WHERE User_Id NOT IN (
                SELECT replace(Key, 'SCANNED_FOLLOWERS_', '')
                FROM System_Meta
                WHERE Key LIKE 'SCANNED_FOLLOWERS_%'
            )
            ORDER BY Followers_Count DESC
            LIMIT 1
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                return cur.fetchone()

    def mark_user_as_scanned(self, user_id):
        self.execute_one_safe(
            """
            INSERT INTO System_Meta (Key, Value)
            VALUES (%s, %s)
            ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value
            """,
            (f"SCANNED_FOLLOWERS_{user_id}", datetime.now().strftime("%Y-%m-%d")),
        )

    def is_user_scanned(self, user_id):
        sql = "SELECT 1 FROM System_Meta WHERE Key = %s"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (f"SCANNED_FOLLOWERS_{user_id}",))
                return cur.fetchone() is not None

    def get_total_users_count(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM users")
                return cur.fetchone()[0]
