import os
import random
import time
from datetime import datetime


class SpiderToolsMixin:
    # ================= 工具方法 =================

    def _get_now_str(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_time(self, timestamp):
        try:
            # 兼容 Unix 时间戳
            if str(timestamp).isdigit():
                ts = float(timestamp) / 1000
                time_local = time.localtime(ts)
                return time.strftime("%Y-%m-%d %H:%M:%S", time_local)
            # 兼容参考代码中的 datetime 字符串格式
            return str(timestamp)
        except:
            return str(timestamp)

    def random_sleep(self, min_s=1.0, max_s=2.0):
        time.sleep(random.uniform(min_s, max_s))

    def safe_action(self):
        self._check_405()
        max_retries = 10
        count = 0
        while self._has_slider():
            count += 1
            if count > 1:
                print(f">>> [滑块] 第 {count} 次尝试...")
            self._solve_slider()
            time.sleep(2)
            if count >= max_retries:
                print(">>> [滑块] 尝试次数过多，刷新页面...")
                self.driver.latest_tab.refresh()
                time.sleep(3)
                count = 0

    def _has_slider(self):
        try:
            tab = self.driver.latest_tab
            return tab.ele('#aliyunCaptcha-sliding-slider', timeout=0.1) or tab.ele('text:访问验证', timeout=0.1)
        except:
            return False

    def _solve_slider(self):
        tab = self.driver.latest_tab
        time.sleep(1)
        try:
            btn = tab.ele('#aliyunCaptcha-sliding-slider', timeout=3)
            if btn:
                btn.drag(random.randint(400, 600), random.randint(5, 10))
        except:
            pass

    def _check_405(self):
        try:
            if "405" in self.driver.latest_tab.title:
                print("\n>>> [严重] 触发405，暂停15分钟...")
                time.sleep(900)
                self.driver.latest_tab.refresh()
        except:
            pass

    def _restart_browser(self):
        try:
            self.driver.quit()
        except:
            pass
        os.system("pkill -f 'Google Chrome'")
        time.sleep(2)
        self.driver = self._init_browser()

