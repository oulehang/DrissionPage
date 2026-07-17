from DrissionPage import ChromiumPage
import time

# 1. 启动浏览器
page = ChromiumPage()

# 2. 进入拼多多商家后台
page.get("https://mms.pinduoduo.com/")

# 3. 登录（手动扫码或输入账号密码）
input("请手动登录拼多多商家后台，并按回车键继续...")  # 避免验证码问题

# 4. 进入商品发布页面
page.get("https://mms.pinduoduo.com/goods/goods_list")
page.ele("text=发布商品").click()  # 点击“发布商品”按钮

# 5. 填写商品信息
page.ele("#title").input("商品标题示例")  # 填写标题
page.ele("#price").input("99.99")  # 填写价格
page.ele("#stock").input("100")  # 填写库存
page.ele("#category").select("女装")  # 选择分类（示例）

# 6. 上传图片
img_upload = page.ele("#image-uploader")
img_upload.input("C:/商品图片.jpg")  # 本地图片路径

# 7. 提交发布
page.ele("#submit-button").click()
print("✅ 商品发布成功！")

# 8. 关闭浏览器
time.sleep(3)
page.quit()
