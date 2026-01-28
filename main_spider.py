from DrissionPage import ChromiumPage, ChromiumOptions
import time
import random
import gzip
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
        # === 【新增】启动时主动探测 Ollama 是否可用 ===
        if HAS_OLLAMA:
            try:
                models = ollama.list()
                model_names = [m['model'] for m in models.get('models', [])]
                if config.AI_MODEL_NAME not in model_names:
                    print(f"⚠️ 警告: 指定模型 '{config.AI_MODEL_NAME}' 未安装！")
                    print(f"   可用模型: {model_names[:5]}{'...' if len(model_names)>5 else ''}")
                else:
                    print(f"✅ Ollama 服务正常，使用模型: {config.AI_MODEL_NAME}")
            except Exception as e:
                print(f"❌ Ollama 服务不可用 (可能未启动): {e}")
                print("   请确保运行: ollama serve")
        else:
            print("❌ Ollama 未安装，AI 功能将跳过所有内容")

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
                规则：1. 如果包含具体股票分析、逻辑、数据、新闻解读等能有助于判断股票涨势的信息，则valuable字段为true。反之，如果全都在讨论和股票、行业无关内容，则valuable为false。
                2. 如果评论里面有股票,则在cat中输出股票的类别，比如:A股，美股，港股，日股，韩股，德股等。否则输出其他。
                必须返回JSON格式：{{"valuable": true/false, "cat": "股票类别"}}"""
                
                try:
                    res = ollama.chat(
                        model=config.AI_MODEL_NAME, 
                        messages=[{'role':'user','content':prompt}],
                        format='json', options={
                            "temperature": 0.1,      # 更确定性
                            "num_predict": 30,       # 严格限制输出长度
                            "top_k": 15,
                            "top_p": 0.85
                        }
                    )
                    js = json.loads(res['message']['content'])
                    valuable = js.get('valuable', False)
                    cat = js.get('cat', '其他')
                    
                    final_cat = cat if valuable else f"[低价值]-{cat}"
                    self.db.execute_one_safe(
                        "INSERT OR IGNORE INTO Value_Comments VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, row['User_Id'], row['Description'], row['Created_At'], row['Stock_Tags'],
                        final_cat,row['Forward'],row['Comment_Count'],row['Like'])
                    )
                    
                    if valuable:
                        print(f"    [AI] 🟢 收录 | {cat} | {clean[:15]}...")
                        self.total_ai_saved += 1
                    else:
                        print(f"    [AI] ⚪ 丢弃 | {cat} | {clean[:15]}...", end='\r')
                    self.db.mark_raw_as_analyzed(sid, 1)
                except Exception as e:
                    print(f"error in AI: {e}")
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
                               u.get('text', ''), now_str) 
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

    # ================= Step 3: 批次爬取 (含长文逻辑) =================

    def _mine_long_articles(self, uid, status_id):
        """
        【修改版】长文获取逻辑：
        直接新建标签页访问长文 URL (https://xueqiu.com/uid/id)，
        抓取完整标题和正文后返回。
        """
        try:
            # 构造长文链接
            url = f"https://xueqiu.com/{uid}/{status_id}"
            
            # 打开新标签页 (DrissionPage 会自动切换焦点到新页面)
            detail_tab = self.driver.new_tab(url)
            
            # 等待核心元素加载 (标题或正文)
            # 给 5 秒超时，防止页面加载太慢卡住
            title_ele = detail_tab.ele('.article__bd__title', timeout=5)
            content_ele = detail_tab.ele('.article__bd__detail', timeout=5)
            
            full_text = ""
            if title_ele: 
                full_text += f"【长文标题】{title_ele.text}\n"
            if content_ele: 
                full_text += f"{content_ele.text}"
            
            # 抓取完成后关闭当前长文页
            detail_tab.close()
            
            # 如果没抓到内容，返回 None
            if not full_text:
                return None
            
            # print(f"    --> [补全成功] 长文 {status_id} ({len(full_text)}字)")   
            return full_text

        except Exception as e:
            # print(f"    ⚠️ 长文补全失败 {status_id}: {e}")
            # 异常保护：如果标签页没关掉，强制关闭
            if self.driver.tabs_count > 1:
                # 简单判断一下当前页是不是列表页，如果不是就关掉
                if str(uid) not in self.driver.latest_tab.url:
                    self.driver.latest_tab.close()
            return None
        
    def step3_batch_mine(self):
        pending = self.db.get_pending_tasks("Target_users", limit=config.PIPELINE_BATCH_SIZE)
        if not pending: return

        print(f"\n=== Step 3: 爬取评论 (批次: {len(pending)} 人) ===")
        
        # 获取当前的列表页 Tab 对象
        list_tab = self.driver.latest_tab
        
        for row in pending:
            uid, uname = row['User_Id'], row['User_Name']
            ai_left = self.db.get_unanalyzed_count()
            print(f"    User: {uname} | AI待办: {ai_left}")
            
            self.safe_action()
            try:
                target_api = 'user_timeline.json'
                list_tab.listen.start(target_api)
                
                list_tab.get(f"https://xueqiu.com/u/{uid}")
                
                # 等待第一页
                res = list_tab.listen.wait(timeout=5)
                
                total_added = 0 
                
                # --- 定义内部函数：统一处理每一页的数据解析逻辑 ---
                # 这样第一页和翻页后的代码不用写两遍
                def process_page_data(response_data):
                    rows = []
                    if response_data and 'statuses' in response_data:
                        for s in response_data['statuses']:
                            readable_time = self._format_time(s['created_at'])
                            
                            # === 1. 尝试获取普通内容 ===
                            content = s.get('text', '')
                            if not content: 
                                content = s.get('description', '')
                            
                            # === 2. 【核心新增逻辑】检测长文并补全 ===
                            # 如果 type 是 1 或 3，说明是长文/专栏，必须进去抓
                            # 或者 content 只有 "..." 结尾的截断内容，也可以尝试抓一下
                            post_type = str(s.get('type', '0'))
                            
                            if post_type in ['1', '3']:
                                # 调用上面的 _mine_long_articles 方法
                                # print(f"    检测到长文(type={post_type})，正在补全...")
                                full_text = self._mine_long_articles(uid, s['id'])
                                if full_text:
                                    content = full_text # 用抓到的完整长文覆盖截断内容
                            
                            rows.append((
                                s['id'], 
                                s['user_id'], 
                                content, 
                                readable_time, 
                                str(s.get('stockCorrelation','')), 
                                0, 
                                s.get('retweet_count', 0), 
                                s.get('reply_count', 0), 
                                s.get('like_count', 0)
                            ))
                    return rows

                # --- 处理第一页 ---
                data = self._decode_response(res)
                if data:
                    raw_rows = process_page_data(data)
                    if raw_rows:
                        self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed, Forward, Comment_Count, Like) VALUES (?,?,?,?,?,?,?,?,?)", raw_rows)
                        total_added += len(raw_rows)
                else:
                    if not res: print(f"    ⚠️ 第一页超时或无数据")

                # --- 循环翻页直到达标 ---
                while total_added < config.ARTICLE_COUNT_LIMIT:
                    if self._has_slider(): self.safe_action()
                    
                    next_btn = list_tab.ele('.pagination__next', timeout=2)
                    if next_btn and next_btn.states.is_displayed: 
                        next_btn.click(by_js=True)
                        
                        # 等待下一页数据包
                        res = list_tab.listen.wait(timeout=5)
                        data = self._decode_response(res)
                        
                        if data:
                            raw_rows = process_page_data(data)
                            if raw_rows:
                                self.db.execute_many_safe("INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed, Forward, Comment_Count, Like) VALUES (?,?,?,?,?,?,?,?,?)", raw_rows)
                                total_added += len(raw_rows)
                            else:
                                break # 有包但没数据，可能到底了
                        else:
                            break # 没包
                    else: 
                        break # 没按钮了
                
                list_tab.listen.stop()
                print(f"    -> 完成: {uname} (入库: {total_added})")
                self.db.update_task_status(uid, "Target_users")
                
            except Exception as e:
                print(f"    ❌ 异常 [{uname}]: {e}")
                if "断开" in str(e) or "disconnected" in str(e): 
                    self._restart_browser(); list_tab = self.driver.latest_tab
                else: 
                    list_tab.listen.stop()

    # --- 新增的辅助方法（放在类内）---
    def _decode_response(self, res):
        """从监听响应中安全解析 JSON 数据（自动处理 gzip 和自动解析）"""
        if not res or not hasattr(res.response, 'body') or res.response.body is None:
            print("error: no res or no res body")
            return None

        body = res.response.body

        # 情况1: DrissionPage 已自动解析为 dict/list（新版行为）
        if isinstance(body, (dict, list)):
            return body

        # 情况2: 是字符串（明文 JSON）
        if isinstance(body, str):
            try:
                return json.loads(body)
            except Exception as e:
                print(f"Failed to parse string body as JSON: {e}")
                return None

        # 情况3: 是 bytes（可能是 gzip 压缩或原始 JSON 字节）
        if isinstance(body, bytes):
            try:
                headers = res.response.headers or {}
                # 检查是否 gzip 压缩
                if 'content-encoding' in headers and 'gzip' in headers['content-encoding'].lower():
                    body = gzip.decompress(body)
                # 现在 body 应该是 JSON 字符串的 bytes
                text = body.decode('utf-8')
                return json.loads(text)
            except Exception as e:
                print(f"Failed to decompress or parse bytes body: {e}")
                return None

        # 其他类型（如 None, int 等）
        print(f"Unexpected body type: {type(body)}")
        return None
        
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