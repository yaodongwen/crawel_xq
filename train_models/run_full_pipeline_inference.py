import json
import csv
import time
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

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

# ==========================================
# 2. 核心批处理与流水线引擎
# ==========================================
def process_pipeline(input_file, output_file, batch_size=128):
    print(f"📦 设置 Batch Size: {batch_size}，开始流式扫描 500 万大关...")
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Comment_Id", "User_Id", "Publish_Time", "Mentioned_Stocks", "Forward", "Comment_Count", "Like", "Sentiment_Score"])
        
    start_time = time.time()
    batch_data = []
    
    processed_total = 0
    valid_total = 0

    with open(input_file, 'r', encoding='utf-8') as fin, \
         open(output_file, 'a', newline='', encoding='utf-8') as fout:
        
        writer = csv.writer(fout)
        
        for line in fin:
            try:
                row = json.loads(line.strip())
                text = row.get("Content", row.get("masked_text", ""))
                if not text:
                    continue
                batch_data.append((row, text))
            except json.JSONDecodeError:
                continue

            # 凑满一个 Batch，发射！
            if len(batch_data) == batch_size:
                valid_count = _process_batch(batch_data, writer)
                
                processed_total += len(batch_data)
                valid_total += valid_count
                batch_data.clear() # 及时释放内存
                
                _print_progress(processed_total, valid_total, start_time)

        # 处理文件末尾的小尾巴
        if len(batch_data) > 0:
            valid_count = _process_batch(batch_data, writer)
            processed_total += len(batch_data)
            valid_total += valid_count

    print("-" * 50)
    print(f"🎉 全量推理彻底竣工！")
    print(f"⏱️ 总耗时: {(time.time() - start_time) / 60:.2f} 分钟")
    print(f"📊 扫描切片总数: {processed_total} 条")
    print(f"🎯 提取有效情绪因子: {valid_total} 条 (剔除噪音率: {1 - valid_total/max(1, processed_total):.1%})")
    print(f"💾 最终量化面板数据已保存至: {output_file}")

def process_batch(batch_data):
    """单次 Batch 的双剑合璧处理逻辑"""
    texts = [item[1] for item in batch_data]
    rows = [item[0] for item in batch_data]
    
    # ==========================================
    # 剑法 1：Transformer 意图秒筛
    # ==========================================
    intent_inputs = intent_tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    intent_inputs = {k: v.to(device) for k, v in intent_inputs.items()}
    
    with torch.no_grad():
        intent_logits = intent_model(**intent_inputs).logits
        # 沿着最后一个维度取最大值的索引 (0 或 1)
        intent_preds = torch.argmax(intent_logits, dim=-1).cpu().tolist()
        
    # 主动清理意图模型的显存，为下一个模型腾空间
    del intent_inputs, intent_logits
    
    # 提取标签为 1 (有效情感) 的索引
    valid_indices = [i for i, pred in enumerate(intent_preds) if pred == 1]
    
    # 触发短路保护：如果这个 Batch 全是垃圾废话，直接跳过打分环节！
    if not valid_indices:
        return 0
        
    valid_texts = [texts[i] for i in valid_indices]
    valid_rows = [rows[i] for i in valid_indices]

    # ==========================================
    # 剑法 2：FinBERT 精准打分
    # ==========================================
    score_inputs = score_tokenizer(valid_texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    score_inputs = {k: v.to(device) for k, v in score_inputs.items()}
    
    with torch.no_grad():
        score_outputs = score_model(**score_inputs)
        # 获取回归分数值 (squeeze 降维)
        scores = score_outputs.logits.squeeze(-1).cpu().tolist()
        
    # 单条数据保护：如果 tolist() 返回的是单个标量，强制转为列表
    if not isinstance(scores, list):
        scores = [scores]
        
    # 主动清理打分模型的显存
    del score_inputs, score_outputs

    # ==========================================
    # 落库：数据安全截断并写入 CSV
    # ==========================================

    row_list = []
    for row, score in zip(valid_rows, scores):
        # 强制把回归可能越界的异常分数拍回 0 到 10 之间
        clamped_score = max(0.0, min(10.0, float(score)))
        
        # 只提取所需字段，原文本销毁，规避数据合规风险
        row_list.append({
            row.get("Comment_Id", ""), 
            row.get("User_Id", ""), 
            row.get("Publish_Time", ""), 
            row.get("Mentioned_Stocks", ""), 
            row.get("Forward", ""), 
            row.get("Comment_Count", ""), 
            row.get("Like", ""), 
            round(clamped_score, 2)
        })
    
    return len(valid_rows),row_list


def _process_batch(batch_data, writer):
    """单次 Batch 的双剑合璧处理逻辑"""
    texts = [item[1] for item in batch_data]
    rows = [item[0] for item in batch_data]
    
    # ==========================================
    # 剑法 1：Transformer 意图秒筛
    # ==========================================
    intent_inputs = intent_tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    intent_inputs = {k: v.to(device) for k, v in intent_inputs.items()}
    
    with torch.no_grad():
        intent_logits = intent_model(**intent_inputs).logits
        # 沿着最后一个维度取最大值的索引 (0 或 1)
        intent_preds = torch.argmax(intent_logits, dim=-1).cpu().tolist()
        
    # 主动清理意图模型的显存，为下一个模型腾空间
    del intent_inputs, intent_logits
    
    # 提取标签为 1 (有效情感) 的索引
    valid_indices = [i for i, pred in enumerate(intent_preds) if pred == 1]
    
    # 触发短路保护：如果这个 Batch 全是垃圾废话，直接跳过打分环节！
    if not valid_indices:
        return 0
        
    valid_texts = [texts[i] for i in valid_indices]
    valid_rows = [rows[i] for i in valid_indices]

    # ==========================================
    # 剑法 2：FinBERT 精准打分
    # ==========================================
    score_inputs = score_tokenizer(valid_texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    score_inputs = {k: v.to(device) for k, v in score_inputs.items()}
    
    with torch.no_grad():
        score_outputs = score_model(**score_inputs)
        # 获取回归分数值 (squeeze 降维)
        scores = score_outputs.logits.squeeze(-1).cpu().tolist()
        
    # 单条数据保护：如果 tolist() 返回的是单个标量，强制转为列表
    if not isinstance(scores, list):
        scores = [scores]
        
    # 主动清理打分模型的显存
    del score_inputs, score_outputs

    # ==========================================
    # 落库：数据安全截断并写入 CSV
    # ==========================================
    for row, score in zip(valid_rows, scores):
        # 强制把回归可能越界的异常分数拍回 0 到 10 之间
        clamped_score = max(0.0, min(10.0, float(score)))
        
        # 只提取所需字段，原文本销毁，规避数据合规风险
        writer.writerow([
            row.get("Comment_Id", ""), 
            row.get("User_Id", ""), 
            row.get("Publish_Time", ""), 
            row.get("Mentioned_Stocks", ""), 
            row.get("Forward", ""), 
            row.get("Comment_Count", ""), 
            row.get("Like", ""), 
            round(clamped_score, 2)
        ])
    
    return len(valid_rows)

def _print_progress(processed, valid, start_time):
    """每处理 10000 条打印一次监控日志"""
    if processed % 100 == 0:
        elapsed = time.time() - start_time
        speed = processed / elapsed
        noise_rate = 1 - (valid / processed)
        print(f"🔄 已扫描: {processed} 条 | 速度: {speed:.0f} 条/秒 | 当前过滤率: {noise_rate:.1%} | 已运行: {elapsed/60:.1f} 分钟")

# ==========================================
# 3. 运行入口
# ==========================================
if __name__ == "__main__":
    # 你的预处理后 JSONL 切片文件路径
    INPUT_FILE = "../xueqiu_slices_ready_for_model.jsonl" 
    
    # 最终的因子大表输出路径
    OUTPUT_FILE = "alpha_factor_sentiment_v1.csv"
    
    # Mac M4 16GB 的舒适区 Batch Size 为 128。如果风扇狂转或内存发黄，可改为 64。
    process_pipeline(INPUT_FILE, OUTPUT_FILE, batch_size=32)