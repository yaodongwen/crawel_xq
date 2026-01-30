import gzip
import json
import time
import re
from lxml import etree
import config
from DrissionPage import ChromiumPage, ChromiumOptions
from spider_tools import SpiderTools

class SpiderPortfolioMixin:
    def __init__(self):
        self.driver = self._init_browser()

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_browser_path(config.MAC_CHROME_PATH)
        co.set_user_data_path(config.USER_DATA_PATH)
        co.set_local_port(9337) 
        co.set_argument('--ignore-certificate-errors')
        # 加速关键：禁用图片加载，大幅缩短页面加载时间
        co.no_imgs(True) 
        try: return ChromiumPage(co)
        except Exception as e: 
            print(f"\n[启动错误] {e}"); exit()

    def _parse_comments_fragment(self, html_content):
        """解析动态评论 HTML 片段，精准提取红框中的作者名"""
        if isinstance(html_content, bytes):
            html_text = html_content.decode('utf-8', errors='ignore')
        else:
            html_text = html_content

        tree = etree.HTML(html_text)
        items = tree.xpath('//div[contains(@class, "status-item")]')
        results = []

        for item in items:
            try:
                # 提取作者名 (对应截图路径)
                author = item.xpath('.//div[@class="status-retweet-user"]/a[@class="name"]/text()')
                author_name = author[0].strip() if author else "未知作者"

                # 提取正文逻辑 (保留你原有的 content_nodes 比较逻辑)
                content_nodes1 = item.xpath('.//div[@class="text"]//text()')
                content_nodes2 = item.xpath('.//script[@class="single-description"]//text()')
                content_nodes = content_nodes2 if len(content_nodes1) < len(content_nodes2) else content_nodes1
                
                text_val = "".join(content_nodes).strip() if isinstance(content_nodes, list) else content_nodes

                likes = item.xpath('.//a[contains(@class, "btn-like")]//em/text()')
                comments = item.xpath('.//a[contains(@class, "btn-status-reply")]//em/text()')

                results.append({
                    "author": author_name,
                    "text": text_val,
                    "likes": likes[0] if likes else "0",
                    "comments_count": comments[0] if comments else "0"
                })
            except Exception:
                continue
        return results

    def _portfolio_status(self, symbol, tab):
        """获取组合关停状态"""
        status = {"is_closed": False}
        cube_closed = tab.ele('xpath://div[@class="cube-closed"]')
        if cube_closed:
            status["is_closed"] = True
            p_elements = cube_closed.eles('xpath:.//div[@class="text"]/p')
            for p in p_elements:
                text = p.text.strip()
                if '创建于' in text:
                    status['create_time'] = text.replace('创建于：', '').strip()
                elif '关停时间' in text:
                    status['close_time'] = text.replace('关停时间：', '').strip()
        return status

    def _mine_portfolio(self, symbol):
        """完整抓取函数：修正 JS 错误并防止字典覆盖"""
        # 初始化结果字典，确保数据不会丢失
        results = {
            "symbol": symbol,
            "Detailed_Position": [],
            "comments": [],
            "rebalances": []
        }
        
        try:
            url = f"https://xueqiu.com/P/{symbol}"
            detail_tab = self.driver.new_tab()
            
            # 1. 启动监听器 (合并监听)
            detail_tab.listen.start(['cube/timeline', 'rebalancing/history.json'])
            detail_tab.get(url)

            # 2. 基础信息
            title_ele = detail_tab.ele('.cube-title', timeout=5)
            results['portfolio_name'] = title_ele.ele('.name').text
            xpath_num = '//div[@class="cube-title"]//div[@class="cube-people-data"]//span[@class="num"]'
            results['portfolio_follows'] = re.search(r'(\d+)', detail_tab.ele('xpath:' + xpath_num).text).group(1)

            # 3. 盈利数据
            info_container = detail_tab.ele('#cube-info', timeout=5)
            per_spans = info_container.eles('.per')
            labels = ["Total_Return_Percentage", "Daily_Return_Percentage", "Monthly_Return_Percentage", 
                      "Net_Worth", "Total_Revenue_Ranking_Exceeds"]
            for i, span in enumerate(per_spans):
                if i < len(labels):
                    results[labels[i]] = span.text.strip()

            # 4. 用户信息
            creator = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//a[contains(@class, "creator")]')
            results['create_user_id'] = creator.attr('href').strip('/').split('/')[-1]
            results['create_user_name'] = creator.ele('.name').text
            results['portfolio_description'] = detail_tab.ele('xpath://div[contains(@class, "cube-creator-info")]//div[@class="desc"]/span[@class="text"]').text

            # 5. 生存状态
            results.update(self._portfolio_status(symbol, detail_tab))

            # 6. 仓位信息 --- 【核心修正：JS 中使用 .trim()】 ---
            # 保留你 spider_portfolio.py 中的逻辑字段名
            get_pos_js = """
            (() => {
                let segments = document.querySelectorAll('.weight-list .segment');
                return Array.from(segments).map(seg => {
                    let name_ele = seg.querySelector('.segment-name');
                    let prop_ele = seg.querySelector('.segment-weight');
                    return {
                        "name": name_ele ? name_ele.innerText.trim() : "未知板块",
                        "proportion": prop_ele ? prop_ele.innerText.trim() : "0%",
                        "stocks": Array.from(seg.querySelectorAll('.stock')).map(s => ({
                            "name": s.querySelector('.name')?.innerText.trim() || '',
                            "price": s.querySelector('.price')?.innerText.trim() || '',
                            "weight": s.querySelector('.stock-weight')?.innerText.trim() || ''
                        }))
                    };
                });
            })()
            """
            results["Detailed_Position"] = detail_tab.run_js(get_pos_js)

            # 7. 触发滚动与点击监听
            detail_tab.scroll.down(1000)
            history_btn = detail_tab.ele('xpath://a[@class="history"]')
            if history_btn:
                history_btn.click(by_js=True)

            # 8. 依次获取监听到的数据包
            # 捕获评论
            res_comm = detail_tab.listen.wait(timeout=5)
            if res_comm:
                results["comments"] = self._parse_comments_fragment(res_comm.response.body)
            
            # 捕获调仓 (按顺序读取队列)
            res_rebal = detail_tab.listen.wait(timeout=3)
            if res_rebal:
                results["rebalances"] = SpiderTools.decode_response(res_rebal)

            detail_tab.close()

        except Exception as e:
            print(f"⚠️ 抓取失败 {symbol}: {e}")
            if self.driver.tabs_count > 1:
                self.driver.latest_tab.close()
        
        return results

if __name__ == "__main__":
    aa = SpiderPortfolioMixin()
    start_time = time.time()
    final_data = aa._mine_portfolio('ZH3084474')
    print(f"--- 最终结果 (总耗时: {time.time() - start_time:.2f}s) ---")
    print(json.dumps(final_data, ensure_ascii=False, indent=2))