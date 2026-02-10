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
        co.set_browser_path(config.get_chrome_path())
        co.set_user_data_path(config.get_user_data_path())
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
                # 提取作者名
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


class PortfolioCrawler(SpiderPortfolioMixin):
    def __init__(self, init_browser_fn=None):
        # Keep signature compatible with main_spider; use internal browser for detail mining.
        super().__init__()

    @staticmethod
    def _first_nonempty_line(text):
        if not text:
            return None
        for line in str(text).splitlines():
            s = line.strip()
            if s:
                return s
        return None

    @staticmethod
    def _clean_title(title):
        """Normalize browser title as a fallback for portfolio name extraction."""
        if not title:
            return None
        t = str(title).strip()
        # Common suffix on xueqiu pages
        for suf in (" - 雪球", "- 雪球"):
            if t.endswith(suf):
                t = t[: -len(suf)].strip()
        return t or None

    def _try_ele(self, root, locator, timeout=2):
        """DrissionPage.ele() raises when not found; treat it as optional."""
        if not root:
            return None
        try:
            return root.ele(locator, timeout=timeout)
        except Exception:
            return None

    def _try_text(self, root, locator, timeout=2):
        el = self._try_ele(root, locator, timeout=timeout)
        try:
            if not el:
                return None
            s = (el.text or "").strip()
            return s or None
        except Exception:
            return None

    def _try_text_any(self, root, locators, timeout=2):
        for loc in locators:
            s = self._try_text(root, loc, timeout=timeout)
            if s:
                return s
        return None

    def _portfolio_status(self, symbol, tab):
        """获取组合关停状态"""
        status = {"is_closed": False}
        cube_closed = tab.ele('xpath://div[contains(@class, "cube-closed")]',timeout=0.5)
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
    
    # 获取当前持仓
    def get_portfolio_holdings(self, symbol):
        """
        组合详情获取逻辑：
        回归手动定位抓取方式，并在主页失效时通过调仓接口补全。
        """
        results = {
            "symbol": symbol,
            "Detailed_Position": []
        }
        
        try:
            url = f"https://xueqiu.com/P/{symbol}"
            # 在访问页面前开启监听调仓接口
            self.driver.listen.start('rebalancing/history.json')
            
            detail_tab = self.driver.new_tab(url)
            print(f"正在访问组合: {symbol}")
            SpiderTools.safe_action(self.driver)

            # 1. 尝试使用“老方法”手动抓取主页持仓
            # 1. 定位到总容器
            weight_list_container = self._try_ele(detail_tab, 'xpath://div[contains(@class, "weight-list")]', timeout=5)
            
            if weight_list_container:
                # 2. 获取容器下的所有直接子元素 (eles 返回的是列表，直接对其进行遍历)
                all_items = weight_list_container.eles('xpath:./*')
                
                current_segment = None
                for item in all_items:
                    tag_class = item.attr('class')
                    if not tag_class: continue

                    # 判定为板块行 (segment)
                    if 'segment' in tag_class:
                        seg_name = self._try_text(item, 'xpath:.//span[contains(@class, "segment-name")]', timeout=1) or ''
                        seg_prop = self._try_text(item, 'xpath:.//span[contains(@class, "segment-weight") and contains(@class, "weight")]', timeout=1) or ''
                        
                        current_segment = {
                            "category_name": seg_name,
                            "proportion": seg_prop,
                            "stocks": []
                        }
                        results["Detailed_Position"].append(current_segment)
                    
                    # 判定为股票行 (stock)，且已进入某个板块
                    elif 'stock' in tag_class and current_segment is not None:
                        # 根据截图，股票名在 div.name，价格在 div.price，权重在 span.stock-weight
                        results["Detailed_Position"][-1]["stocks"].append({
                            "name": self._try_text(item, 'xpath:.//div[contains(@class, "name")]', timeout=1) or "",
                            "price": self._try_text(item, 'xpath:.//div[contains(@class, "price")]', timeout=1) or "",
                            "weight": self._try_text(item, 'xpath:.//span[contains(@class, "stock-weight")]', timeout=1) or ""
                        })

            detail_tab.close()
            return results

        except Exception as e:
            print(f"⚠️ 抓取失败 {symbol}: {e}")
            if self.driver.tabs_count > 1:
                self.driver.latest_tab.close()
            return results


    def _mine_portfolio(self, symbol):
        SpiderTools.safe_action(self.driver)
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
            SpiderTools.safe_action(self.driver)

            # 2. 基础信息
            title_ele = self._try_ele(detail_tab, '.cube-title', timeout=10)
            results['portfolio_name'] = (
                self._try_text_any(
                    title_ele,
                    ['.name', 'tag:h1', 'xpath:.//*[contains(@class, "name") or contains(@class, "title")]'],
                    timeout=1,
                )
                or self._first_nonempty_line(getattr(title_ele, 'text', '') if title_ele else '')
                or self._clean_title(detail_tab.title)
            )

            follows_raw = self._try_text(
                detail_tab,
                'xpath://div[contains(@class, "cube-title")]//div[contains(@class, "cube-people-data")]//span[contains(@class, "num")]',
                timeout=3,
            )
            m = re.search(r'(\d+)', follows_raw or '')
            results['portfolio_follows'] = m.group(1) if m else None


            # 3. 盈利数据
            info_container = self._try_ele(detail_tab, '#cube-info', timeout=5)
            per_spans = info_container.eles('.per') if info_container else []
            labels = ["Total_Return_Percentage", "Daily_Return_Percentage", "Monthly_Return_Percentage", 
                      "Net_Worth", "Total_Revenue_Ranking_Exceeds"]
            for i, span in enumerate(per_spans):
                if i < len(labels):
                    results[labels[i]] = span.text.strip()

            # 4. 用户信息
            creator = self._try_ele(detail_tab, 'xpath://div[contains(@class, "cube-creator-info")]//a[contains(@class, "creator")]', timeout=5)
            href = creator.attr('href') if creator else ''
            results['create_user_id'] = href.strip('/').split('/')[-1] if href else None
            results['create_user_name'] = (
                self._try_text_any(creator, ['.name', 'xpath:.//*[contains(@class, "name")]'], timeout=1)
                or self._first_nonempty_line(getattr(creator, 'text', '') if creator else '')
            )
            results['portfolio_description'] = self._try_text(
                detail_tab,
                'xpath://div[contains(@class, "cube-creator-info")]//div[contains(@class, "desc")]//span[contains(@class, "text")]',
                timeout=3,
            )
            
            
            # 5. 生存状态
            results.update(self._portfolio_status(symbol, detail_tab))

            # 6. 仓位信息 --- 【核心修正：JS 中使用 .trim()】 ---
            results["Detailed_Position"] = self.get_portfolio_holdings(symbol)["Detailed_Position"]

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
    aa = PortfolioCrawler()
    start_time = time.time()
    final_data = aa._mine_portfolio('ZH3084474')
    print(f"--- 最终结果 (总耗时: {time.time() - start_time}s) ---")
    print(json.dumps(final_data, ensure_ascii=False, indent=2))
