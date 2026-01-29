import gzip
import json
import time
import re

import config
from DrissionPage import ChromiumPage, ChromiumOptions
import time
import threading
import os
import config
from db_manager import DBManager

class SpiderPortfolioMixin:
    # ================= Step 3: 批次爬取 (含长文逻辑) =================
    def __init__(self):
        self.driver = self._init_browser()

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_browser_path(config.MAC_CHROME_PATH)
        co.set_user_data_path(config.USER_DATA_PATH)
        co.set_local_port(9337) 
        co.set_argument('--ignore-certificate-errors')
        try: return ChromiumPage(co)
        except Exception as e: 
            print(f"\n[启动错误] {e}"); exit()

    def _mine_long_articles(self, symbol):
        """
        组合详情获取逻辑：
        直接新建标签页访问组合详情页 URL (https://xueqiu.com/P/{symbol})，
        抓取完整组合信息后返回。
        """
        try:
            # 构造长文链接
            url = f"https://xueqiu.com/P/{symbol}"

            # 打开新标签页 (DrissionPage 会自动切换焦点到新页面)
            detail_tab = self.driver.new_tab(url)

            # 等待核心元素加载 (标题或正文)
            # 给 5 秒超时，防止页面加载太慢卡住
            title_ele = detail_tab.ele('.cube-title', timeout=5)  # 注意：class 是 cube-title，不是 article__bd__title

            # 获取 组合名和关注数 
            name_text = title_ele.ele('.name').text
            xpath = '//div[@class="cube-title"]//div[@class="cube-people-data"]//span[@class="num"]'
            follows_span = detail_tab.ele('xpath:' + xpath)
            follows_num = re.search(r'(\d+)', follows_span.text).group(1)  # 得到 '103'

            # 获取盈利数据
            info_container = detail_tab.ele('#cube-info', timeout=5)
            # 获取所有 per 类的 span
            per_spans = info_container.eles('.per')

            # 遍历并打印每个值 分别是总收益，日，月，净值，总收益排行超过%
            for i, span in enumerate(per_spans):
                print(f"第{i+1}个 per 值: {span.text}")

            # 获取用户信息
            # 定位整个 creator-info 区域（可选，用于限定范围）
            creator_info = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]')

            # 1. 获取 ID：从 creator 链接的 href 中提取
            href = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//a[contains(@class, "creator")]', timeout=5).attr('href')
            user_id = href.strip('/').split('/')[-1]  # 得到 "1433550277"

            # 2. 获取用户名：在 creator 下的 .name
            name = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//a[contains(@class, "creator")]//div[@class="name"]').text

            # 3. 获取描述：在 desc > span.text
            desc = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//div[@class="desc"]/span[@class="text"]').text

            print(f"ID: {user_id}")
            print(f"名称: {name}")
            print(f"描述: {desc}")


            # 获取仓位信息
            # 获取所有 stock <a> 标签（使用 XPath）
            stock_names = detail_tab.eles('xpath://div[@class="weight-list"]//div[contains(@class, "segment")]')
            for stocks in stock_names:
                stock_name = detail_tab.ele('xpath://div[@class="weight-list"]//span[@class="segment-name"]').text
                stock_num = detail_tab.ele('xpath://div[@class="weight-list"]//span[@class="segment-weight weight"]').text
                print(stock_name)
                print(stock_num)

                stock_elements = detail_tab.eles('xpath://div[@class="weight-list"]//a[contains(@class, "stock")]')

                for stock in stock_elements:
                    name = stock.ele('xpath:.//div[@class="name"]').text
                    price = stock.ele('xpath:.//div[@class="price"]').text
                    weight = stock.ele('xpath:.//span[contains(@class, "stock-weight")]').text

                    print(f"{name} | {price} | {weight}")

            # 获取 评论
            # 等待评论列表加载（关键！）
            # 等待 status-list 加载完成
            status_list = detail_tab.ele('xpath://div[@class="status-list"]', timeout=5)

            # 获取所有 status-item
            items = status_list.eles('xpath:.//div[contains(@class, "status-item")]')

            for item in items:
                # 1. 提取正文文本（从 .text 中的所有 <p>）
                text_div = item.ele('xpath:.//div[@class="text"]')
                if text_div:
                    paragraphs = text_div.eles('xpath:.//p')
                    full_text = "\n".join([p.text for p in paragraphs])
                else:
                    full_text = ""

                # 2. 提取互动数据
                likes = item.ele('xpath:.//a[@class="btn-like"]//span[@class="number"]/em').text
                reposts = item.ele('xpath:.//a[@class="btn-repost"]//span[@class="number"]/em').text
                comments = item.ele('xpath:.//a[@class="btn-status-reply last"]//span[@class="number"]/em').text

                # 打印结果
                print(f"=== 动态内容 ===")
                print(f"正文:\n{full_text}")
                print(f"赞: {likes}, 转发: {reposts}, 讨论: {comments}")
                print("-" * 50)

            # 获取历史调仓
            # 获取当前列表页 Tab
            list_tab = self.driver.latest_tab

            try:
                # 监听组合调仓历史接口（关键：用 'rebalancing/history.json' 作为关键词）
                list_tab.listen.start('rebalancing/history.json')
                # 访问组合页面（会触发 AJAX 请求）
                list_tab.get(f"https://xueqiu.com/P/{symbol}")
                # 等待接口响应（超时 5 秒）
                res = list_tab.listen.wait(timeout=5)
                if res is None:
                    print("❌ 超时：未捕获到调仓记录接口")
                # 获取 JSON 数据
                rebalancing_data = res.response.json()
                # 打印或处理数据
                print(f"✅ 捕获到 {len(rebalancing_data.get('list', []))} 条调仓记录")
                for item in rebalancing_data.get('list', [])[:5]:  # 打印前3条
                    print(f"  - {item['name']} ({item['symbol']}) → {item['weight']}%")
            except Exception as e:
                print(f"⚠️ 处理用户 {symbol} 时出错: {str(e)}")
                    

            # 抓取完成后关闭当前长文页
            detail_tab.close()


        except Exception as e:
            # print(f"    ⚠️ 长文补全失败 {status_id}: {e}")
            # 异常保护：如果标签页没关掉，强制关闭
            if self.driver.tabs_count > 1:
                # 简单判断一下当前页是不是列表页，如果不是就关掉
                if str(symbol) not in self.driver.latest_tab.url:
                    self.driver.latest_tab.close()
            return None
        
    

aa = SpiderPortfolioMixin()
aa._mine_long_articles('ZH3084474')
