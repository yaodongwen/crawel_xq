import os
from huggingface_hub import snapshot_download

def check_models_exist(base_dir="."):
    """检查需要的模型文件夹是否已经存在且不为空"""
    expected_dirs = [
        "model_finbert_regression",
        "model_intent_classifier_best",
        # "model_transformer_intention"
    ]
    
    for d in expected_dirs:
        dir_path = os.path.join(base_dir, d)
        # 如果文件夹不存在，或者文件夹存在但是空的，就认为模型不完整，需要下载
        if not os.path.exists(dir_path) or len(os.listdir(dir_path)) == 0:
            return False
            
    return True

def download_my_models():
    # 增加的检测逻辑
    if check_models_exist():
        print("✅ 检测到本地已存在完整的模型文件夹，跳过下载。")
        return

    repo_id = "dongwenyao/quant-sentiment-models"
    print(f"正在从 {repo_id} 下载模型...")
    
    # 自动下载仓库里的内容到当前目录的对应文件夹中
    snapshot_download(
        repo_id=repo_id,
        local_dir=".",
        allow_patterns=[
            "model_finbert_regression/*", 
            "model_intent_classifier_best/*", 
            "model_transformer_intention/*"
        ], 
        # 如果你的 HF 仓库设为 Private，需要取消下方注释并传入具有 READ 权限的 token
        # token="你的_HF_READ_TOKEN" 
    )
    print("🎉 模型下载完成！")

if __name__ == "__main__":
    download_my_models()