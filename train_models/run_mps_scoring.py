import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import json
import csv
import time

# ==========================================
# 1. 硬件加速与模型加载
# ==========================================
# 强制开启苹果 Metal 硬件加速
if not torch.backends.mps.is_available():
    raise RuntimeError("MPS 不可用，请检查 macOS 和 PyTorch 版本！")
device = torch.device("mps")
print("🔥 成功挂载 Apple MPS (Metal Performance Shaders) 硬件加速引擎！")

# 这里填入你在 HuggingFace 选用的中文金融情感模型
# 例如: "TechxGenus/FinBERT-chinese" 或 "hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2"
# 第一次运行会自动下载，之后会使用本地缓存
MODEL_NAME = "hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2" 
print(f"正在加载模型 {MODEL_NAME} 到芯片中...")

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
model.to(device)
model.eval() # 开启推理模式，关闭 Dropout，极其重要

# ==========================================
# 2. 情感得分映射逻辑
# ==========================================
def calculate_score(probs, labels_mapping):
    """
    将模型输出的概率转化为 0-10 的标量得分。
    注意：不同的开源模型对应的 label 索引不同，请根据模型说明卡片(Model Card)调整！
    假设当前模型的输出顺序为: 0=负面, 1=中性, 2=正面
    """
    prob_neg = probs[0].item()
    prob_neu = probs[1].item()
    prob_pos = probs[2].item()
    
    # 期望值算法：看多拉满给10分，中性给5分，看空给0分
    score = (prob_pos * 10) + (prob_neu * 5) + (prob_neg * 0)
    return round(score, 2)

# ==========================================
# 3. 核心批处理与写库引擎
# ==========================================
def process_batches_and_save(input_file, output_csv, batch_size=128):
    # 准备写入 CSV，只存因子所需的结构化面板数据
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "author_id", "stock_ticker", "sentiment_score"]) # 表头
        
    print(f"🚀 开始流式推理，Batch Size 设置为: {batch_size}")
    
    batch_data = []
    batch_texts = []
    processed_count = 0
    start_time = time.time()

    with open(input_file, 'r', encoding='utf-8') as fin:
        for line in fin:
            try:
                row = json.loads(line.strip())
                # 收集批次数据
                batch_data.append(row)
                batch_texts.append(row["text_for_model"])
            except:
                continue
                
            # 当累积到一个 Batch Size 时，集体扔进 GPU (MPS)
            if len(batch_texts) == batch_size:
                _run_inference_and_write(batch_texts, batch_data, writer, output_csv)
                processed_count += batch_size
                
                # 清空当前批次，释放内存
                batch_texts.clear()
                batch_data.clear()
                
                if processed_count % (batch_size * 10) == 0:
                    elapsed = time.time() - start_time
                    speed = processed_count / elapsed
                    print(f"✅ 已处理 {processed_count} 个切片 | 速度: {speed:.2f} 条/秒")

        # 处理文件末尾剩下的小于 batch_size 的尾巴数据
        if len(batch_texts) > 0:
            _run_inference_and_write(batch_texts, batch_data, writer, output_csv)
            processed_count += len(batch_texts)

    print("-" * 50)
    print(f"🎉 情感打分因子提取全线竣工！总耗时: {(time.time() - start_time)/60:.2f} 分钟。")
    print(f"📈 结构化因子数据已保存至: {output_csv}")

def _run_inference_and_write(texts, data_rows, writer_obj, output_csv):
    """单次 Batch 推理的内部函数"""
    # 1. Tokenize 并将张量送入 MPS
    inputs = tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    
    # 2. 无梯度前向传播（极省显存）
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        
    # 3. 将计算结果移回 CPU 进行后处理
    probs_cpu = probs.cpu()
    
    # 4. 追加写入 CSV（彻底抛弃明文）
    with open(output_csv, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for i, row in enumerate(data_rows):
            score = calculate_score(probs_cpu[i], labels_mapping=None)
            
            # 只写入量化因子需要的四个字段
            writer.writerow([
                row["timestamp"], 
                row["author_id"], 
                row["stock_id"], 
                score
            ])
            
    # 主动释放 MPS 显存引用
    del inputs, outputs, probs
    # 如果内存还是涨，可以取消下面这行的注释
    # torch.mps.empty_cache()

# ==========================================
# 4. 启动口
# ==========================================
if __name__ == "__main__":
    INPUT_JSONL = "xueqiu_slices_ready_for_model.jsonl" # 上一步预处理好的文件
    OUTPUT_CSV = "alternative_sentiment_factor.csv"     # 最终生成的因子表
    
    # M系列芯片带宽高，Batch Size 可以从 128 起步测试
    # 如果跑的过程中发现活动监视器里内存压力变黄，就改成 64
    process_batches_and_save(INPUT_JSONL, OUTPUT_CSV, batch_size=128)