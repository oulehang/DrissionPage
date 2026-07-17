from DrissionPage import SessionPage
import json
from datetime import datetime

class JuejinPinsCrawler:
    def __init__(self):
        self.page = SessionPage()
        self.base_url = 'https://api.juejin.cn/recommend_api/v1/short_msg/recommend'
        
    def get_pins(self, limit=20):
        """获取沸点内容"""
        # 构建请求数据
        data = {
            "limit": limit,
            "cursor": "0",
            "sort_type": 200
        }
        
        # 添加必要的请求头
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 发送POST请求
        response = self.page.post(self.base_url, json=data, headers=headers)
        # 获取响应内容并解析JSON
        result = json.loads(response.response.text)
        
        if 'data' in result:
            for item in result['data']:
                # 提取内容
                msg_info = {
                    '用户': item['author_user_info']['user_name'],
                    '内容': item['msg_Info']['content'],
                    '时间': datetime.fromtimestamp(item['msg_Info']['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                    '点赞数': item['msg_Info']['digg_count'],
                    '评论数': item['msg_Info']['comment_count']
                }
                
                # 如果有图片，添加图片链接
                if 'pic_list' in item['msg_Info']:
                    msg_info['图片'] = [pic['url'] for pic in item['msg_Info']['pic_list']]
                
                # 打印信息
                print('\n' + '='*50)
                for key, value in msg_info.items():
                    print(f'{key}: {value}')

if __name__ == '__main__':
    crawler = JuejinPinsCrawler()
    print('开始获取掘金沸点内容...')
    crawler.get_pins(10)  # 获取10条沸点