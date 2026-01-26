import time
import json
import re
import threading
from tqdm import tqdm
import config
from db_manager import DBManager

# 尝试导入 ollama
try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    print(">>> 错误: 未安装 ollama")
    exit()

def run_pure_ai():
    print(">>> [纯净模式] 初始化数据库连接...")
    db = DBManager()
    
    # 检查积压总量
    total_backlog = db.get_unanalyzed_count()
    if total_backlog == 0:
        print(">>> 恭喜！所有数据都已分析完毕，没有积压。")
        return

    print(f"\n" + "="*50)
    print(f"   发现待处理积压数据: {total_backlog} 条")
    print(f"   当前模式: 不启动浏览器，全速运行 AI 分析")
    print(f"   使用模型: {config.AI_MODEL_NAME}")
    print("="*50 + "\n")

    # 进度条
    pbar = tqdm(total=total_backlog, desc="[AI 消化中]", unit="条")
    success_count = 0

    while True:
        # 1. 批量获取数据 (一次取 20 条减少 IO)
        batch = db.get_unanalyzed_raw_data(limit=20)
        
        if not batch:
            break # 处理完了

        for row in batch:
            sid, content = row['Status_Id'], row['Description']
            
            # 清洗
            clean = re.sub(r'<[^>]+>', '', content).strip().replace('\n', ' ')
            
            # 1. 字数太少直接跳过 (标记为已完成)
            if len(clean) < 20: # 稍微放宽一点标准
                db.mark_raw_as_analyzed(sid, 1)
                pbar.update(1)
                continue

            # 2. AI 分析
            prompt = f"""分析评论: "{clean}"
            要求: 1.个股逻辑/行业干货->valuable:true。2.纯情绪/水贴->valuable:false。
            """
            
            try:
                # 纯净模式下，不需要 sleep，全速跑
                res = ollama.chat(
                    model=config.AI_MODEL_NAME, 
                    messages=[{'role':'user','content':prompt}],
                    format='json'
                )
                
                js = json.loads(res['message']['content'])
                valuable = js.get('valuable', False)
                cat = js.get('cat', '未知')
                
                if valuable:
                    db.execute_one_safe(
                        "INSERT OR IGNORE INTO Value_Comments VALUES (?,?,?,?,?,?)",
                        (sid, row['User_Id'], row['Description'], row['Created_At'], row['Stock_Tags'], cat)
                    )
                    success_count += 1
                    pbar.set_postfix_str(f"入库:{cat}")
                
                # 标记成功
                db.mark_raw_as_analyzed(sid, 1)
                
            except Exception as e:
                # 报错标记为2，防止死循环
                db.mark_raw_as_analyzed(sid, 2)
            
            pbar.update(1)

    pbar.close()
    print(f"\n>>> 处理完成！本次新入库高价值评论: {success_count} 条")

if __name__ == '__main__':
    run_pure_ai()