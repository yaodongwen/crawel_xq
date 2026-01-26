import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据库名保持不变
DB_PATH = os.path.join(BASE_DIR, "xueqiu_pro_v3.db") 
USER_DATA_PATH = os.path.join(BASE_DIR, "drission_userdata_pro")
MAC_CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

print(f">>> [Config] 数据库路径: {DB_PATH}")

SEED_USER_URL = 'https://xueqiu.com/u/9887656769' 
ARTICLE_COUNT_LIMIT = 5

FOCUS_COUNT_LIMIT = 100
TARGET_GOAL = 20

# === 【新增】流水线批次大小 ===
# 意思是：Step 1 找到 10 个优质用户就停下来，转而去跑 Step 2
PIPELINE_BATCH_SIZE = 2

CACHE_DAYS = 21           
AI_MODEL_NAME = "qwen3:4b-q4_K_M" 

API = {
    'FOCUS': 'friendships/groups/members.json',
    'TIMELINE': 'v4/statuses/user_timeline.json',
    'STOCK': 'quote.json', 
}

SQL_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS System_Meta (Key TEXT PRIMARY KEY, Value TEXT);""",
    """CREATE TABLE IF NOT EXISTS users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Status_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS High_quality_users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Status_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS Target_users (
        User_Id INTEGER PRIMARY KEY, User_Name TEXT, Status_Count INTEGER, 
        Friends_Count INTEGER, Followers_Count INTEGER, Description TEXT, Last_Updated TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS Raw_Statuses (
        Status_Id INTEGER PRIMARY KEY, User_Id INTEGER, Description TEXT, 
        Created_At TEXT, Stock_Tags TEXT, Is_Analyzed INTEGER DEFAULT 0
    );""",
    """CREATE TABLE IF NOT EXISTS Value_Comments (
        Comment_Id INTEGER PRIMARY KEY, User_Id INTEGER, Content TEXT, 
        Publish_Time TEXT, Mentioned_Stocks TEXT, Category TEXT
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
        User_Id INTEGER,
        Symbol TEXT,     
        Name TEXT,       
        Net_Value REAL,  
        Total_Gain REAL, 
        Monthly_Gain REAL,
        Daily_Gain REAL,
        Updated_At TEXT,
        UNIQUE(User_Id, Symbol) ON CONFLICT REPLACE
    );"""
]