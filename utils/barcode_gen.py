"""条形码生成辅助。

- `_make_barcode_png(data: str) -> bytes | None`：把任意字符串用 Code128 编码
  成 PNG 字节流（用于 Excel 嵌入 / PDF 嵌入）。失败返回 None。
- `python -m utils.barcode_gen F2036` 本地 CLI 调试用法。
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


if __name__ == "__main__":
    # CLI 调试用法：python -m utils.barcode_gen F2036
    import sys
    data_to_encode = sys.argv[1] if len(sys.argv) > 1 else "F2036"
    barcode_obj = barcode.Code128(data_to_encode, writer=ImageWriter())
    filename = barcode_obj.save("./tmp/" + data_to_encode)
    print(f"条形码已成功生成，文件名为: {filename}")