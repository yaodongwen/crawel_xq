# 从sqlite数据库中导出评论到jsonl文件
import sqlite3
import json

# 数据库文件路径
db_path = 'xueqiu_pro_v3.db'
# 输出文件路径
output_file = 'xueqiu_comments_raw.jsonl'

# 连接数据库
conn = sqlite3.connect(db_path)
# 设置行工厂，以便通过列名访问数据
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 执行查询
cursor.execute("SELECT * FROM Value_comments")

# 打开文件准备写入
with open(output_file, 'w', encoding='utf-8') as f:
    # 遍历每一行
    for row in cursor.fetchall():
        # 将行转换为字典 (key为列名，value为对应值)
        row_dict = dict(row)
        # 转换为 JSON 字符串并写入文件，每个对象占一行
        f.write(json.dumps(row_dict, ensure_ascii=False) + '\n')

# 关闭连接
conn.close()

print(f"数据已成功导出到 {output_file}")