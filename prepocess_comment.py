import ahocorasick
import json
import os
import re

def remove_a_tags(text):
    """
    移除文本中所有的 <a>...</a> 标签及其内容。
    支持处理标签内的属性（如 href, class 等）以及跨行内容。
    """
    if not text:
        return ""
    
    # 正则解释：
    # <a\s*             : 匹配 <a 开头，后面可能有空格
    # [^>]*             : 匹配标签内的属性（如 href="..."），直到遇到 >
    # >                 : 匹配标签结束符
    # .*?               : 非贪婪匹配标签中间的内容（包括换行符）
    # </a>              : 匹配结束标签
    pattern = r'<a\s[^>]*>.*?</a>'
    
    # re.DOTALL (或 re.S) 让 . 可以匹配换行符
    cleaned_text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
    
    return cleaned_text


def extract_cashtags_tuples(text):
    """
    从文本中提取 $...$ 格式的股票信息。
    
    参数:
        text: 输入文本，例如 "$波司登(03998)$ 和 $特斯拉(HK:BK2484)$"
        
    返回:
        列表 of 元组: [(标准化代码, 原始名称), ...]
        示例: [('HK03998', '波司登'), ('HKBK2484', '特斯拉')]
    """
    results = []
    
    # 1. 正则提取 $...$ 中间的所有内容
    # 匹配 $ 开头，$ 结尾，中间任意非贪婪字符
    matches = re.findall(r'\$(.*?)\$', text)
    
    for content in matches:
        name_part = ""
        code_part = ""
        
        # 2. 尝试解析 "名称(代码)" 格式
        # 正则：(.*) 捕获括号前的名称，\((.*)\) 捕获括号内的代码
        bracket_match = re.match(r'^(.*?)\((.*)\)$', content.strip())
        
        if bracket_match:
            name_part = bracket_match.group(1).strip()
            raw_code = bracket_match.group(2).strip()
        else:
            # 如果没有括号，假设整个内容就是代码（如 $AAPL$），名称为空或等于代码
            # 这里为了统一，如果没括号，我们把整个内容当代码，名称留空或稍后处理
            # 但根据你的例子，通常都有括号。如果没有，我们暂时跳过或视情况而定。
            # 策略：如果没有括号，且内容是纯字母/数字，视为代码，名称设为 None 或原内容
            raw_code = content.strip()
            name_part = "" # 或者 name_part = raw_code
            
        if not raw_code:
            continue
            
        # 3. 清洗代码格式
        # 去掉冒号、空格，转大写 (处理 HK:BK2484 -> HKBK2484)
        clean_code = raw_code.replace(":", "").replace("-", "").replace(" ", "").upper()
        
        # 4. 智能补全前缀 (针对纯数字)
        candidates = [clean_code]
        
        if clean_code.isdigit():
            if len(clean_code) == 5:
                # 5位数字通常是港股 -> HK03998
                candidates.append("HK" + clean_code)
            elif len(clean_code) == 6:
                # 6位数字可能是 A 股 -> SHxxxxxx 或 SZxxxxxx
                candidates.append("SH" + clean_code)
                candidates.append("SZ" + clean_code)
                # 也有可能是港股老代码，但概率低，暂不加 HK
        
        # 5. 验证并生成结果
        found_code = None
        for code in candidates:
            found_code = code
            break
        
        if found_code:
            # 如果名称为空（即没有括号的情况），可以用 found_code 代替，或者保留空
            final_name = name_part if name_part else found_code 
            results.append((found_code, final_name))
            
    return results

# ==========================================
# 1. 动态加载海量映射字典
# ==========================================
def load_stock_aliases(json_path):
    """从本地 JSON 文件加载股票映射字典"""
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"找不到字典文件：{json_path}。请先运行字典生成脚本。")
        
    print(f"正在加载字典文件: {json_path}...")
    with open(json_path, 'r', encoding='utf-8') as f:
        aliases_dict = json.load(f)
    print(f"✅ 成功加载 {len(aliases_dict)} 个股票映射关系！")
    return aliases_dict

def build_automaton(alias_dict):
    """构建 AC 自动机，实现毫秒级多模式匹配"""
    print("正在构建 Aho-Corasick 匹配引擎...")
    A = ahocorasick.Automaton()
    for alias, ticker in alias_dict.items():
        A.add_word(alias, (ticker, alias))
    A.make_automaton()
    print("✅ 匹配引擎构建完成！")
    return A

# ==========================================
# 2. 核心处理逻辑：提取、切片、目标掩码
# ==========================================
def process_single_text(text, automaton, window_size=100):  
    matches = list(automaton.iter(text))
    if not matches:
        return []

    found_tickers = set()
    ticker_to_aliases = {}
    for end_idx, (ticker, alias) in matches:

        found_tickers.add(ticker)
        if ticker not in ticker_to_aliases:
            ticker_to_aliases[ticker] = set()
        ticker_to_aliases[ticker].add(alias)

    stocks_code = extract_cashtags_tuples(text)
    if stocks_code is not None:
        for (ticker, alias) in stocks_code:
            found_tickers.add(ticker)
            if ticker not in ticker_to_aliases:
                ticker_to_aliases[ticker] = set()
            ticker_to_aliases[ticker].add(alias)


    results = []
    
    for target_ticker in found_tickers:
        # # 1. 定位中心点
        # first_match_end = next(end for end, (t, a) in matches if t == target_ticker)
        
        # # 2. 截取上下文
        # start_idx = max(0, first_match_end - window_size)
        # end_idx = min(len(text), first_match_end + window_size)
        slice_text = text[:500]
        
        # 3. 目标实体掩码 (按长度降序替换，防止别名互相包含)
        all_aliases_in_slice = []
        for t, aliases in ticker_to_aliases.items():
            for a in aliases:
                all_aliases_in_slice.append((a, t))
        all_aliases_in_slice.sort(key=lambda x: len(x[0]), reverse=True)

        masked_text = slice_text
        for alias, t in all_aliases_in_slice:
            if t == target_ticker:
                masked_text = masked_text.replace(alias, "该股票")
            else:
                masked_text = masked_text.replace(alias, "其他股票")
        
        masked_text = masked_text.replace("$", "")

        results.append({
            "Mentioned_Stocks": target_ticker,
            "masked_text": masked_text
        })
        
    return results

def is_noisy_post(text):
    """
    通过启发式规则判定是否为教学贴、炫耀贴或无意义打招呼
    """
    # 1. 过滤炫耀贴 (过度关注个人账户的过去时态)
    bragging_words = ["我赚了", "回本了", "账户新高", "翻倍了", "今天收益", "吃大肉", "跑赢大盘"]
    if sum(1 for w in bragging_words if w in text) >= 2: # 命中两个词以上视为炫耀
        return True
        
    # 2. 过滤教学/科普贴 (过度使用技术术语，通常篇幅极长且无明确多空)
    tutorial_words = ["MACD", "KDJ", "布林带", "支撑位", "阻力位", "金叉", "死叉", "基本面分析", "科普"]
    if sum(1 for w in tutorial_words if w in text) >= 3:
        return True
        
    # 3. 过滤纯打招呼/无意义社交
    social_words = ["早上好", "大家早", "点赞", "转发", "感谢分享"]
    if any(w in text[:20] for w in social_words): # 出现在开头直接干掉
        return True
        
    # 4. 长度过滤
    # 超过800字的通常是长篇大论的深度研报或科普，FinBERT处理这么长的文本注意力会涣散，直接丢弃或单独处理。
    if len(text) > 800:
        return True

    return False

# ==========================================
# 3. 流式读取与导出（16GB 内存安全版）
# ==========================================
def run_pipeline(input_file, output_file, dict_file):
    aliases_dict = load_stock_aliases(dict_file)
    automaton = build_automaton(aliases_dict)
    
    processed_count = 0
    valid_slice_count = 0
    
    print("\n🚀 开始流式处理 500 万条雪球数据...")
    
    # 使用 yield 和逐行读写，防止 OOM
    with open(input_file, 'r', encoding='utf-8') as fin, \
         open(output_file, 'w', encoding='utf-8') as fout:
        
        for line in fin:
            processed_count += 1
            try:
                # 假设输入是 JSONL 格式
                row = json.loads(line.strip())
                raw_text = remove_a_tags(row.get("Content", ""))
            except:
                continue 

            # ======= 新增的降噪拦截网 ======= 后续使用训练好的模型优化
            if is_noisy_post(raw_text):
                continue # 是教学/炫耀/打招呼，直接跳过，根本不进入后续的股票切片和模型打分！
            # ==============================
                
            slices = process_single_text(raw_text, automaton, window_size=50)
            
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
                fout.write(json.dumps(output_row, ensure_ascii=False) + '\n')
                valid_slice_count += 1
                
            if processed_count % 100000 == 0:
                print(f"已扫描 {processed_count} 条，提取有效切片 {valid_slice_count} 个...")

    print("-" * 40)
    print(f"🎉 处理彻底完成！")
    print(f"原始数据总数: {processed_count} 条")
    print(f"生成待打分切片: {valid_slice_count} 个")
    print(f"切片已保存至: {output_file}")
    print("-" * 40)

# ==========================================
# 4. 执行入口
# ==========================================
if __name__ == "__main__":
    # 配置你的文件路径
    INPUT_DATA_FILE = "./data/xueqiu_comments_raw.jsonl"  # 你的500万条原始数据
    OUTPUT_DATA_FILE = "./data/xueqiu_slices_ready_for_model.jsonl" # 预处理后的输出结果
    DICT_FILE = "./data/ultimate_stock_aliases.json"      # 生成的字典
    
    # 如果为了测试，可以先自己造一个小的 input 文件测一下
    if not os.path.exists(INPUT_DATA_FILE):
        print(f"⚠️ 未找到原始数据文件 {INPUT_DATA_FILE}，创建测试文件...")
        with open(INPUT_DATA_FILE, 'w', encoding='utf-8') as f:
            f.write(json.dumps({"User_Id": "v123", "Publish_Time": "2023-10-01 10:00:00", "text": "今天$洋河股份$太拉垮了，清仓换了五粮液，顺便加仓了一点腾讯控股。"}) + '\n')
            f.write(json.dumps({"User_Id": "v456", "Publish_Time": "2023-10-01 10:05:00", "text": "早安雪球，今天不看盘，去喝杯咖啡。"}) + '\n')
            
    run_pipeline(INPUT_DATA_FILE, OUTPUT_DATA_FILE, DICT_FILE)