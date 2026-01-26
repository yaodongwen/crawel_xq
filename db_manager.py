import sqlite3
import time
from datetime import datetime, timedelta
from config import DB_PATH, SQL_CREATE_TABLES, CACHE_DAYS

class DBManager:
    def __init__(self):
        self.db_path = DB_PATH
        self.init_tables()
        self._enable_wal()

    def get_conn(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def _enable_wal(self):
        conn = None
        try:
            conn = self.get_conn()
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.commit()
        except: pass
        finally:
            if conn: conn.close()

    def init_tables(self):
        conn = None
        try:
            conn = self.get_conn()
            for sql in SQL_CREATE_TABLES: conn.execute(sql)
            conn.commit()
        except Exception as e: print(f"[DB Init Error] {e}")
        finally:
            if conn: conn.close()

    def execute_many_safe(self, sql, data, retries=3):
        if not data: return
        for attempt in range(retries):
            conn = None
            try:
                conn = self.get_conn()
                with conn: conn.executemany(sql, data)
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e): time.sleep(1)
                else: break
            finally:
                if conn: conn.close()

    def execute_one_safe(self, sql, params=(), retries=3):
        for attempt in range(retries):
            conn = None
            try:
                conn = self.get_conn()
                with conn: conn.execute(sql, params)
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e): time.sleep(1)
                else: break
            finally:
                if conn: conn.close()

    def get_existing_user_ids(self):
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT User_Id FROM users")
            return {row[0] for row in cursor.fetchall()}
        finally:
            if conn: conn.close()

    def get_existing_target_ids(self):
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT User_Id FROM Target_users")
            return {row[0] for row in cursor.fetchall()}
        finally:
            if conn: conn.close()

    def get_pending_tasks(self, table_name, limit=None):
        """获取待办任务，支持限制数量"""
        cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        
        # 基础 SQL
        sql = f"SELECT * FROM {table_name} WHERE Last_Updated IS NULL OR Last_Updated < ?"
        
        # 如果指定了 limit，拼接到 SQL 后面
        if limit:
            sql += f" LIMIT {limit}"
            
        conn = None
        try:
            conn = self.get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, (cutoff,))
            return cursor.fetchall()
        finally:
            if conn: conn.close()

    def update_task_status(self, user_id, table_name):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.execute_one_safe(f"UPDATE {table_name} SET Last_Updated = ? WHERE User_Id = ?", (now, user_id))

    def check_seed_scanned(self, seed_id):
        return False 

    def mark_seed_scanned(self, seed_id):
        pass

    def get_unanalyzed_raw_data(self, limit=50):
        sql = "SELECT * FROM Raw_Statuses WHERE Is_Analyzed = 0 LIMIT ?"
        conn = None
        try:
            conn = self.get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, (limit,))
            return cursor.fetchall()
        finally:
            if conn: conn.close()

    def mark_raw_as_analyzed(self, status_id, status_code=1):
        self.execute_one_safe("UPDATE Raw_Statuses SET Is_Analyzed = ? WHERE Status_Id = ?", (status_code, status_id))

    def get_unanalyzed_count(self):
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT count(*) FROM Raw_Statuses WHERE Is_Analyzed = 0")
            return cursor.fetchone()[0]
        finally:
            if conn: conn.close()

    # === 【新增】获取目标用户总数 ===
    def get_target_count(self):
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT count(*) FROM Target_users")
            return cursor.fetchone()[0]
        finally:
            if conn: conn.close()

    # === 新增：裂变扫描状态管理 ===
    
    def get_next_source_user(self):
        """
        从 High_quality_users 表中找一个【没扫过关注列表】的用户作为新的种子。
        优先找粉丝多的，质量可能更高。
        """
        # 排除掉已经在 System_Meta 里标记为 SCANNED_FOLLOWERS_xxx 的用户
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
        conn = None
        try:
            conn = self.get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql)
            return cursor.fetchone() # 返回 (User_Id, User_Name) 或者 None
        finally:
            if conn: conn.close()

    def mark_user_as_scanned(self, user_id):
        """标记该用户的关注列表已扫完"""
        self.execute_one_safe(
            "INSERT OR REPLACE INTO System_Meta (Key, Value) VALUES (?, ?)", 
            (f"SCANNED_FOLLOWERS_{user_id}", datetime.now().strftime("%Y-%m-%d"))
        )


    def is_user_scanned(self, user_id):
        """检查某用户的关注列表是否已标记为扫描完成"""
        sql = "SELECT Value FROM System_Meta WHERE Key = ?"
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute(sql, (f"SCANNED_FOLLOWERS_{user_id}",))
            return cursor.fetchone() is not None
        finally:
            if conn: conn.close()


    # === 【新增】统计专用方法 ===
    
    def get_total_users_count(self):
        """统计总共扫描入库了多少用户"""
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT count(*) FROM users")
            return cursor.fetchone()[0]
        finally:
            if conn: conn.close()

    def get_total_comments_count(self):
        """统计 AI 一共入库了多少条高价值评论"""
        conn = None
        try:
            conn = self.get_conn()
            cursor = conn.execute("SELECT count(*) FROM Value_Comments")
            return cursor.fetchone()[0]
        finally:
            if conn: conn.close()

    def get_db_size(self):
        """(可选) 获取数据库文件大小 MB"""
        import os
        try:
            size = os.path.getsize(self.db_path)
            return round(size / (1024 * 1024), 2)
        except: return 0