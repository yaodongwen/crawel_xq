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

# === Slider / Risk-control handling ===
# 如果误判滑块导致 safe_action 卡循环，可临时打开调试查看原因：
SLIDER_DEBUG = True
# 避免无限循环刷新的上限
SLIDER_MAX_REFRESHES = 5

# === Raw_Statuses staging policy ===
# Raw_Statuses is a staging/queue table for AI processing. For large datasets, keeping all processed raw text
# will bloat storage significantly. Enable the flag below to delete Raw_Statuses rows after AI processed them.
# IMPORTANT: This is destructive. Keep it False until you've verified `Value_Comments` is being populated as expected.
DELETE_ANALYZED_RAW_STATUSES = False
# If True, also delete rows that failed AI processing (Is_Analyzed=2). Usually keep False for debugging/retry.
DELETE_FAILED_RAW_STATUSES = False

# Step3 "resume" checkpoint: prefer Value_Comments(Publish_Time) to decide where to stop paging (incremental crawl).
# This avoids the old "crawl older than Raw_Statuses oldest" strategy (which breaks once raw rows are deleted).
RESUME_BY_VALUE_COMMENTS = True

# Max new Raw_Statuses rows to enqueue per user per run (safety cap).
# Set to None here; we will default it to ARTICLE_COUNT_LIMIT after that constant is defined below.
STEP3_MAX_NEW_RAW_PER_USER = None

# Step3 debug: print detailed checkpoint sources (Value_Comments vs System_Meta).
STEP3_DEBUG_CHECKPOINTS = False

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
# ARTICLE_COUNT_LIMIT = 5
# FOCUS_COUNT_LIMIT = 20
# TARGET_GOAL = 5
# PIPELINE_BATCH_SIZE = 2

ARTICLE_COUNT_LIMIT = 3000
FOCUS_COUNT_LIMIT = 300000
TARGET_GOAL = 10000

# Default max new raw rows per user per run.
if STEP3_MAX_NEW_RAW_PER_USER is None:
    STEP3_MAX_NEW_RAW_PER_USER = ARTICLE_COUNT_LIMIT

# === 【新增】流水线批次大小 ===
PIPELINE_BATCH_SIZE = 10 # 意思是：Step 1 找到 10 个优质用户就停下来，转而去跑 Step 2


CACHE_DAYS = 21           
# AI_MODEL_NAME = "qwen2.5:1.5b" 

# JSON 响应解析失败时是否输出详细日志（默认关闭，避免刷屏）
VERBOSE_DECODE_ERRORS = False

# === AI 情绪因子模型配置 ===
# 预处理股票别名字典（本地 JSON）
STOCK_ALIASES_JSON = os.path.join(BASE_DIR, "data", "ultimate_stock_aliases.json")

# 意图分类器（0/1）与情绪回归模型（0-10 分）
INTENT_MODEL_PATH = os.path.join(BASE_DIR, "train_models", "model_intent_classifier_best")
SENTIMENT_MODEL_PATH = os.path.join(BASE_DIR, "train_models", "model_finbert_regression")

# AI 处理批次（从 Raw_Statuses 一次取多少条做推理）
AI_BATCH_SIZE = 8

# 大V的门槛
MIN_FOLLOWERS = 5000
MIN_COMMENTS = 20

# 组合详情页重新抓取间隔（小时）。
# 依据 User_Combinations.Portfolio_Last_Crawled 判断：
# - 在这个时间窗口内抓过详情页，则本次不再进入组合详情页
# - 超过这个时间窗口，再重新抓取详情页做增量同步
PORTFOLIO_DETAIL_REFRESH_HOURS = 24 * 7
# 兼容旧代码/旧配置名。
PORTFOLIO_CACHE_HOURS = PORTFOLIO_DETAIL_REFRESH_HOURS

# 仅对“新组合”（库里不存在）抓取详情页；已有组合只做列表层更新/关注关系落库。
# 开启会更快，但会减少已有组合的持仓/调仓更新频率（需要的话关掉或调大 PORTFOLIO_DETAIL_REFRESH_HOURS）。
PORTFOLIO_DETAIL_ONLY_IF_NEW = True

# Step2 组合列表等待时长（秒）：等待 `portfolio/stock/list.json` 等响应
PORTFOLIO_LIST_WAIT_SECONDS = 6
# Step2 子页签等待时长（秒）：等待“创建/关注”子页签渲染出来
PORTFOLIO_SUBTAB_WAIT_SECONDS = 5
# Step2 点击子页签后等待时长（秒）：等待该子页签触发新的列表响应
PORTFOLIO_CLICK_WAIT_SECONDS = 6

# 如果触发风控/被封（常见表现：405），暂停的秒数
BLOCK_SLEEP_SECONDS = 600

# 任一 worker 触发 405 时写入全局退避文件，避免其它 worker 继续打导致更严重风控
GLOBAL_BLOCK_ON_405 = True

# 405 时不在 safe_action 内部睡眠，而是抛出全局退避信号：关闭浏览器等待，到点后再重启浏览器继续跑
HIBERNATE_ON_405 = True

# WAF/滑块验证触发时的退避策略（不尝试自动绕过验证）。
WAF_SLEEP_SECONDS = BLOCK_SLEEP_SECONDS
HIBERNATE_ON_WAF = True
SLIDER_SLEEP_SECONDS = BLOCK_SLEEP_SECONDS
HIBERNATE_ON_SLIDER = True
AUTO_SOLVE_SLIDER = True
SLIDER_MAX_RETRIES = 10
SLIDER_MAX_REFRESHES = 3
SLIDER_DEBUG = True

# 长文补全：优先使用 JSON API（推荐），避免打开 https://xueqiu.com/{uid}/{id} 详情页触发 405/滑块
LONG_ARTICLE_API_ONLY = True
LONG_ARTICLE_BLOCK_COOLDOWN_SECONDS = 1800

# Step1 扫描关注列表：最多翻多少页（0 表示不限制；建议在风控较严时设一个上限）
FOLLOW_SCAN_MAX_PAGES = 50

# 输出 405 触发时的 url/title，便于排查误判
BLOCK_DEBUG = True
WAF_DEBUG = True
MAX_CONSECUTIVE_GLOBAL_BACKOFFS = 3

# 组合页签调试：输出“创建/关注/收藏”等子页签的文本，便于修正选择器
PORTFOLIO_TAB_DEBUG = True
# 调试时最多输出多少个子页签候选 a 标签（避免刷屏）
PORTFOLIO_TAB_DEBUG_MAX = 20

API = {
    'FOCUS': 'friendships/groups/members.json',
    'TIMELINE': 'v4/statuses/user_timeline.json',
    'STOCK': 'quote.json', 
    'PORTFOLIO': 'portfolio/stock/list.json', 
}

# === Optional: portfolio comments storage ===
# 组合动态评论抓取/落库目前默认关闭（省空间/减少请求），需要时改为 True 并在 main_spider 里打开写入逻辑。
ENABLE_PORTFOLIO_COMMENTS = False

SQL_CREATE_TABLES = [
    # PostgreSQL schema (compatible with existing SQL identifiers)
    """    CREATE TABLE IF NOT EXISTS System_Meta (
        Key TEXT PRIMARY KEY,
        Value TEXT
    );
    """,
    """    CREATE TABLE IF NOT EXISTS Stocks (
        Stock_Id BIGSERIAL PRIMARY KEY,
        Stock_Symbol TEXT NOT NULL UNIQUE,
        Stock_Name TEXT,
        Market TEXT
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
        Get_Follow INTEGER DEFAULT 0,
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
        Comment_Id BIGINT NOT NULL,
        User_Id BIGINT,
        Stock_Id BIGINT NOT NULL,
        Sentiment_Score DOUBLE PRECISION,
        Publish_Time TEXT,
        Forward INTEGER,
        Comment_Count INTEGER,
        Like_Count INTEGER,
        PRIMARY KEY (Comment_Id, Stock_Id),
        FOREIGN KEY (Stock_Id) REFERENCES Stocks(Stock_Id)
    );
    """,
    """    CREATE TABLE IF NOT EXISTS User_Stocks (
        Record_Id BIGSERIAL PRIMARY KEY,
        User_Id BIGINT,
        Stock_Id BIGINT NOT NULL,
        Current_Price DOUBLE PRECISION,
        Percent DOUBLE PRECISION,
        Updated_At TEXT,
        UNIQUE(User_Id, Stock_Id),
        FOREIGN KEY (User_Id) REFERENCES users(User_Id),
        FOREIGN KEY (Stock_Id) REFERENCES Stocks(Stock_Id)
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
        Stock_Id BIGINT NOT NULL,
        Prev_Weight DOUBLE PRECISION,
        Target_Weight DOUBLE PRECISION,
        Price DOUBLE PRECISION,
        Cash_Value DOUBLE PRECISION,
        Status TEXT,
        Transaction_Time TEXT NOT NULL,
        Notes TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        FOREIGN KEY (Stock_Id) REFERENCES Stocks(Stock_Id),
        UNIQUE(Comb_Id, Transaction_Time, Stock_Id)
    );
    """,
    # """    CREATE TABLE IF NOT EXISTS Portfolio_Comments (
    #     Status_Id BIGINT PRIMARY KEY,
    #     Comb_Id BIGINT NOT NULL,
    #     User_Id BIGINT,
    #     Content TEXT NOT NULL,
    #     Publish_Time TEXT NOT NULL,
    #     Like_Count INTEGER DEFAULT 0,
    #     Reply_Count INTEGER DEFAULT 0,
    #     Forward_Count INTEGER DEFAULT 0,
    #     FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
    #     FOREIGN KEY (User_Id) REFERENCES users(User_Id)
    # );
    # """,
    """    CREATE TABLE IF NOT EXISTS Portfolio_Positions (
        Pos_Id BIGSERIAL PRIMARY KEY,
        Comb_Id BIGINT NOT NULL,
        Segment_Name TEXT,
        Segment_Weight TEXT,
        Stock_Id BIGINT,
        Stock_Price TEXT,
        Stock_Weight TEXT,
        Updated_At TEXT,
        FOREIGN KEY (Comb_Id) REFERENCES User_Combinations(Comb_Id),
        FOREIGN KEY (Stock_Id) REFERENCES Stocks(Stock_Id)
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
    "CREATE INDEX IF NOT EXISTS idx_user_portfolio_follows_user ON User_Portfolio_Follows(User_Id);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_positions_unique ON Portfolio_Positions(Comb_Id, Segment_Name, Stock_Id, Stock_Price, Stock_Weight, Segment_Weight);",
]

if ENABLE_PORTFOLIO_COMMENTS:
    SQL_CREATE_TABLES.extend(
        [
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
            "CREATE INDEX IF NOT EXISTS idx_portfolio_comments_comb_time ON Portfolio_Comments(Comb_Id, Publish_Time);",
        ]
    )
