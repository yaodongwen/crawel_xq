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
            ts = float(timestamp) / 1000
            time_local = time.localtime(ts)
            return time.strftime("%Y-%m-%d %H:%M:%S", time_local)
        except: return str(timestamp)

    def random_sleep(self, min_s=1.0, max_s=2.0):
        time.sleep(random.uniform(min_s, max_s))

    def safe_action(self):
        self._check_405()
        # 循环处理滑块，直到消失
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
        # print("\n>>> [滑块] 动作执行...")
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

    # ================= AI 线程 (保持不变) =================
    
    def global_ai_worker(self):
        print(">>> [后台AI] 引擎启动...")
        while True:
            raw_batch = self.db.get_unanalyzed_raw_data(limit=10)
            if not raw_batch:
                if self.is_main_job_finished: break
                time.sleep(2); continue

            for row in raw_batch:
                sid, content = row['Status_Id'], row['Description']
                clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')
                
                if len(clean) < 30:
                    self.db.mark_raw_as_analyzed(sid, 1); continue

                # 2. 优化 prompt
                prompt = f"""判断以下评论是否有投资价值（含个股逻辑或行业干货）：
                评论：{clean}
                仅输出JSON:{{"valuable": true}} 或 {{"valuable": false}}"""
                try:
                    res = ollama.chat(model=config.AI_MODEL_NAME, messages=[{'role':'user','content':prompt}], format='json',
                                        options={
                                            "num_predict": 20,
                                            "temperature": 0.1,
                                            "num_ctx": 2048,
                                    })
                    js = json.loads(res['message']['content'])
                    if js.get('valuable', False):
                        self.db.execute_one_safe(
                            "INSERT OR IGNORE INTO Value_Comments VALUES (?,?,?,?,?,?)",
                            (sid, row['User_Id'], row['Description'], row['Created_At'], row['Stock_Tags'], js.get('cat', '未知'))
                        )
                        self.total_ai_saved += 1
                    self.db.mark_raw_as_analyzed(sid, 1)
                except: self.db.mark_raw_as_analyzed(sid, 2)

    # ================= Step 1: 批次扫描 =================

    def step1_batch_scan(self):
        """
        扫描关注列表，直到找到 BATCH_SIZE 个新的优质用户，或者扫描完当前宿主。
        """
        # 如果已经有足够多的待处理 Step 2 任务，就先跳过 Step 1，防止堆积太多
        pending_hq = len(self.db.get_pending_tasks("High_quality_users", limit=config.PIPELINE_BATCH_SIZE * 5))
        if pending_hq >= config.PIPELINE_BATCH_SIZE * 5:
             # print(">>> [跳过Step1] 待筛选用户充足，优先去筛选...")
             return

        # === 【修改点】在这里检查总人数上限 ===
        # 如果库里的人数已经超过了设定的限制，就不再扫描新人了，直接返回
        current_users_count = self.db.get_total_users_count()
        if current_users_count >= config.FOCUS_COUNT_LIMIT:
            # 可以在这里打印一句提示，也可以不打印，保持清爽
            # print(f">>> [跳过Step1] 用户库已满 ({current_users_count}/{config.FOCUS_COUNT_LIMIT})")
            return

        print(f"\n=== Step 1: 寻找新用户 (目标新增: {config.PIPELINE_BATCH_SIZE} 人) ===")
        
        # 寻找宿主
        if self.db.is_user_scanned(self.seed_id): current_source_id = None
        else: current_source_id = self.seed_id
        
        # 如果没有指定宿主，找新的
        if not current_source_id:
            next_user = self.db.get_next_source_user()
            if not next_user: print(">>> 无可用宿主"); return
            current_source_id = next_user['User_Id']
            print(f">>> 切换宿主: {next_user['User_Name']}")
        else:
            print(f">>> 继续宿主: {current_source_id}")

        tab = self.driver.latest_tab
        new_hq_added_in_this_batch = 0 # 本批次计数器
        
        try:
            tab.get(f"https://xueqiu.com/u/{current_source_id}")
            time.sleep(2)
            if "follow" not in tab.url:
                btn = tab.ele('tag:a@@href=#/follow', timeout=3)
                if btn: btn.click()
                else: 
                    self.db.mark_user_as_scanned(current_source_id)
                    return # 换人
            
            tab.listen.start(config.API['FOCUS'])
            page_count = 0
            
            # 翻页循环
            while True:
                self.safe_action()
                
                # 退出条件1: 本批次任务完成
                if new_hq_added_in_this_batch >= config.PIPELINE_BATCH_SIZE:
                    # print(f">>> [暂停Step1] 本批次已找到 {new_hq_added_in_this_batch} 个新人，转入筛选...")
                    break 

                next_btn = tab.ele('.pagination__next', timeout=3)
                if not next_btn or not next_btn.states.is_displayed: 
                    self.db.mark_user_as_scanned(current_source_id) # 到底了，标记完成
                    break
                
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
                            hq_row = list(row)
                            hq_row[-1] = None 
                            new_hq.append(tuple(hq_row))
                            
                            new_hq_added_in_this_batch += 1
                    
                    if new_users: self.db.execute_many_safe("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?)", new_users)
                    if new_hq: self.db.execute_many_safe("INSERT OR IGNORE INTO High_quality_users VALUES (?,?,?,?,?,?,?)", new_hq)
                    
                    print(f"    [扫描] 本轮新增优质: {new_hq_added_in_this_batch}/{config.PIPELINE_BATCH_SIZE}", end='\r')

                page_count += 1
                # 限制单人扫描页数，防止死磕一个人
                if page_count > 20: 
                    # print("    单人扫描超过20页，暂时切换...")
                    break 
            
            tab.listen.stop()

        except Exception as e:
            # print(f"Step1 Err: {e}")
            pass

    # ================= Step 2: 批次筛选 =================

    def step2_batch_filter(self):
        # 只取 BATCH_SIZE 个待办
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
                    
                    # 循环监听
                    end_time = time.time() + 4
                    has_agu = False; has_waipan = False; now_str = self._get_now_str()
                    
                    while time.time() < end_time:
                        res = tab.listen.wait(timeout=1.0)
                        if not res: continue
                        data = res.response.body
                        if not data: continue

                        # A. 组合
                        if 'net_value' in str(data):
                            comb_list = []
                            iterator = data.values() if isinstance(data, dict) else data
                            for item in iterator:
                                if not isinstance(item, dict) or 'symbol' not in item: continue
                                comb_list.append((uid, item.get('symbol'), item.get('name'), float(item.get('net_value',0) or 0), str(item.get('total_gain',0)), str(item.get('monthly_gain',0)), str(item.get('daily_gain',0)), now_str))
                            if comb_list: self.db.execute_many_safe("INSERT OR REPLACE INTO User_Combinations (User_Id, Symbol, Name, Net_Value, Total_Gain, Monthly_Gain, Daily_Gain, Updated_At) VALUES (?,?,?,?,?,?,?,?)", comb_list)

                        # B. 自选股
                        else:
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
                        target_data = list(row)
                        # 【核心修改】新发现的目标，时间必须设为 None，Step 3 才会去爬它！
                        target_data[-1] = None 
                        
                        self.db.execute_one_safe("INSERT OR IGNORE INTO Target_users VALUES (?,?,?,?,?,?,?)", tuple(target_data))
                        self.target_ids_cache.add(uid)
                    tab.listen.stop()
                
                self.db.update_task_status(uid, "High_quality_users")
            except: pass

    # ================= Step 3: 批次爬取 =================

    def step3_batch_mine(self):
        # 只取 BATCH_SIZE 个待办
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
                # 【修改点1】使用更短的关键词，防止 API 变动导致匹配不上
                # 雪球 API 通常包含 'user_timeline.json'
                target_api = 'user_timeline.json'
                tab.listen.start(target_api)
                
                tab.get(f"https://xueqiu.com/u/{uid}")
                
                # 【修改点2】第一页：死等数据包返回，最多等 5 秒
                # 这种 wait 模式比 steps 更稳健，它会一直阻塞直到抓到那个特定包
                res = tab.listen.wait(timeout=5)
                
                pages = int(config.ARTICLE_COUNT_LIMIT / 10)
                total_added = 0 
                
                # 处理第一页数据
                if res and res.response.body and 'statuses' in res.response.body:
                    raw_rows = []
                    for s in res.response.body['statuses']:
                        readable_time = self._format_time(s['created_at'])
                        raw_rows.append((s['id'], s['user_id'], s['description'], readable_time, str(s.get('stockCorrelation','')), 0))
                    if raw_rows:
                        self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed) VALUES (?,?,?,?,?,?)", raw_rows)
                        total_added += len(raw_rows)
                else:
                    # 如果第一页都没抓到，打印一下它到底抓到了啥，方便调试
                    if res:
                        print(f"    ⚠️ 第一页数据包异常，URL: {res.request.url}")
                    else:
                        print(f"    ⚠️ 第一页超时未抓到包")

                # 处理后续翻页
                for p in range(pages - 1): # 减1是因为刚才已经处理了第0页
                    # 翻页前检查滑块
                    if self._has_slider(): self.safe_action()

                    next_btn = tab.ele('.pagination__next', timeout=2)
                    if next_btn and next_btn.states.is_displayed: 
                        next_btn.click(by_js=True)
                        
                        # 【修改点3】翻页后也是死等
                        res = tab.listen.wait(timeout=5)
                        
                        if res and res.response.body and 'statuses' in res.response.body:
                            raw_rows = []
                            for s in res.response.body['statuses']:
                                readable_time = self._format_time(s['created_at'])
                                raw_rows.append((s['id'], s['user_id'], s['description'], readable_time, str(s.get('stockCorrelation','')), 0))
                            if raw_rows:
                                self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed) VALUES (?,?,?,?,?,?)", raw_rows)
                                total_added += len(raw_rows)
                    else: 
                        break # 没下一页了
                
                print(f"    -> 完成: {uname} (入库 {total_added} 条)")
                
                tab.listen.stop()
                self.db.update_task_status(uid, "Target_users")
                
            except Exception as e:
                print(f"    ❌ 异常 [{uname}]: {e}")
                # 如果是连接断开，尝试重启
                if "断开" in str(e) or "disconnected" in str(e): 
                    self._restart_browser()
                    tab = self.driver.latest_tab
                else: 
                    tab.listen.stop()

    # === 【新增】统计报告打印 ===
    def print_report(self):
        print("\n" + "="*60)
        print("                 📊 爬虫运行报告 📊")
        print("="*60)
        
        total_users = self.db.get_total_users_count()
        total_targets = self.db.get_target_count()
        total_comments = self.db.get_total_comments_count()
        ai_left = self.db.get_unanalyzed_count()
        db_size = self.db.get_db_size()
        
        print(f"1. 👥 用户扫描总库:  {total_users} / {config.FOCUS_COUNT_LIMIT} 人")
        print(f"2. 🎯 目标用户(双修): {total_targets} / {config.TARGET_GOAL} 人")
        print(f"3. 💎 高价值评论入库: {total_comments} 条")
        print(f"4. ⏳ AI后台积压数据: {ai_left} 条 (建议跑 run_ai_only.py 消化)")
        print(f"5. 💾 数据库文件大小: {db_size} MB")
        
        print("-" * 60)
        print("🛑 停止原因判定:")
        
        if total_targets >= config.TARGET_GOAL:
            print("   ✅ 【成功】已收集到足够的目标用户！")
        elif total_users >= config.FOCUS_COUNT_LIMIT:
            print("   ⚠️ 【上限】已达到扫描用户数量上限，建议增加 FOCUS_COUNT_LIMIT。")
        else:
            print("   👋 【手动】用户手动中断或暂无更多新数据。")
        print("="*60 + "\n")

    def run(self):
        print(">>> 启动...")
        ai_thread = threading.Thread(target=self.global_ai_worker, daemon=True)
        ai_thread.start()
        
        self.driver.get("https://xueqiu.com")
        print("\n" + "="*50); input(">>> 请扫码登录，完成后按【回车】..."); print("="*50 + "\n")
        
        # === 使用 try...except 捕获 Ctrl+C ===
        try:
            while True:
                # 1. 检查目标是否达成 (只有目标达成才是真正的“完结撒花”)
                current_targets = self.db.get_target_count()
                if current_targets >= config.TARGET_GOAL:
                    print("\n>>> 🎉🎉🎉 恭喜！目标用户收集完成！🎉🎉🎉")
                    break 
                
                # === 【修改点】移除这里的 FOCUS_COUNT_LIMIT 检查 ===
                # 不要在这里 break！
                # 即使 current_users >= 100，也要继续循环，因为 step2 和 step3 可能还有活要干
                
                current_users = self.db.get_total_users_count()
                
                # 打印进度条
                ai_backlog = self.db.get_unanalyzed_count()
                print(f"\n>>> [循环] 目标:{current_targets}/{config.TARGET_GOAL} | 用户库:{current_users}/{config.FOCUS_COUNT_LIMIT} | AI积压:{ai_backlog}")
                
                # 流水线作业
                self.step3_batch_mine()   # 优先消化库存
                self.step2_batch_filter() # 优先筛选库存
                self.step1_batch_scan()   # 最后才考虑进货 (内部会检查 LIMIT)
                
                # 如果所有步骤都没事干了（比如 Step1被限流，Step2/3也没待办），可以睡久一点避免空转
                # 简单的处理是每次都睡 2 秒
                time.sleep(2)

        except KeyboardInterrupt:
            print("\n\n>>> 🛑 检测到用户中断 (Ctrl+C)...")
        
        except Exception as e:
            print(f"\n\n>>> ❌ 发生未捕获异常: {e}")

        finally:
            self.is_main_job_finished = True
            self.print_report()
            
            left = self.db.get_unanalyzed_count()
            if left > 0:
                print(f">>> 提示: AI 线程还在处理剩余的 {left} 条数据...")
                # ai_thread.join()

if __name__ == '__main__':
    bot = XueqiuSpider()
    bot.run()