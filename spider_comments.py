import gzip
import json
import time

import config


class SpiderCommentsMixin:
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
        if not pending:
            return

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
                data = self._decode_response(res)
                if data:
                    raw_rows = process_page_data(data)
                    if raw_rows:
                        self.db.execute_many_safe(
                            "INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed, Forward, Comment_Count, Like) VALUES (?,?,?,?,?,?,?,?,?)",
                            raw_rows,
                        )
                        total_added += len(raw_rows)
                else:
                    if not res:
                        print(f"    ⚠️ 第一页超时或无数据")

                # --- 循环翻页直到达标 ---
                while total_added < config.ARTICLE_COUNT_LIMIT:
                    if self._has_slider():
                        self.safe_action()

                    next_btn = list_tab.ele('.pagination__next', timeout=2)
                    if next_btn and next_btn.states.is_displayed:
                        next_btn.click(by_js=True)

                        # 等待下一页数据包
                        res = list_tab.listen.wait(timeout=5)
                        data = self._decode_response(res)

                        if data:
                            raw_rows = process_page_data(data)
                            if raw_rows:
                                self.db.execute_many_safe(
                                    "INSERT OR IGNORE INTO Raw_Statuses (Status_Id, User_Id, Description, Created_At, Stock_Tags, Is_Analyzed, Forward, Comment_Count, Like) VALUES (?,?,?,?,?,?,?,?,?)",
                                    raw_rows,
                                )
                                total_added += len(raw_rows)
                            else:
                                break  # 有包但没数据，可能到底了
                        else:
                            break  # 没包
                    else:
                        break  # 没按钮了

                list_tab.listen.stop()
                print(f"    -> 完成: {uname} (入库: {total_added})")
                self.db.update_task_status(uid, "Target_users")

            except Exception as e:
                print(f"    ❌ 异常 [{uname}]: {e}")
                if "断开" in str(e) or "disconnected" in str(e):
                    self._restart_browser()
                    list_tab = self.driver.latest_tab
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

