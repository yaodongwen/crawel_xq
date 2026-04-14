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
                        # Migrations for existing tables (CREATE TABLE IF NOT EXISTS won't add columns).
                        # High_quality_users follow-scan marker.
                        cur.execute(
                            "ALTER TABLE High_quality_users ADD COLUMN IF NOT EXISTS Get_Follow INTEGER DEFAULT 0"
                        )
                        cur.execute("UPDATE High_quality_users SET Get_Follow = 0 WHERE Get_Follow IS NULL")
        except Exception as e:
            raise RuntimeError(f"[DB Init Error] {e}") from e

    def ensure_high_quality_user(self, user_id, user_name=None, get_follow=0):
        """Ensure a row exists in High_quality_users (used for seed bootstrapping)."""
        if not user_id:
            return
        try:
            gf = int(get_follow)
        except Exception:
            gf = 0
        self.execute_one_safe(
            """
            INSERT INTO High_quality_users (User_Id, User_Name, Get_Follow, Last_Updated)
            VALUES (%s,%s,%s,NULL)
            ON CONFLICT (User_Id) DO NOTHING
            """,
            (int(user_id), user_name, gf),
        )

    def get_next_follow_scan_user(self):
        """Pick next High_quality_users row whose follow list has not been scanned."""
        sql = """
            SELECT User_Id, User_Name
            FROM High_quality_users
            WHERE COALESCE(Get_Follow, 0) = 0
            ORDER BY COALESCE(Followers_Count, 0) DESC
            LIMIT 1
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                return cur.fetchone()

    def mark_high_quality_follow_scanned(self, user_id):
        if not user_id:
            return
        self.execute_one_safe(
            "UPDATE High_quality_users SET Get_Follow = 1 WHERE User_Id = %s",
            (int(user_id),),
        )

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

    def execute_many_count_safe(self, sql, data, retries=3):
        """Execute many and return affected rows (best-effort).

        Useful for `INSERT ... ON CONFLICT DO NOTHING` where the inserted row count matters.
        Note: psycopg2 may report -1 in some cases; we normalize to 0.
        """
        if not data:
            return 0
        for attempt in range(int(retries)):
            try:
                with self._get_conn() as conn:
                    with conn:
                        with conn.cursor() as cur:
                            cur.executemany(sql, data)
                            try:
                                rc = int(cur.rowcount or 0)
                            except Exception:
                                rc = 0
                            return rc if rc > 0 else 0
            except (psycopg2.errors.DeadlockDetected, psycopg2.errors.SerializationFailure):
                if attempt >= retries - 1:
                    raise
                time.sleep(0.5 * (attempt + 1))
        return 0

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

    def get_portfolio_last_crawled_map(self, symbols):
        """Bulk fetch Portfolio_Last_Crawled for multiple symbols.

        Returns: dict[symbol] -> last_crawled_str|None (only includes symbols that exist in DB)
        """
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
        sql = f"SELECT Symbol, Portfolio_Last_Crawled FROM User_Combinations WHERE Symbol IN ({placeholders})"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(uniq))
                return {row[0]: (row[1] if row and len(row) > 1 else None) for row in cur.fetchall()}

    def should_skip_portfolios(self, symbols, cache_hours):
        """Bulk skip decision for portfolio detail crawling.

        Returns: (skip_set, last_map)
          - skip_set: set(symbol) that are fresh within cache_hours
          - last_map: dict(symbol)->last_crawled_str|None for existing symbols
        """
        last_map = self.get_portfolio_last_crawled_map(symbols)
        if not last_map:
            return set(), {}
        try:
            h = float(cache_hours)
        except Exception:
            h = 0.0
        if h <= 0:
            return set(), last_map
        cutoff = datetime.now() - timedelta(hours=h)
        skip = set()
        for sym, last in last_map.items():
            if not last:
                continue
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if last_dt >= cutoff:
                skip.add(sym)
        return skip, last_map

    def get_comb_ids_by_symbols(self, symbols):
        if not symbols:
            return {}
        placeholders = ",".join(["%s"] * len(symbols))
        sql = f"SELECT Comb_Id, Symbol FROM User_Combinations WHERE Symbol IN ({placeholders})"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(symbols))
                return {row[1]: row[0] for row in cur.fetchall()}

    def delete_portfolio_positions_by_comb_ids(self, comb_ids):
        if not comb_ids:
            return
        uniq = []
        seen = set()
        for x in comb_ids:
            try:
                cid = int(x)
            except Exception:
                continue
            if cid <= 0 or cid in seen:
                continue
            seen.add(cid)
            uniq.append(cid)
        if not uniq:
            return
        placeholders = ",".join(["%s"] * len(uniq))
        sql = f"DELETE FROM Portfolio_Positions WHERE Comb_Id IN ({placeholders})"
        self.execute_one_safe(sql, tuple(uniq))

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

    def delete_raw_statuses_by_ids(self, status_ids):
        if not status_ids:
            return 0
        ids = []
        seen = set()
        for x in status_ids:
            try:
                sid = int(x)
            except Exception:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            ids.append(sid)
        if not ids:
            return 0
        placeholders = ",".join(["%s"] * len(ids))
        sql = f"DELETE FROM Raw_Statuses WHERE Status_Id IN ({placeholders})"
        with self._get_conn() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(ids))
                    try:
                        return int(cur.rowcount or 0)
                    except Exception:
                        return 0

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

    # === Raw_Statuses helpers (resume / avoid re-crawl) ===

    def get_user_raw_statuses_count(self, user_id):
        if not user_id:
            return 0
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM Raw_Statuses WHERE User_Id = %s", (user_id,))
                return int(cur.fetchone()[0] or 0)

    def get_user_raw_oldest_created_at(self, user_id):
        """Return oldest Created_At (TEXT) for a user, or None."""
        if not user_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MIN(Created_At) FROM Raw_Statuses WHERE User_Id = %s", (user_id,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    def get_user_raw_newest_created_at(self, user_id):
        """Return newest Created_At (TEXT) for a user, or None."""
        if not user_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(Created_At) FROM Raw_Statuses WHERE User_Id = %s", (user_id,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    # === Value_Comments helpers (incremental resume) ===

    def get_user_value_comments_newest_publish_time(self, user_id):
        if not user_id:
            return None
        try:
            uid = int(user_id)
        except Exception:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(Publish_Time) FROM Value_Comments WHERE User_Id = %s", (uid,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    def get_user_value_comments_distinct_count(self, user_id):
        if not user_id:
            return 0
        try:
            uid = int(user_id)
        except Exception:
            return 0
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(DISTINCT Comment_Id) FROM Value_Comments WHERE User_Id = %s", (uid,))
                row = cur.fetchone()
                try:
                    return int(row[0] or 0) if row else 0
                except Exception:
                    return 0

    def get_user_comments_count_hint(self, user_id):
        """Best-effort total status/comments count for a user.

        Source priority is not strict; we take the max across tables to avoid under-estimating.
        This helps Step3 avoid trying to backfill to ARTICLE_COUNT_LIMIT when the user simply has less.
        """
        if not user_id:
            return 0
        try:
            uid = int(user_id)
        except Exception:
            return 0
        sql = """
            SELECT GREATEST(
                COALESCE((SELECT Comments_Count FROM Target_users WHERE User_Id = %s), 0),
                COALESCE((SELECT Comments_Count FROM High_quality_users WHERE User_Id = %s), 0),
                COALESCE((SELECT Comments_Count FROM users WHERE User_Id = %s), 0)
            ) AS n
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uid, uid, uid))
                row = cur.fetchone()
                try:
                    return int(row[0] or 0) if row else 0
                except Exception:
                    return 0

    def set_target_user_comments_count_if_missing(self, user_id, comments_count):
        if not user_id:
            return
        try:
            uid = int(user_id)
            n = int(comments_count or 0)
        except Exception:
            return
        if n <= 0:
            return
        self.execute_one_safe(
            """
            UPDATE Target_users
            SET Comments_Count = %s
            WHERE User_Id = %s AND (Comments_Count IS NULL OR Comments_Count = 0)
            """,
            (n, uid),
        )

    def get_target_count(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM Target_users")
                return cur.fetchone()[0]

    def get_next_source_user(self):
        # System_Meta.Key/Value are TEXT, while User_Id is BIGINT.
        # Use NOT EXISTS + casting and allow rescan after FOLLOW_RESCAN_DAYS.
        rescan_days = int(getattr(config, "FOLLOW_RESCAN_DAYS", 7) or 7)
        cutoff = (datetime.now() - timedelta(days=rescan_days)).strftime("%Y-%m-%d")
        sql = """
            WITH candidates AS (
                SELECT User_Id,
                       MAX(Followers_Count) AS Followers_Count,
                       MAX(User_Name) AS User_Name
                FROM (
                    SELECT User_Id, Followers_Count, User_Name FROM Target_users
                    UNION ALL
                    SELECT User_Id, Followers_Count, User_Name FROM High_quality_users
                ) u
                GROUP BY User_Id
            )
            SELECT c.User_Id, c.User_Name
            FROM candidates c
            WHERE NOT EXISTS (
                SELECT 1
                FROM System_Meta m
                WHERE m.Key = ('SCANNED_FOLLOWERS_' || c.User_Id::text)
                  AND m.Value >= %s
            )
            ORDER BY c.Followers_Count DESC
            LIMIT 1
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (cutoff,))
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

    def get_user_scanned_date(self, user_id):
        if not user_id:
            return None
        sql = "SELECT Value FROM System_Meta WHERE Key = %s"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (f"SCANNED_FOLLOWERS_{user_id}",))
                row = cur.fetchone()
                return row[0] if row and row[0] else None

    def is_user_scanned_recently(self, user_id, rescan_days=None):
        """Return True if the user's followers list has been scanned within rescan_days."""
        if rescan_days is None:
            rescan_days = int(getattr(config, "FOLLOW_RESCAN_DAYS", 7) or 7)
        val = self.get_user_scanned_date(user_id)
        if not val:
            return False
        try:
            scanned = datetime.strptime(val, "%Y-%m-%d")
        except Exception:
            return False
        cutoff = datetime.now() - timedelta(days=int(rescan_days))
        return scanned >= cutoff

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
