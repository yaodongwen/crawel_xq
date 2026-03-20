import gzip
import json
import os
import random
import re
import time
from datetime import datetime

import config


class GlobalBackoff(Exception):
    """Signal a global backoff (e.g., 405/WAF) that should abort current step and sleep/restart safely."""

    def __init__(self, seconds, reason="block"):
        try:
            self.seconds = int(seconds)
        except Exception:
            self.seconds = 0
        self.reason = reason
        super().__init__(f"{reason}:{self.seconds}")


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
    def write_global_block_file(seconds):
        """Write a global backoff deadline to a shared file (best-effort).

        This is used to coordinate multiple processes (or just leave a breadcrumb for operators).
        """
        try:
            s = int(seconds)
        except Exception:
            s = 0
        if s <= 0:
            return False
        try:
            path = getattr(config, "GLOBAL_BLOCK_FILE", None)
        except Exception:
            path = None
        if not path:
            try:
                base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
            except Exception:
                base = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base, "data", "global_block_until.txt")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        try:
            until = datetime.fromtimestamp(time.time() + s).strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "w", encoding="utf-8") as f:
                f.write(until)
            return True
        except Exception:
            return False

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
            # Propagate global backoff ASAP (so other workers stop immediately).
            try:
                if bool(getattr(config, "GLOBAL_BLOCK_ON_405", False)) and sleep_s > 0:
                    cls.write_global_block_file(sleep_s)
            except Exception:
                pass

            # Prefer hibernation (close browser & wait at a safe boundary) over sleeping inside safe_action.
            if bool(getattr(config, "HIBERNATE_ON_405", False)) and sleep_s > 0:
                raise GlobalBackoff(sleep_s, reason="405")
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
    def check_waf(cls, driver):
        """Detect WAF-style block pages and trigger global backoff.

        Xueqiu sometimes returns an interstitial page like:
        "很抱歉...您的访问被阻断...请求ID..."
        """
        try:
            tab = driver.latest_tab
        except Exception:
            return False
        try:
            title = tab.title or ""
            url = tab.url or ""
        except Exception:
            title, url = "", ""

        hints = (
            "访问被阻断",
            "您的访问被阻断",
            "安全威胁",
            "请求ID",
            "被阻断",
            "Access Denied",
            "Request blocked",
        )

        title_hit = any(h in title for h in hints)
        url_hit = any(h.lower() in (url or "").lower() for h in ("waf", "deny", "blocked"))
        text_hit = False
        if not (title_hit or url_hit):
            try:
                # Only probe DOM when not already obvious from title/url (avoid overhead).
                text_hit = any(cls._is_displayed(cls._try_ele(tab, f"text:{h}", timeout=0.1)) for h in hints)
            except Exception:
                text_hit = False

        if not (title_hit or url_hit or text_hit):
            return False

        sleep_s = int(getattr(config, "WAF_SLEEP_SECONDS", getattr(config, "BLOCK_SLEEP_SECONDS", 600)))
        if sleep_s <= 0:
            print("\n>>> [严重] 触发访问阻断（WAF）（已关闭自动等待），跳过等待。")
            return True

        now = time.time()
        last = float(getattr(cls, "_last_waf_sleep_ts", 0.0))
        if now - last < max(30.0, sleep_s * 0.8):
            return True
        cls._last_waf_sleep_ts = now

        mins = max(1, int(round(sleep_s / 60)))
        # Prefer hibernation (close browser & wait at a safe boundary) when enabled.
        if bool(getattr(config, "HIBERNATE_ON_WAF", False)):
            raise GlobalBackoff(sleep_s, reason="WAF")
        print(f"\n>>> [严重] 触发访问阻断（WAF），暂停 {mins} 分钟...")
        time.sleep(sleep_s)
        try:
            tab.refresh()
        except Exception:
            pass
        return True

    @classmethod
    def safe_action(cls, driver):
        # WAF block takes precedence.
        if cls.check_waf(driver):
            return
        # 405 block takes precedence.
        if cls.check_405(driver):
            return

        has, reason = cls.detect_slider(driver)
        if not has:
            return

        try:
            url = getattr(driver.latest_tab, "url", "") or ""
        except Exception:
            url = ""

        # Don't attempt to automatically bypass verification challenges.
        print(f">>> [滑块] 检测到验证页: {reason} | url={url}")

        sleep_s = int(getattr(config, "SLIDER_SLEEP_SECONDS", getattr(config, "BLOCK_SLEEP_SECONDS", 600)))
        if sleep_s <= 0:
            return
        if bool(getattr(config, "HIBERNATE_ON_SLIDER", True)):
            raise GlobalBackoff(sleep_s, reason="slider")

        mins = max(1, int(round(sleep_s / 60)))
        print(f">>> [滑块] 暂停 {mins} 分钟后重试...")
        time.sleep(sleep_s)
        try:
            driver.latest_tab.refresh()
        except Exception:
            pass
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
                enc = None
                try:
                    if isinstance(headers, dict):
                        enc = headers.get("content-encoding") or headers.get("Content-Encoding")
                except Exception:
                    enc = None
                is_gzip = False
                try:
                    if enc and "gzip" in str(enc).lower():
                        is_gzip = True
                    elif len(body) >= 2 and body[0] == 0x1F and body[1] == 0x8B:
                        # Some intermediaries omit/alter headers; detect gzip via magic bytes.
                        is_gzip = True
                except Exception:
                    is_gzip = False
                if is_gzip:
                    try:
                        body = gzip.decompress(body)
                    except Exception:
                        pass
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

    @staticmethod
    def response_text_snippet(res, limit=600):
        """Return a short decoded text snippet for debugging non-JSON responses."""
        if not res or not hasattr(res, "response") or not hasattr(res.response, "body"):
            return ""
        body = res.response.body
        if body is None:
            return ""
        if isinstance(body, (dict, list)):
            try:
                s = json.dumps(body, ensure_ascii=False)
            except Exception:
                s = str(body)
            return s[: int(limit)]
        if isinstance(body, str):
            return body.strip()[: int(limit)]
        if isinstance(body, bytes):
            try:
                headers = res.response.headers or {}
                enc = None
                try:
                    if isinstance(headers, dict):
                        enc = headers.get("content-encoding") or headers.get("Content-Encoding")
                except Exception:
                    enc = None
                is_gzip = False
                try:
                    if enc and "gzip" in str(enc).lower():
                        is_gzip = True
                    elif len(body) >= 2 and body[0] == 0x1F and body[1] == 0x8B:
                        is_gzip = True
                except Exception:
                    is_gzip = False
                if is_gzip:
                    try:
                        body = gzip.decompress(body)
                    except Exception:
                        pass
                return body.decode("utf-8", errors="ignore").strip()[: int(limit)]
            except Exception:
                return ""
        return str(body).strip()[: int(limit)]

    @classmethod
    def response_looks_blocked(cls, res):
        """Heuristic: detect HTML/WAF block responses where JSON is expected."""
        if not res or not hasattr(res, "response"):
            return False, None
        try:
            url = getattr(getattr(res, "request", None), "url", "") or ""
        except Exception:
            url = ""

        snippet = cls.response_text_snippet(res, limit=1200)
        if not snippet:
            return False, None

        hints = (
            "访问被阻断",
            "您的访问被阻断",
            "安全威胁",
            "请求ID",
            "Access Denied",
            "Request blocked",
            "Not Allowed",
        )
        if any(h in snippet for h in hints):
            return True, "WAF"

        # Some blocks are plain HTML without the above keywords; keep this conservative.
        if snippet[:1] == "<" and "xueqiu.com" in url and ("captcha" in snippet.lower() or "verify" in snippet.lower()):
            return True, "captcha"

        return False, None
