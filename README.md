# 雪球爬取数据库说明

本项目爬取雪球用户、发帖、关注组合、组合持仓和调仓记录，用于后续研究用户关注/自建组合、股票提及、组合调仓以及 A 股与境外股票共同持有关系。

当前代码默认使用 PostgreSQL 作为主数据库；仓库根目录下的 `xueqiu_pro_v3.db` 是 SQLite 快照/历史库，可用于本地只读查询或数据交接。两者表名基本一致，但字段有少量历史差异，下面会注明。

## 数据库位置

- PostgreSQL 主库：连接参数在 `config.py` 中的 `PG_HOST`、`PG_PORT`、`PG_DBNAME`、`PG_USER`、`PG_PASSWORD`。
- SQLite 快照：`xueqiu_pro_v3.db`。
- 派生矩阵结果：`tools/output/pit_pair_matrix/`，由 `tools/build_monthly_pit_pair_matrix.py` 生成。

建议同事做研究时优先连接 PostgreSQL；如果只是复现实验或离线查看，可直接使用 SQLite 快照。

## 如何访问和查询

### PostgreSQL

安装依赖：

```bash
pip install -r requirements.txt
```

Python 查询示例：

```python
import psycopg2
import config

conn = psycopg2.connect(
    host=config.PG_HOST,
    port=config.PG_PORT,
    dbname=config.PG_DBNAME,
    user=config.PG_USER,
    password=config.PG_PASSWORD,
)

with conn, conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM User_Combinations;")
    print(cur.fetchone())

conn.close()
```

也可以用 `psql`：

```bash
psql -h <PG_HOST> -p <PG_PORT> -U <PG_USER> -d <PG_DBNAME>
```

进入后常用命令：

```sql
\dt
\d User_Combinations
SELECT COUNT(*) FROM Value_Comments;
```

### SQLite 快照

命令行查询：

```bash
sqlite3 xueqiu_pro_v3.db
```

进入后常用命令：

```sql
.tables
.schema User_Combinations
SELECT COUNT(*) FROM Raw_Statuses;
```

Python 查询示例：

```python
import sqlite3

conn = sqlite3.connect("xueqiu_pro_v3.db")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT Symbol, Name, Net_Value, Total_Gain, Updated_At
    FROM User_Combinations
    ORDER BY Updated_At DESC
    LIMIT 10
""").fetchall()

for row in rows:
    print(dict(row))

conn.close()
```

## 表结构总览

### `System_Meta`

系统运行状态和增量抓取检查点。

| 字段 | 含义 |
| --- | --- |
| `Key` | 元数据键，主键，例如某个用户是否已扫描、评论抓取进度等 |
| `Value` | 元数据值，通常是日期、时间戳或状态字符串 |

### `users`

普通雪球用户基础信息。

| 字段 | 含义 |
| --- | --- |
| `User_Id` | 雪球用户 ID，主键 |
| `User_Name` | 用户名 |
| `Comments_Count` | 用户发帖/评论数量，来自雪球用户信息 |
| `Friends_Count` | 关注数 |
| `Followers_Count` | 粉丝数 |
| `Description` | 用户简介 |
| `Last_Updated` | 本项目最后更新该用户信息的时间 |

### `High_quality_users`

高质量用户候选池，通常由粉丝数、发帖数等规则筛选。

字段与 `users` 基本一致。PostgreSQL 版本额外包含：

| 字段 | 含义 |
| --- | --- |
| `Get_Follow` | 是否已抓取该用户关注列表，`0` 表示未抓，`1` 表示已抓 |

### `Target_users`

最终目标用户池，即后续重点抓取动态、组合、关注关系的用户。

字段与 `users` 基本一致。

### `Raw_Statuses`

从雪球用户时间线抓到的原始动态/帖子，是 AI 分析或文本处理前的暂存表。

| 字段 | 含义 |
| --- | --- |
| `Status_Id` | 雪球动态/帖子 ID，主键 |
| `User_Id` | 发帖用户 ID |
| `Description` | 原始正文或清洗后的正文 |
| `Created_At` | 发布时间 |
| `Stock_Tags` | 原始文本中识别到或接口返回的股票标签 |
| `Is_Analyzed` | 分析状态，常见值：`0` 未分析，`1` 已分析，`2` 分析失败 |
| `Forward` | 转发数 |
| `Comment_Count` | 评论数 |
| `Like_Count` | 点赞数；SQLite 旧库字段名可能是 `Like` |

### `Stocks`

股票维表，仅 PostgreSQL 新结构中使用。SQLite 旧库通常把股票名称和代码直接存在业务表中。

| 字段 | 含义 |
| --- | --- |
| `Stock_Id` | 内部股票 ID，主键 |
| `Stock_Symbol` | 股票代码，例如 `SH600000`、`SZ000001`、`HK00700` |
| `Stock_Name` | 股票名称 |
| `Market` | 市场，例如 `SH`、`SZ`、`HK`、`US` |

### `Value_Comments`

有研究价值的股票相关评论/帖子分析结果。

PostgreSQL 新结构：

| 字段 | 含义 |
| --- | --- |
| `Comment_Id` | 原始动态/评论 ID |
| `User_Id` | 发帖用户 ID |
| `Stock_Id` | 关联 `Stocks.Stock_Id` |
| `Sentiment_Score` | 情绪得分，模型输出的数值结果 |
| `Publish_Time` | 发布时间 |
| `Forward` | 转发数 |
| `Comment_Count` | 评论数 |
| `Like_Count` | 点赞数 |

主键为 `(Comment_Id, Stock_Id)`，表示一条评论可以关联多只股票。

SQLite 旧库字段：

| 字段 | 含义 |
| --- | --- |
| `Comment_Id` | 原始动态/评论 ID，主键 |
| `User_Id` | 发帖用户 ID |
| `Content` | 文本内容 |
| `Publish_Time` | 发布时间 |
| `Mentioned_Stocks` | 提及股票，通常是文本或序列化结果 |
| `Category` | 模型或规则分类结果 |
| `Forward` / `Comment_Count` / `Like` | 互动数据 |

### `User_Stocks`

用户关注或持有的股票记录。

PostgreSQL 新结构：

| 字段 | 含义 |
| --- | --- |
| `Record_Id` | 自增主键 |
| `User_Id` | 用户 ID |
| `Stock_Id` | 关联 `Stocks.Stock_Id` |
| `Current_Price` | 抓取时当前价格 |
| `Percent` | 涨跌幅或接口返回的百分比字段 |
| `Updated_At` | 更新时间 |

SQLite 旧库直接包含 `Stock_Name`、`Stock_Symbol`、`Market`，没有 `Stock_Id`。

### `User_Combinations`

雪球组合基础信息。

| 字段 | 含义 |
| --- | --- |
| `Comb_Id` | 内部组合 ID，主键 |
| `User_Id` | 组合创建者用户 ID，可能为空或待补全 |
| `Symbol` | 雪球组合代码，例如 `ZH123456`，唯一 |
| `Name` | 组合名称 |
| `Net_Value` | 最新净值 |
| `Total_Gain` | 总收益率 |
| `Monthly_Gain` | 月收益率 |
| `Daily_Gain` | 日收益率 |
| `Create_Time` | 组合创建时间 |
| `Updated_At` | 组合信息最后更新时间 |
| `Portfolio_Last_Crawled` | 组合详情页最后抓取时间，用于增量抓取 |
| `Close_At_Time` | 组合关闭时间；为空通常表示未关闭 |
| `Description` | 组合简介 |
| `Is_Public` | 是否公开，`1` 表示公开 |

### `Portfolio_Transactions`

组合调仓历史，是研究组合历史持仓和时点持仓的核心表。

PostgreSQL 新结构：

| 字段 | 含义 |
| --- | --- |
| `Txn_Id` | 自增主键 |
| `Comb_Id` | 关联 `User_Combinations.Comb_Id` |
| `Stock_Id` | 关联 `Stocks.Stock_Id` |
| `Prev_Weight` | 调仓前目标股票权重 |
| `Target_Weight` | 调仓后目标股票权重 |
| `Price` | 调仓价格 |
| `Cash_Value` | 现金比例或现金值，取决于接口返回 |
| `Status` | 调仓状态 |
| `Transaction_Time` | 调仓发生时间 |
| `Notes` | 备注或接口返回说明 |

SQLite 旧库使用 `Stock_Symbol`、`Stock_Name` 代替 `Stock_Id`。

### `Portfolio_Positions`

组合当前或最近一次抓取到的持仓快照。

PostgreSQL 新结构：

| 字段 | 含义 |
| --- | --- |
| `Pos_Id` | 自增主键 |
| `Comb_Id` | 关联 `User_Combinations.Comb_Id` |
| `Segment_Name` | 持仓分组名称，例如股票/现金等接口分组 |
| `Segment_Weight` | 分组权重 |
| `Stock_Id` | 关联 `Stocks.Stock_Id`，现金类分组可能为空 |
| `Stock_Price` | 股票价格 |
| `Stock_Weight` | 股票权重 |
| `Updated_At` | 抓取更新时间 |

SQLite 旧库使用 `Stock_Name`，没有 `Stock_Id`。

### `User_Portfolio_Follows`

用户与组合的关注/创建关系。

| 字段 | 含义 |
| --- | --- |
| `User_Id` | 用户 ID |
| `Symbol` | 组合代码，关联 `User_Combinations.Symbol` |
| `Build_Or_Collection` | `0` 表示用户自建组合，`1` 表示用户收藏/关注组合 |
| `Follow_Time` | 关注或创建时间，取决于接口可得字段 |

主键为 `(User_Id, Symbol)`。

### `Portfolio_Comments`

组合动态评论表。该表在配置 `ENABLE_PORTFOLIO_COMMENTS=True` 时写入；SQLite 快照中已存在。

| 字段 | 含义 |
| --- | --- |
| `Status_Id` | 雪球动态 ID，主键 |
| `Comb_Id` | 关联 `User_Combinations.Comb_Id` |
| `User_Id` | 发帖用户 ID |
| `Content` | 评论/动态内容 |
| `Publish_Time` | 发布时间 |
| `Like_Count` | 点赞数 |
| `Reply_Count` | 回复数 |
| `Forward_Count` | 转发数 |

## 常用查询示例

查看所有目标用户：

```sql
SELECT User_Id, User_Name, Followers_Count, Comments_Count
FROM Target_users
ORDER BY Followers_Count DESC
LIMIT 50;
```

查看某个用户关注或创建的组合：

```sql
SELECT f.User_Id, f.Build_Or_Collection, c.Symbol, c.Name, c.Total_Gain, c.Updated_At
FROM User_Portfolio_Follows f
JOIN User_Combinations c ON c.Symbol = f.Symbol
WHERE f.User_Id = 9887656769
ORDER BY c.Updated_At DESC;
```

查看某个组合的调仓历史：

PostgreSQL：

```sql
SELECT c.Symbol, c.Name, s.Stock_Symbol, s.Stock_Name,
       t.Prev_Weight, t.Target_Weight, t.Price, t.Transaction_Time
FROM Portfolio_Transactions t
JOIN User_Combinations c ON c.Comb_Id = t.Comb_Id
JOIN Stocks s ON s.Stock_Id = t.Stock_Id
WHERE c.Symbol = 'ZH123456'
ORDER BY t.Transaction_Time DESC;
```

SQLite 快照：

```sql
SELECT c.Symbol, c.Name, t.Stock_Symbol, t.Stock_Name,
       t.Prev_Weight, t.Target_Weight, t.Price, t.Transaction_Time
FROM Portfolio_Transactions t
JOIN User_Combinations c ON c.Comb_Id = t.Comb_Id
WHERE c.Symbol = 'ZH123456'
ORDER BY t.Transaction_Time DESC;
```

查看股票相关评论分析结果：

PostgreSQL：

```sql
SELECT vc.Comment_Id, vc.User_Id, s.Stock_Symbol, s.Stock_Name,
       vc.Sentiment_Score, vc.Publish_Time
FROM Value_Comments vc
JOIN Stocks s ON s.Stock_Id = vc.Stock_Id
ORDER BY vc.Publish_Time DESC
LIMIT 100;
```

SQLite 快照：

```sql
SELECT Comment_Id, User_Id, Content, Mentioned_Stocks, Category, Publish_Time
FROM Value_Comments
ORDER BY Publish_Time DESC
LIMIT 100;
```

## 派生矩阵数据

`tools/output/pit_pair_matrix/` 保存按月生成的 A 股与境外股票共同持有矩阵，适合直接用于量化研究。

主要文件：

| 文件 | 含义 |
| --- | --- |
| `manifest.json` | 生成范围、矩阵维度、时点列表和假设说明 |
| `row_index_a_share.csv` | 矩阵行索引，行对应 A 股 |
| `col_index_external.csv` | 矩阵列索引，列对应港股/美股等境外股票 |
| `matrix_YYYYMMDD_HHMMSS.npz` | SciPy 稀疏矩阵，值为共同持有该 A 股和境外股票的用户数 |
| `pairs_YYYYMMDD_HHMMSS.csv.gz` | 同一时点的长表格式，字段为 `a_stock_id`、`ext_sym_key`、`blogger_count` |
| `meta_YYYYMMDD_HHMMSS.json` | 单个时点矩阵的维度、非零元素数量、总计数 |

读取示例：

```python
import pandas as pd
from scipy import sparse

rows = pd.read_csv("tools/output/pit_pair_matrix/row_index_a_share.csv")
cols = pd.read_csv("tools/output/pit_pair_matrix/col_index_external.csv")
mat = sparse.load_npz("tools/output/pit_pair_matrix/matrix_20240101_080000.npz")

print(mat.shape)
print(rows.head())
print(cols.head())
```

## 运行和维护

安装依赖：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

启动爬虫：

```bash
python main_spider.py
```

重置数据库结构：

```bash
python tools/reset_db.py
```

重新生成月度共同持有矩阵：

```bash
python tools/build_monthly_pit_pair_matrix.py
```

可选参数：

```bash
python tools/build_monthly_pit_pair_matrix.py --start-month 2020-01 --end-month 2024-12
```

## 注意事项

- 时间字段大多以文本形式保存，常见格式为 `YYYY-MM-DD HH:MM:SS`；做时间比较时建议先转换为数据库时间类型或在 Python 中解析。
- SQLite 快照是历史兼容结构；如果研究需要最新模型字段、归一化股票表和 `Sentiment_Score`，优先使用 PostgreSQL。
- `Raw_Statuses` 是原始暂存数据，体积较大；`Value_Comments` 是处理后的股票相关评论结果，更适合直接做文本和情绪研究。
- 历史持仓应优先用 `Portfolio_Transactions` 重建；`Portfolio_Positions` 更适合作为最近一次抓取到的持仓快照。
