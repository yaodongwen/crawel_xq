import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# ==========================================
# 1. 解决 macOS matplotlib 中文显示问题
# ==========================================
# Mac 系统自带的高质量中文字体
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Arial Unicode MS', 'Heiti TC']
plt.rcParams['axes.unicode_minus'] = False  # 正常显示负号

def plot_score_distribution(jsonl_file):
    print(f"正在加载数据集: {jsonl_file}")
    
    # 2. 读取 JSONL 数据
    df = pd.read_json(jsonl_file, lines=True)
    
    # 3. 过滤出有效数据
    # 意图识别为有效 (is_valid == 1)，且分数在 0-10 之间
    valid_df = df[(df['is_valid'] == 1) & (df['score'] >= 0)]
    
    total_samples = len(df)
    valid_samples = len(valid_df)
    print(f"总样本数: {total_samples}")
    print(f"有效情绪样本数: {valid_samples} (占比 {valid_samples/total_samples*100:.1f}%)")

    # 4. 绘制直方图
    plt.figure(figsize=(10, 6), dpi=120)
    
    # 使用 seaborn 绘制带核密度估计(KDE)的直方图
    # bins=11 刚好对应 0,1,2...10 这 11 个离散整数
    sns.histplot(
        valid_df['score'], 
        bins=11, 
        binrange=(-0.5, 10.5), # 让柱子居中对齐刻度
        kde=False,             # 如果想看平滑曲线，可以把 False 改为 True
        color='#4C72B0', 
        edgecolor='white'
    )
    
    # 5. 图表美化
    plt.title('雪球大V情绪得分分布图 (0-10分)', fontsize=16, fontweight='bold', pad=15)
    plt.xlabel('情绪得分 (0=极度看空, 5=中性, 10=极度看多)', fontsize=12)
    plt.ylabel('切片数量 (频数)', fontsize=12)
    
    # 设置 X 轴刻度为 0 到 10 的整数
    plt.xticks(range(0, 11))
    
    # 添加 Y 轴水平网格线，方便读数
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    
    # 在每个柱子上方标出具体数字
    counts = valid_df['score'].value_counts().sort_index()
    for score_val, count in counts.items():
        plt.text(score_val, count + (max(counts)*0.01), str(count), 
                 ha='center', va='bottom', fontsize=10)

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # 填入你上一阶段 DeepSeek 跑出来的 JSONL 文件路径
    DATASET_FILE = "smoothed_training_dataset.jsonl" 
    plot_score_distribution(DATASET_FILE)