import json
import re
import time

import config

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    print(">>> 警告: 未安装 ollama")
    HAS_OLLAMA = False


class SpiderAIMixin:
    # ================= AI 线程 =================

    def global_ai_worker(self):
        # === 【新增】启动时主动探测 Ollama 是否可用 ===
        if HAS_OLLAMA:
            try:
                models = ollama.list()
                model_names = [m['model'] for m in models.get('models', [])]
                if config.AI_MODEL_NAME not in model_names:
                    print(f"⚠️ 警告: 指定模型 '{config.AI_MODEL_NAME}' 未安装！")
                    print(f"   可用模型: {model_names[:5]}{'...' if len(model_names)>5 else ''}")
                else:
                    print(f"✅ Ollama 服务正常，使用模型: {config.AI_MODEL_NAME}")
            except Exception as e:
                print(f"❌ Ollama 服务不可用 (可能未启动): {e}")
                print("   请确保运行: ollama serve")
        else:
            print("❌ Ollama 未安装，AI 功能将跳过所有内容")

        print(">>> [后台AI] 引擎已启动，调试模式开启...")
        while True:
            raw_batch = self.db.get_unanalyzed_raw_data(limit=10)
            if not raw_batch:
                if self.is_main_job_finished:
                    break
                time.sleep(2)
                continue

            for row in raw_batch:
                sid, content = row["status_id"], row["description"]
                clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')

                if len(clean) < 10:
                    self.db.mark_raw_as_analyzed(sid, 1)
                    continue

                prompt = f"""任务：判断这条财经评论是否有含金量。
                评论内容："{clean}"
                规则：1. 如果包含具体股票分析、逻辑、数据、新闻解读等能有助于判断股票涨势的信息，则valuable字段为true。反之，如果全都在讨论和股票、行业无关内容，则valuable为false。
                2. 如果评论里面有股票,则在cat中输出股票的类别，比如:A股，美股，港股，日股，韩股，德股等。否则输出其他。
                必须返回JSON格式：{{"valuable": true/false, "cat": "股票类别"}}"""

                try:
                    res = ollama.chat(
                        model=config.AI_MODEL_NAME,
                        messages=[{'role': 'user', 'content': prompt}],
                        format='json', options={
                            "temperature": 0.1,      # 更确定性
                            "num_predict": 30,       # 严格限制输出长度
                            "top_k": 15,
                            "top_p": 0.85
                        }
                    )
                    js = json.loads(res['message']['content'])
                    valuable = js.get('valuable', False)
                    cat = js.get('cat', '其他')

                    final_cat = cat if valuable else f"[低价值]-{cat}"
                    self.db.execute_one_safe(
                        """
                        INSERT INTO Value_Comments (
                            Comment_Id, User_Id, Content, Publish_Time, Mentioned_Stocks,
                            Category, Forward, Comment_Count, Like_Count
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (Comment_Id) DO NOTHING
                        """,
                        (
                            sid,
                            row["user_id"],
                            row["description"],
                            row["created_at"],
                            row["stock_tags"],
                            final_cat,
                            row["forward"],
                            row["comment_count"],
                            row["like_count"],
                        ),
                    )

                    if valuable:
                        print(f"    [AI] 🟢 收录 | {cat} | {clean[:15]}...")
                        self.total_ai_saved += 1
                    else:
                        print(f"    [AI] ⚪ 丢弃 | {cat} | {clean[:15]}...", end='\r')
                    self.db.mark_raw_as_analyzed(sid, 1)
                except Exception as e:
                    print(f"error in AI: {e}")
                    self.db.mark_raw_as_analyzed(sid, 2)


class AIWorker:
    def __init__(self, db, is_main_job_finished_fn, on_saved=None):
        self._db = db
        self._is_main_job_finished_fn = is_main_job_finished_fn
        self._on_saved = on_saved

    def run(self):
        # === 【新增】启动时主动探测 Ollama 是否可用 ===
        if HAS_OLLAMA:
            try:
                models = ollama.list()
                model_names = [m['model'] for m in models.get('models', [])]
                if config.AI_MODEL_NAME not in model_names:
                    print(f"⚠️ 警告: 指定模型 '{config.AI_MODEL_NAME}' 未安装！")
                    print(f"   可用模型: {model_names[:5]}{'...' if len(model_names)>5 else ''}")
                else:
                    print(f"✅ Ollama 服务正常，使用模型: {config.AI_MODEL_NAME}")
            except Exception as e:
                print(f"❌ Ollama 服务不可用 (可能未启动): {e}")
                print("   请确保运行: ollama serve")
        else:
            print("❌ Ollama 未安装，AI 功能将跳过所有内容")

        print(">>> [后台AI] 引擎已启动，调试模式开启...")
        while True:
            raw_batch = self._db.get_unanalyzed_raw_data(limit=10)
            if not raw_batch:
                if self._is_main_job_finished_fn():
                    break
                time.sleep(2)
                continue

            for row in raw_batch:
                sid, content = row["status_id"], row["description"]
                clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')

                if len(clean) < 10:
                    self._db.mark_raw_as_analyzed(sid, 1)
                    continue

                prompt = f"""任务：判断这条财经评论是否有含金量。
                评论内容："{clean}"
                规则：1. 如果包含具体股票分析、逻辑、数据、新闻解读等能有助于判断股票涨势的信息，则valuable字段为true。反之，如果全都在讨论和股票、行业无关内容，则valuable为false。
                2. 如果评论里面有股票,则在cat中输出股票的类别，比如:A股，美股，港股，日股，韩股，德股等。否则输出其他。
                必须返回JSON格式：{{"valuable": true/false, "cat": "股票类别"}}"""

                try:
                    res = ollama.chat(
                        model=config.AI_MODEL_NAME,
                        messages=[{'role': 'user', 'content': prompt}],
                        format='json', options={
                            "temperature": 0.1,      # 更确定性
                            "num_predict": 30,       # 严格限制输出长度
                            "top_k": 15,
                            "top_p": 0.85
                        }
                    )
                    js = json.loads(res['message']['content'])
                    valuable = js.get('valuable', False)
                    cat = js.get('cat', '其他')

                    final_cat = cat if valuable else f"[低价值]-{cat}"
                    self._db.execute_one_safe(
                        """
                        INSERT INTO Value_Comments (
                            Comment_Id, User_Id, Content, Publish_Time, Mentioned_Stocks,
                            Category, Forward, Comment_Count, Like_Count
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (Comment_Id) DO NOTHING
                        """,
                        (
                            sid,
                            row["user_id"],
                            row["description"],
                            row["created_at"],
                            row["stock_tags"],
                            final_cat,
                            row["forward"],
                            row["comment_count"],
                            row["like_count"],
                        ),
                    )

                    if valuable:
                        print(f"    [AI] 🟢 收录 | {cat} | {clean[:15]}...")
                        if self._on_saved:
                            self._on_saved()
                    else:
                        print(f"    [AI] ⚪ 丢弃 | {cat} | {clean[:15]}...", end='\r')
                    self._db.mark_raw_as_analyzed(sid, 1)
                except Exception as e:
                    print(f"error in AI: {e}")
                    self._db.mark_raw_as_analyzed(sid, 2)


def run_ai_process(stop_event):
    from db_manager import DBManager
    db = DBManager()
    worker = AIWorker(db=db, is_main_job_finished_fn=lambda: stop_event.is_set())
    worker.run()
