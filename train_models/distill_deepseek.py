import asyncio
import json
import time
from openai import AsyncOpenAI

# ==========================================
# 1. 配置 DeepSeek API
# ==========================================
API_KEY = "sk-82a0661a777f40178e8a0e1b2e62877b"  # 替换为你的真实 Key
BASE_URL = "https://api.deepseek.com"

# 初始化异步客户端
client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

# 控制并发量（根据你的 API 级别调整，DeepSeek 一般支持 20-50 并发）
SEMAPHORE = asyncio.Semaphore(32) 

# ==========================================
# 2. 强约束 Prompt 设计 (灵魂所在)
# ==========================================
SYSTEM_PROMPT = """你是一个顶级的量化金融分析师。你的任务是分析股民在论坛上的发言切片，提取情绪信号。
文本中目标股票已被替换为“该股票”，其他股票被替换为“其他股票”。

你需要完成两项任务：
1. 【意图识别】(is_valid): 
   - 如果文本包含对“该股票”的主观评价、看多看空情绪、明确操作建议，输出 1。
   - 如果文本是纯客观的新闻转发、大段技术指标教学（MACD/KDJ等）、单纯炫耀历史收益（如“今天吃大肉”、“赚了多少钱”而无对未来预期）、无意义社交打招呼等，输出 0。
   
2. 【情绪打分】(score):
   - 仅当 is_valid 为 1 时打分，范围为 0 到 10 的整数。
   - 0: 极度看空 / 强烈建议卖出 / 破口大骂 / 认为极其拉垮
   - 5: 中性 / 观望 / 持有不动
   - 10: 极度看多 / 强烈建议买入 / 极度看好
   - 注意：识别A股的反讽语境（如“感谢该股票每天给我稳稳的跌停”应打 0-2 分）。
   - 如果 is_valid 为 0，score 直接输出 -1。

严格按照以下 JSON 格式输出，不要包含任何其他说明文字或 Markdown 标记：
{"is_valid": 1, "score": 8}
"""

# ==========================================
# 3. 异步调用核心函数
# ==========================================
async def fetch_label(session_id, text_slice, retries=3):
    """带重试机制的单条文本异步标注"""
    async with SEMAPHORE:
        for attempt in range(retries):
            try:
                response = await client.chat.completions.create(
                    model="deepseek-chat", # 或者 deepseek-coder
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"请分析以下切片：\n{text_slice}"}
                    ],
                    temperature=0.1, # 温度调低，保证输出的稳定性
                    response_format={"type": "json_object"} # 强制 JSON 输出
                )
                
                result_str = response.choices[0].message.content
                result_json = json.loads(result_str)
                
                # 校验返回格式是否符合预期
                if "is_valid" in result_json and "score" in result_json:
                    return result_json
                else:
                    raise ValueError("JSON 字段缺失")
                    
            except Exception as e:
                if attempt == retries - 1:
                    print(f"[任务 {session_id}] 最终失败: {e}")
                    return {"is_valid": 0, "score": -1, "error": str(e)}
                await asyncio.sleep(1) # 失败退避

# ==========================================
# 4. 批量执行与文件读写
# ==========================================
async def process_dataset(input_file, output_file):
    print("🚀 开始启动 DeepSeek 知识蒸馏流水线...")
    start_time = time.time()
    
    tasks = []
    raw_data = []
    
    # 1. 读取你需要标注的 5000 条切片数据
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                row = json.loads(line.strip())
                raw_data.append(row)
            except:
                continue

    print(f"✅ 成功加载 {len(raw_data)} 条切片数据。正在构建并发任务...")

    # 2. 构建异步任务列表
    for i, row in enumerate(raw_data):
        text = row.get("Content", "")
        # 创建协程任务
        tasks.append(fetch_label(i, text))

    # 3. 并发执行所有任务 (进度条功能可用 tqdm(asyncio.as_completed) 进阶实现)
    results = await asyncio.gather(*tasks)

    # 4. 结果合并与写出
    valid_count = 0
    with open(output_file, 'w', encoding='utf-8') as fout:
        for i, row in enumerate(raw_data):
            row["is_valid"] = results[i].get("is_valid", 0)
            row["score"] = results[i].get("score", -1)
            
            if row["is_valid"] == 1:
                valid_count += 1
                
            fout.write(json.dumps(row, ensure_ascii=False) + '\n')

    elapsed = time.time() - start_time
    print("-" * 40)
    print(f"🎉 标注完成！总耗时: {elapsed:.2f} 秒。")
    print(f"📊 原始数据: {len(raw_data)} 条")
    print(f"🎯 提取出有效主观评价 (is_valid=1): {valid_count} 条")
    print(f"💾 高质量训练集已保存至: {output_file}")
    print("-" * 40)

# ==========================================
# 5. 启动点
# ==========================================
if __name__ == "__main__":
    # 假设你已经从 500 万条数据中随机采样了 5000 条，并跑过了预处理的切片脚本
    INPUT_FILE = "xueqiu_slices_ready_for_model.jsonl" 
    OUTPUT_FILE = "deepseek_labeled_dataset.jsonl"
    
    # 为了防止你没有测试文件，这里造两条假数据
    import os
    if not os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(json.dumps({"stock_id": "SH600519", "Content": "今天该股票太拉垮了，全仓换了其他股票，感觉被主力套牢了。"}) + '\n')
            f.write(json.dumps({"stock_id": "SZ000858", "Content": "给大家科普一下该股票的酿造工艺和历史，这是一家好企业。"}) + '\n')

    # 运行异步事件循环
    asyncio.run(process_dataset(INPUT_FILE, OUTPUT_FILE))