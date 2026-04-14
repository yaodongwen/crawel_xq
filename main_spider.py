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
from spider_tools import SpiderTools, GlobalBackoff
from spider_portfolio import PortfolioCrawler


class XueqiuSpider:
    def __init__(self):
        print(">>> [系统] 正在清理残留进程...")
        # Windows doesn't have pkill; use taskkill instead.
        if getattr(config, "OS_TYPE", "").lower() == "windows":
            os.system('taskkill /F /IM chrome.exe /T >NUL 2>&1')
        else:
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

    def _hibernate_and_restart(self, seconds, reason="block"):
        try:
            s = int(seconds)
        except Exception:
            s = 0
        if s <= 0:
            return
        mins = max(1, int(round(s / 60)))
        print(f"\n>>> [严重] 触发{reason}，关闭浏览器等待 {mins} 分钟...")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        try:
            # Portfolio crawler uses its own driver instance.
            if getattr(self, "_portfolio_crawler", None) and getattr(self._portfolio_crawler, "driver", None):
                self._portfolio_crawler.driver.quit()
        except Exception:
            pass

        deadline = time.time() + s
        while time.time() < deadline and not self.stop_event.is_set():
            time.sleep(min(60, max(1, int(deadline - time.time()))))

        # Restart browsers (reuse user_data_path so session usually persists).
        self.driver = self._init_browser()
        try:
            self.driver.get("https://xueqiu.com/")
        except Exception:
            pass
        self._portfolio_crawler = PortfolioCrawler(init_browser_fn=self._init_browser)

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
        # 从 High_quality_users 里找 Get_Follow=0 的用户作为“宿主”，扫描其关注列表。
        # 若为空则用种子宿主兜底（并确保种子在 High_quality_users 里，Get_Follow=0）。
        next_user = self.db.get_next_follow_scan_user()
        if not next_user:
            self.db.ensure_high_quality_user(self.seed_id, user_name="seed", get_follow=0)
            current_source_id = self.seed_id
            print(f">>> 无可用宿主，使用种子宿主: {current_source_id}")
        else:
            current_source_id = next_user["user_id"]
            print(f">>> 扫描宿主关注列表: {next_user.get('user_name')}")

        tab = self.driver.latest_tab
        new_hq_added_in_this_batch = 0
        
        try:
            did_scan_any_page = False
            page_count = 0
            max_pages = int(getattr(config, "FOLLOW_SCAN_MAX_PAGES", 0) or 0)

            tab.listen.start(config.API['FOCUS'])
            tab.get(f"https://xueqiu.com/u/{current_source_id}")
            time.sleep(2)
            if "follow" not in tab.url:
                btn = tab.ele('tag:a@@href=#/follow', timeout=3)
                if btn:
                    btn.click(by_js=True)
                    SpiderTools.random_sleep()
                else:
                    print(">>> ⚠️ 无法进入关注列表页（可能被风控/页面结构变化），稍后重试")
                    return

            def _extract_users(res):
                try:
                    payload = SpiderTools.decode_response(res)
                except Exception:
                    payload = None
                if not payload and res and hasattr(res, "response"):
                    payload = getattr(res.response, "body", None)
                if isinstance(payload, dict):
                    if "users" in payload:
                        return payload.get("users") or []
                    data = payload.get("data")
                    if isinstance(data, dict) and "users" in data:
                        return data.get("users") or []
                return None

            # 首包（第一页）
            res = tab.listen.wait(timeout=8)
            users = _extract_users(res)
            if users is None:
                print(">>> ⚠️ 关注列表首包未获取到 users（可能被风控/超时），稍后重试")
                return
            did_scan_any_page = True

            while True:
                SpiderTools.safe_action(self.driver)
                if new_hq_added_in_this_batch >= config.PIPELINE_BATCH_SIZE:
                    break

                new_users = []
                new_hq = []
                now_str = SpiderTools.get_now_str()

                for u in (users or []):
                    uid = u.get('id')
                    if uid in self.existing_ids:
                        continue
                    self.existing_ids.add(uid)

                    row = (uid, u.get('screen_name'), u.get('status_count', 0),
                           u.get('friends_count', 0), u.get('followers_count', 0),
                           u.get('description', ''), now_str)
                    new_users.append(row)

                    if int(u.get('followers_count', 0)) > config.MIN_FOLLOWERS and int(u.get('status_count', 0)) > config.MIN_COMMENTS:
                        # Get_Follow=0 => 后续会作为宿主继续扫描关注列表
                        new_hq.append((
                            uid, u.get('screen_name'), u.get('status_count', 0),
                            u.get('friends_count', 0), u.get('followers_count', 0),
                            u.get('description', ''), 0, None
                        ))
                        new_hq_added_in_this_batch += 1

                if new_users:
                    self.db.execute_many_safe(
                        """
                        INSERT INTO users (
                            User_Id, User_Name, Comments_Count, Friends_Count, Followers_Count, Description, Last_Updated
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (User_Id) DO NOTHING
                        """,
                        new_users,
                    )
                if new_hq:
                    self.db.execute_many_safe(
                        """
                        INSERT INTO High_quality_users (
                            User_Id, User_Name, Comments_Count, Friends_Count, Followers_Count, Description, Get_Follow, Last_Updated
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (User_Id) DO NOTHING
                        """,
                        new_hq,
                    )
                print(f"    [扫描] 本轮新增优质: {new_hq_added_in_this_batch}/{config.PIPELINE_BATCH_SIZE}", end='\r')

                page_count += 1
                if max_pages and page_count >= max_pages:
                    print("\n>>> ⚠️ 关注列表页数过多，已达上限，先标记完成（可调整 FOLLOW_SCAN_MAX_PAGES）")
                    break

                next_btn = tab.ele('.pagination__next', timeout=3)
                if not next_btn or not next_btn.states.is_displayed:
                    break

                next_btn.click(by_js=True)
                SpiderTools.random_sleep()
                res = tab.listen.wait(timeout=8)
                users = _extract_users(res)
                if users is None:
                    break
                did_scan_any_page = True

            if did_scan_any_page and not self.stop_event.is_set():
                self.db.mark_high_quality_follow_scanned(current_source_id)
        except GlobalBackoff:
            raise
        except Exception as e: 
            print(f"error in step1")
            pass
        finally:
            try:
                tab.listen.stop()
            except Exception:
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
            uid, uname = row["user_id"], row["user_name"]
            ai_left = self.db.get_unanalyzed_count()
            print(f"    Check: {uname} | AI待办: {ai_left}", end='\r')
            stock_ok = False
            portfolio_ok = False
            is_target_user = False

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
                stock_listen_started = False
                tab.listen.start(config.API['STOCK'])
                stock_listen_started = True
                
                stock_btn = tab.ele('tag:a@@href=#/stock', timeout=4)
                if stock_btn:
                    stock_btn.click()
                    end_time = time.time() + 4
                    has_agu = False; has_waipan = False; now_str = SpiderTools.get_now_str()
                    stock_dim_rows = []
                    tmp = []
                    got_items = False
                    
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
                                for it in items:
                                    s = it.get('quote', it)
                                    symbol = s.get('symbol') or s.get('code', '')
                                    if not symbol: continue
                                    market = '未知'
                                    if symbol.startswith('SH') or symbol.startswith('SZ'): market = 'CN'; has_agu = True
                                    elif len(symbol)==5 and symbol.isdigit(): market = 'HK'; has_waipan = True
                                    elif '.' not in symbol and len(symbol)<5: market = 'US'; has_waipan = True
                                    name = s.get('name', '') or None
                                    stock_dim_rows.append((symbol, name, market if market != '未知' else None))
                                    tmp.append((symbol, float(s.get('current',0) or 0), float(s.get('percent',0) or 0)))
                                got_items = True
                                break

                    is_target_user = bool(has_agu and has_waipan)

                    # Only persist holdings for target users (avoid wasting IO for non-target users).
                    if got_items and is_target_user and stock_dim_rows:
                        user_stock_rows = []
                        self.db.upsert_stocks(stock_dim_rows)
                        stock_id_map = self.db.get_stock_id_map([r[0] for r in stock_dim_rows])
                        for symbol, current_price, pct in tmp:
                            stock_id = stock_id_map.get(symbol)
                            if not stock_id:
                                continue
                            user_stock_rows.append((uid, stock_id, current_price, pct, now_str))

                        if user_stock_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO User_Stocks (
                                    User_Id, Stock_Id, Current_Price, Percent, Updated_At
                                ) VALUES (%s,%s,%s,%s,%s)
                                ON CONFLICT (User_Id, Stock_Id) DO UPDATE SET
                                    Current_Price = EXCLUDED.Current_Price,
                                    Percent = EXCLUDED.Percent,
                                    Updated_At = EXCLUDED.Updated_At
                                """,
                                user_stock_rows,
                            )

                    if is_target_user:
                        # `row` is a PostgreSQL dict row; build values explicitly (don't use list(row) which yields keys).
                        target_data = (
                            uid,
                            uname,
                            row.get("comments_count", 0),
                            row.get("friends_count", 0),
                            row.get("followers_count", 0),
                            row.get("description", ""),
                            None,
                        )
                        self.db.execute_one_safe(
                            """
                            INSERT INTO Target_users (
                                User_Id, User_Name, Comments_Count, Friends_Count, Followers_Count, Description, Last_Updated
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (User_Id) DO NOTHING
                            """,
                            target_data,
                        )
                        self.target_ids_cache.add(uid)
            except GlobalBackoff:
                raise
            except Exception as e:
                print(f"error in step2:{e}")

            finally:
                if stock_listen_started:
                    try:
                        tab.listen.stop()
                    except Exception:
                        pass
                if not self.stop_event.is_set():
                    stock_ok = True

            # If not a target user, skip portfolio crawling entirely to reduce requests and avoid 405 risk.
            if not is_target_user:
                if not self.stop_event.is_set():
                    self.db.update_task_status(uid, "High_quality_users")
                continue

            # 组合
            portfolio_listen_started = False
            try:
                tab.get(f"https://xueqiu.com/u/{uid}")
                SpiderTools.random_sleep(1.5, 2.0)
                tab.listen.start(config.API['PORTFOLIO'])
                portfolio_listen_started = True
                
                portfolio_btn = tab.ele('tag:a@@href=#/portfolio', timeout=4)
                # 创建的组合会自动加载
                # build_btn = tab.ele('xpath://div[contains(@class, "profile-tab-item") and text()="创建的组合"]')
                if portfolio_btn:
                    portfolio_btn.click(by_js=True)
                    SpiderTools.random_sleep(0.6, 1.0)
                    end_time = time.time() + float(getattr(config, "PORTFOLIO_LIST_WAIT_SECONDS", 6) or 6)
                    now_str = SpiderTools.get_now_str()
                    # 组合页签下通常有子页签：创建的组合 / 关注的组合（有时默认就是关注页）。
                    # 这里两者都抓，避免遗漏。
                    def _find_sub_tab(keyword):
                        locators = (
                            # Xueqiu often stores the sub-tab text inside data-analytics-data JSON (sub_tab field).
                            f'xpath://*[@id="app"]//a[contains(@data-analytics-data, "sub_tab") and contains(@data-analytics-data, "{keyword}")]',
                            f'xpath://*[@id="app"]//a[contains(@data-analytics-data, "{keyword}")]',
                            f'xpath://*[@id="app"]//a[normalize-space(.)="{keyword}"]',
                            f'xpath://*[@id="app"]//a[contains(normalize-space(.), "{keyword}")]',
                        )
                        for loc in locators:
                            try:
                                el = tab.ele(loc, timeout=0.6)
                            except Exception:
                                el = None
                            try:
                                if el and el.states.is_displayed:
                                    return el
                            except Exception:
                                continue
                        return None

                    def _find_sub_tab_any(keywords):
                        for kw in keywords:
                            el = _find_sub_tab(kw)
                            if el:
                                return el
                        return None

                    created_tab = None
                    followed_tab = None
                    # Sub tabs may render asynchronously after entering portfolio; wait a bit.
                    subtab_deadline = time.time() + float(getattr(config, "PORTFOLIO_SUBTAB_WAIT_SECONDS", 5) or 5)
                    while time.time() < subtab_deadline and not (created_tab and followed_tab):
                        created_tab = created_tab or _find_sub_tab_any(("创建的组合",))
                        # 关注页在不同版本可能显示为“关注的组合”或“收藏的组合”
                        followed_tab = followed_tab or _find_sub_tab_any(("关注的组合", "收藏的组合"))
                        time.sleep(0.3)

                    if bool(getattr(config, "PORTFOLIO_TAB_DEBUG", False)):
                        try:
                            debug_max = int(getattr(config, "PORTFOLIO_TAB_DEBUG_MAX", 20) or 20)
                            items = tab.eles('xpath://*[@id="app"]//a[contains(@data-analytics-data,"sub_tab")]')
                            if not items:
                                items = tab.eles('xpath://*[@id="app"]//a')
                            rows = []
                            for it in items[:debug_max]:
                                try:
                                    t = (it.text or "").strip()
                                except Exception:
                                    t = ""
                                try:
                                    cls = (it.attr("class") or "").strip()
                                except Exception:
                                    cls = ""
                                try:
                                    data = (it.attr("data-analytics-data") or "").strip()
                                except Exception:
                                    data = ""
                                if len(data) > 160:
                                    data = data[:160] + "..."
                                if t or "sub_tab" in data:
                                    rows.append({"text": t, "class": cls, "data": data})
                            print(f">>> [组合页签调试] candidates={len(items)} show={len(rows)} rows={rows}")
                        except Exception:
                            print(">>> [组合页签调试] tabs=[]")

                    sub_tabs = []
                    if created_tab:
                        sub_tabs.append(("创建的组合", created_tab))
                    if followed_tab:
                        sub_tabs.append(("关注的组合", followed_tab))

                    if not sub_tabs:
                        print("没有找到用户关注/创建的组合按钮（将继续使用默认页签数据）")

                    processed_symbols = set()

                    def _process_iterator(iterator, default_build_or_collection):
                        if not iterator:
                            return
                        # Materialize iterator once (we need to bulk query existing symbols).
                        if not isinstance(iterator, list):
                            try:
                                iterator = list(iterator)
                            except Exception:
                                iterator = []
                        if not iterator:
                            return

                        symbols_all = []
                        for it in iterator:
                            if not isinstance(it, dict):
                                continue
                            sym = it.get("symbol") or it.get("cube_symbol")
                            if sym:
                                symbols_all.append(sym)
                        detail_refresh_hours = float(
                            getattr(
                                config,
                                "PORTFOLIO_DETAIL_REFRESH_HOURS",
                                getattr(config, "PORTFOLIO_CACHE_HOURS", 24 * 7),
                            )
                            or 24 * 7
                        )
                        skip_set, last_map = self.db.should_skip_portfolios(symbols_all, detail_refresh_hours)
                        existing_symbols = set(last_map.keys())

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
                            symbol = item.get("symbol") or item.get("cube_symbol")
                            if not symbol or symbol in processed_symbols:
                                continue
                            processed_symbols.add(symbol)

                            # Decide whether to crawl detail:
                            # - Skip if fresh within cache window
                            # - Optionally skip detail entirely for existing combos (PORTFOLIO_DETAIL_ONLY_IF_NEW)
                            last_crawled = last_map.get(symbol)
                            skip_detail = symbol in skip_set
                            if bool(getattr(config, "PORTFOLIO_DETAIL_ONLY_IF_NEW", False)) and symbol in existing_symbols and last_crawled:
                                skip_detail = True
                            detail = None
                            if not skip_detail:
                                try:
                                    detail = self._portfolio_crawler._mine_portfolio(symbol)
                                except GlobalBackoff as e:
                                    raise
                                except Exception as e:
                                    print(f"error in get portfolio information: {e}")
                                    detail = None
                            detail_ok = isinstance(detail, dict)
                            # Only mark detail as crawled after a successful detail fetch.
                            # This keeps failed/new combos retryable in later runs.
                            last_crawled_value = now_str if detail_ok else last_crawled

                            create_user_id = detail.get("create_user_id") if isinstance(detail, dict) else None
                            if str(create_user_id).isdigit():
                                creator_id = int(create_user_id)
                            else:
                                creator_id = 0

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

                            name = detail.get("portfolio_name") if isinstance(detail, dict) else item.get("name")
                            net_value = detail.get("Net_Worth") if isinstance(detail, dict) else item.get("net_value")
                            total_gain = detail.get("Total_Return_Percentage") if isinstance(detail, dict) else item.get("total_gain", 0)
                            monthly_gain = detail.get("Monthly_Return_Percentage") if isinstance(detail, dict) else item.get("monthly_gain", 0)
                            daily_gain = detail.get("Daily_Return_Percentage") if isinstance(detail, dict) else item.get("daily_gain", 0)
                            create_time = detail.get("create_time") if isinstance(detail, dict) else None
                            close_time = detail.get("close_time") if isinstance(detail, dict) else item.get("closed_at", 0)
                            description = detail.get("portfolio_description") if isinstance(detail, dict) else None
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

                            # Follow mapping: mark which sub tab it came from if we can.
                            follow_rows.append((uid, symbol, default_build_or_collection, now_str))

                        if comb_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO User_Combinations (
                                    User_Id, Symbol, Name, Net_Value, Total_Gain, Monthly_Gain, Daily_Gain,
                                    Create_Time, Updated_At, Portfolio_Last_Crawled, Close_At_Time, Description, Is_Public
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (Symbol) DO NOTHING
                                """,
                                comb_rows,
                            )
                        if update_rows:
                            self.db.execute_many_safe(
                                "UPDATE User_Combinations SET User_Id=%s, Name=%s, Net_Value=%s, Total_Gain=%s, Monthly_Gain=%s, Daily_Gain=%s, Create_Time=%s, Updated_At=%s, Portfolio_Last_Crawled=%s, Close_At_Time=%s, Description=%s, Is_Public=%s WHERE Symbol=%s",
                                update_rows,
                            )

                        if follow_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO User_Portfolio_Follows (User_Id, Symbol, Build_Or_Collection, Follow_Time)
                                VALUES (%s,%s,%s,%s)
                                ON CONFLICT (User_Id, Symbol) DO NOTHING
                                """,
                                follow_rows,
                            )

                        # For positions/transactions/comments we rely on detail_cache and comb_id map (existing logic below),
                        # but only when detail is fetched.
                        symbols = [row[1] for row in comb_rows if row and len(row) > 1 and row[1]]
                        comb_id_map = self.db.get_comb_ids_by_symbols(symbols)
                        stock_upsert_rows = []
                        position_rows_raw = []
                        rebalance_rows_raw = []

                        def _guess_market(sym):
                            if not isinstance(sym, str):
                                return None
                            s = sym.upper()
                            if s.startswith(("SH", "SZ")):
                                return "CN"
                            if s.startswith("HK"):
                                return "HK"
                            if s.startswith(("US", "NYSE", "NASDAQ")):
                                return "US"
                            return None

                        for symbol, detail, creator_id in detail_cache:
                            comb_id = comb_id_map.get(symbol)
                            if not comb_id or not isinstance(detail, dict):
                                continue
                            positions = detail.get("Detailed_Position")
                            if isinstance(positions, list):
                                for seg in positions:
                                    if not isinstance(seg, dict):
                                        continue
                                    seg_name = seg.get("category_name")
                                    seg_weight = seg.get("proportion")
                                    stocks = seg.get("stocks", [])
                                    if not isinstance(stocks, list) or not stocks:
                                        continue
                                    for s in stocks:
                                        if not isinstance(s, dict):
                                            continue
                                        stock_symbol = s.get("symbol")
                                        stock_name = s.get("name")
                                        stock_price = s.get("price")
                                        stock_weight = s.get("weight")
                                        if stock_symbol:
                                            stock_upsert_rows.append((stock_symbol, stock_name, _guess_market(stock_symbol)))
                                            position_rows_raw.append(
                                                (comb_id, seg_name, seg_weight, stock_symbol, stock_price, stock_weight, now_str)
                                            )

                            rebalances = detail.get("rebalances") if isinstance(detail.get("rebalances"), list) else []
                            for reb in rebalances:
                                if not isinstance(reb, dict):
                                    continue
                                cash_value = reb.get("cash_value") or reb.get("cashValue")
                                status = reb.get("status")
                                reb_time = SpiderTools.format_time(reb.get("updated_at") or reb.get("created_at") or reb.get("updatedAt"))
                                histories = reb.get("rebalancing_histories") or reb.get("rebalancingHistories") or []
                                if not isinstance(histories, list):
                                    histories = []
                                for h in histories:
                                    if not isinstance(h, dict):
                                        continue
                                    stock_symbol = h.get("stock_symbol") or h.get("stockSymbol")
                                    if not stock_symbol:
                                        continue
                                    stock_name = h.get("stock_name") or h.get("stockName")
                                    prev_weight = h.get("weight") or h.get("prev_weight")
                                    target_weight = h.get("target_weight")
                                    price = h.get("price")
                                    notes = h.get("comment") if isinstance(h.get("comment"), str) else None
                                    stock_upsert_rows.append((stock_symbol, stock_name, _guess_market(stock_symbol)))
                                    rebalance_rows_raw.append(
                                        (
                                            comb_id,
                                            stock_symbol,
                                            prev_weight,
                                            target_weight,
                                            price,
                                            cash_value,
                                            status,
                                            reb_time,
                                            notes,
                                        )
                                    )

                            comments = detail.get("comments") if isinstance(detail.get("comments"), list) else []
                            for c in comments:
                                if not isinstance(c, dict):
                                    continue
                                author = c.get("author", "")
                                content = c.get("text", "") or ""
                                likes = c.get("likes", "0")
                                replies = c.get("comments_count", "0")
                                try:
                                    like_count = int(likes)
                                except Exception:
                                    like_count = 0
                                try:
                                    reply_count = int(replies)
                                except Exception:
                                    reply_count = 0
                                status_id = int(
                                    hashlib.md5(f"{symbol}|{author}|{content}|{like_count}|{reply_count}".encode("utf-8")).hexdigest()[:15],
                                    16,
                                )
                                comment_rows.append((status_id, comb_id, None, content, now_str, like_count, reply_count, 0))

                        # === Map stock symbols to Stock_Id for Portfolio_Positions / Portfolio_Transactions ===
                        if stock_upsert_rows:
                            self.db.upsert_stocks(stock_upsert_rows)
                        stock_id_map = self.db.get_stock_id_map([r[0] for r in stock_upsert_rows if r and r[0]])

                        detail_success_comb_ids = []
                        for symbol, detail, creator_id in detail_cache:
                            if not isinstance(detail, dict):
                                continue
                            comb_id = comb_id_map.get(symbol)
                            if comb_id:
                                detail_success_comb_ids.append(comb_id)

                        # Portfolio_Positions stores the current holding snapshot.
                        # On each successful detail crawl, replace the previous snapshot for that combination.
                        if detail_success_comb_ids:
                            self.db.delete_portfolio_positions_by_comb_ids(detail_success_comb_ids)

                        position_rows = []
                        for comb_id, seg_name, seg_weight, stock_symbol, stock_price, stock_weight, updated_at in position_rows_raw:
                            stock_id = stock_id_map.get(str(stock_symbol).strip()) if stock_symbol else None
                            if not stock_id:
                                continue
                            position_rows.append(
                                (comb_id, seg_name, seg_weight, stock_id, stock_price, stock_weight, updated_at)
                            )

                        rebalance_rows = []
                        for comb_id, stock_symbol, prev_weight, target_weight, price, cash_value, status, reb_time, notes in rebalance_rows_raw:
                            stock_id = stock_id_map.get(str(stock_symbol).strip()) if stock_symbol else None
                            if not stock_id:
                                continue
                            rebalance_rows.append(
                                (
                                    comb_id,
                                    stock_id,
                                    _to_float(prev_weight),
                                    _to_float(target_weight),
                                    _to_float(price),
                                    _to_float(cash_value),
                                    status,
                                    reb_time or now_str,
                                    notes,
                                )
                            )

                        if position_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO Portfolio_Positions (
                                    Comb_Id, Segment_Name, Segment_Weight, Stock_Id, Stock_Price, Stock_Weight, Updated_At
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT DO NOTHING
                                """,
                                position_rows,
                            )
                        if rebalance_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO Portfolio_Transactions (
                                    Comb_Id, Stock_Id, Prev_Weight, Target_Weight,
                                    Price, Cash_Value, Status, Transaction_Time, Notes
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (Comb_Id, Transaction_Time, Stock_Id) DO UPDATE SET
                                    Prev_Weight = EXCLUDED.Prev_Weight,
                                    Target_Weight = EXCLUDED.Target_Weight,
                                    Price = EXCLUDED.Price,
                                    Cash_Value = EXCLUDED.Cash_Value,
                                    Status = EXCLUDED.Status,
                                    Notes = EXCLUDED.Notes
                                """,
                                rebalance_rows,
                            )
                        if bool(getattr(config, "ENABLE_PORTFOLIO_COMMENTS", False)) and comment_rows:
                            self.db.execute_many_safe(
                                """
                                INSERT INTO Portfolio_Comments (
                                    Status_Id, Comb_Id, User_Id, Content, Publish_Time, Like_Count, Reply_Count, Forward_Count
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (Status_Id) DO NOTHING
                                """,
                                comment_rows,
                            )

                    # Try to process the "default" sub-tab list first (sometimes switching to the active tab won't trigger a new request).
                    active_tab_name = None
                    default_build_or_collection = 0
                    try:
                        active_el = tab.ele(
                            'xpath://*[@id="app"]//a[contains(@data-analytics-data,"sub_tab") and contains(@class,"active")]',
                            timeout=0.8,
                        )
                        if not active_el:
                            active_el = tab.ele('xpath://*[@id="app"]//a[contains(@class,"active")]', timeout=0.6)
                        active_text = ((active_el.text or "").strip() if active_el else "")
                        if "关注" in active_text or "收藏" in active_text:
                            active_tab_name = "关注的组合"
                            default_build_or_collection = 1
                        elif "创建" in active_text:
                            active_tab_name = "创建的组合"
                            default_build_or_collection = 0
                    except Exception:
                        pass

                    initial_ok = False
                    initial_deadline = time.time() + 3.0
                    while time.time() < initial_deadline:
                        res = tab.listen.wait(timeout=0.8)
                        if not res:
                            continue
                        data = res.response.body
                        if not data:
                            continue
                        payload = SpiderTools.decode_response(res) or data
                        iterator = _extract_portfolios(payload)
                        if iterator:
                            _process_iterator(iterator, default_build_or_collection)
                            initial_ok = True
                            break

                    # Drain remaining listen queue to better align response with the click below.
                    try:
                        drain_deadline = time.time() + 0.4
                        while time.time() < drain_deadline:
                            if not tab.listen.wait(timeout=0.1):
                                break
                    except Exception:
                        pass

                    click_tabs = sub_tabs
                    if initial_ok and active_tab_name:
                        click_tabs = [(n, e) for (n, e) in sub_tabs if n != active_tab_name]

                    # Click each sub tab and process data from API.
                    for tab_name, tab_ele in click_tabs:
                        try:
                            # Drain queue for this click.
                            try:
                                drain_deadline = time.time() + 0.3
                                while time.time() < drain_deadline:
                                    if not tab.listen.wait(timeout=0.1):
                                        break
                            except Exception:
                                pass
                            tab_ele.click(by_js=True)
                        except Exception:
                            continue

                        click_deadline = time.time() + float(getattr(config, "PORTFOLIO_CLICK_WAIT_SECONDS", 6) or 6)
                        while time.time() < click_deadline:
                            res = tab.listen.wait(timeout=1.0)
                            if not res:
                                continue
                            data = res.response.body
                            if not data:
                                continue
                            payload = SpiderTools.decode_response(res) or data
                            iterator = _extract_portfolios(payload)
                            if iterator:
                                # Build_Or_Collection: 1 means "关注/收藏" in original code.
                                build_or_collection = 1 if tab_name == "关注的组合" else 0
                                _process_iterator(iterator, build_or_collection)
                                break

                    # Fallback: if we still didn't get any portfolio list, try to process whatever comes in within time window.
                    if not processed_symbols:
                        while time.time() < end_time:
                            res = tab.listen.wait(timeout=1.0)
                            if not res:
                                continue
                            data = res.response.body
                            if not data:
                                continue
                            payload = SpiderTools.decode_response(res) or data
                            iterator = _extract_portfolios(payload)
                            if iterator:
                                _process_iterator(iterator, default_build_or_collection=default_build_or_collection)
                                break

            except GlobalBackoff:
                raise
            except Exception as e:
                print(f"error in step2 in portfolio:{e}")

            finally:
                if portfolio_listen_started:
                    try:
                        tab.listen.stop()
                    except Exception:
                        pass
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
        consecutive_global_backoffs = 0
        try:
            while True:
                current_targets = self.db.get_target_count()
                if current_targets >= config.TARGET_GOAL:
                    print("\n>>> 🎉🎉🎉 恭喜！目标用户收集完成！🎉🎉🎉"); break 
                
                current_users = self.db.get_total_users_count()
                ai_backlog = self.db.get_unanalyzed_count()
                print(f"\n>>> [循环] 目标:{current_targets}/{config.TARGET_GOAL} | 用户库:{current_users}/{config.FOCUS_COUNT_LIMIT} | AI积压:{ai_backlog}")
                
                try:
                    self.step1_batch_scan()
                    self.step2_batch_filter()
                    self.step3_batch_mine()
                    consecutive_global_backoffs = 0
                except GlobalBackoff as e:
                    consecutive_global_backoffs += 1
                    max_backoffs = int(getattr(config, "MAX_CONSECUTIVE_GLOBAL_BACKOFFS", 3) or 3)
                    reason = getattr(e, "reason", "block")
                    print(f">>> [全局退避] 连续触发 {reason}: {consecutive_global_backoffs}/{max_backoffs}")
                    if consecutive_global_backoffs >= max_backoffs:
                        print(">>> [停止] 连续多次触发全局风控，继续重试大概率没有进度，请先人工处理后再重启。")
                        break
                    self._hibernate_and_restart(getattr(e, "seconds", 0), reason=reason)
                    continue
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
