import akshare as ak
import json
import re
import time
from collections import defaultdict

EXCLUDE_LIST = [
    "今天", "明天", "昨日", "日经"
]

# 引入上面定义的黑话字典
# CORE_SLANG_DICT = { ... } (把上面的字典粘贴在这里)
CORE_SLANG_DICT = {
    # ======= A股黑话 =======
    "茅子": "SH600519", "股王": "SH600519", "国酒": "SH600519",
    "宁王": "SZ300750", "电池茅": "SZ300750",
    "宇宙行": "SH601398", "工行": "SH601398",
    "招行": "SH600036", "零售之王": "SH600036",
    "平安": "SH601318", "中国平安": "SH601318",
    "东财": "SZ300059", "券茅": "SZ300059", "牛市旗手": "SZ300059",
    "比王": "SZ002594", "迪王": "SZ002594",
    "药茅": "SH600276", "恒瑞": "SH600276",
    "猪茅": "SZ002714", "猪王": "SZ002714",
    "酱茅": "SH603288", "海天": "SH603288",
    "眼茅": "SZ300015", "爱尔": "SZ300015",
    "牙茅": "SH600763", "通策": "SH600763",
    "免税茅": "SH601888", "中免": "SH601888",
    "光伏茅": "SH601012", "隆基": "SH601012",
    "安防茅": "SZ002415", "海康": "SZ002415",
    "挖掘机茅": "SH600031", "三一": "SH600031",
    "泥茅": "SH600585", "海螺": "SH600585",
    "化茅": "SH600309", "万华": "SH600309",
    "矿茅": "SH601899", "紫金": "SH601899",
    "煤王": "SH601088", "神华": "SH601088",
    "空调茅": "SZ000651", "格力": "SZ000651", "董小姐": "SZ000651",
    "美的": "SZ000333", "海尔": "SH600690",
    "浓香老大": "SZ000858", "五粮": "SZ000858",
    "老窖": "SZ000568", "汾酒": "SH600809",
    "中芯": "SH688981", "村长": "SH688981", # 有时大V戏称
    "富联": "SH601138", "工业富联": "SH601138",
    "京东方": "SZ000725", "面板茅": "SZ000725",
    "万科": "SZ000002", "宇宙房企": "SZ000002",
    
    # ======= 港股黑话 =======
    "企鹅": "HK00700", "鹅厂": "HK00700", "腾讯": "HK00700",
    "阿里": "HK09988", "猫厂": "HK09988", "福报厂": "HK09988",
    "美团": "HK03690", "团子": "HK03690", "开水团": "HK03690",
    "发哥": "HK00388", "港交所": "HK00388",
    "汇控": "HK00005", "汇丰": "HK00005",
    "小破站": "HK09626", "B站": "HK09626", "陈睿": "HK09626",
    "快手": "HK01024", "老铁厂": "HK01024",
    "粗粮": "HK01810", "猴厂": "HK01810", "小米": "HK01810", "雷布斯": "HK01810",
    "猪厂": "HK09999", "网易": "HK09999",
    "狼厂": "HK09888", "度娘": "HK09888",
    "理想": "HK02015", "蔚来": "HK09866", "小鹏": "HK09868", "鹏厂": "HK09868",
    "中芯国际港股": "HK00981",
    
    # ======= 美股黑话 =======
    "果子": "AAPL", "苹果": "AAPL", "厨子": "AAPL",
    "巨硬": "MSFT", "微软": "MSFT",
    "谷歌": "GOOGL", "狗家": "GOOGL",
    "脸书": "META", "非死不可": "META", "元宇宙": "META",
    "亚麻": "AMZN", "亚马逊": "AMZN",
    "特毛": "TSLA", "电车哥": "TSLA", "马斯克": "TSLA", "特斯拉": "TSLA",
    "皮衣客": "NVDA", "核弹厂": "NVDA", "英伟达": "NVDA", "老黄": "NVDA",
    "农企": "AMD", "按摩店": "AMD", "苏妈": "AMD",
    "牙膏厂": "INTC", "英特尔": "INTC",
    "网飞": "NFLX", "奈飞": "NFLX",
    "木头姐": "ARKK", # ETF，但在雪球经常被当作一种情绪标的
    "拼夕夕": "PDD", "黄峥": "PDD",
    "台积电": "TSM", "神山": "TSM"
}

def fetch_with_retry(fetch_func, retries=3, delay=2):
    """通用的重试装饰器，防止偶尔的网络抖动"""
    for i in range(retries):
        try:
            return fetch_func()
        except Exception as e:
            print(f"  [第 {i+1} 次尝试失败] {e}")
            time.sleep(delay)
    return None

def get_value_by_possible_keys(row, keys):
    """自适应提取器：按优先级尝试获取列值"""
    for key in keys:
        if key in row.index:
            return str(row[key]).strip()
    return None

def extract_heuristics(name):
    if not name: 
        return None
    
    original_name = name.strip() # 确保去除首尾空格
    if len(original_name) < 2:
        return None
    
    # 1. 基础商业后缀停用词
    legal_suffixes = [
        "股份有限公司", "有限公司", "有限责任公司", "集团", "控股", 
        "控股有限公司", "集团有限公司", "Corp", "Inc", "Limited", "Ltd", "CO.", "LTD."
    ]
    
    short_name = original_name
    for word in legal_suffixes:
        if short_name.endswith(word):
            short_name = short_name[:-len(word)]
    
    short_name = short_name.strip()
    
    # --- 核心新规则：前缀提取法 ---
    geo_stopwords = {"中国", "美国", "香港", "北京", "上海", "深圳", "南方", "北方", "亚洲", "欧洲"}
    
    candidate = None
    
    # 只有当清理后的名字长度 >= 4 时，才尝试截取前缀
    # 如果长度 < 4 (例如 "李宁", "小米")，直接跳过截取，进入后面的验证逻辑
    if len(short_name) >= 4:
        prefix_2 = short_name[:2]
        prefix_3 = short_name[:3]
        
        if prefix_2 not in geo_stopwords:
            candidate = prefix_2
        elif len(prefix_3) >= 3 and prefix_3 not in geo_stopwords:
            candidate = prefix_3
            
        if candidate:
            # 英文检查
            if re.fullmatch(r'[a-zA-Z]+', candidate):
                if len(candidate) >= 4:
                    return candidate
                else:
                    candidate = None 
            else:
                # 中文前缀有效，直接返回
                return candidate

    # --- 备用逻辑：如果前缀法没命中，或者原名就很短 ---
    # 修复点：允许 temp_name == original_name (即原名就是简称的情况，如 "李宁")
    
    # 修复语法错误：补全逗号
    industry_suffixes = [
        "体育用品", "食品", "饮料", "啤酒", "白酒", "房地产", "地产",
        "银行", "保险", "证券", "互联网", "游戏", "手机", "电器",
        "服装", "服饰", "物流", "快递", "外卖", "旅游", "酒店", "化工", # 加了逗号
        "科技", "网络", "药业", "汽车", "国际", "电气", "通信", "装备", "实业"
    ]
    
    temp_name = short_name
    # 尝试去除行业后缀
    for word in industry_suffixes:
        if temp_name.endswith(word):
            temp_name = temp_name[:-len(word)]
    
    temp_name = temp_name.strip()
    
    # 验证逻辑
    # 1. 长度 >= 2
    # 2. 不是地域词
    # 3. 如果是英文，长度需 >= 4
    if len(temp_name) >= 2:
        if temp_name in geo_stopwords:
            return None
        if re.fullmatch(r'[a-zA-Z]+', temp_name):
            if len(temp_name) >= 4:
                return temp_name
            else:
                return None
        # 【关键修改】只要不是纯地域或非法英文，就返回！
        # 即使 temp_name == original_name (例如 "李宁" -> "李宁") 也返回
        return temp_name

    return None
def generate_ultimate_stock_mapping():
    print("🚀 正在启动全市场股票数据抓取，请稍候...")
    raw_alias_to_codes = defaultdict(set)
    
    for alias, code in CORE_SLANG_DICT.items():
        raw_alias_to_codes[alias].add(code)

    # 可能的列名列表（按可能性排序）
    code_cols = ['代码', 'symbol', 'code']
    name_cols = ['名称', 'name']

    # ==========================
    # 1. 抓取 A 股
    # ==========================
    print("正在拉取 A股静态列表...")
    df_a = fetch_with_retry(ak.stock_info_a_code_name)
    if df_a is not None:
        for _, row in df_a.iterrows():
            code = get_value_by_possible_keys(row, code_cols)
            name = get_value_by_possible_keys(row, name_cols)
            
            if not code or not name: continue
            
            if code.startswith(('60', '68')): std_code = f"SH{code}"
            elif code.startswith(('00', '30')): std_code = f"SZ{code}"
            elif code.startswith(('43', '83', '87')): std_code = f"BJ{code}"
            else: std_code = code
                
            raw_alias_to_codes[name].add(std_code)
            short_name = extract_heuristics(name)
            if short_name: raw_alias_to_codes[short_name].add(std_code)

    # ==========================
    # 2. 抓取 港股
    # ==========================
    print("正在拉取 港股...")
    
    # 【修改点】针对港股接口，列名通常是 '中文名称' 和 '代码'
    # 我们直接在循环前指定正确的列名，或者扩大匹配范围
    hk_code_cols = ['代码', 'symbol', 'code']
    hk_name_cols = ['中文名称', '名称', 'name'] # <--- 新增 '中文名称'

    df_hk = fetch_with_retry(ak.stock_hk_spot)
    if df_hk is not None:
        # 调试打印：确认列名
        # print(f"DEBUG: 港股实际列名: {df_hk.columns.tolist()}")
        
        for _, row in df_hk.iterrows():
            # 使用专门针对港股的列名列表
            code = get_value_by_possible_keys(row, hk_code_cols)
            name = get_value_by_possible_keys(row, hk_name_cols)
            
            if not code or not name: continue
            
            # 港股代码格式化：确保是5位数字 (例如 700 -> 00700)
            # akshare 有时返回 '00700', 有时返回 '700', 统一处理一下比较稳妥
            code_clean = str(code).strip().zfill(5)
            std_code = f"HK{code_clean}"
            
            raw_alias_to_codes[name].add(std_code)
            
            short_name = extract_heuristics(name)
            if short_name: 
                raw_alias_to_codes[short_name].add(std_code)
                
            # 【可选调试】如果想知道是否抓到了安踏李宁，取消下面注释
            # if "安踏" in name or "李宁" in name:
            #     print(f"🔍 捕获: {name} -> {short_name} ({std_code})")

    else:
        print("   ❌ 港股数据获取失败")

    # ==========================
    # 3. 抓取 美股
    # ==========================
    # print("正在拉取 美股...")
    # df_us = fetch_with_retry(ak.stock_us_spot)
    # if df_us is not None:
    #     for _, row in df_us.iterrows():
    #         code = get_value_by_possible_keys(row, code_cols)
    #         name = get_value_by_possible_keys(row, name_cols)
            
    #         if not code or not name: continue
    #         code = code.split('.')[0] 
            
    #         name_clean = name.replace(" Inc", "").replace(" Corp", "")
    #         raw_alias_to_codes[name_clean].add(code)
    #         raw_alias_to_codes[code].add(code)

    # ==========================
    # 4. 冲突解决与导出
    # ==========================
    print("\n⚔️ 开始执行防冲突去重逻辑...")
    final_dict = {}
    conflict_count = 0
    
    for alias, codes in raw_alias_to_codes.items():
        if not alias or len(alias) < 2: continue
        if re.fullmatch(r'[a-zA-Z]+', alias) and len(alias) < 4:
            continue
        if alias in EXCLUDE_LIST:
            continue
            
        if len(codes) == 1:
            final_dict[alias] = list(codes)[0]
        else:
            a_shares = [c for c in codes if c.startswith(('SH', 'SZ', 'BJ'))]
            hk_shares = [c for c in codes if c.startswith('HK')]
            if len(a_shares) == 1 and len(hk_shares) >= 1:
                final_dict[alias] = a_shares[0]
            else:
                conflict_count += 1

    output_file = 'ultimate_stock_aliases.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_dict, f, ensure_ascii=False, indent=4)
        
    print("-" * 40)
    print(f"✅ 生成完毕！保存在 {output_file}")
    print(f"📊 最终可用安全映射数量: {len(final_dict)} 个")
    print(f"🗑️ 成功拦截并丢弃高危冲突词: {conflict_count} 个")
    print("-" * 40)

if __name__ == "__main__":
    generate_ultimate_stock_mapping()