import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据库名保持不变
DB_PATH = os.path.join(BASE_DIR, "xueqiu_pro_v3.db") 
USER_DATA_PATH = os.path.join(BASE_DIR, "drission_userdata_pro")
MAC_CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

print(f">>> [Config] 数据库路径: {DB_PATH}")

SEED_USER_URL = 'https://xueqiu.com/u/9887656769' 
ARTICLE_COUNT_LIMIT = 20

FOCUS_COUNT_LIMIT = 30
TARGET_GOAL = 5

# === 【新增】流水线批次大小 ===
# 意思是：Step 1 找到 10 个优质用户就停下来，转而去跑 Step 2
PIPELINE_BATCH_SIZE = 1

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
    """CREATE TABLE IF NOT EXISTS System_Meta (Key TEXT PRIMARY KEY, Value TEXT);""",
    """CREATE TABLE IF NOT EXISTS users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Comments_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS High_quality_users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Comments_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS Target_users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Comments_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS Raw_Statuses (
        Status_Id INTEGER PRIMARY KEY, User_Id INTEGER, Description TEXT, 
        Created_At TEXT, Stock_Tags TEXT, Is_Analyzed INTEGER DEFAULT 0,
        Forward INTEGER, Comment_Count INTEGER, Like INTEGER
    );""",
    """CREATE TABLE IF NOT EXISTS Value_Comments (
        Comment_Id INTEGER PRIMARY KEY, User_Id INTEGER, Content TEXT, 
        Publish_Time TEXT, Mentioned_Stocks TEXT, Category TEXT, Forward INTEGER,
        Comment_Count INTEGER, Like INTEGER
    );""",
    """CREATE TABLE IF NOT EXISTS User_Stocks (
        Record_Id INTEGER PRIMARY KEY AUTOINCREMENT,
        User_Id INTEGER,
        Stock_Name TEXT,
        Stock_Symbol TEXT,
        Current_Price REAL,
        Percent REAL, 
        Market TEXT,
        Updated_At TEXT,
        UNIQUE(User_Id, Stock_Symbol) ON CONFLICT REPLACE
    );""",
    """CREATE TABLE IF NOT EXISTS User_Combinations (
        Comb_Id INTEGER PRIMARY KEY AUTOINCREMENT,
        User_Id INTEGER, -- 创建者 User_Id（可能需要后续补全）
        Symbol TEXT NOT NULL UNIQUE, -- 雪球组合代码，如 ZH123456，全局唯一
        Name TEXT,
        Net_Value REAL, -- 最新净值
        Total_Gain REAL, -- 总收益（%）
        Monthly_Gain REAL, -- 月收益（%）
        Daily_Gain REAL, -- 日收益（%）
        Create_Time TEXT, -- 组合创建时间（可能需要后续补全）
        Updated_At TEXT, -- 最后更新时间（来自雪球/爬虫抓取时间）
        Portfolio_Last_Crawled TEXT, -- 上次抓取“组合详情”(调仓/动态)的时间，用于缓存/增量更新
        Close_At_Time TEXT, -- 关闭时间；NULL/0 表示未关闭
        Description TEXT,
        Is_Public INTEGER DEFAULT 1
    );""",
    """CREATE TABLE IF NOT EXISTS Portfolio_Transactions (
        Txn_Id INTEGER PRIMARY KEY AUTOINCREMENT,
        Comb_Id INTEGER NOT NULL,
        Stock_Symbol TEXT NOT NULL,
        Stock_Name TEXT,
        Prev_Weight REAL, -- 调仓前权重（%）
        Target_Weight REAL, -- 调仓后权重（%）
        Price REAL, -- 调仓价格（若接口返回）
        Cash_Value REAL, -- 调仓后现金比例/现金值（若接口返回）
        Status TEXT, -- 调仓状态字段（若接口返回）
        Transaction_Time TEXT NOT NULL, -- 调仓发生时间（精确到秒）
        Notes TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        UNIQUE(Comb_Id, Transaction_Time, Stock_Symbol)
    );""",
    """CREATE TABLE IF NOT EXISTS Portfolio_Comments (
        Status_Id INTEGER PRIMARY KEY, -- 使用雪球动态 id 做主键防重
        Comb_Id INTEGER NOT NULL,
        User_Id INTEGER,
        Content TEXT NOT NULL,
        Publish_Time TEXT NOT NULL,
        Like_Count INTEGER DEFAULT 0,
        Reply_Count INTEGER DEFAULT 0,
        Forward_Count INTEGER DEFAULT 0,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        FOREIGN KEY (User_Id) REFERENCES users(User_Id)
    );""",
    """CREATE TABLE IF NOT EXISTS Portfolio_Positions (
        Pos_Id INTEGER PRIMARY KEY AUTOINCREMENT,
        Comb_Id INTEGER NOT NULL,
        Segment_Name TEXT,
        Segment_Weight TEXT,
        Stock_Name TEXT,
        Stock_Price TEXT,
        Stock_Weight TEXT,
        Updated_At TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id)
    );""",
    """CREATE TABLE IF NOT EXISTS User_Portfolio_Follows (
        User_Id INTEGER NOT NULL,
        Symbol TEXT NOT NULL, -- 组合唯一编码，如 ZH123456
        Build_Or_Collection INTEGER, -- 如果是0就是用户自建的，为1则为用户收藏的组合
        Follow_Time TEXT,
        PRIMARY KEY (User_Id, Symbol),
        FOREIGN KEY (User_Id) REFERENCES users(User_Id),
        FOREIGN KEY (Symbol) REFERENCES User_Combinations(Symbol)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_portfolio_txn_comb_time ON Portfolio_Transactions(Comb_Id, Transaction_Time);""",
    """CREATE INDEX IF NOT EXISTS idx_portfolio_comments_comb_time ON Portfolio_Comments(Comb_Id, Publish_Time);""",
    """CREATE INDEX IF NOT EXISTS idx_user_portfolio_follows_user ON User_Portfolio_Follows(User_Id);""",
]
