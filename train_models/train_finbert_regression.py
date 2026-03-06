import pandas as pd
import torch
from datasets import Dataset
from transformers import (
    AutoTokenizer, 
    AutoModelForSequenceClassification, 
    TrainingArguments, 
    Trainer
)
from sklearn.model_selection import train_test_split

print("🚀 启动 FinBERT 情绪回归打分器训练流水线...")

# ==========================================
# 1. 数据清洗与转化
# ==========================================
df = pd.read_json("smoothed_training_dataset.jsonl", lines=True)

# 【极度关键】只提取 is_valid == 1 的有效数据来训练情绪！
valid_df = df[df['is_valid'] == 1].copy()

# 将需要的列重命名，HuggingFace 默认识别 'text' 和 'labels'
valid_df = valid_df[['Content', 'score']].rename(columns={'Content': 'text', 'score': 'labels'})

# 【极度关键】回归任务的标签必须是 Float 类型
valid_df['labels'] = valid_df['labels'].astype('float32')

train_df, test_df = train_test_split(valid_df, test_size=0.2, random_state=42)
train_dataset = Dataset.from_pandas(train_df)
test_dataset = Dataset.from_pandas(test_df)

print(f"✅ 回归数据准备完毕！训练集: {len(train_dataset)} 条, 测试集: {len(test_dataset)} 条")

# ==========================================
# 2. 加载金融模型与分词器
# ==========================================
model_name = "hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2"
tokenizer = AutoTokenizer.from_pretrained(model_name)

# 魔改头部：num_labels=1 会自动把模型末端变成线性回归层，输出单一数值
model = AutoModelForSequenceClassification.from_pretrained(
    model_name, 
    num_labels=1, 
    problem_type="regression", # <--- 核心修复：强行覆盖底层 Config！
    ignore_mismatched_sizes=True 
)

# ==========================================
# 3. 数据 Tokenize 预处理
# ==========================================
def tokenize_function(examples):
    return tokenizer(examples["text"], padding="max_length", truncation=True, max_length=128)

tokenized_train = train_dataset.map(tokenize_function, batched=True)
tokenized_test = test_dataset.map(tokenize_function, batched=True)

# ==========================================
# 4. 训练参数设定 (适配 M4 芯片)
# ==========================================
# HuggingFace 会自动检测到你的 Mac M4 并启用 MPS 加速
training_args = TrainingArguments(
    output_dir="./finbert_results",
    evaluation_strategy="epoch",
    save_strategy="epoch",
    learning_rate=2e-5,          # 微调的经典学习率
    per_device_train_batch_size=16, # 16G内存设置 16 非常安全
    per_device_eval_batch_size=16,
    num_train_epochs=4,          # 跑 4 轮
    weight_decay=0.01,
    load_best_model_at_end=True,
    logging_dir='./logs',
)

# 为了评估回归效果，计算 MSE (均方误差)
def compute_metrics(eval_pred):
    predictions, labels = eval_pred
    # 压平数组
    predictions = predictions[:, 0]
    mse = ((predictions - labels) ** 2).mean().item()
    return {"mse": mse}

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized_train,
    eval_dataset=tokenized_test,
    compute_metrics=compute_metrics,
)

# ==========================================
# 5. 启动训练！
# ==========================================
print("🔥 开始训练 FinBERT 回归模型 (M4 芯片 MPS 引擎已自动挂载)...")
trainer.train()

save_directory = "./model_finbert_regression"
model.save_pretrained(save_directory)
tokenizer.save_pretrained(save_directory)
print(f"🎉 模型已成功保存至: {save_directory}")