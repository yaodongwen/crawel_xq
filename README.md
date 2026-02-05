为了清理残余进程，先运行：
```bash
pkill -f python
pkill -f main_spider
pkill -f "Google Chrome"
```

然后再运行：
```bash
python main_spider.py
```

需要修改配置可以去 `config.py` 文件，然后就可以一键使用了。

### Windows 支持（配置切换）
在 `config.py` 中修改：
- `OS_TYPE = "windows"`
- 如果你的 Chrome 路径不同，更新 `CHROME_PATHS["windows"]`
- 如需隔离用户数据目录，可改 `USER_DATA_PATHS["windows"]`
