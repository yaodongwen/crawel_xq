import config
from datetime import datetime
import time
from spider_tools import SpiderTools, GlobalBackoff


class CommentsCrawler:
    def __init__(self, init_browser_fn, stop_event=None):
        self._init_browser_fn = init_browser_fn
        self._stop_event = stop_event
        self._long_article_block_until_ts = 0.0
        self._long_article_block_reason = None

    def _try_fetch_status_via_api(self, driver, status_id):
        """Prefer JSON API over HTML long-article page to reduce risk-control triggers."""
        api_tab = None
        try:
            api_url = f"https://xueqiu.com/statuses/show.json?id={status_id}"
            api_tab = driver.new_tab()
            api_tab.listen.start("statuses/show.json")
            api_tab.get(api_url)
            res = api_tab.listen.wait(timeout=6)
            payload = SpiderTools.decode_response(res)
            if not payload:
                if res:
                    blocked, reason = SpiderTools.response_looks_blocked(res)
                    if blocked:
                        sleep_s = int(getattr(config, "WAF_SLEEP_SECONDS", getattr(config, "BLOCK_SLEEP_SECONDS", 600)))
                        raise GlobalBackoff(sleep_s, reason=reason or "block")
                # If the tab itself is a verification page, just skip long-article fetch for now.
                try:
                    SpiderTools.safe_action(driver, tab=api_tab)
                except GlobalBackoff:
                    raise
                except Exception:
                    pass
                return None

            if isinstance(payload, dict):
                status = payload.get("status") or payload.get("data") or payload
                if isinstance(status, dict):
                    return status
            return None
        except GlobalBackoff:
            raise
        except Exception:
            return None
        finally:
            try:
                if api_tab:
                    api_tab.listen.stop()
            except Exception:
                pass
            try:
                if api_tab:
                    api_tab.close()
            except Exception:
                pass

    def _mine_long_articles(self, driver, uid, status_id):
        detail_tab = None
        """
        【修改版】长文获取逻辑：
        直接新建标签页访问长文 URL (https://xueqiu.com/uid/id)，
        抓取完整标题和正文后返回。
        """
        try:
            if self._stop_event and self._stop_event.is_set():
                return None
            if time.time() < float(getattr(self, "_long_article_block_until_ts", 0.0) or 0.0):
                return None

            # 0) Try JSON API first (usually avoids slider)
            status = self._try_fetch_status_via_api(driver, status_id)
            if isinstance(status, dict):
                title = (status.get("title") or "").strip()
                body = status.get("text") or status.get("description") or ""
                body = str(body).strip()
                if title or body:
                    if title:
                        return f"【长文标题】{title}\n{body}".strip()
                    return body
            # Avoid opening the HTML long-article detail page by default.
            # It is much more likely to trigger 405/WAF/slider than the JSON API.
            if bool(getattr(config, "LONG_ARTICLE_API_ONLY", True)):
                return None

            # 构造长文链接（不推荐，容易触发风控）
            url = f"https://xueqiu.com/{uid}/{status_id}"

            # 打开新标签页 (DrissionPage 会自动切换焦点到新页面)
            detail_tab = driver.new_tab(url)
            # 长文页更容易触发风控/滑块；先处理滑块再去找正文元素
            SpiderTools.safe_action(driver)
            if SpiderTools.has_slider(driver):
                # 不做人工介入：直接跳过当前长文，避免卡死主流程
                detail_tab.close()
                return None

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

        except GlobalBackoff as e:
            cooldown_s = int(getattr(config, "LONG_ARTICLE_BLOCK_COOLDOWN_SECONDS", 1800) or 1800)
            self._long_article_block_until_ts = time.time() + max(0, cooldown_s)
            self._long_article_block_reason = getattr(e, "reason", "block")
            mins = max(1, int(round(max(0, cooldown_s) / 60))) if cooldown_s > 0 else 0
            print(
                f"    -> [长文跳过] status_id={status_id} 触发 {getattr(e, 'reason', 'block')}，"
                f"保留原始内容；暂停长文补全 {mins} 分钟，继续抓取其它动态"
            )
            try:
                if detail_tab:
                    detail_tab.close()
            except Exception:
                pass
            return None
        except Exception as e:
            # print(f"    ⚠️ 长文补全失败 {status_id}: {e}")
            # 异常保护：如果标签页没关掉，强制关闭
            if driver.tabs_count > 1:
                # 简单判断一下当前页是不是列表页，如果不是就关掉
                if str(uid) not in driver.latest_tab.url:
                    driver.latest_tab.close()
            return None

    @staticmethod
    def _parse_time(ts_str):
        if not ts_str:
            return None
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def step3_batch_mine(self, driver, db):
        if self._stop_event and self._stop_event.is_set():
            return driver
        pending = db.get_pending_tasks("Target_users", limit=config.PIPELINE_BATCH_SIZE)
        if not pending:
            return driver

        print(f"\n=== Step 3: 爬取评论 (批次: {len(pending)} 人) ===")

        # 获取当前的列表页 Tab 对象
        list_tab = driver.latest_tab

        for row in pending:
            if self._stop_event and self._stop_event.is_set():
                break
            uid, uname = row["user_id"], row["user_name"]
            ai_left = db.get_unanalyzed_count()
            print(f"    User: {uname} | AI待办: {ai_left}")

            SpiderTools.safe_action(driver)
            try:
                user_completed = False
                got_any_json = False
                seen_ids = set()

                wait_timeout = int(getattr(config, "TIMELINE_WAIT_SECONDS", 6) or 6)
                first_wait_tries = int(getattr(config, "TIMELINE_FIRST_WAIT_TRIES", 3) or 3)
                page_wait_tries = int(getattr(config, "TIMELINE_PAGE_WAIT_TRIES", 2) or 2)
                kick_scroll_px = int(getattr(config, "TIMELINE_KICK_SCROLL_PX", 1200) or 1200)

                def _wait_next_payload(tries):
                    """Wait next listened response and decode JSON; raise GlobalBackoff on block-like responses."""
                    last_res = None
                    for _ in range(max(1, int(tries))):
                        if self._stop_event and self._stop_event.is_set():
                            return None, None
                        res0 = list_tab.listen.wait(timeout=wait_timeout)
                        if res0:
                            last_res = res0
                            data0 = SpiderTools.decode_response(res0)
                            if data0 is not None:
                                return data0, res0
                            blocked, reason = SpiderTools.response_looks_blocked(res0)
                            if blocked:
                                sleep_s = int(getattr(config, "WAF_SLEEP_SECONDS", getattr(config, "BLOCK_SLEEP_SECONDS", 600)))
                                raise GlobalBackoff(sleep_s, reason=reason or "block")
                        # Kick: some pages only request timeline after scrolling.
                        try:
                            list_tab.scroll.down(kick_scroll_px)
                        except Exception:
                            pass
                        time.sleep(1)
                    return None, last_res

                # Incremental resume checkpoint:
                # - If enabled, use Value_Comments(Publish_Time) newest as the "already processed" cursor.
                # - This keeps working even when Raw_Statuses is periodically deleted after AI processing.
                try:
                    user_total = int(row.get("comments_count") or 0)
                except Exception:
                    user_total = 0
                if user_total <= 0:
                    user_total = int(db.get_user_comments_count_hint(uid) or 0)
                    if user_total > 0:
                        db.set_target_user_comments_count_if_missing(uid, user_total)

                value_checkpoint_str = None
                meta_checkpoint_str = None
                checkpoint_dt = None
                if bool(getattr(config, "RESUME_BY_VALUE_COMMENTS", True)):
                    value_checkpoint_str = db.get_user_value_comments_newest_publish_time(uid)
                    meta_checkpoint_str = db.get_user_comments_last_crawled(uid)
                    value_dt = self._parse_time(value_checkpoint_str) if value_checkpoint_str else None
                    meta_dt = self._parse_time(meta_checkpoint_str) if meta_checkpoint_str else None
                    # Use the later checkpoint to avoid repeatedly scanning pages when Value_Comments is sparse.
                    if value_dt and meta_dt:
                        checkpoint_dt = value_dt if value_dt >= meta_dt else meta_dt
                    else:
                        checkpoint_dt = value_dt or meta_dt

                # Cap new enqueued raw rows per user per run.
                max_new = int(getattr(config, "STEP3_MAX_NEW_RAW_PER_USER", config.ARTICLE_COUNT_LIMIT) or config.ARTICLE_COUNT_LIMIT)
                if max_new <= 0:
                    max_new = int(config.ARTICLE_COUNT_LIMIT or 3000)
                if user_total > 0:
                    max_new = min(max_new, int(user_total))

                # For logging only (not used as resume cursor).
                try:
                    value_cnt_start = int(db.get_user_value_comments_distinct_count(uid) or 0)
                except Exception:
                    value_cnt_start = 0

                # Keep newest_seen for incremental updates metadata.
                newest_seen = None
                reached_checkpoint = False
                target_api = 'user_timeline.json'
                list_tab.listen.start(target_api)

                list_tab.get(f"https://xueqiu.com/u/{uid}")

                # 等待第一页
                # 等待第一页（部分账号需要滚动才会触发 user_timeline.json）
                data, res = _wait_next_payload(first_wait_tries)

                total_added = 0
                last_page_status_count = 0
                last_page_min_dt = None

                # --- 定义内部函数：统一处理每一页的数据解析逻辑 ---
                # 这样第一页和翻页后的代码不用写两遍
                def process_page_data(response_data):
                    nonlocal newest_seen
                    nonlocal last_page_status_count, last_page_min_dt
                    nonlocal reached_checkpoint
                    rows = []
                    last_page_status_count = 0
                    last_page_min_dt = None
                    if response_data and 'statuses' in response_data:
                        statuses = response_data['statuses'] or []
                        if isinstance(statuses, list):
                            last_page_status_count = len(statuses)
                        for s in statuses:
                            # Avoid infinite loops when the page returns repeated items.
                            try:
                                sid = s.get("id")
                            except Exception:
                                sid = None
                            if sid is not None:
                                try:
                                    sid_int = int(sid)
                                except Exception:
                                    sid_int = None
                                if sid_int is not None:
                                    if sid_int in seen_ids:
                                        continue
                                    seen_ids.add(sid_int)
                            readable_time = SpiderTools.format_time(s['created_at'])
                            created_dt = self._parse_time(readable_time)
                            if created_dt and (newest_seen is None or created_dt > newest_seen):
                                newest_seen = created_dt
                            if created_dt and (last_page_min_dt is None or created_dt < last_page_min_dt):
                                last_page_min_dt = created_dt

                            # Incremental resume: stop once we reached already-processed time.
                            if checkpoint_dt and created_dt and created_dt <= checkpoint_dt:
                                reached_checkpoint = True
                                break

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
                                full_text = self._mine_long_articles(driver, uid, s['id'])
                                if full_text:
                                    content = full_text  # 用抓到的完整长文覆盖截断内容

                            rows.append((
                                s['id'],
                                s['user_id'],
                                content,
                                readable_time,
                                str(s.get('stockCorrelation', '')),
                                0,
                                s.get('retweet_count', 0),
                                s.get('reply_count', 0),
                                s.get('like_count', 0)
                            ))
                    return rows

                # --- 处理第一页 ---
                if data:
                    got_any_json = True
                    raw_rows = process_page_data(data)
                    if raw_rows:
                        inserted = db.execute_many_count_safe(
                            """
                            INSERT INTO Raw_Statuses (
                                Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed,
                                Forward, Comment_Count, Like_Count
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (Status_Id) DO NOTHING
                            """,
                            raw_rows,
                        )
                        total_added += int(inserted or 0)
                    else:
                        # JSON 正常但没有任何动态：标记为已处理，避免反复尝试。
                        try:
                            statuses = data.get("statuses") if isinstance(data, dict) else None
                        except Exception:
                            statuses = None
                        if isinstance(statuses, list) and len(statuses) == 0:
                            print("    -> 该用户动态为空/不可见，标记为已处理（避免重复尝试）")
                            db.update_task_status(uid, "Target_users")
                            try:
                                list_tab.listen.stop()
                            except Exception:
                                pass
                            continue
                else:
                    if not res:
                        print("    ⚠️ 第一页超时或未捕获到 user_timeline.json（可能需要滚动触发）")
                    else:
                        snippet = SpiderTools.response_text_snippet(res, limit=180)
                        if snippet:
                            print("    ⚠️ 第一页响应非 JSON（可能被风控/阻断），稍后重试")
                        else:
                            print("    ⚠️ 第一页响应非 JSON（空响应），稍后重试")
                    try:
                        list_tab.listen.stop()
                    except Exception:
                        pass
                    continue

                # --- 循环翻页直到达标/到达 checkpoint ---
                while total_added < max_new and not reached_checkpoint:
                    if self._stop_event and self._stop_event.is_set():
                        break
                    if SpiderTools.has_slider(driver):
                        SpiderTools.safe_action(driver)

                    next_btn = list_tab.ele('.pagination__next', timeout=2)
                    if next_btn and next_btn.states.is_displayed:
                        next_btn.click(by_js=True)
                    else:
                        # Some pages use infinite scroll instead of pagination buttons.
                        try:
                            list_tab.scroll.down(1800)
                        except Exception:
                            break

                    # 等待下一页数据包（翻页/滚动都会触发 user_timeline.json）
                    data, res = _wait_next_payload(page_wait_tries)

                    if data:
                        got_any_json = True
                        raw_rows = process_page_data(data)
                        if raw_rows:
                            inserted = db.execute_many_count_safe(
                                """
                                INSERT INTO Raw_Statuses (
                                    Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed,
                                    Forward, Comment_Count, Like_Count
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (Status_Id) DO NOTHING
                                """,
                                raw_rows,
                            )
                            total_added += int(inserted or 0)
                        else:
                            # Empty rows can mean we reached checkpoint (all remaining are old), or end/no more.
                            if reached_checkpoint:
                                break
                            break  # 有包但没数据，可能到底了/无更多
                    else:
                        # 没包/非 JSON：避免误判为“完成”，留给后续重试；如果像是阻断则触发全局退避。
                        if res:
                            blocked, reason = SpiderTools.response_looks_blocked(res)
                            if blocked:
                                sleep_s = int(getattr(config, "WAF_SLEEP_SECONDS", getattr(config, "BLOCK_SLEEP_SECONDS", 600)))
                                raise GlobalBackoff(sleep_s, reason=reason or "block")
                            print("    ⚠️ 翻页响应非 JSON（可能被风控/阻断），中止本用户以避免重复消耗")
                        else:
                            print("    ⚠️ 翻页超时或未捕获到数据包，中止本用户以避免卡死")
                        break

                list_tab.listen.stop()

                try:
                    value_cnt = int(db.get_user_value_comments_distinct_count(uid) or 0)
                except Exception:
                    value_cnt = 0
                ck_val = value_checkpoint_str or "-"
                ck_meta = meta_checkpoint_str or "-"
                ck_use = ck_val
                try:
                    if checkpoint_dt and meta_checkpoint_str and self._parse_time(meta_checkpoint_str) == checkpoint_dt:
                        ck_use = ck_meta
                    elif checkpoint_dt and value_checkpoint_str and self._parse_time(value_checkpoint_str) == checkpoint_dt:
                        ck_use = ck_val
                except Exception:
                    ck_use = ck_val

                if bool(getattr(config, "STEP3_DEBUG_CHECKPOINTS", False)):
                    print(
                        f"    -> 完成: {uname} (本次入库Raw: {total_added} | 价值评论累计: {value_cnt} | "
                        f"checkpoint_use: {ck_use} | checkpoint_value: {ck_val} | checkpoint_meta: {ck_meta})"
                    )
                else:
                    print(f"    -> 完成: {uname} (本次入库Raw: {total_added} | 价值评论累计: {value_cnt} | checkpoint: {ck_use})")
                if not (self._stop_event and self._stop_event.is_set()):
                    user_completed = True
                if user_completed:
                    # Mark as updated when we made progress or we have a checkpoint-based incremental scan.
                    # This prevents the same user from being re-processed every loop and triggering risk-control.
                    if got_any_json and (checkpoint_dt or reached_checkpoint or total_added > 0):
                        db.update_task_status(uid, "Target_users")
                    if got_any_json and newest_seen:
                        db.set_user_comments_last_crawled(uid, newest_seen.strftime("%Y-%m-%d %H:%M:%S"))

            except GlobalBackoff:
                raise
            except Exception as e:
                print(f"    ❌ 异常 [{uname}]: {e}")
                if "断开" in str(e) or "disconnected" in str(e):
                    driver = SpiderTools.restart_browser(driver, self._init_browser_fn)
                    list_tab = driver.latest_tab
                else:
                    try:
                        list_tab.listen.stop()
                    except Exception:
                        pass

        return driver
