## 使用方法

### 1) 安装依赖
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2) 配置
修改 `config.py`：
- `OS_TYPE = "mac"` 或 `"windows"`
- 如有需要，更新 `CHROME_PATHS` 和 `USER_DATA_PATHS`
- PostgreSQL 连接信息：`PG_HOST` / `PG_PORT` / `PG_DBNAME` / `PG_USER` / `PG_PASSWORD`

### 3) 启动
为清理残余进程，先运行：
```bash
pkill -f python
pkill -f main_spider
pkill -f "Google Chrome"
```

然后再运行：
```bash
python main_spider.py
```

## Windows 支持（配置切换）
在 `config.py` 中修改：
- `OS_TYPE = "windows"`
- 如果你的 Chrome 路径不同，更新 `CHROME_PATHS["windows"]`
- 如需隔离用户数据目录，可改 `USER_DATA_PATHS["windows"]`

## AI（可选）
如需启用 AI 价值判断，请提前安装并启动 Ollama：
```bash
ollama serve
```
并确保已安装配置中的模型（`config.AI_MODEL_NAME`）。
