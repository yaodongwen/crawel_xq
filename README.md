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

需要修改配置可以去config.py文件，然后就可以一键使用了。