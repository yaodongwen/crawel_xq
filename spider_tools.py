import gzip
import json
import os
import random
import time
from datetime import datetime

import config


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
            # Old Aliyun slider id + a few common texts on xueqiu risk-control pages.
            return (
                tab.ele('#aliyunCaptcha-sliding-slider', timeout=0.1)
                or tab.ele('xpath://*[@id="aliyunCaptcha-sliding-slider"]', timeout=0.1)
                or tab.ele('xpath://iframe[contains(@src,"captcha") or contains(@src,"Captcha")]', timeout=0.1)
                or tab.ele('text:访问验证', timeout=0.1)
                or tab.ele('text:安全验证', timeout=0.1)
                or tab.ele('text:请完成验证', timeout=0.1)
                or tab.ele('text:滑动验证', timeout=0.1)
            )
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
        if getattr(config, "OS_TYPE", "").lower() == "windows":
            os.system('taskkill /F /IM chrome.exe /T >NUL 2>&1')
        else:
            os.system("pkill -f 'Google Chrome'")
        time.sleep(2)
        return init_browser_fn()

    @staticmethod
    def decode_response(res):
        """从监听响应中安全解析 JSON 数据（自动处理 gzip）。

        返回 dict/list；若 body 不是 JSON（例如空串/HTML 风控页）则返回 None。
        """
        verbose = bool(getattr(config, "VERBOSE_DECODE_ERRORS", False))

        if not res or not hasattr(res, "response") or not hasattr(res.response, "body"):
            if verbose:
                print("decode_response: no response/body")
            return None

        body = res.response.body
        if body is None:
            return None

        # 情况1: DrissionPage 已自动解析为 dict/list（新版行为）
        if isinstance(body, (dict, list)):
            return body

        def _parse_text(text):
            if text is None:
                return None
            s = str(text).strip()
            if not s:
                return None
            # 非 JSON（常见：HTML 风控页/跳转页）
            if s[0] not in "{[":
                return None
            try:
                return json.loads(s)
            except Exception as e:
                if verbose:
                    print(f"decode_response: json parse failed: {e}")
                return None

        # 情况2: 是字符串（明文 JSON）
        if isinstance(body, str):
            return _parse_text(body)

        # 情况3: 是 bytes（可能是 gzip 压缩或原始 JSON 字节）
        if isinstance(body, bytes):
            try:
                headers = res.response.headers or {}
                if (
                    isinstance(headers, dict)
                    and "content-encoding" in headers
                    and "gzip" in str(headers["content-encoding"]).lower()
                ):
                    body = gzip.decompress(body)
                text = body.decode("utf-8", errors="ignore")
                return _parse_text(text)
            except Exception as e:
                if verbose:
                    print(f"decode_response: bytes decode failed: {e}")
                return None

        # 其他类型（如 int 等）
        if verbose:
            print(f"decode_response: unexpected body type: {type(body)}")
        return None
