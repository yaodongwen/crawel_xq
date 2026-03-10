import gzip
import json
import os
import random
import re
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
    def _try_ele(root, locator, timeout=0.1):
        try:
            return root.ele(locator, timeout=timeout) if root else None
        except Exception:
            return None

    @staticmethod
    def _is_displayed(ele):
        try:
            return bool(ele) and bool(ele.states.is_displayed)
        except Exception:
            # Be strict: if we can't confirm visibility, don't treat it as a slider signal.
            return False

    @classmethod
    def detect_slider(cls, driver):
        """Detect whether current page is blocked by risk-control/captcha.

        Returns: (has_slider: bool, reason: str|None)
        """
        try:
            tab = driver.latest_tab
        except Exception:
            return False, None

        # 1) Most reliable: Aliyun slider handle exists and is displayed.
        btn = cls._try_ele(tab, "#aliyunCaptcha-sliding-slider", timeout=0.1)
        if cls._is_displayed(btn):
            return True, "aliyun_slider"

        try:
            url = tab.url or ""
            title = tab.title or ""
        except Exception:
            url, title = "", ""

        looks_like_verify = (
            ("验证" in title)
            or ("访问验证" in title)
            or ("安全验证" in title)
            or ("验证" in url)
            or ("captcha" in url.lower())
            or ("verify" in url.lower())
        )

        # 2) Text hints (only when the page itself looks like a verify page).
        if looks_like_verify:
            for t in ("访问验证", "安全验证", "请完成验证", "滑动验证"):
                el = cls._try_ele(tab, f"text:{t}", timeout=0.1)
                if cls._is_displayed(el):
                    return True, f"text:{t}"

        # 3) Captcha iframe is too broad; only treat as slider when url/title indicates verification.
        iframe = cls._try_ele(
            tab,
            'xpath://iframe[contains(@src,"captcha") or contains(@src,"Captcha") or contains(@src,"verify") or contains(@src,"Verify")]',
            timeout=0.1,
        )
        if cls._is_displayed(iframe) and looks_like_verify:
            return True, "captcha_iframe"

        return False, None

    @classmethod
    def has_slider(cls, driver):
        has, _ = cls.detect_slider(driver)
        return has

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

    @classmethod
    def check_405(cls, driver):
        try:
            tab = driver.latest_tab
            title = tab.title or ""
            url = tab.url or ""

            # "405" can appear in random ids/params; use stricter matching.
            title_has_405 = bool(re.search(r"(^|\\D)405(\\D|$)", title))
            url_has_405 = bool(re.search(r"(^|[^0-9])405([^0-9]|$)", url))
            text_405 = cls._try_ele(tab, "text:405", timeout=0.1)
            has_text_405 = cls._is_displayed(text_405)

            # Some normal pages may contain "405" in unrelated content (post ids, etc.).
            # Only treat "text:405" as a block signal when accompanied by typical block hints.
            hint_texts = (
                "访问过于频繁",
                "请求过于频繁",
                "访问受限",
                "暂时无法访问",
                "系统繁忙",
                "请稍后再试",
                "稍后再试",
                "访问异常",
                "服务异常",
                "页面不存在",
                "Not Allowed",
                "Access Denied",
            )
            has_hint = any(
                cls._is_displayed(cls._try_ele(tab, f"text:{t}", timeout=0.1)) for t in hint_texts
            )

            # If we're on a JSON endpoint, the tab title is often the URL itself.
            # Avoid long sleeps for JSON requests unless we see strong block hints.
            is_json = ".json" in url.lower()

            # Avoid false positives: require at least one strong signal.
            if not (title_has_405 or url_has_405 or (has_text_405 and has_hint)):
                return False
            if is_json and not has_hint and not ("Not Allowed" in title or "Not Allowed" in url):
                if bool(getattr(config, "BLOCK_DEBUG", False)):
                    print(f"\n>>> [405调试] JSON 请求疑似误判，忽略。")
                return False

            # Further tighten: if only title has 405 but URL isn't xueqiu, ignore.
            if title_has_405 and not (url_has_405 or "xueqiu.com" in url or (has_text_405 and has_hint)):
                return False

            if bool(getattr(config, "BLOCK_DEBUG", False)):
                print(
                    f"\n>>> [405调试] title={title!r} url={url!r} "
                    f"signals=title:{title_has_405} url:{url_has_405} text405:{has_text_405} hint:{has_hint}"
                )

            sleep_s = int(getattr(config, "BLOCK_SLEEP_SECONDS", 600))
            # Allow disabling long sleeps in testing.
            if sleep_s <= 0:
                print("\n>>> [严重] 触发405（已关闭自动等待），跳过等待。")
                return True

            now = time.time()
            last = float(getattr(cls, "_last_405_sleep_ts", 0.0))
            # Avoid repeated long sleeps when safe_action is called frequently.
            if now - last < max(30.0, sleep_s * 0.8):
                return True

            cls._last_405_sleep_ts = now
            mins = max(1, int(round(sleep_s / 60)))
            print(f"\n>>> [严重] 触发405，暂停 {mins} 分钟...")
            time.sleep(sleep_s)
            try:
                tab.refresh()
            except Exception:
                pass
            return True
        except Exception:
            return False

    @classmethod
    def safe_action(cls, driver):
        # 405 block takes precedence: don't try slider when we're rate-limited/blocked.
        if cls.check_405(driver):
            return
        max_retries = 10
        count = 0
        refreshes = 0
        max_refreshes = int(getattr(config, "SLIDER_MAX_REFRESHES", 3))
        last_reason = None
        debug = bool(getattr(config, "SLIDER_DEBUG", False))

        while True:
            if cls.check_405(driver):
                return
            has, reason = cls.detect_slider(driver)
            if not has:
                return

            # If we didn't find the actual slider handle, don't loop forever.
            # (Most of these cases are either false positives or non-slider verification pages.)
            if reason != "aliyun_slider":
                if debug:
                    print(
                        f">>> [滑块] 非滑块验证页({reason})，跳过自动拖动 | url={getattr(driver.latest_tab, 'url', '')}"
                    )
                return

            count += 1
            if debug and reason != last_reason:
                last_reason = reason
                print(f">>> [滑块] 检测到验证页: {reason} | url={getattr(driver.latest_tab, 'url', '')}")
            if count > 1:
                print(f">>> [滑块] 第 {count} 次尝试...")

            cls.solve_slider(driver)
            time.sleep(2)
            if count >= max_retries:
                print(">>> [滑块] 尝试次数过多，刷新页面...")
                refreshes += 1
                try:
                    driver.latest_tab.refresh()
                except Exception:
                    pass
                time.sleep(3)
                count = 0
                if refreshes >= max_refreshes:
                    print(f">>> [滑块] 刷新仍未解除验证，停止重试（{refreshes}/{max_refreshes}）。")
                    return

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
