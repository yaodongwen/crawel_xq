import re
import time
import traceback

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import config
from prepocess_comment import load_stock_aliases, build_automaton, process_single_text, is_noisy_post


class AIWorker:
    """Run comment intent classification + sentiment regression, then store only a score.

    Minimal contract:
    - Read pending rows from Raw_Statuses (Is_Analyzed=0)
    - If valuable/relevant: write one row into Value_Comments with Sentiment_Score (no full text)
    - Mark Raw_Statuses as analyzed to unblock the pipeline
    """

    def __init__(
        self,
        db,
        is_main_job_finished_fn,
        dict_file=None,
        on_saved=None,
        batch_size=None,
        stop_when_empty=False,
        max_statuses=None,
        max_seconds=None,
        progress_every=None,
    ):
        self._db = db
        self._is_main_job_finished_fn = is_main_job_finished_fn
        self._on_saved = on_saved

        self._dict_file = dict_file or getattr(config, "STOCK_ALIASES_JSON", None)
        self._batch_size = int(batch_size or getattr(config, "AI_BATCH_SIZE", 8))
        self._stop_when_empty = bool(stop_when_empty)
        self._max_statuses = int(max_statuses) if max_statuses is not None else None
        self._max_seconds = int(max_seconds) if max_seconds is not None else None
        self._progress_every = int(progress_every) if progress_every is not None else None

        self._device = None
        self._automaton = None
        self._intent_tokenizer = None
        self._intent_model = None
        self._score_tokenizer = None
        self._score_model = None

    @staticmethod
    def _pick_device():
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _ensure_ready(self):
        if self._device is None:
            self._device = self._pick_device()
            print(f"🔥 当前使用的硬件加速引擎: {self._device}")

        if self._automaton is None:
            if not self._dict_file:
                raise RuntimeError("Missing STOCK_ALIASES_JSON (set it in config.py)")
            aliases_dict = load_stock_aliases(self._dict_file)
            self._automaton = build_automaton(aliases_dict)

        if self._intent_model is None or self._score_model is None:
            intent_path = getattr(config, "INTENT_MODEL_PATH", None) or "./model_intent_classifier_best"
            score_path = getattr(config, "SENTIMENT_MODEL_PATH", None) or "./model_finbert_regression"

            print(f"正在加载 意图分类器: {intent_path} ...")
            self._intent_tokenizer = AutoTokenizer.from_pretrained(intent_path)
            self._intent_model = AutoModelForSequenceClassification.from_pretrained(intent_path).to(self._device)
            self._intent_model.eval()

            print(f"正在加载 情绪回归器: {score_path} ...")
            self._score_tokenizer = AutoTokenizer.from_pretrained(score_path)
            self._score_model = AutoModelForSequenceClassification.from_pretrained(score_path).to(self._device)
            self._score_model.eval()

            print(">>> [后台AI] 引擎已启动（分类 -> 评分 -> 落库Sentiment_Score）...")

    @staticmethod
    def _strip_html(text: str) -> str:
        if not text:
            return ""
        # Keep anchor inner text; only strip tags.
        return re.sub(r"<[^>]+>", "", text).strip().replace("\n", " ")

    @staticmethod
    def _clamp_score(val):
        try:
            x = float(val)
        except Exception:
            return 0.0
        return max(0.0, min(10.0, x))

    def run(self):
        self._ensure_ready()

        started = time.time()
        processed_statuses = 0

        def _should_stop():
            if self._max_seconds is not None and self._max_seconds > 0:
                if time.time() - started >= float(self._max_seconds):
                    return True
            if self._max_statuses is not None and self._max_statuses > 0:
                if processed_statuses >= int(self._max_statuses):
                    return True
            return False

        while True:
            if _should_stop():
                break

            raw_batch = self._db.get_unanalyzed_raw_data(limit=self._batch_size)
            if not raw_batch:
                if self._stop_when_empty or self._is_main_job_finished_fn():
                    break
                time.sleep(2)
                continue

            # Expand each raw status into slices (masked texts). We later aggregate back to 1 score per status_id.
            candidates = []
            skipped_sids = set()
            ok_sids = set()
            failed_sids = set()

            for row in raw_batch:
                sid = row.get("status_id")
                if sid is None:
                    continue

                content = row.get("description") or ""
                clean = self._strip_html(content)

                if len(clean) < 10:
                    skipped_sids.add(sid)
                    continue

                # Heuristic noise filter.
                if is_noisy_post(clean):
                    skipped_sids.add(sid)
                    continue

                slices = process_single_text(clean, self._automaton, window_size=50)
                if not slices:
                    skipped_sids.add(sid)
                    continue

                for s in slices:
                    masked_text = s.get("masked_text") or ""
                    if not masked_text:
                        continue
                    candidates.append(
                        {
                            "sid": sid,
                            "user_id": row.get("user_id"),
                            "publish_time": row.get("created_at"),
                            "mentioned_stock": s.get("Mentioned_Stocks") or "",
                            "forward": row.get("forward", 0) or 0,
                            "comment_count": row.get("comment_count", 0) or 0,
                            "like_count": row.get("like_count", 0) or 0,
                            "text": masked_text,
                        }
                    )

            # Mark obviously-skipped rows as analyzed so they don't clog the queue.
            for sid in skipped_sids:
                try:
                    self._db.mark_raw_as_analyzed(sid, 1)
                    processed_statuses += 1
                    ok_sids.add(sid)
                except Exception:
                    pass

            if not candidates:
                # Optional: delete the skipped raw rows after processing.
                try:
                    if ok_sids and bool(getattr(config, "DELETE_ANALYZED_RAW_STATUSES", False)):
                        self._db.delete_raw_statuses_by_ids(list(ok_sids))
                except Exception:
                    pass
                continue

            try:
                # Unique sids in this batch (for progress / max_statuses caps).
                batch_sids = {c["sid"] for c in candidates if c.get("sid") is not None}

                texts = [c["text"] for c in candidates]

                # 1) Intent classification (0/1). Keep label==1.
                intent_inputs = self._intent_tokenizer(
                    texts, padding=True, truncation=True, max_length=128, return_tensors="pt"
                )
                intent_inputs = {k: v.to(self._device) for k, v in intent_inputs.items()}
                with torch.no_grad():
                    intent_logits = self._intent_model(**intent_inputs).logits
                    intent_preds = torch.argmax(intent_logits, dim=-1).cpu().tolist()

                valid_indices = [i for i, p in enumerate(intent_preds) if p == 1]
                if not valid_indices:
                    for sid in {c["sid"] for c in candidates}:
                        self._db.mark_raw_as_analyzed(sid, 1)
                    continue

                # 2) Sentiment regression (0-10).
                valid_texts = [texts[i] for i in valid_indices]
                score_inputs = self._score_tokenizer(
                    valid_texts, padding=True, truncation=True, max_length=128, return_tensors="pt"
                )
                score_inputs = {k: v.to(self._device) for k, v in score_inputs.items()}
                with torch.no_grad():
                    score_logits = self._score_model(**score_inputs).logits.squeeze(-1).cpu().tolist()
                if not isinstance(score_logits, list):
                    score_logits = [score_logits]

                # Upsert mentioned stocks and map symbol -> Stock_Id.
                mentioned_symbols = []
                for idx in valid_indices:
                    sym = str(candidates[idx].get("mentioned_stock") or "").strip()
                    if sym:
                        mentioned_symbols.append(sym)
                if mentioned_symbols:
                    self._db.upsert_stocks([(s, None, None) for s in mentioned_symbols])
                stock_id_map = self._db.get_stock_id_map(mentioned_symbols)

                insert_rows = []
                for idx, score in zip(valid_indices, score_logits):
                    c = candidates[idx]
                    sym = str(c.get("mentioned_stock") or "").strip()
                    stock_id = stock_id_map.get(sym)
                    if not stock_id:
                        continue
                    insert_rows.append(
                        (
                            c["sid"],
                            c.get("user_id"),
                            stock_id,
                            round(float(self._clamp_score(score)), 2),
                            c.get("publish_time"),
                            c.get("forward", 0),
                            c.get("comment_count", 0),
                            c.get("like_count", 0),
                        )
                    )

                if insert_rows:
                    self._db.execute_many_safe(
                        """
                        INSERT INTO Value_Comments (
                            Comment_Id, User_Id, Stock_Id, Sentiment_Score, Publish_Time,
                            Forward, Comment_Count, Like_Count
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (Comment_Id, Stock_Id) DO NOTHING
                        """,
                        insert_rows,
                    )
                    if self._on_saved:
                        # Only count the "valuable" rows we actually stored.
                        for _ in insert_rows:
                            try:
                                self._on_saved()
                            except Exception:
                                break

                # Mark all processed statuses as analyzed.
                for sid in batch_sids:
                    self._db.mark_raw_as_analyzed(sid, 1)
                    processed_statuses += 1
                    ok_sids.add(sid)

                # Optional: delete processed raw rows to keep Raw_Statuses small.
                try:
                    if ok_sids and bool(getattr(config, "DELETE_ANALYZED_RAW_STATUSES", False)):
                        self._db.delete_raw_statuses_by_ids(list(ok_sids))
                except Exception:
                    pass

                if self._progress_every and processed_statuses % int(self._progress_every) == 0:
                    try:
                        left = int(self._db.get_unanalyzed_count() or 0)
                    except Exception:
                        left = -1
                    print(f">>> [AI] processed={processed_statuses} left={left}")

            except Exception as e:
                print(f"error in AI: {e}")
                traceback.print_exc()
                for sid in {c["sid"] for c in candidates}:
                    try:
                        self._db.mark_raw_as_analyzed(sid, 2)
                        processed_statuses += 1
                        failed_sids.add(sid)
                    except Exception:
                        pass

                try:
                    if failed_sids and bool(getattr(config, "DELETE_FAILED_RAW_STATUSES", False)):
                        self._db.delete_raw_statuses_by_ids(list(failed_sids))
                except Exception:
                    pass


def run_ai_process(stop_event):
    from db_manager import DBManager

    db = DBManager()
    worker = AIWorker(db=db, is_main_job_finished_fn=lambda: stop_event.is_set())
    worker.run()
