import gzip
import json
import time
import re
from lxml import etree  # 必须导入
import config
from DrissionPage import ChromiumPage, ChromiumOptions
import time
import threading
import os
import config
from db_manager import DBManager
from spider_tools import SpiderTools

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
    
    # ================= 优化后的评论获取逻辑 =================
    def _parse_comments_fragment(self, html_content):
        """
        核心提取逻辑：解析监听到的 HTML 片段
        """
        # 如果 body 是 bytes 类型，先解码
        if isinstance(html_content, bytes):
            html_text = html_content.decode('utf-8', errors='ignore')
        else:
            html_text = html_content

        tree = etree.HTML(html_text)
        # 获取所有动态条目
        items = tree.xpath('//div[contains(@class, "status-item")]')
        results = []

        for item in items:
            try:
                # 1. 提取作者名 (对应你截图中的：96船票_)
                # 路径定位到 status-bd 下的 status-retweet-user 里的 a 标签
                author = item.xpath('.//div[@class="status-retweet-user"]/a[@class="name"]/text()')
                author_name = author[0].strip() if author else "未知作者"

                # 2. 提取正文 (text 里的所有文字)
                content_nodes1 = item.xpath('.//div[@class="text"]//text()')
                content_nodes2 = item.xpath('.//script[@class="single-description"]//text()')
                content_nodes = content_nodes2 if len(content_nodes1) < len(content_nodes2) else content_nodes1
                content = content_nodes

                # 3. 提取互动数 (点赞和讨论)
                likes = item.xpath('.//a[contains(@class, "btn-like")]//em/text()')
                comments = item.xpath('.//a[contains(@class, "btn-status-reply")]//em/text()')

                results.append({
                    "author": author_name,
                    "text": content,
                    "likes": likes[0] if likes else "0",
                    "comments": comments[0] if comments else "0"
                })
            except Exception:
                continue
        return results
    
    def _portfolio_status(self, symbol, tab):
            
            # 2. 访问页面
            url = f"https://xueqiu.com/P/{symbol}"
            
            # 假设 tab 是当前标签页对象
            cube_closed = tab.ele('xpath://div[@class="cube-closed"]')

            if cube_closed:
                # 获取 .text 下的两个 p 标签
                p_elements = cube_closed.eles('xpath:.//div[@class="text"]/p')
                
                create_time = None
                close_time = None
                
                for p in p_elements:
                    text = p.text.strip()
                    if '创建于' in text:
                        create_time = text.replace('创建于：', '').strip()
                    elif '关停时间' in text:
                        close_time = text.replace('关停时间：', '').strip()
                
                print(f"创建时间: {create_time}")
                print(f"关停时间: {close_time}")
            else:
                print("组合开启中")

    def _mine_portfolio(self, symbol):
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
            # 在 detail_tab 中执行一段 JS，一次性提取所有动态数据
            # 1. 设置监听
            self.driver.listen.start('cube/timeline')
            
            # 2. 访问页面
            url = f"https://xueqiu.com/P/{symbol}"
            self.driver.get(url)
            
            # 3. 触发加载 (向下滚动)
            self.driver.scroll.down(1000)
            
            # 4. 获取拦截到的数据包
            res = self.driver.listen.wait(timeout=5)
            if res:
                # 拿到接口返回的混合 HTML 文本
                comments = self._parse_comments_fragment(res.response.body)
                
                for c in comments:
                    print(f"【{c['author']}】: {c['text'][:50]}...")
                    print(f"   📊 赞: {c['likes']} | 讨论: {c['comments']}")
                    print("-" * 40)
            else:
                print("❌ 未捕获到 timeline 接口数据")



            # 获取历史调仓
            res_rebalances = self._mine_rebalance(symbol,detail_tab)
            print(res_rebalances)

            # 抓取完成后关闭当前长文页
            detail_tab.close()


        except Exception as e:
            print(f"    ⚠️ 组合获取失败 {symbol}: {e}")
            # 异常保护：如果标签页没关掉，强制关闭
            if self.driver.tabs_count > 1:
                # 简单判断一下当前页是不是列表页，如果不是就关掉
                if str(symbol) not in self.driver.latest_tab.url:
                    self.driver.latest_tab.close()
            return None
    

    def _mine_rebalance(self, symbol, tab):
        try:
            url = f"https://xueqiu.com/P/{symbol}"
            # tab = self.driver.new_tab(url)
            print(f"已打开组合页: {symbol}")

            # 监听调仓接口
            tab.listen.start('rebalancing/history.json')
            tab.get(url)

            btn = tab.ele('xpath://a[@class="history"]')
            if btn:
                btn.click(by_js=True)
            # 等待请求（new_tab 已加载页面，直接等即可）
            res = tab.listen.wait(timeout=3)
            data = SpiderTools.decode_response(res)

            if data is None:
                print(f"❌ {symbol}: 未捕获到调仓记录接口")
                return None


        except Exception as e:
            print(f"⚠️ {symbol} 出错: {e}")
            return None
        finally:
            if 'tab' in locals():
                tab.close()

        return data
    

aa = SpiderPortfolioMixin()
aa._mine_portfolio('ZH3084474')
