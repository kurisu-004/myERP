from barcode import Code128
from barcode.writer import ImageWriter

# 1. 定义你要编码的数据
data_to_encode = "ADMIN777"

# 2. 创建一个 Code128 条形码对象，并指定用 ImageWriter 来生成图片
#    ImageWriter() 是生成图片所必需的[reference:7]
barcode_obj = Code128(data_to_encode, writer=ImageWriter())

# 3. 保存条形码为图片
#    文件将保存为 'f1001_barcode.png'
#    save 方法会返回保存的完整文件名
filename = barcode_obj.save('./tmp/admin777_barcode')

print(f"条形码已成功生成，文件名为: {filename}")