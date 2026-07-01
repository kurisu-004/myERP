"""腾讯云 COS (对象存储) 上传与下载示例。

依赖安装:
    pip install cos-python-sdk-v5 python-dotenv

环境变量（在 .env 文件中配置）:
    COS_SECRET_ID       腾讯云 API 密钥 SecretId
    COS_SECRET_KEY      腾讯云 API 密钥 SecretKey
    COS_REGION          存储桶所在地域，例如 ap-guangzhou
    COS_BUCKET          存储桶名称，格式：<BucketName>-<AppId>
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

# 加载 .env 中的环境变量
load_dotenv()

# ---------- 配置 ----------

SECRET_ID = os.getenv("COS_SECRET_ID")
SECRET_KEY = os.getenv("COS_SECRET_KEY")
REGION = os.getenv("COS_REGION", "ap-guangzhou")
BUCKET = os.getenv("COS_BUCKET")  # 形如 "example-1250000000"

# 缺失关键配置时直接报错，避免上线后静默失败
_missing = [k for k, v in {
    "COS_SECRET_ID": SECRET_ID,
    "COS_SECRET_KEY": SECRET_KEY,
    "COS_BUCKET": BUCKET,
}.items() if not v]
if _missing:
    raise RuntimeError(f"缺少必要的环境变量: {', '.join(_missing)}")


def get_client() -> CosS3Client:
    """构造 COS 客户端。"""
    config = CosConfig(
        Region=REGION,
        SecretId=SECRET_ID,
        SecretKey=SECRET_KEY,
        Scheme="https",  # 默认走 https，更安全
    )
    return CosS3Client(config)


# ---------- 上传 ----------

def upload_file(
    client: CosS3Client,
    local_path: str | Path,
    key: str | None = None,
) -> str:
    """上传本地文件到 COS。

    Args:
        client:    COS 客户端。
        local_path: 本地文件路径。
        key:       对象在 COS 中的 Key（路径）。
                   为空时默认使用文件名。

    Returns:
        上传后的 ETag。
    """
    local_path = Path(local_path)
    if not local_path.is_file():
        raise FileNotFoundError(f"本地文件不存在: {local_path}")

    key = key or local_path.name

    try:
        # put_object_from_local_file 内部会做分块/简单上传，文件较小时直接用这个最方便。
        # 大文件 (>5GB) 或需要断点续传时建议使用 upload_file 接口的高级参数。
        response = client.put_object_from_local_file(
            Bucket=BUCKET,
            Key=key,
            LocalFilePath=str(local_path),
        )
        etag = response.get("ETag", "")
        print(f"[upload] ok  key={key}  etag={etag}")
        return etag
    except CosClientError as e:
        # 客户端错误：参数、网络、权限等
        raise RuntimeError(f"上传失败 (client error): {e}") from e
    except CosServiceError as e:
        # 服务端错误：签名、bucket 配置、COS 内部异常等
        raise RuntimeError(
            f"上传失败 (service error): code={e.get_error_code()} "
            f"msg={e.get_error_msg()}"
        ) from e


# ---------- 下载 ----------

def download_file(
    client: CosS3Client,
    key: str,
    local_path: str | Path,
) -> None:
    """从 COS 下载对象到本地文件。

    Args:
        client:     COS 客户端。
        key:        对象在 COS 中的 Key。
        local_path: 下载到本地的目标路径（若目录不存在会自动创建）。
    """
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = client.get_object(
            Bucket=BUCKET,
            Key=key,
        )
        # get_object 返回的是一个 StreamingBody，需要 .read() 取字节流
        body = response["Body"]
        with local_path.open("wb") as f:
            f.write(body.get_raw_stream().read())

        size = local_path.stat().st_size
        print(f"[download] ok  key={key} -> {local_path}  size={size}B")
    except CosServiceError as e:
        if e.get_error_code() == "NoSuchKey":
            raise FileNotFoundError(f"COS 上找不到对象: {key}") from e
        raise RuntimeError(
            f"下载失败 (service error): code={e.get_error_code()} "
            f"msg={e.get_error_msg()}"
        ) from e
    except CosClientError as e:
        raise RuntimeError(f"下载失败 (client error): {e}") from e


# ---------- 入口示例 ----------

def main() -> None:
    client = get_client()

    # 1) 上传：把本地的 example.txt 上传到 COS 的根目录
    upload_file(client, "example.txt", key="example.txt")

    # 2) 下载：把 COS 上的 example.txt 下载到本地 downloads/example.txt
    download_file(client, "example.txt", "downloads/example.txt")

    # 3) 下载到内存（不落盘，比如想直接处理字节流）
    response = client.get_object(Bucket=BUCKET, Key="example.txt")
    data: bytes = response["Body"].get_raw_stream().read()
    print(f"[memory] 读取到 {len(data)} 字节")


if __name__ == "__main__":
    main()