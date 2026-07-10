"""条形码生成辅助。

- `_make_barcode_png(data: str) -> bytes | None`：把任意字符串用 Code128 编码
  成 PNG 字节流（用于 Excel 嵌入 / PDF 嵌入）。失败返回 None。
- 保留原 `barcode_obj.save(...)` 的 CLI 用法供本地调试。
"""
from __future__ import annotations

import io
import logging

import barcode
from barcode.writer import ImageWriter

logger = logging.getLogger(__name__)


def _make_barcode_png(data: str) -> bytes | None:
    """把 data 编码成 Code128 PNG，返回字节流；失败返回 None。

    异常吞掉 + logger.warning —— 调用方业务流不应因条码生成失败而中断。
    """
    if not data:
        return None
    try:
        obj = barcode.Code128(data, writer=ImageWriter())
        buf = io.BytesIO()
        obj.write(buf, options={"write_text": False, "quiet_zone": 1.0})
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001 — 条码是 best-effort
        logger.warning("make_barcode_png failed for %r: %s", data, e)
        return None


# 1. 定义你要编码的数据
data_to_encode = "F2036"

# 2. 创建一个 Code128 条形码对象，并指定用 ImageWriter 来生成图片
#    ImageWriter() 是生成图片所必需的[reference:7]
barcode_obj = barcode.Code128(data_to_encode, writer=ImageWriter())

# 3. 保存条形码为图片
#    文件将保存为 'l2014_barcode.png'
#    save 方法会返回保存的完整文件名
filename = barcode_obj.save('./tmp/F2036')

print(f"条形码已成功生成，文件名为: {filename}")