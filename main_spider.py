from DrissionPage import ChromiumPage, ChromiumOptions
import time
import threading
import os
import config
from db_manager import DBManager

from spider_ai import AIWorker
from spider_comments import CommentsCrawler
from spider_tools import SpiderTools


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
        self._ai_worker = AIWorker(
            db=self.db,
            is_main_job_finished_fn=lambda: self.is_main_job_finished,
            on_saved=self._on_ai_saved,
        )
        self._comments_crawler = CommentsCrawler(init_browser_fn=self._init_browser)

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_browser_path(config.MAC_CHROME_PATH)
        co.set_user_data_path(config.USER_DATA_PATH)
        co.set_local_port(9337) 
        co.set_argument('--ignore-certificate-errors')
        try: return ChromiumPage(co)
        except Exception as e: 
            print(f"\n[启动错误] {e}"); exit()

    def _on_ai_saved(self):
        self.total_ai_saved += 1

    def global_ai_worker(self):
        self._ai_worker.run()

    def step3_batch_mine(self):
        self.driver = self._comments_crawler.step3_batch_mine(self.driver, self.db)

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
                SpiderTools.safe_action(self.driver)
                if new_hq_added_in_this_batch >= config.PIPELINE_BATCH_SIZE: break 

                next_btn = tab.ele('.pagination__next', timeout=3)
                if not next_btn or not next_btn.states.is_displayed: 
                    self.db.mark_user_as_scanned(current_source_id); break
                
                next_btn.click(by_js=True)
                SpiderTools.random_sleep()
                
                res = tab.listen.wait(timeout=6)
                if res and 'users' in res.response.body:
                    users = res.response.body['users']
                    new_users = []
                    new_hq = []
                    now_str = SpiderTools.get_now_str()

                    for u in users:
                        uid = u.get('id')
                        if uid in self.existing_ids: continue
                        self.existing_ids.add(uid)
                        
                        row = (uid, u.get('screen_name'), u.get('comments_count', 0),
                               u.get('friends_count', 0), u.get('followers_count', 0), 
                               u.get('text', ''), now_str) 
                        new_users.append(row)
                        
                        if int(u.get('followers_count', 0)) > config.MIN_FOLLOWERS \
                              and int(u.get('comments_count', 0) > config.MIN_COMMENTS): 
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
            
            SpiderTools.safe_action(self.driver)
            try:
                tab.get(f"https://xueqiu.com/u/{uid}")
                SpiderTools.random_sleep(1.5, 2.0)
                tab.listen.start(config.API['STOCK'])
                
                stock_btn = tab.ele('tag:a@@href=#/stock', timeout=4)
                if stock_btn:
                    stock_btn.click()
                    end_time = time.time() + 4
                    has_agu = False; has_waipan = False; now_str = SpiderTools.get_now_str()
                    
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
                                comb_list.append((uid, item.get('symbol'), item.get('name'), float(item.get('net_value',0) or 0), str(item.get('total_gain',0)), str(item.get('monthly_gain',0)), str(item.get('daily_gain',0)), now_str, str(item.get('closed_at',0))))
                            if comb_list: self.db.execute_many_safe("INSERT OR REPLACE INTO User_Combinations (User_Id, Symbol, Name, Net_Value, Total_Gain, Monthly_Gain, Daily_Gain, Updated_At, Close_At_Time) VALUES (?,?,?,?,?,?,?,?,?)", comb_list)

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

    def run(self):
        print(">>> 启动...")
        ai_thread = threading.Thread(target=self.global_ai_worker, daemon=False)
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
            while left > 0:
                print(f">>> 提示: AI 线程还在处理剩余的 {left} 条数据...")
                print(">>> 等待 AI 处理完成...")
                ai_thread.join(timeout=20)  # 最多等 1 小时，防止卡死
                left = self.db.get_unanalyzed_count()
            print(">>> 程序安全退出")

if __name__ == '__main__':
    bot = XueqiuSpider()
    bot.run()
