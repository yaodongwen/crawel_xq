from DrissionPage import ChromiumPage, ChromiumOptions
import time
import random
import json
import re
import threading
import os
from datetime import datetime
from tqdm import tqdm
import config
from db_manager import DBManager

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    print(">>> 警告: 未安装 ollama")
    HAS_OLLAMA = False

class XueqiuSpider:
    def __init__(self):
        print(">>> [系统] 正在清理残留进程...")
        os.system("pkill -f 'Google Chrome'") 
        time.sleep(2) 

        print(">>> 初始化数据库...")
        self.db = DBManager()
        self.seed_id = config.SEED_USER_URL.split('u/')[-1]
        
        self.existing_ids = self.db.get_existing_user_ids()
        self.target_ids_cache = self.db.get_existing_target_ids()
        
        print(">>> 启动浏览器...")
        self.driver = self._init_browser()
        
        self.total_ai_saved = 0
        self.is_main_job_finished = False 

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_browser_path(config.MAC_CHROME_PATH)
        co.set_user_data_path(config.USER_DATA_PATH)
        co.set_local_port(9337) 
        co.set_argument('--ignore-certificate-errors')
        try: return ChromiumPage(co)
        except Exception as e: 
            print(f"\n[启动错误] {e}"); exit()

    # ================= 工具方法 =================
    
    def _get_now_str(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_time(self, timestamp):
        try:
            # 兼容 Unix 时间戳
            if str(timestamp).isdigit():
                ts = float(timestamp) / 1000
                time_local = time.localtime(ts)
                return time.strftime("%Y-%m-%d %H:%M:%S", time_local)
            # 兼容参考代码中的 datetime 字符串格式
            return str(timestamp)
        except: return str(timestamp)

    def random_sleep(self, min_s=1.0, max_s=2.0):
        time.sleep(random.uniform(min_s, max_s))

    def safe_action(self):
        self._check_405()
        max_retries = 10
        count = 0
        while self._has_slider():
            count += 1
            if count > 1: print(f">>> [滑块] 第 {count} 次尝试...")
            self._solve_slider()
            time.sleep(2)
            if count >= max_retries:
                print(">>> [滑块] 尝试次数过多，刷新页面..."); 
                self.driver.latest_tab.refresh(); time.sleep(3); count=0

    def _has_slider(self):
        try:
            tab = self.driver.latest_tab
            return tab.ele('#aliyunCaptcha-sliding-slider', timeout=0.1) or tab.ele('text:访问验证', timeout=0.1)
        except: return False

    def _solve_slider(self):
        tab = self.driver.latest_tab
        time.sleep(1)
        try:
            btn = tab.ele('#aliyunCaptcha-sliding-slider', timeout=3)
            if btn: btn.drag(random.randint(400, 600), random.randint(5, 10))
        except: pass

    def _check_405(self):
        try:
            if "405" in self.driver.latest_tab.title:
                print("\n>>> [严重] 触发405，暂停15分钟...")
                time.sleep(900)
                self.driver.latest_tab.refresh()
        except: pass
    
    def _restart_browser(self):
        try: self.driver.quit()
        except: pass
        os.system("pkill -f 'Google Chrome'")
        time.sleep(2)
        self.driver = self._init_browser()

    # ================= AI 线程 =================
    
    def global_ai_worker(self):
        print(">>> [后台AI] 引擎已启动，调试模式开启...")
        while True:
            raw_batch = self.db.get_unanalyzed_raw_data(limit=10)
            if not raw_batch:
                if self.is_main_job_finished: break
                time.sleep(2); continue

            for row in raw_batch:
                sid, content = row['Status_Id'], row['Description']
                clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')
                
                if len(clean) < 10: 
                    self.db.mark_raw_as_analyzed(sid, 1); continue

                prompt = f"""任务：判断这条财经评论是否有含金量。
                评论内容："{clean}"
                规则：1. 包含具体股票分析、逻辑、数据、新闻解读 -> valuable: true
                2. 纯情绪发泄、打卡、无意义水贴 -> valuable: false
                必须返回JSON格式：{{"valuable": true/false, "cat": "分类标签"}}"""
                
                try:
                    res = ollama.chat(
                        model=config.AI_MODEL_NAME, 
                        messages=[{'role':'user','content':prompt}],
                        format='json', options={"temperature": 0.1}
                    )
                    js = json.loads(res['message']['content'])
                    valuable = js.get('valuable', False)
                    cat = js.get('cat', '其他')
                    
                    final_cat = cat if valuable else f"[低价值]-{cat}"
                    self.db.execute_one_safe(
                        "INSERT OR IGNORE INTO Value_Comments VALUES (?,?,?,?,?,?)",
                        (sid, row['User_Id'], row['Description'], row['Created_At'], row['Stock_Tags'], final_cat)
                    )
                    
                    if valuable:
                        print(f"    [AI] 🟢 收录 | {cat} | {clean[:15]}...")
                        self.total_ai_saved += 1
                    else:
                        print(f"    [AI] ⚪ 丢弃 | {cat} | {clean[:15]}...", end='\r')
                    self.db.mark_raw_as_analyzed(sid, 1)
                except Exception as e:
                    self.db.mark_raw_as_analyzed(sid, 2)

    # ================= Step 1: 批次扫描 =================

    def step1_batch_scan(self):
        pending_hq = len(self.db.get_pending_tasks("High_quality_users", limit=config.PIPELINE_BATCH_SIZE * 5))
        if pending_hq >= config.PIPELINE_BATCH_SIZE * 5: return

        current_users_count = self.db.get_total_users_count()
        if current_users_count >= config.FOCUS_COUNT_LIMIT: return

        print(f"\n=== Step 1: 寻找新用户 (目标新增: {config.PIPELINE_BATCH_SIZE} 人) ===")
        if self.db.is_user_scanned(self.seed_id): current_source_id = None
        else: current_source_id = self.seed_id
        
        if not current_source_id:
            next_user = self.db.get_next_source_user()
            if not next_user: print(">>> 无可用宿主"); return
            current_source_id = next_user['User_Id']
            print(f">>> 切换宿主: {next_user['User_Name']}")
        else: print(f">>> 继续宿主: {current_source_id}")

        tab = self.driver.latest_tab
        new_hq_added_in_this_batch = 0
        
        try:
            tab.get(f"https://xueqiu.com/u/{current_source_id}")
            time.sleep(2)
            if "follow" not in tab.url:
                btn = tab.ele('tag:a@@href=#/follow', timeout=3)
                if btn: btn.click()
                else: 
                    self.db.mark_user_as_scanned(current_source_id); return
            
            tab.listen.start(config.API['FOCUS'])
            page_count = 0
            
            while True:
                self.safe_action()
                if new_hq_added_in_this_batch >= config.PIPELINE_BATCH_SIZE: break 

                next_btn = tab.ele('.pagination__next', timeout=3)
                if not next_btn or not next_btn.states.is_displayed: 
                    self.db.mark_user_as_scanned(current_source_id); break
                
                next_btn.click(by_js=True)
                self.random_sleep()
                
                res = tab.listen.wait(timeout=6)
                if res and 'users' in res.response.body:
                    users = res.response.body['users']
                    new_users = []
                    new_hq = []
                    now_str = self._get_now_str()

                    for u in users:
                        uid = u.get('id')
                        if uid in self.existing_ids: continue
                        self.existing_ids.add(uid)
                        
                        row = (uid, u.get('screen_name'), u.get('status_count', 0),
                               u.get('friends_count', 0), u.get('followers_count', 0), 
                               u.get('description', ''), now_str) 
                        new_users.append(row)
                        
                        if int(u.get('followers_count', 0)) > 5000: 
                            hq_row = list(row); hq_row[-1] = None 
                            new_hq.append(tuple(hq_row))
                            new_hq_added_in_this_batch += 1
                    
                    if new_users: self.db.execute_many_safe("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?)", new_users)
                    if new_hq: self.db.execute_many_safe("INSERT OR IGNORE INTO High_quality_users VALUES (?,?,?,?,?,?,?)", new_hq)
                    print(f"    [扫描] 本轮新增优质: {new_hq_added_in_this_batch}/{config.PIPELINE_BATCH_SIZE}", end='\r')

                page_count += 1
                if page_count > 20: break 
            tab.listen.stop()
        except Exception as e: pass

    # ================= Step 2: 批次筛选 =================

    def step2_batch_filter(self):
        pending = self.db.get_pending_tasks("High_quality_users", limit=config.PIPELINE_BATCH_SIZE)
        if not pending: return

        print(f"\n=== Step 2: 筛选持仓 (批次: {len(pending)} 人) ===")
        tab = self.driver.latest_tab
        
        for row in pending:
            uid, uname = row['User_Id'], row['User_Name']
            ai_left = self.db.get_unanalyzed_count()
            print(f"    Check: {uname} | AI待办: {ai_left}", end='\r')

            if uid in self.target_ids_cache:
                self.db.update_task_status(uid, "High_quality_users"); continue
            
            self.safe_action()
            try:
                tab.get(f"https://xueqiu.com/u/{uid}")
                self.random_sleep(1.5, 2.0)
                tab.listen.start(config.API['STOCK'])
                
                stock_btn = tab.ele('tag:a@@href=#/stock', timeout=4)
                if stock_btn:
                    stock_btn.click()
                    end_time = time.time() + 4
                    has_agu = False; has_waipan = False; now_str = self._get_now_str()
                    
                    while time.time() < end_time:
                        res = tab.listen.wait(timeout=1.0)
                        if not res: continue
                        data = res.response.body
                        if not data: continue

                        if 'net_value' in str(data): # 组合
                            comb_list = []
                            iterator = data.values() if isinstance(data, dict) else data
                            for item in iterator:
                                if not isinstance(item, dict) or 'symbol' not in item: continue
                                comb_list.append((uid, item.get('symbol'), item.get('name'), float(item.get('net_value',0) or 0), str(item.get('total_gain',0)), str(item.get('monthly_gain',0)), str(item.get('daily_gain',0)), now_str))
                            if comb_list: self.db.execute_many_safe("INSERT OR REPLACE INTO User_Combinations (User_Id, Symbol, Name, Net_Value, Total_Gain, Monthly_Gain, Daily_Gain, Updated_At) VALUES (?,?,?,?,?,?,?,?)", comb_list)

                        else: # 自选股
                            items = []
                            if isinstance(data, dict):
                                if 'data' in data and 'items' in data['data']: items = data['data']['items']
                                elif 'items' in data: items = data['items']
                            if items:
                                stock_list = []
                                for it in items:
                                    s = it.get('quote', it)
                                    symbol = s.get('symbol') or s.get('code', '')
                                    if not symbol: continue
                                    market = '未知'
                                    if symbol.startswith('SH') or symbol.startswith('SZ'): market = 'CN'; has_agu = True
                                    elif len(symbol)==5 and symbol.isdigit(): market = 'HK'; has_waipan = True
                                    elif '.' not in symbol and len(symbol)<5: market = 'US'; has_waipan = True
                                    stock_list.append((uid, s.get('name',''), symbol, float(s.get('current',0) or 0), float(s.get('percent',0) or 0), market, now_str))
                                if stock_list: self.db.execute_many_safe("INSERT OR REPLACE INTO User_Stocks (User_Id, Stock_Name, Stock_Symbol, Current_Price, Percent, Market, Updated_At) VALUES (?,?,?,?,?,?,?)", stock_list)

                    if has_agu and has_waipan:
                        target_data = list(row); target_data[-1] = None # Step 3 待办
                        self.db.execute_one_safe("INSERT OR IGNORE INTO Target_users VALUES (?,?,?,?,?,?,?)", tuple(target_data))
                        self.target_ids_cache.add(uid)
                    tab.listen.stop()
                
                self.db.update_task_status(uid, "High_quality_users")
            except: pass

    # ================= Step 3: 批次爬取 (含长文逻辑) =================

    def _mine_long_articles(self, tab, uid):
        """【新增】专门挖掘长文，获取完整内容"""
        try:
            # 1. 尝试点击“长文”标签
            # 使用 contains 模糊匹配防止页面微调
            long_tab = tab.ele('xpath://a[contains(text(), "长文")]', timeout=2)
            if not long_tab: return 0
            
            long_tab.click()
            self.random_sleep(1.5, 2.5)
            
            # 2. 获取当前页面所有长文卡片
            # 参考代码使用的 class 选择器
            articles = tab.eles('.timeline__item__content timeline__item__content--longtext', timeout=3)
            if not articles: return 0
            
            count = 0
            # 限制每次只爬前 5 篇长文，避免太慢
            for article_ele in articles[:5]:
                try:
                    # 点击进入长文详情页 (这会打开新标签或在当前页跳转，DrissionPage 会自动处理新 Tab)
                    article_ele.click()
                    time.sleep(2)
                    
                    # 获取最新标签页（即文章详情页）
                    detail_tab = self.driver.latest_tab
                    
                    # === 抓取逻辑 ===
                    current_url = detail_tab.url
                    # 提取 ID: https://xueqiu.com/12345/67890 -> status_id = 67890
                    parts = current_url.split('/')
                    if len(parts) >= 5:
                        comment_id = parts[-1]
                        
                        # 获取时间
                        pub_time = ""
                        time_ele = detail_tab.ele('xpath://div[@class="avatar__subtitle"]/a/time', timeout=2)
                        if time_ele:
                            # 可能是 text 或 datetime 属性
                            pub_time = time_ele.attr('datetime') or time_ele.text
                            pub_time = self._format_time(pub_time)

                        # 获取全量内容 (标题 + 正文)
                        title_ele = detail_tab.ele('.article__bd__title', timeout=2)
                        content_ele = detail_tab.ele('.article__bd__detail', timeout=2)
                        
                        full_text = ""
                        if title_ele: full_text += f"【长文标题】{title_ele.text}\n"
                        if content_ele: full_text += f"{content_ele.text}"
                        
                        if full_text and comment_id.isdigit():
                            # === 入库 ===
                            # 使用 REPLACE，如果之前 JSON 抓到过截断版，这里会用完整版覆盖
                            self.db.execute_one_safe(
                                "INSERT OR REPLACE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed) VALUES (?,?,?,?,?,?)",
                                (comment_id, uid, full_text, pub_time, "LongArticle", 0) # 重置为 0 让 AI 重新分析
                            )
                            count += 1
                            print(f"    --> [长文] 获取成功: {comment_id} (字数: {len(full_text)})")

                    # 关闭详情页，切回列表页
                    detail_tab.close()
                    time.sleep(1)
                    
                except Exception as e:
                    # print(f"长文抓取单条失败: {e}")
                    # 如果出错了，确保把可能打开的标签页关掉
                    if self.driver.tabs_count > 1:
                        self.driver.latest_tab.close()
            
            return count

        except Exception as e:
            # print(f"长文模块异常: {e}")
            return 0

    def step3_batch_mine(self):
        pending = self.db.get_pending_tasks("Target_users", limit=config.PIPELINE_BATCH_SIZE)
        if not pending: return

        print(f"\n=== Step 3: 爬取评论 (批次: {len(pending)} 人) ===")
        tab = self.driver.latest_tab
        
        for row in pending:
            uid, uname = row['User_Id'], row['User_Name']
            ai_left = self.db.get_unanalyzed_count()
            print(f"    User: {uname} | AI待办: {ai_left}")
            
            self.safe_action()
            try:
                # === 阶段 1: 快速抓取 JSON (短贴 + 动态) ===
                target_api = 'user_timeline.json'
                tab.listen.start(target_api)
                
                tab.get(f"https://xueqiu.com/u/{uid}")
                
                # 等待第一页 JSON
                res = tab.listen.wait(timeout=5)
                
                total_added = 0 
                if res and res.response.body and 'statuses' in res.response.body:
                    raw_rows = []
                    for s in res.response.body['statuses']:
                        readable_time = self._format_time(s['created_at'])
                        raw_rows.append((s['id'], s['user_id'], s['description'], readable_time, str(s.get('stockCorrelation','')), 0))
                    if raw_rows:
                        self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed) VALUES (?,?,?,?,?,?)", raw_rows)
                        total_added += len(raw_rows)
                else:
                     if not res: print(f"    ⚠️ 第一页超时")

                # 简单翻两页 (获取更多短贴)
                for p in range(2): 
                    if self._has_slider(): self.safe_action()
                    next_btn = tab.ele('.pagination__next', timeout=2)
                    if next_btn and next_btn.states.is_displayed: 
                        next_btn.click(by_js=True)
                        res = tab.listen.wait(timeout=5)
                        if res and res.response.body and 'statuses' in res.response.body:
                            raw_rows = []
                            for s in res.response.body['statuses']:
                                readable_time = self._format_time(s['created_at'])
                                raw_rows.append((s['id'], s['user_id'], s['description'], readable_time, str(s.get('stockCorrelation','')), 0))
                            if raw_rows:
                                self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed) VALUES (?,?,?,?,?,?)", raw_rows)
                                total_added += len(raw_rows)
                    else: break
                
                tab.listen.stop()

                # === 阶段 2: 深度抓取长文 (获取完整逻辑) ===
                # 这里调用新增的方法
                long_count = self._mine_long_articles(tab, uid)
                
                print(f"    -> 完成: {uname} (短贴: {total_added}, 长文补全: {long_count})")
                self.db.update_task_status(uid, "Target_users")
                
            except Exception as e:
                print(f"    ❌ 异常 [{uname}]: {e}")
                if "断开" in str(e) or "disconnected" in str(e): 
                    self._restart_browser(); tab = self.driver.latest_tab
                else: tab.listen.stop()

    def run(self):
        print(">>> 启动...")
        ai_thread = threading.Thread(target=self.global_ai_worker, daemon=True)
        ai_thread.start()
        
        self.driver.get("https://xueqiu.com")
        print("\n" + "="*50); input(">>> 请扫码登录，完成后按【回车】..."); print("="*50 + "\n")
        
        try:
            while True:
                current_targets = self.db.get_target_count()
                if current_targets >= config.TARGET_GOAL:
                    print("\n>>> 🎉🎉🎉 恭喜！目标用户收集完成！🎉🎉🎉"); break 
                
                current_users = self.db.get_total_users_count()
                ai_backlog = self.db.get_unanalyzed_count()
                print(f"\n>>> [循环] 目标:{current_targets}/{config.TARGET_GOAL} | 用户库:{current_users}/{config.FOCUS_COUNT_LIMIT} | AI积压:{ai_backlog}")
                
                self.step3_batch_mine()   
                self.step2_batch_filter() 
                self.step1_batch_scan()   
                time.sleep(2)

        except KeyboardInterrupt: print("\n\n>>> 🛑 检测到用户中断 (Ctrl+C)...")
        except Exception as e: print(f"\n\n>>> ❌ 发生未捕获异常: {e}")
        finally:
            self.is_main_job_finished = True
            left = self.db.get_unanalyzed_count()
            if left > 0: print(f">>> 提示: AI 线程还在处理剩余的 {left} 条数据...")

if __name__ == '__main__':
    bot = XueqiuSpider()
    bot.run()