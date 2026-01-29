import gzip
import json
import os
import random
import time
from datetime import datetime


class SpiderTools:
    """Utilities shared across spiders.

    Prefer explicit inputs (driver/res/etc.) over implicit `self` so modules can be decoupled.
    """

    @staticmethod
    def get_now_str():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def format_time(timestamp):
        try:
            # 兼容 Unix 时间戳 (毫秒)
            if str(timestamp).isdigit():
                ts = float(timestamp) / 1000
                time_local = time.localtime(ts)
                return time.strftime("%Y-%m-%d %H:%M:%S", time_local)
            # 兼容 datetime 字符串格式
            return str(timestamp)
        except Exception:
            return str(timestamp)

    @staticmethod
    def random_sleep(min_s=1.0, max_s=2.0):
        time.sleep(random.uniform(min_s, max_s))

    @staticmethod
    def has_slider(driver):
        try:
            tab = driver.latest_tab
            return tab.ele('#aliyunCaptcha-sliding-slider', timeout=0.1) or tab.ele('text:访问验证', timeout=0.1)
        except Exception:
            return False

    @staticmethod
    def solve_slider(driver):
        tab = driver.latest_tab
        time.sleep(1)
        try:
            btn = tab.ele('#aliyunCaptcha-sliding-slider', timeout=3)
            if btn:
                btn.drag(random.randint(400, 600), random.randint(5, 10))
        except Exception:
            pass

    @staticmethod
    def check_405(driver):
        try:
            if "405" in driver.latest_tab.title:
                print("\n>>> [严重] 触发405，暂停15分钟...")
                time.sleep(900)
                driver.latest_tab.refresh()
        except Exception:
            pass

    @classmethod
    def safe_action(cls, driver):
        cls.check_405(driver)
        max_retries = 10
        count = 0
        while cls.has_slider(driver):
            count += 1
            if count > 1:
                print(f">>> [滑块] 第 {count} 次尝试...")
            cls.solve_slider(driver)
            time.sleep(2)
            if count >= max_retries:
                print(">>> [滑块] 尝试次数过多，刷新页面...")
                driver.latest_tab.refresh()
                time.sleep(3)
                count = 0

    @staticmethod
    def restart_browser(driver, init_browser_fn):
        try:
            driver.quit()
        except Exception:
            pass
        os.system("pkill -f 'Google Chrome'")
        time.sleep(2)
        return init_browser_fn()

    @staticmethod
    def decode_response(res):
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

