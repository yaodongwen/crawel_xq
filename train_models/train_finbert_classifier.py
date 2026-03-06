import pandas as pd
import torch
import numpy as np
from datasets import Dataset
from transformers import (
    AutoTokenizer, 
    AutoModelForSequenceClassification, 
    TrainingArguments, 
    Trainer
)
from sklearn.model_selection import train_test_split
import evaluate

print("🚀 启动 Transformer 意图分类器标准微调...")

# ==========================================
# 1. 准备全量分类数据
# ==========================================
df = pd.read_json("deepseek_labeled_dataset.jsonl", lines=True)
df = df[df['is_valid'].isin([0, 1])]

# HuggingFace 默认识别 text 和 labels 列
df = df[['Content', 'is_valid']].rename(columns={'Content': 'text', 'is_valid': 'labels'})

train_df, test_df = train_test_split(df, test_size=0.2, random_state=42)
train_dataset = Dataset.from_pandas(train_df)
test_dataset = Dataset.from_pandas(test_df)

print(f"✅ 数据切分完毕！训练集: {len(train_dataset)} 条, 测试集: {len(test_dataset)} 条")

# ==========================================
# 2. 加载模型 (重用你下载过的金融 BERT)
# ==========================================
model_name = "hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2"
tokenizer = AutoTokenizer.from_pretrained(model_name)

# 核心：这次是标准的 2 分类任务！
model = AutoModelForSequenceClassification.from_pretrained(
    model_name, 
    num_labels=2, 
    problem_type="single_label_classification", 
    ignore_mismatched_sizes=True
)

def tokenize_function(examples):
    return tokenizer(examples["text"], padding="max_length", truncation=True, max_length=128)

tokenized_train = train_dataset.map(tokenize_function, batched=True)
tokenized_test = test_dataset.map(tokenize_function, batched=True)

# ==========================================
# 3. 配置评估指标 (准确率、F1等)
# ==========================================
# 如果 evaluate 报错，可以 pip install evaluate scikit-learn
metric = evaluate.load("accuracy")

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    return metric.compute(predictions=predictions, references=labels)

# ==========================================
# 4. 启动 M4 极速微调
# ==========================================
training_args = TrainingArguments(
    output_dir="./classifier_results",
    evaluation_strategy="epoch",  # 每个 epoch 评估一次
    save_strategy="epoch",
    learning_rate=2e-5,
    per_device_train_batch_size=32, # 16G M4 跑 32 毫无压力
    per_device_eval_batch_size=32,
    num_train_epochs=3,             # 跑 3 轮足够收敛
    weight_decay=0.01,
    load_best_model_at_end=True,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized_train,
    eval_dataset=tokenized_test,
    compute_metrics=compute_metrics,
)

print("🔥 开始训练分类网络 (M4 MPS 已挂载，预计耗时几分钟)...")
trainer.train()

# 验证最终准确率
eval_results = trainer.evaluate()
print("-" * 40)
print(f"📊 最终测试集验证结果: {eval_results}")
print("-" * 40)

# 保存最终大杀器
save_directory = "./model_transformer_intention"
model.save_pretrained(save_directory)
tokenizer.save_pretrained(save_directory)
print(f"🎉 意图分类器已成功保存至: {save_directory}")