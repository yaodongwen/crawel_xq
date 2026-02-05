from DrissionPage import ChromiumPage, ChromiumOptions
import time
import hashlib
import threading
import multiprocessing
import os
import config
from db_manager import DBManager

from spider_ai import AIWorker, run_ai_process
from spider_comments import CommentsCrawler
from spider_tools import SpiderTools
from spider_portfolio import PortfolioCrawler


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
        self.stop_event = threading.Event()
        self.ai_stop_event = multiprocessing.Event()
        self._ai_worker = AIWorker(
            db=self.db,
            is_main_job_finished_fn=lambda: self.is_main_job_finished,
            on_saved=self._on_ai_saved,
        )
        self._comments_crawler = CommentsCrawler(init_browser_fn=self._init_browser, stop_event=self.stop_event)
        self._portfolio_crawler = PortfolioCrawler(init_browser_fn=self._init_browser)

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_browser_path(config.get_chrome_path())
        co.set_user_data_path(config.get_user_data_path())
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
        if self.stop_event.is_set():
            return
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
                    if not self.stop_event.is_set():
                        self.db.mark_user_as_scanned(current_source_id)
                    return
            
            tab.listen.start(config.API['FOCUS'])
            page_count = 0
            
            while True:
                SpiderTools.safe_action(self.driver)
                if new_hq_added_in_this_batch >= config.PIPELINE_BATCH_SIZE: break 

                next_btn = tab.ele('.pagination__next', timeout=3)
                if not next_btn or not next_btn.states.is_displayed: 
                    if not self.stop_event.is_set():
                        self.db.mark_user_as_scanned(current_source_id)
                    break
                
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
                        
                        row = (uid, u.get('screen_name'), u.get('status_count', 0),
                               u.get('friends_count', 0), u.get('followers_count', 0), 
                               u.get('description', ''), now_str) 
                        new_users.append(row)
                        
                        if int(u.get('followers_count', 0)) > config.MIN_FOLLOWERS \
                              and int(u.get('status_count', 0)) > config.MIN_COMMENTS: 
                            hq_row = list(row); hq_row[-1] = None 
                            new_hq.append(tuple(hq_row))
                            new_hq_added_in_this_batch += 1
                    
                    if new_users: self.db.execute_many_safe("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?)", new_users)
                    if new_hq: self.db.execute_many_safe("INSERT OR IGNORE INTO High_quality_users VALUES (?,?,?,?,?,?,?)", new_hq)
                    print(f"    [扫描] 本轮新增优质: {new_hq_added_in_this_batch}/{config.PIPELINE_BATCH_SIZE}", end='\r')

                page_count += 1
                if page_count > 20: break 
            tab.listen.stop()
        except Exception as e: 
            print(f"error in step1")
            pass

    # ================= Step 2: 批次筛选 =================

    def step2_batch_filter(self):
        if self.stop_event.is_set():
            return
        pending = self.db.get_pending_tasks("High_quality_users", limit=config.PIPELINE_BATCH_SIZE)
        if not pending: return

        print(f"\n=== Step 2: 筛选持仓 (批次: {len(pending)} 人) ===")
        tab = self.driver.latest_tab
        
        for row in pending:
            uid, uname = row['User_Id'], row['User_Name']
            ai_left = self.db.get_unanalyzed_count()
            print(f"    Check: {uname} | AI待办: {ai_left}", end='\r')
            stock_ok = False
            portfolio_ok = False

            if uid in self.target_ids_cache:
                # print("user in cache, continue!")
                if not self.stop_event.is_set():
                    self.db.update_task_status(uid, "High_quality_users")
                continue
            
            SpiderTools.safe_action(self.driver)

            def _extract_portfolios(p):
                            if isinstance(p, list):
                                if any(isinstance(it, dict) and (it.get('symbol') or it.get('cube_symbol') or 'net_value' in it) for it in p):
                                    return p
                                return []
                            if isinstance(p, dict):
                                if isinstance(p.get('list'), list):
                                    lst = p['list']
                                elif isinstance(p.get('data'), dict) and isinstance(p['data'].get('stocks'), list):
                                    lst = p['data']['stocks']
                                elif isinstance(p.get('data'), dict) and isinstance(p['data'].get('items'), list):
                                    lst = p['data']['items']
                                elif isinstance(p.get('items'), list):
                                    lst = p['items']
                                else:
                                    lst = []
                                if lst and any(isinstance(it, dict) and (it.get('symbol') or it.get('cube_symbol') or 'net_value' in it) for it in lst):
                                    return lst
                                vals = [v for v in p.values() if isinstance(v, dict)]
                                if vals and any((v.get('symbol') or v.get('cube_symbol') or 'net_value' in v) for v in vals):
                                    return vals
                            return []
        
            # 自选
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
                        if not data:
                            continue
                        payload = SpiderTools.decode_response(res) or data

                        iterator = _extract_portfolios(payload)

                        if not (isinstance(data, dict) and any(isinstance(item, dict) and 'net_value' in item for item in data.values())):
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
            except Exception as e:
                print(f"error in step2:{e}")

            finally:
                try:
                    tab.listen.stop()
                except Exception:
                    print("error in stop listening quote")
                if not self.stop_event.is_set():
                    stock_ok = True

            # 组合
            try:
                tab.get(f"https://xueqiu.com/u/{uid}")
                SpiderTools.random_sleep(1.5, 2.0)
                tab.listen.start(config.API['PORTFOLIO'])
                
                portfolio_btn = tab.ele('tag:a@@href=#/portfolio', timeout=4)
                # 创建的组合会自动加载
                # build_btn = tab.ele('xpath://div[contains(@class, "profile-tab-item") and text()="创建的组合"]')
                # 关注组合
                follow_btn = tab.ele('xpath://div[contains(@class, "profile-tab-item") and text()="关注的组合"]')
                if portfolio_btn:
                    portfolio_btn.click()
                    end_time = time.time() + 4
                    now_str = SpiderTools.get_now_str()
                    if follow_btn:
                        follow_btn.click(by_js=True)  # 必须用 JS 点击！
                    else:
                        print("没有找到用户收藏的组合按钮")
                    
                    while time.time() < end_time:
                        res = tab.listen.wait(timeout=1.0)
                        if not res: continue
                        data = res.response.body
                        if not data:
                            continue

                        payload = SpiderTools.decode_response(res) or data
                        iterator = _extract_portfolios(payload)

                        if iterator: # 组合
                            comb_rows = []
                            update_rows = []
                            follow_rows = []
                            rebalance_rows = []
                            comment_rows = []
                            position_rows = []
                            detail_cache = []

                            for item in iterator:
                                if not isinstance(item, dict):
                                    continue
                                symbol = item.get('symbol') or item.get('cube_symbol')
                                if not symbol:
                                    print(f"warning: cannot get symbol in portfolio: {item}")
                                    continue

                                skip_detail, last_crawled = self.db.should_skip_portfolio(
                                    symbol, config.PORTFOLIO_CACHE_HOURS
                                )
                                detail = None
                                if not skip_detail:
                                    try:
                                        detail = self._portfolio_crawler._mine_portfolio(symbol)
                                    except Exception as e:
                                        print(f"error in get portfolio information: {e}")
                                        detail = None
                                detail_ok = isinstance(detail, dict)
                                # Only update crawl time when detail is fetched successfully.
                                last_crawled_value = now_str if detail_ok else last_crawled

                                create_user_id = detail.get('create_user_id') if isinstance(detail, dict) else None
                                if str(create_user_id).isdigit():
                                    creator_id = int(create_user_id)
                                else:
                                    creator_id = 0 #无Id

                                def _to_float(val):
                                    if val is None:
                                        return 0.0
                                    if isinstance(val, (int, float)):
                                        return float(val)
                                    s = str(val).strip()
                                    if not s or s in ("--", "None"):
                                        return 0.0
                                    s = s.replace("%", "").replace(",", "")
                                    try:
                                        return float(s)
                                    except Exception:
                                        return 0.0

                                name = detail.get('portfolio_name') if isinstance(detail, dict) else item.get('name')
                                net_value = detail.get('Net_Worth') if isinstance(detail, dict) else item.get('net_value')
                                total_gain = detail.get('Total_Return_Percentage') if isinstance(detail, dict) else item.get('total_gain', 0)
                                monthly_gain = detail.get('Monthly_Return_Percentage') if isinstance(detail, dict) else item.get('monthly_gain', 0)
                                daily_gain = detail.get('Daily_Return_Percentage') if isinstance(detail, dict) else item.get('daily_gain', 0)
                                create_time = detail.get('create_time') if isinstance(detail, dict) else None
                                close_time = detail.get('close_time') if isinstance(detail, dict) else item.get('closed_at', 0)
                                description = detail.get('portfolio_description') if isinstance(detail, dict) else None
                                is_public = 1

                                comb_rows.append((
                                    creator_id,
                                    symbol,
                                    name,
                                    _to_float(net_value),
                                    _to_float(total_gain),
                                    _to_float(monthly_gain),
                                    _to_float(daily_gain),
                                    create_time,
                                    now_str,
                                    last_crawled_value,
                                    str(close_time or 0),
                                    description,
                                    is_public,
                                ))
                                update_rows.append((
                                    creator_id,
                                    name,
                                    _to_float(net_value),
                                    _to_float(total_gain),
                                    _to_float(monthly_gain),
                                    _to_float(daily_gain),
                                    create_time,
                                    now_str,
                                    last_crawled_value,
                                    str(close_time or 0),
                                    description,
                                    is_public,
                                    symbol,
                                ))
                                detail_cache.append((symbol, detail, creator_id))

                            if comb_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO User_Combinations (User_Id, Symbol, Name, Net_Value, Total_Gain, Monthly_Gain, Daily_Gain, Create_Time, Updated_At, Portfolio_Last_Crawled, Close_At_Time, Description, Is_Public) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    comb_rows,
                                )
                            if update_rows:
                                self.db.execute_many_safe(
                                    "UPDATE User_Combinations SET User_Id=?, Name=?, Net_Value=?, Total_Gain=?, Monthly_Gain=?, Daily_Gain=?, Create_Time=?, Updated_At=?, Portfolio_Last_Crawled=?, Close_At_Time=?, Description=?, Is_Public=? WHERE Symbol=?",
                                    update_rows,
                                )

                            symbols = [row[1] for row in comb_rows if row and len(row) > 1 and row[1]]
                            comb_id_map = self.db.get_comb_ids_by_symbols(symbols)

                            for symbol, detail, creator_id in detail_cache:
                                comb_id = comb_id_map.get(symbol)
                                build_or_collection = 0 if int(creator_id) == int(uid) else 1
                                follow_rows.append((uid, symbol, build_or_collection, now_str))
                                if not comb_id:
                                    continue

                                if not isinstance(detail, dict):
                                    continue

                                # Detailed positions -> Portfolio_Positions
                                positions = detail.get('Detailed_Position')
                                if isinstance(positions, list):
                                    for seg in positions:
                                        if not isinstance(seg, dict):
                                            print(f"seg type is {type(seg)}")
                                            continue
                                        seg_name = seg.get('category_name')
                                        seg_weight = seg.get('proportion')
                                        stocks = seg.get('stocks', [])
                                        if not isinstance(stocks, list) or not stocks:
                                            position_rows.append(
                                                (comb_id, seg_name, seg_weight, None, None, None, now_str)
                                            )
                                            print(f"stocks type is {type(stocks)}")
                                            continue
                                        for s in stocks:
                                            if not isinstance(s, dict):
                                                continue
                                            position_rows.append(
                                                (
                                                    comb_id,
                                                    seg_name,
                                                    seg_weight,
                                                    s.get('name'),
                                                    s.get('price'),
                                                    s.get('weight'),
                                                    now_str,
                                                )
                                            )
                                elif type(positions) == 'NoneType':
                                    pass
                                else:
                                    print(f"Detailed_Position type is {type(positions)}")

                                # Rebalancing history -> Portfolio_Transactions
                                rebalances = detail.get('rebalances')
                                reb_list = []
                                if isinstance(rebalances, dict):
                                    if isinstance(rebalances.get('list'), list):
                                        reb_list = rebalances['list']
                                    elif isinstance(rebalances.get('data'), dict) and isinstance(rebalances['data'].get('list'), list):
                                        reb_list = rebalances['data']['list']
                                    elif isinstance(rebalances.get('data'), list):
                                        reb_list = rebalances['data']
                                elif isinstance(rebalances, list):
                                    reb_list = rebalances
                                else:
                                    print(f"rebalances type is {type(rebalances)}")

                                for reb in reb_list:
                                    if not isinstance(reb, dict):
                                        continue
                                    status = reb.get('status')
                                    cash_value = reb.get('cash_value') or reb.get('cash')
                                    reb_time = SpiderTools.format_time(reb.get('updated_at') or reb.get('created_at') or reb.get('updatedAt'))
                                    histories = reb.get('rebalancing_histories') or reb.get('rebalancingHistories') or []
                                    if not isinstance(histories, list):
                                        histories = []
                                    for h in histories:
                                        if not isinstance(h, dict):
                                            continue
                                        stock_symbol = h.get('stock_symbol') or h.get('stockSymbol')
                                        if not stock_symbol:
                                            continue
                                        stock_name = h.get('stock_name') or h.get('stockName')
                                        prev_weight = h.get('weight') or h.get('prev_weight')
                                        target_weight = h.get('target_weight')
                                        price = h.get('price')
                                        notes = h.get('comment') if isinstance(h.get('comment'), str) else None
                                        rebalance_rows.append(
                                            (
                                                comb_id,
                                                stock_symbol,
                                                stock_name,
                                                prev_weight,
                                                target_weight,
                                                price,
                                                cash_value,
                                                status,
                                                reb_time,
                                                notes,
                                            )
                                        )

                                # Comments -> Portfolio_Comments
                                comments = detail.get('comments') if isinstance(detail.get('comments'), list) else []
                                for c in comments:
                                    if not isinstance(c, dict):
                                        continue
                                    author = c.get('author', '')
                                    content = c.get('text', '') or ''
                                    likes = c.get('likes', '0')
                                    replies = c.get('comments_count', '0')
                                    try:
                                        like_count = int(likes)
                                    except Exception:
                                        like_count = 0
                                    try:
                                        reply_count = int(replies)
                                    except Exception:
                                        reply_count = 0
                                    status_id = int(hashlib.md5(f"{symbol}|{author}|{content}|{like_count}|{reply_count}".encode('utf-8')).hexdigest()[:15], 16)
                                    comment_rows.append(
                                        (
                                            status_id,
                                            comb_id,
                                            None,
                                            content,
                                            now_str,
                                            like_count,
                                            reply_count,
                                            0,
                                        )
                                    )

                            if follow_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO User_Portfolio_Follows (User_Id, Symbol, Build_Or_Collection, Follow_Time) VALUES (?,?,?,?)",
                                    follow_rows,
                                )
                            if position_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO Portfolio_Positions (Comb_Id, Segment_Name, Segment_Weight, Stock_Name, Stock_Price, Stock_Weight, Updated_At) VALUES (?,?,?,?,?,?,?)",
                                    position_rows,
                                )
                            if rebalance_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO Portfolio_Transactions (Comb_Id, Stock_Symbol, Stock_Name, Prev_Weight, Target_Weight, Price, Cash_Value, Status, Transaction_Time, Notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    rebalance_rows,
                                )
                            if comment_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO Portfolio_Comments (Status_Id, Comb_Id, User_Id, Content, Publish_Time, Like_Count, Reply_Count, Forward_Count) VALUES (?,?,?,?,?,?,?,?)",
                                    comment_rows,
                                )

            except Exception as e:
                print(f"error in step2 in portfolio:{e}")

            finally:
                try:
                    tab.listen.stop()
                except Exception:
                    print("error in stop listening portfolio")
                if not self.stop_event.is_set():
                    portfolio_ok = True

            if not self.stop_event.is_set() and stock_ok and portfolio_ok:
                self.db.update_task_status(uid, "High_quality_users")


    def run(self):
        print(">>> 启动...")
        ai_process = multiprocessing.Process(target=run_ai_process, args=(self.ai_stop_event,), daemon=True)
        ai_process.start()
        
        self.driver.get("https://xueqiu.com")
        print("\n" + "="*50); input(">>> 请扫码登录，完成后按【回车】..."); print("="*50 + "\n")
        
        interrupted = False
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

        except KeyboardInterrupt:
            interrupted = True
            self.stop_event.set()
            print("\n\n>>> 🛑 检测到用户中断 (Ctrl+C)...")
        except Exception as e: print(f"\n\n>>> ❌ 发生未捕获异常: {e}")
        finally:
            self.is_main_job_finished = True
            self.ai_stop_event.set()
            if not interrupted:
                left = self.db.get_unanalyzed_count()
                while left > 0 and ai_process.is_alive():
                    print(f">>> 提示: AI 线程还在处理剩余的 {left} 条数据...")
                    print(">>> 等待 AI 处理完成...")
                    ai_process.join(timeout=20)
                    left = self.db.get_unanalyzed_count()
            print(">>> 程序安全退出")

if __name__ == '__main__':
    bot = XueqiuSpider()
    bot.run()
