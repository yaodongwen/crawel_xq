import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据库名保持不变
DB_PATH = os.path.join(BASE_DIR, "xueqiu_pro_v3.db")

# === Database (PostgreSQL) ===
# 由于数据量巨大/需要并行写入，推荐使用 PostgreSQL 替代 sqlite。
# 注意：不要在日志里打印密码。
PG_HOST = "192.168.1.33"
PG_PORT = 5432
PG_DBNAME = "postgres"
PG_USER = "dwyao"
PG_PASSWORD = "123123"

# 连接池大小（并发抓取/AI 进程会用到多连接）
PG_POOL_MIN = 1
PG_POOL_MAX = 10

# === 系统切换配置 ===
# 可选: "mac" 或 "windows"
OS_TYPE = "windows"

# Chrome 路径（可按需修改）
CHROME_PATHS = {
    "mac": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "windows": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    # 备用: r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
}

# DrissionPage 用户数据目录（按系统拆分，避免互相覆盖）
USER_DATA_PATHS = {
    "mac": os.path.join(BASE_DIR, "drission_userdata_pro"),
    "windows": os.path.join(BASE_DIR, "drission_userdata_pro_win"),
}


def get_chrome_path():
    return CHROME_PATHS.get(OS_TYPE, CHROME_PATHS["mac"])


def get_user_data_path():
    return USER_DATA_PATHS.get(OS_TYPE, USER_DATA_PATHS["mac"])

print(f">>> [Config] PostgreSQL: {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DBNAME}")

SEED_USER_URL = 'https://xueqiu.com/u/9887656769' 

# TEST
ARTICLE_COUNT_LIMIT = 5
FOCUS_COUNT_LIMIT = 20
TARGET_GOAL = 5
PIPELINE_BATCH_SIZE = 2

# ARTICLE_COUNT_LIMIT = 3000
# FOCUS_COUNT_LIMIT = 300000
# TARGET_GOAL = 10000

## === 【新增】流水线批次大小 ===
# PIPELINE_BATCH_SIZE = 10 # 意思是：Step 1 找到 10 个优质用户就停下来，转而去跑 Step 2

CACHE_DAYS = 21           
AI_MODEL_NAME = "qwen2.5:1.5b" 

# 大V的门槛
MIN_FOLLOWERS = 5000
MIN_COMMENTS = 20

# 组合(Portfolio/Cube)抓取缓存：在这段时间内再次遇到同一个组合就跳过“详情抓取”，
# 仅通过列表接口补充/更新基础字段；超过时间再做增量更新（天）。
PORTFOLIO_CACHE_HOURS = 3

# 如果触发风控/被封（常见表现：405），暂停的秒数
BLOCK_SLEEP_SECONDS = 600

API = {
    'FOCUS': 'friendships/groups/members.json',
    'TIMELINE': 'v4/statuses/user_timeline.json',
    'STOCK': 'quote.json', 
    'PORTFOLIO': 'portfolio/stock/list.json', 
}

SQL_CREATE_TABLES = [
    # PostgreSQL schema (compatible with existing SQL identifiers)
    """    CREATE TABLE IF NOT EXISTS System_Meta (
        Key TEXT PRIMARY KEY,
        Value TEXT
    );
    """,
    """    CREATE TABLE IF NOT EXISTS users (
        User_Id BIGINT PRIMARY KEY,
        User_Name TEXT,
        Comments_Count INTEGER,
        Friends_Count INTEGER,
        Followers_Count INTEGER,
        Description TEXT,
        Last_Updated TEXT
    );
    """,
    """    CREATE TABLE IF NOT EXISTS High_quality_users (
        User_Id BIGINT PRIMARY KEY,
        User_Name TEXT,
        Comments_Count INTEGER,
        Friends_Count INTEGER,
        Followers_Count INTEGER,
        Description TEXT,
        Last_Updated TEXT
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Target_users (
        User_Id BIGINT PRIMARY KEY,
        User_Name TEXT,
        Comments_Count INTEGER,
        Friends_Count INTEGER,
        Followers_Count INTEGER,
        Description TEXT,
        Last_Updated TEXT
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Raw_Statuses (
        Status_Id BIGINT PRIMARY KEY,
        User_Id BIGINT,
        Description TEXT,
        Created_At TEXT,
        Stock_Tags TEXT,
        Is_Analyzed INTEGER DEFAULT 0,
        Forward INTEGER,
        Comment_Count INTEGER,
        Like_Count INTEGER
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Value_Comments (
        Comment_Id BIGINT PRIMARY KEY,
        User_Id BIGINT,
        Content TEXT,
        Publish_Time TEXT,
        Mentioned_Stocks TEXT,
        Category TEXT,
        Forward INTEGER,
        Comment_Count INTEGER,
        Like_Count INTEGER
    );
    """,
    """    CREATE TABLE IF NOT EXISTS User_Stocks (
        Record_Id BIGSERIAL PRIMARY KEY,
        User_Id BIGINT,
        Stock_Name TEXT,
        Stock_Symbol TEXT,
        Current_Price DOUBLE PRECISION,
        Percent DOUBLE PRECISION,
        Market TEXT,
        Updated_At TEXT,
        UNIQUE(User_Id, Stock_Symbol)
    );
    """,
    """    CREATE TABLE IF NOT EXISTS User_Combinations (
        Comb_Id BIGSERIAL PRIMARY KEY,
        User_Id BIGINT,
        Symbol TEXT NOT NULL UNIQUE,
        Name TEXT,
        Net_Value DOUBLE PRECISION,
        Total_Gain DOUBLE PRECISION,
        Monthly_Gain DOUBLE PRECISION,
        Daily_Gain DOUBLE PRECISION,
        Create_Time TEXT,
        Updated_At TEXT,
        Portfolio_Last_Crawled TEXT,
        Close_At_Time TEXT,
        Description TEXT,
        Is_Public INTEGER DEFAULT 1
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Portfolio_Transactions (
        Txn_Id BIGSERIAL PRIMARY KEY,
        Comb_Id BIGINT NOT NULL,
        Stock_Symbol TEXT NOT NULL,
        Stock_Name TEXT,
        Prev_Weight DOUBLE PRECISION,
        Target_Weight DOUBLE PRECISION,
        Price DOUBLE PRECISION,
        Cash_Value DOUBLE PRECISION,
        Status TEXT,
        Transaction_Time TEXT NOT NULL,
        Notes TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        UNIQUE(Comb_Id, Transaction_Time, Stock_Symbol)
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Portfolio_Comments (
        Status_Id BIGINT PRIMARY KEY,
        Comb_Id BIGINT NOT NULL,
        User_Id BIGINT,
        Content TEXT NOT NULL,
        Publish_Time TEXT NOT NULL,
        Like_Count INTEGER DEFAULT 0,
        Reply_Count INTEGER DEFAULT 0,
        Forward_Count INTEGER DEFAULT 0,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        FOREIGN KEY (User_Id) REFERENCES users(User_Id)
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Portfolio_Positions (
        Pos_Id BIGSERIAL PRIMARY KEY,
        Comb_Id BIGINT NOT NULL,
        Segment_Name TEXT,
        Segment_Weight TEXT,
        Stock_Name TEXT,
        Stock_Price TEXT,
        Stock_Weight TEXT,
        Updated_At TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id)
    );
    """,
    """    CREATE TABLE IF NOT EXISTS User_Portfolio_Follows (
        User_Id BIGINT NOT NULL,
        Symbol TEXT NOT NULL,
        Build_Or_Collection INTEGER,
        Follow_Time TEXT,
        PRIMARY KEY (User_Id, Symbol),
        FOREIGN KEY (User_Id) REFERENCES users(User_Id),
        FOREIGN KEY (Symbol) REFERENCES User_Combinations(Symbol)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_portfolio_txn_comb_time ON Portfolio_Transactions(Comb_Id, Transaction_Time);",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_comments_comb_time ON Portfolio_Comments(Comb_Id, Publish_Time);",
    "CREATE INDEX IF NOT EXISTS idx_user_portfolio_follows_user ON User_Portfolio_Follows(User_Id);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_positions_unique ON Portfolio_Positions(Comb_Id, Segment_Name, Stock_Name, Stock_Price, Stock_Weight, Segment_Weight);",
]

