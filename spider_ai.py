import json
import re
import csv
import time
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import traceback
import config

from prepocess_comment import load_stock_aliases, build_automaton, process_single_text, is_noisy_post, remove_a_tags
from train_models.run_full_pipeline_inference import _print_progress, process_batch

class AIWorker:
    def __init__(self, db, is_main_job_finished_fn, dict_file, on_saved=None, batch_size=1):
        self._db = db
        self._is_main_job_finished_fn = is_main_job_finished_fn
        self._on_saved = on_saved
        self.batch_size = batch_size

        aliases_dict = load_stock_aliases(dict_file)
        self.automaton = build_automaton(aliases_dict)

        try:
            print("🚀 启动双 Transformer 全量量化推理管线...")
            # ==========================================
            # 1. 硬件加速与模型挂载
            # ==========================================
            device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
            print(f"🔥 当前使用的硬件加速引擎: {device}")

            # ------------------------------------------
            # 加载 Model 1: 意图分类器 (直接读取 81% 准确率的 Epoch 3 快照)
            # ------------------------------------------
            INTENT_MODEL_PATH = "./model_intent_classifier_best"
            print(f"正在加载 意图分类器 (81% 巅峰版): {INTENT_MODEL_PATH} ...")

            # 核心修复：直接从开源原模型拉取字典，或者读取我们最后存入的 ./model_transformer_intention 也可以
            intent_tokenizer = AutoTokenizer.from_pretrained(INTENT_MODEL_PATH)
            intent_model = AutoModelForSequenceClassification.from_pretrained(INTENT_MODEL_PATH).to(device)
            intent_model.to(device)
            intent_model.eval()

            # ------------------------------------------
            # 加载 Model 2: FinBERT 情绪回归器 (0-10分)
            # ------------------------------------------
            SCORE_MODEL_PATH = "./model_finbert_regression"
            print(f"正在加载 情绪回归器: {SCORE_MODEL_PATH} ...")

            score_tokenizer = AutoTokenizer.from_pretrained(SCORE_MODEL_PATH)
            score_model = AutoModelForSequenceClassification.from_pretrained(SCORE_MODEL_PATH)
            score_model.to(device)
            score_model.eval() # 开启推理模式

            self.intent_model = intent_model
            self.intent_tokenizer = intent_tokenizer
            self.score_model = score_model
            self.score_tokenizer = score_tokenizer
            print("成功加载分类模型和评分模型")

        except Exception as e:
            print(f"模型启动失败: {e}")
            traceback.print_stack()

        

    def run(self):
        start_time = time.time()
        valid_slice_count = 0
        while True:
            raw_batch = self._db.get_unanalyzed_raw_data(limit=self.batch_size)
            if not raw_batch:
               if self._is_main_job_finished_fn():
                    break
               time.sleep(20)
               continue
            
            for row in raw_batch:
                # 1. 预处理，排除明显没有用的评论
                sid, content = row["status_id"], row["description"]
                clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')
                if len(clean) < 10:
                    self._db.mark_raw_as_analyzed(sid,1)
                    continue
                
                if is_noisy_post(content):
                    continue # 是教学/炫耀/打招呼，直接跳过，根本不进入后续的股票切片和模型打分！

                slices = process_single_text(content, self.automaton, window_size=50)

                output_row_list = []
                for s in slices:
                    output_row = {
                        "Comment_Id": row.get("Comment_Id"),
                        "User_Id": row.get("User_Id"),
                        "Publish_Time": row.get("Publish_Time"),
                        "Mentioned_Stocks": s["Mentioned_Stocks"],
                        "Content": s["masked_text"],
                        "Forward": row.get("Forward",0),
                        "Comment_Count": row.get("Comment_Count",0),
                        "Like": row.get("Like",0),
                        
                    }
                    output_row_list.append(output_row)
                    valid_slice_count += 1
            
                # 2. 分类，进一步筛掉没用的评论
                valid_count, row_list = process_batch(output_row_list)
                
                processed_total += len(output_row_list)
                valid_total += valid_count
                output_row_list.clear() # 及时释放内存
                
                _print_progress(processed_total, valid_total, start_time)
                  


    


               
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
