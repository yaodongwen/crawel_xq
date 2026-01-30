import gzip
import json
import time
import re
from lxml import etree  # 必须导入
import config
from DrissionPage import ChromiumPage, ChromiumOptions
import threading
import os
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
        if isinstance(html_content, bytes):
            html_text = html_content.decode('utf-8', errors='ignore')
        else:
            html_text = html_content

        tree = etree.HTML(html_text)
        items = tree.xpath('//div[contains(@class, "status-item")]')
        results = []

        for item in items:
            try:
                # 1. 提取作者名
                author = item.xpath('.//div[@class="status-retweet-user"]/a[@class="name"]/text()')
                author_name = author[0].strip() if author else "未知作者"

                # 2. 提取正文
                content_nodes1 = item.xpath('.//div[@class="text"]//text()')
                content_nodes2 = item.xpath('.//script[@class="single-description"]//text()')
                content = content_nodes2 if len(content_nodes1) < len(content_nodes2) else content_nodes1

                # 3. 提取互动数
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
        """
        获取组合生存状态
        """
        results = {"is_closed": False}
        cube_closed = tab.ele('xpath://div[@class="cube-closed"]')
        try:
            if cube_closed:
                results["is_closed"] = True
                p_elements = cube_closed.eles('xpath:.//div[@class="text"]/p')
                for p in p_elements:
                    text = p.text.strip()
                    if '创建于' in text:
                        results['create_time'] = text.replace('创建于：', '').strip()
                    elif '关停时间' in text:
                        results['close_time'] = text.replace('关停时间：', '').strip()
            else:
                print("组合开启中")
        except Exception as e:
            print(f"error in get portfolio status: {e}")
        return results

    def _mine_portfolio(self, symbol):
        """
        抓取完整组合信息并返回字典
        """
        results = {
            "symbol": symbol,
            "Detailed_Position": [],
            "comments": [],
            "rebalances": []
        }
        try:
            url = f"https://xueqiu.com/P/{symbol}"
            detail_tab = self.driver.new_tab(url)

            # 获取组合基本状态
            status_info = self._portfolio_status(symbol, detail_tab)
            results.update(status_info)

            # 等待核心元素加载
            title_ele = detail_tab.ele('.cube-title', timeout=5)
            if not title_ele:
                print(f"无法找到组合标题: {symbol}")
                detail_tab.close()
                return results

            # 获取 组合名和关注数 
            results['portfolio_name'] = title_ele.ele('.name').text
            xpath = '//div[@class="cube-title"]//div[@class="cube-people-data"]//span[@class="num"]'
            follows_span = detail_tab.ele('xpath:' + xpath)
            results['portfolio_follows'] = re.search(r'(\d+)', follows_span.text).group(1)

            # 获取盈利数据
            info_container = detail_tab.ele('#cube-info', timeout=5)
            per_spans = info_container.eles('.per')
            labels = ["Total_Return_Percentage", "Daily_Return_Percentage", "Monthly_Return_Percentage", 
                      "Net_Worth", "Total_Revenue_Ranking_Exceeds"]
            for i, span in enumerate(per_spans):
                if i < len(labels):
                    results[labels[i]] = span.text.strip()

            # 获取用户信息
            creator_link = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//a[contains(@class, "creator")]', timeout=5)
            href = creator_link.attr('href')
            results['create_user_id'] = href.strip('/').split('/')[-1]
            results['create_user_name'] = creator_link.ele('xpath:.//div[@class="name"]').text
            results['portfolio_description'] = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//div[@class="desc"]/span[@class="text"]').text

            # 获取仓位信息 (采用局部查找逻辑，防止错位)
            category_elements = detail_tab.eles('xpath://div[@class="weight-list"]//div[contains(@class, "segment")]')
            for category in category_elements:
                name_ele = category.ele('xpath:.//span[@class="segment-name"]')
                prop_ele = category.ele('xpath:.//span[@class="segment-weight weight"]')
                
                cat_name = name_ele.text.strip() if name_ele else "其他"
                cat_prop = prop_ele.text.strip() if prop_ele else "0%"
                
                stocks = []
                stock_elements = category.eles('xpath:.//a[contains(@class, "stock")]')
                for s_ele in stock_elements:
                    stocks.append({
                        "name": s_ele.ele('xpath:.//div[@class="name"]').text.strip(),
                        "price": s_ele.ele('xpath:.//div[@class="price"]').text.strip(),
                        "weight": s_ele.ele('xpath:.//span[contains(@class, "stock-weight")]').text.strip()
                    })
                
                results["Detailed_Position"].append({
                    "category_name": cat_name,
                    "proportion": cat_prop,
                    "stocks": stocks
                })

            # 获取动态评论 (监听逻辑)
            detail_tab.listen.start('cube/timeline')
            detail_tab.get(url) # 刷新或重新访问以触发 timeline
            detail_tab.scroll.down(1000)
            
            res_comment = detail_tab.listen.wait(timeout=5)
            if res_comment:
                comments_list = self._parse_comments_fragment(res_comment.response.body)
                for c in comments_list:
                    results["comments"].append({
                        "author": c['author'],
                        "text": c['text'],
                        "likes": c['likes'],
                        "comments_count": c['comments']
                    })

            # 获取历史调仓
            rebalance_data = self._mine_rebalance(symbol, detail_tab)
            if rebalance_data:
                results["rebalances"] = rebalance_data

            detail_tab.close()

        except Exception as e:
            print(f"    ⚠️ 组合获取失败 {symbol}: {e}")
            if self.driver.tabs_count > 1:
                self.driver.latest_tab.close()
        
        return results

    def _mine_rebalance(self, symbol, tab):
        try:
            # 监听调仓接口
            tab.listen.start('rebalancing/history.json')
            # 点击历史调仓按钮触发请求
            btn = tab.ele('xpath://a[@class="history"]')
            if btn:
                btn.click(by_js=True)
            
            res = tab.listen.wait(timeout=3)
            data = SpiderTools.decode_response(res)
            return data
        except Exception as e:
            print(f"⚠️ {symbol} 调仓抓取出错: {e}")
            return None

# 测试运行
if __name__ == "__main__":
    aa = SpiderPortfolioMixin()
    final_data = aa._mine_portfolio('ZH3084474')
    print(json.dumps(final_data, ensure_ascii=False, indent=4))