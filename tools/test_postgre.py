import psycopg2

# 1. 配置连接参数
config = {
    'host': '192.168.1.33',
    'port': 5432,
    'database': 'postgres', # 或者你刚创建的新数据库名
    'user': 'dwyao',
    'password': '123123'
}

try:
    # 2. 建立连接
    conn = psycopg2.connect(**config)
    cur = conn.cursor()

    # 3. 创建一张表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_data (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. 插入一条数据
    cur.execute("INSERT INTO test_data (name) VALUES (%s)", ("Hello Postgres!",))

    # 5. 提交并关闭
    conn.commit()
    print("数据保存成功！")

    # 查询一下看看
    cur.execute("SELECT * FROM test_data;")
    print("查询结果:", cur.fetchone())

    cur.close()
    conn.close()

except Exception as e:
    print(f"连接失败: {e}")