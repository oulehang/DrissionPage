# -*- coding: utf-8 -*-
r"""
Local web console for preparing WeChat sticker submissions through ChatGPT web automation.

Run:
    .\.venv\Scripts\python.exe chatgpt_image_web_app.py

Open:
    http://127.0.0.1:8765
"""
from argparse import ArgumentParser
from base64 import b64decode
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from json import JSONDecodeError, JSONDecoder, dumps, loads
from mimetypes import guess_type
from pathlib import Path
from re import search, sub
from threading import Lock, Thread
from time import sleep, time
from urllib.parse import unquote, urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageOps

from chatgpt_web_cli import ChatGPTWebClient


CHATGPT_URL = "https://chatgpt.com/"
WECHAT_STICKER_URL = "https://sticker.weixin.qq.com/cgi-bin/mmemoticonwebnode-bin/pages/stickerPage/detail"

DEFAULT_MEANINGS = [
    "收到", "开心", "疑惑", "震惊", "委屈", "生气", "谢谢", "晚安",
    "加班", "别催", "已疯", "老板", "报销", "没钱", "明天", "摸鱼",
    "裂开", "可以", "太难", "已读", "算账", "冲鸭", "救命", "躺平",
]
STICKER_IDEAS = DEFAULT_MEANINGS[:8]

DEFAULT_TEMPLATES = [
    *(f"表情图{i + 1:02d}-{idea}：根据主题「{{theme}}」生成微信静态表情单图。要求：严格沿用主题指定的题材类型和主体，不要擅自改成人物；单图正方形构图，适配240*240像素，JPG/PNG/GIF均可但不要动画，风格统一，有梗，表情差异明显，必须包含1-4个中文大字短梗文案，文字清晰可读并融入画面，不要水印，不要大面积留白，背景透明或纯净。"
      for i, idea in enumerate(STICKER_IDEAS)),
    "详情页横幅：根据主题「{theme}」生成一张微信表情包详情页横幅。要求：严格沿用主题指定的题材类型和主体，不要擅自改成人物；横向构图，适配750*400像素，JPG或PNG，画面丰富有故事性，色调活泼明朗，避免白色背景和透明背景，不要文字，不要水印。",
    "表情封面图：根据主题「{theme}」生成一张微信表情包封面图。要求：正方形构图，适配240*240像素，PNG，透明背景，主体为最具辨识度的角色/物件/符号正面形象，不要文字，不要水印，不要白色描边。",
    "聊天页图标：根据主题「{theme}」生成一张微信聊天页表情图标。要求：正方形构图，适配50*50像素，PNG，透明背景，仅保留最具辨识度的主体核心特征，画面简洁清晰，不要装饰元素，不要文字，不要水印。",
    "赞赏引导图：根据主题「{theme}」生成一张微信表情赞赏引导图。要求：适配750*560像素，JPG或PNG，展示在选择赞赏金额页面，用于吸引用户赞赏；风格需与表情一致，不出现与表情无关内容，不要水印。",
    "赞赏致谢图：根据主题「{theme}」生成一张微信表情赞赏致谢图。要求：适配750*750像素，JPG或PNG，用户赞赏后展示在答谢页面；风格需与表情一致，表达感谢和分享氛围，不出现与表情无关内容，不要水印。",
]

SPRITE_SHEET_PROMPT = """创建24个微信表情贴纸，主题为「{theme}」。
主体设定：先从主题中判断题材类型，再严格沿用对应主体；可以是节日祝福卡、花束、宠物、人物关系、动物、物件、职业身份、情绪符号或场景梗。除非主题明确要求人物/亲子/情侣/职业人物，否则不要主动加入人物；主体需像同一套表情包。
画面要求：
1. 生成一张完整的6列×4行贴纸网格图，共24个不同表情。
2. 每格都是独立正方形贴纸，主体居中，占画面70%-90%，留白少。
3. 每个贴纸动作和情绪都不同，且每格文案必须按顺序对应这24个含义词：{meanings}。
4. 风格统一，有网感、有梗、好转发，适合中国微信用户在私聊、群聊、工作沟通、亲友问候、节日祝福中高频使用。
5. 每个贴纸必须包含对应含义词的1-4个中文大字短梗文案，文字要清晰、粗体、适合微信聊天小图阅读。
6. 不要水印，不要边框，不要编号，不要英文，不要长句。
7. 网格尽量规整，格子之间留清晰间距，方便后续自动裁剪成24张240*240表情图。
"""

COPY_PROMPT = """你是微信表情开放平台的表情专辑策划助手。
请根据用户需求生成提交表情专辑所需文案和分类建议。

用户需求：
{theme}

严格要求：
1. 表情名称不超过8个汉字，5个汉字以内优先；不含标点符号；中文名称不要空格；尽量避免常见重名。
2. 表情介绍不超过80个汉字，充分展现表情形象特点或故事情节。
3. 版权信息不超过10个汉字，默认原创。
4. 表情风格从这些里面选1到2项：日常、软萌可爱、二次元、华丽风、搞笑、酷炫、魔性鬼畜、恶搞、简笔画、赛博朋克、蒸汽波、像素、暗黑、复古。
5. 表情主题从这些里面选1到2项：万能通用、网络热点、节日、考试学习、工作职场、情侣、毕业、刷屏、红包相关、游戏、运动健身、怼人斗图、群聊必备、节气、邀约约起来、励志鼓舞。
6. 为每张小表情生成含义词，每个不超过4个汉字，数量按24个优先，不足也至少8个。
7. 赞赏引导语为5到15个汉字，语气幽默，需与表情相关。
8. 本工具会生成静态表情专辑：8到24张表情图、1张详情页横幅、1张表情封面图、1张聊天页图标、1张赞赏引导图、1张赞赏致谢图。
9. 角色/内容必须贴合用户需求，不默认写人物或动物；先判断用户要的是节日祝福、贺卡海报、花束、宠物、人物关系、物件、职业身份、情绪符号还是场景梗，再填写对应内容。
10. 如果用户需求是母亲节、生日、早晚安、节气祝福等图片类表情，可以把内容写成祝福贺卡/花束/节日元素/手写字，不要强行改成单一人物角色。
11. 返回纯JSON，不要Markdown，不要解释。

JSON格式：
{{
  "name": "不超过8个汉字",
  "intro": "不超过80个汉字",
  "copyright": "原创",
  "type": "静态表情",
  "character": "贴合主题的题材和主体",
  "style_tags": ["日常", "搞笑"],
  "theme_tags": ["万能通用", "群聊必备"],
  "price": "免费",
  "download_region": "中国大陆",
  "sticker_meanings": ["收到", "开心", "疑惑", "震惊", "委屈", "生气", "谢谢", "晚安"],
  "reward_enabled": true,
  "reward_guide_text": "喜欢就赞赏一下"
}}
"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>微信表情提交准备工具</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: #eef2f6;
      color: #1f2328;
      --panel: #ffffff;
      --line: #d7dee8;
      --muted: #667085;
      --field: #fbfcfe;
      --primary: #1264d8;
      --primary-dark: #0b56bf;
      --success: #16833f;
      --neutral: #5c6675;
      --soft-green: #e9f7ef;
      --soft-yellow: #fff6d7;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: #eef2f6; color: #1f2328; }
    main { max-width: 1320px; margin: 0 auto; padding: 28px 24px 40px; }
    h1 {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 24px;
      line-height: 1.2;
      margin: 0 0 20px;
      letter-spacing: 0;
    }
    h1::before {
      content: "";
      width: 10px;
      height: 28px;
      border-radius: 4px;
      background: #20a464;
      flex: 0 0 auto;
    }
    h2 { font-size: 16px; line-height: 1.25; margin: 0 0 14px; letter-spacing: 0; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }
    label { display: block; font-size: 13px; font-weight: 700; margin-bottom: 7px; color: #344054; }
    textarea, input, select {
      width: 100%;
      border: 1px solid #c7d0dd;
      border-radius: 7px;
      padding: 10px 11px;
      font: inherit;
      background: var(--field);
      color: #1f2328;
      outline: none;
      transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
    }
    textarea:focus, input:focus, select:focus {
      border-color: var(--primary);
      background: #fff;
      box-shadow: 0 0 0 3px rgba(18, 100, 216, 0.12);
    }
    textarea { min-height: 92px; resize: vertical; line-height: 1.55; }
    input[type="file"] { padding: 9px; background: #fff; }
    button {
      border: 0;
      border-radius: 7px;
      padding: 10px 14px;
      min-height: 40px;
      background: var(--primary);
      color: white;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
      transition: background .15s ease, transform .05s ease;
    }
    button:hover { background: var(--primary-dark); }
    button:active { transform: translateY(1px); }
    button.secondary { background: var(--neutral); }
    button.secondary:hover { background: #485260; }
    button.success { background: var(--success); }
    button.success:hover { background: #0f6f34; }
    button:disabled { background: #98a2b3; cursor: not-allowed; transform: none; }
    a { color: var(--primary); text-decoration: none; font-weight: 700; }
    a:hover { text-decoration: underline; }
    .task-grid { display: grid; grid-template-columns: minmax(360px, 1fr) 170px 160px 210px 130px; gap: 12px; align-items: end; }
    .task-grid > button { height: 42px; }
    .theme-tools { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 9px; }
    .theme-tools button { padding: 7px 11px; min-height: 32px; font-size: 12px; }
    .metadata { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .metadata .wide { grid-column: 1 / -1; }
    .prompt-grid { display: grid; grid-template-columns: minmax(360px, 1.15fr) minmax(320px, .85fr); gap: 14px; align-items: start; }
    .reference-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 9px; min-height: 8px; }
    .reference-list span {
      display: inline-flex;
      align-items: center;
      max-width: 260px;
      border: 1px solid #c9d7ea;
      border-radius: 7px;
      padding: 5px 9px;
      background: #eef6ff;
      color: #315679;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .history-list { display: grid; gap: 8px; }
    .history-item {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fbfcfe;
    }
    .history-item b { display: block; font-size: 13px; margin-bottom: 4px; }
    .history-item span {
      display: block;
      max-width: 980px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .asset-prompts { display: grid; gap: 10px; }
    .asset-prompts textarea { min-height: 74px; }
    .status { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfe;
      min-height: 72px;
    }
    .metric b { display: block; font-size: 12px; color: var(--muted); margin-bottom: 7px; }
    .metric span { font-size: 18px; font-weight: 700; line-height: 1.25; overflow-wrap: anywhere; }
    .log {
      height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      background: #111827;
      color: #dbeafe;
      border-radius: 8px;
      padding: 13px 14px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      line-height: 1.55;
      border: 1px solid #1f2937;
    }
    .section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
    .section-head h2 { margin-bottom: 0; }
    .actions { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; }
    .actions button { padding: 7px 11px; min-height: 34px; font-size: 12px; }
    .specs { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 14px; }
    .spec, .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border-radius: 999px;
      padding: 4px 9px;
      background: var(--soft-green);
      color: #13723d;
      font-size: 12px;
      line-height: 1.2;
    }
    .badge.warn { background: var(--soft-yellow); color: #836100; }
    .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 14px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: white;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }
    .card img {
      display: block;
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: contain;
      background: linear-gradient(180deg, #f7f9fc, #eef2f6);
      border-bottom: 1px solid var(--line);
    }
    .card div { padding: 11px; font-size: 13px; }
    .card b { display: block; margin-bottom: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .card a { display: inline-flex; margin-top: 8px; }
    .hint { color: var(--muted); font-size: 13px; line-height: 1.55; margin: 9px 0 0; }
    @media (max-width: 1160px) {
      .task-grid { grid-template-columns: 1fr 170px 1fr; }
      .task-grid > button { width: 100%; }
      .prompt-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 900px) {
      main { padding: 18px 14px 30px; }
      h1 { font-size: 21px; }
      section { padding: 14px; }
      .task-grid, .status, .metadata, .prompt-grid { grid-template-columns: 1fr; }
      .section-head { align-items: flex-start; flex-direction: column; }
      .actions { width: 100%; }
      .actions button, .actions a, .theme-tools button { width: 100%; justify-content: center; text-align: center; }
      .history-item { align-items: stretch; flex-direction: column; }
      .history-item span { white-space: normal; }
      .gallery { grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); }
    }
  </style>
</head>
<body>
<main>
  <h1>微信表情提交准备工具</h1>

  <section>
    <h2>任务</h2>
    <div class="task-grid">
      <div>
        <label for="theme">总需求</label>
        <textarea id="theme">小狗早安问候</textarea>
        <div class="theme-tools">
          <button class="secondary" id="hotThemeBtn" type="button">联网参考热门</button>
        </div>
      </div>
      <div>
        <label for="mode">生成模式</label>
        <select id="mode">
          <option value="sprite24" selected>24宫格一次生成</option>
          <option value="sequential">逐张生成</option>
        </select>
      </div>
      <button id="startBtn">生成文案和图片</button>
      <button class="success" id="syncBtn">同步到微信表情开放平台</button>
      <button class="secondary" id="reconnectBtn" type="button">手动重连</button>
    </div>
    <p class="hint">同步会打开微信表情开放平台页面并尽量自动填写表单；请登录后人工复核，确认无误再提交。</p>
    <div style="margin-top:12px;">
      <label for="referenceImages">参考图（可选，用于提取画风、主体、构图和文字风格）</label>
      <input id="referenceImages" type="file" accept="image/png,image/jpeg,image/jpg,image/webp" multiple>
      <div class="reference-list" id="referenceList"></div>
    </div>
  </section>

  <section>
    <h2>提交文案</h2>
    <div class="metadata">
      <div><label>表情名称</label><input id="metaName" maxlength="8"></div>
      <div><label>版权</label><input id="metaCopyright" maxlength="10"></div>
      <div><label>角色/内容</label><input id="metaCharacter"></div>
      <div class="wide"><label>表情介绍</label><textarea id="metaIntro" maxlength="80"></textarea></div>
      <div><label>表情风格</label><input id="metaStyles"></div>
      <div><label>表情主题</label><input id="metaThemes"></div>
      <div><label>价格/地区</label><input id="metaOther"></div>
      <div class="wide"><label>小表情含义词（每行一个，4个字以内）</label><textarea id="metaMeanings"></textarea></div>
      <div><label><input id="rewardEnabled" type="checkbox" checked style="width:auto; margin-right:6px;">接受赞赏</label></div>
      <div class="wide"><label>赞赏引导语（5-15个汉字）</label><input id="rewardGuideText" maxlength="15"></div>
    </div>
  </section>

  <section class="templates">
    <h2>图片提示词</h2>
    <div class="prompt-grid">
      <div>
        <label for="stickerPrompt">表情图</label>
        <textarea id="stickerPrompt">创建24个微信表情贴纸，主题为「{theme}」。先判断主题题材类型并严格沿用对应主体，不要擅自改成人物；除非主题明确要求人物/亲子/情侣/职业人物，否则不要加入人物。生成一张完整的6列×4行贴纸网格图，共24个不同表情。每格都是独立正方形贴纸，主体居中，必须包含1-4个中文大字短梗文案，文字清晰可读并融入画面。每格文案必须按顺序对应这24个含义词：{meanings}。不要水印、边框、编号、英文和长句，格子之间留清晰间距，方便裁剪成24张240*240表情图。</textarea>
        <p class="hint">24宫格模式会使用这条提示词生成一张大图并自动裁剪；逐张模式会使用内置表情模板。</p>
      </div>
      <div class="asset-prompts">
        <div>
          <label>详情页横幅</label>
          <textarea class="template">详情页横幅：根据主题「{theme}」生成一张微信表情包详情页横幅。要求：横向构图，适配750*400像素，画面丰富有故事性，色调活泼明朗，避免白色背景和透明背景，不要文字，不要水印。</textarea>
        </div>
        <div>
          <label>表情封面图</label>
          <textarea class="template">表情封面图：根据主题「{theme}」生成一张微信表情包封面图。要求：正方形构图，适配240*240像素，PNG，透明背景，主体为最具辨识度的角色/物件/符号正面形象，不要文字，不要水印，不要白色描边。</textarea>
        </div>
        <div>
          <label>聊天页图标</label>
          <textarea class="template">聊天页图标：根据主题「{theme}」生成一张微信聊天页表情图标。要求：正方形构图，适配50*50像素，PNG，透明背景，仅保留最具辨识度的主体核心特征，画面简洁清晰，不要装饰元素，不要文字，不要水印。</textarea>
        </div>
        <div>
          <label>赞赏引导图</label>
          <textarea class="template">赞赏引导图：根据主题「{theme}」生成一张微信表情赞赏引导图。要求：适配750*560像素，展示在选择赞赏金额页面，用于吸引用户赞赏；风格需与表情一致，不出现与表情无关内容，不要水印。</textarea>
        </div>
        <div>
          <label>赞赏致谢图</label>
          <textarea class="template">赞赏致谢图：根据主题「{theme}」生成一张微信表情赞赏致谢图。要求：适配750*750像素，用户赞赏后展示在答谢页面；风格需与表情一致，表达感谢和分享氛围，不出现与表情无关内容，不要水印。</textarea>
        </div>
      </div>
    </div>
  </section>

  <section>
    <h2>状态</h2>
    <div class="status">
      <div class="metric"><b>运行状态</b><span id="state">idle</span></div>
      <div class="metric"><b>进度</b><span id="progress">0 / 0</span></div>
      <div class="metric"><b>当前步骤</b><span id="current">-</span></div>
      <div class="metric"><b>图片数量</b><span id="count">0</span></div>
    </div>
    <div class="actions" style="margin-top:12px;">
      <button class="secondary" id="saveHistoryBtn" type="button">保存历史</button>
      <button class="secondary" id="clearBtn" type="button">清空当前</button>
      <button class="secondary" id="saveClearBtn" type="button">清空并保存</button>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>历史记录</h2>
      <button class="secondary" id="refreshHistoryBtn" type="button">刷新历史</button>
    </div>
    <div class="history-list" id="historyList"></div>
  </section>

  <section>
    <h2>日志</h2>
    <div class="log" id="log"></div>
  </section>

  <section>
    <div class="section-head">
      <div>
        <h2>图片汇总</h2>
        <div class="specs">
          <span class="spec">表情图 8-24张 / 240*240 PNG</span>
          <span class="spec">详情页横幅 1张 / 750*400 PNG</span>
          <span class="spec">封面 1张 / 240*240 PNG</span>
          <span class="spec">图标 1张 / 50*50 PNG</span>
          <span class="spec">赞赏引导图 1张 / 750*560 PNG</span>
          <span class="spec">赞赏致谢图 1张 / 750*750 PNG</span>
        </div>
      </div>
      <div class="actions"><a href="/download/all" download="wechat_sticker_assets.zip">下载全部图片</a></div>
    </div>
    <div class="gallery" id="gallery"></div>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);
let lastMetaKey = "";
let lastGalleryKey = "";
let lastHotThemeKey = "";
let metaDirty = false;
let referenceFiles = [];
let lastHistoryKey = "";
const metaIds = ["metaName", "metaIntro", "metaCopyright", "metaCharacter", "metaStyles", "metaThemes", "metaOther", "metaMeanings", "rewardGuideText"];
metaIds.forEach(id => $(id).addEventListener("input", () => { metaDirty = true; }));
$("rewardEnabled").addEventListener("change", () => { metaDirty = true; });

$("referenceImages").addEventListener("change", () => {
  referenceFiles = [...$("referenceImages").files];
  $("referenceList").innerHTML = referenceFiles.map(file => `<span>${file.name}</span>`).join("");
});

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name: file.name, type: file.type, data: reader.result});
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function uploadReferences() {
  if (!referenceFiles.length) return [];
  const files = await Promise.all(referenceFiles.map(readFileAsDataUrl));
  const res = await fetch("/api/upload_references", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({files})
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || "参考图上传失败");
  }
  return data.paths || [];
}

function appendLog(message) {
  $("log").textContent += `\n${message}`;
  $("log").scrollTop = $("log").scrollHeight;
}

async function postJson(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function refreshHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  renderHistory(data.history || []);
}

function renderHistory(history) {
  const key = JSON.stringify(history.map(item => [item.id, item.name, item.theme, item.image_count, item.created_at]));
  if (key === lastHistoryKey) return;
  lastHistoryKey = key;
  $("historyList").innerHTML = history.length ? history.map(item => `
    <div class="history-item">
      <div>
        <b>${item.name || item.theme || "未命名历史"}</b>
        <span>${item.created_at || ""} / ${item.image_count || 0} 张 / ${item.theme || ""}</span>
      </div>
      <button class="secondary" type="button" data-history-id="${item.id}">查看</button>
    </div>
  `).join("") : `<p class="hint">暂无历史记录。</p>`;
  [...document.querySelectorAll("[data-history-id]")].forEach(button => {
    button.onclick = async () => {
      try {
        await postJson("/api/load_history", {id: button.dataset.historyId});
        lastMetaKey = "";
        lastGalleryKey = "";
        await poll();
      } catch (e) {
        appendLog(`\n加载历史失败：${e.message || e}`);
      }
    };
  });
}

$("saveHistoryBtn").onclick = async () => {
  try {
    await postJson("/api/save_history");
    await refreshHistory();
  } catch (e) {
    appendLog(`保存历史失败：${e.message || e}`);
  }
};

$("clearBtn").onclick = async () => {
  try {
    await postJson("/api/clear_current");
    lastMetaKey = "";
    lastGalleryKey = "";
    metaDirty = false;
    await poll();
  } catch (e) {
    appendLog(`清空失败：${e.message || e}`);
  }
};

$("saveClearBtn").onclick = async () => {
  try {
    await postJson("/api/save_history");
    await postJson("/api/clear_current");
    lastMetaKey = "";
    lastGalleryKey = "";
    metaDirty = false;
    await refreshHistory();
    await poll();
  } catch (e) {
    appendLog(`清空并保存失败：${e.message || e}`);
  }
};

$("refreshHistoryBtn").onclick = refreshHistory;

$("hotThemeBtn").onclick = async () => {
  $("hotThemeBtn").disabled = true;
  try {
    const current = $("theme").value.trim();
    const stickerMeanings = $("metaMeanings").value.split(/\n/).map(x => x.trim()).filter(Boolean);
    const referenceImages = await uploadReferences();
    const res = await fetch("/api/search_theme", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({keyword: current || "热门微信表情包", stickerMeanings, referenceImages})
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "联网参考热门失败");
    }
    if (data.theme) $("theme").value = data.theme;
    if (data.meanings && data.meanings.length) {
      $("metaMeanings").value = data.meanings.join("\n");
      metaDirty = true;
    }
  } catch (e) {
    $("log").textContent += `\n联网参考热门失败：${e.message || e}`;
    $("log").scrollTop = $("log").scrollHeight;
  } finally {
    $("hotThemeBtn").disabled = false;
  }
};

$("startBtn").onclick = async () => {
  const theme = $("theme").value.trim();
  const mode = $("mode").value;
  const stickerPrompt = $("stickerPrompt").value.trim();
  const stickerMeanings = $("metaMeanings").value.split(/\n/).map(x => x.trim()).filter(Boolean);
  const templates = [...document.querySelectorAll(".template")].map(x => x.value.trim()).filter(Boolean);
  $("startBtn").disabled = true;
  metaDirty = false;
  lastMetaKey = "";
  lastHotThemeKey = "";
  try {
    const referenceImages = await uploadReferences();
    const res = await fetch("/api/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({theme, templates, mode, stickerPrompt, stickerMeanings, referenceImages})
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "启动生成失败");
    }
  } catch (e) {
    $("log").textContent += `\n启动失败：${e.message || e}`;
    $("log").scrollTop = $("log").scrollHeight;
    $("startBtn").disabled = false;
  }
};

$("syncBtn").onclick = async () => {
  $("syncBtn").disabled = true;
  try {
    const metadata = {
      name: $("metaName").value,
      intro: $("metaIntro").value,
      copyright: $("metaCopyright").value,
      character: $("metaCharacter").value,
      style_tags: $("metaStyles").value.split(/[，,]/).map(x => x.trim()).filter(Boolean),
      theme_tags: $("metaThemes").value.split(/[，,]/).map(x => x.trim()).filter(Boolean),
      sticker_meanings: $("metaMeanings").value.split(/\n/).map(x => x.trim()).filter(Boolean),
      reward_enabled: $("rewardEnabled").checked,
      reward_guide_text: $("rewardGuideText").value.trim(),
    };
    await fetch("/api/sync_wechat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({metadata})
    });
  } finally {
    try {
      await poll();
    } catch (e) {
      $("syncBtn").disabled = false;
    }
  }
};

$("reconnectBtn").onclick = async () => {
  $("reconnectBtn").disabled = true;
  try {
    await fetch("/api/reconnect", {method: "POST"});
  } finally {
    try {
      await poll();
    } catch (e) {
      $("reconnectBtn").disabled = false;
    }
  }
};

function fillMeta(meta) {
  const key = JSON.stringify(meta || {});
  if (!meta) {
    if (lastMetaKey === key) return;
    $("metaName").value = "";
    $("metaIntro").value = "";
    $("metaCopyright").value = "";
    $("metaCharacter").value = "";
    $("metaStyles").value = "";
    $("metaThemes").value = "";
    $("metaOther").value = "";
    $("metaMeanings").value = "";
    $("rewardEnabled").checked = true;
    $("rewardGuideText").value = "";
    lastMetaKey = key;
    metaDirty = false;
    return;
  }
  if (key === lastMetaKey) return;
  if (metaDirty && lastMetaKey) return;
  $("metaName").value = meta.name || "";
  $("metaIntro").value = meta.intro || "";
  $("metaCopyright").value = meta.copyright || "";
  $("metaCharacter").value = meta.character || "";
  $("metaStyles").value = (meta.style_tags || []).join("，");
  $("metaThemes").value = (meta.theme_tags || []).join("，");
  $("metaOther").value = `${meta.price || "免费"} / ${meta.download_region || "中国大陆"}`;
  $("metaMeanings").value = (meta.sticker_meanings || []).join("\n");
  $("rewardEnabled").checked = meta.reward_enabled !== false;
  $("rewardGuideText").value = meta.reward_guide_text || "";
  lastMetaKey = key;
  metaDirty = false;
}

function renderGallery(images) {
  const key = JSON.stringify(images.map(img => [img.url, img.label, img.kind, img.size]));
  if (key === lastGalleryKey) return;
  lastGalleryKey = key;
  $("gallery").innerHTML = images.map((img, i) => `
    <div class="card">
      <img src="${img.url}" alt="generated image ${i + 1}" loading="lazy">
      <div>
        <b>${img.label || "图片 " + (i + 1)}</b>
        <span class="badge">${img.format || "PNG"}</span>
        <span class="badge">${img.size || ""}</span>
        <span class="badge warn">${img.kind || "asset"}</span><br>
        <a href="${img.url}" download>下载这张</a>
      </div>
    </div>
  `).join("");
}

async function poll() {
  const res = await fetch("/api/status");
  const data = await res.json();
  $("state").textContent = data.state;
  $("progress").textContent = `${data.done} / ${data.total}`;
  $("current").textContent = data.current || "-";
  $("count").textContent = data.images.length;
  const nextLog = data.log.join("\n");
  if ($("log").textContent !== nextLog) {
    $("log").textContent = nextLog;
    $("log").scrollTop = $("log").scrollHeight;
  }
  if (data.hot_theme && !data.metadata) {
    const hotKey = JSON.stringify(data.hot_theme);
    if (hotKey !== lastHotThemeKey) {
      lastHotThemeKey = hotKey;
    if (data.hot_theme.theme) $("theme").value = data.hot_theme.theme;
    if (data.hot_theme.meanings && data.hot_theme.meanings.length) {
      $("metaMeanings").value = data.hot_theme.meanings.join("\n");
      metaDirty = true;
    }
    }
  }
  if (data.current === "已加载历史记录" && data.theme) {
    $("theme").value = data.theme;
  }
  const busy = data.state === "running" || data.state === "reconnecting" || data.state === "searching";
  $("startBtn").disabled = busy;
  $("syncBtn").disabled = busy;
  $("reconnectBtn").disabled = busy;
  $("hotThemeBtn").disabled = busy;
  $("saveHistoryBtn").disabled = busy;
  $("clearBtn").disabled = busy;
  $("saveClearBtn").disabled = busy;
  fillMeta(data.metadata);
  renderGallery(data.images);
}

setInterval(poll, 1200);
poll();
refreshHistory();
</script>
</body>
</html>
"""


def safe_name(text, limit=8):
    text = sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    return text[:limit] or "萌趣小狗"


def clamp_text(text, limit):
    text = (text or "").strip().replace("\n", "")
    return text[:limit]


def extract_json(text):
    raw = text or ""
    cleaned = (
        raw.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .strip()
    )
    candidates = []
    candidates.extend(sub(r"^json\s*", "", block.strip(), flags=2) for block in search_code_blocks(cleaned))
    candidates.extend(scan_json_objects(cleaned))
    candidates.append(cleaned)

    last_error = None
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if candidate.startswith("```"):
            candidate = sub(r"^```(?:json)?", "", candidate).strip()
            candidate = sub(r"```$", "", candidate).strip()
        try:
            return loads(candidate)
        except JSONDecodeError as e:
            last_error = e
        decoded = decode_embedded_json(candidate)
        if decoded is not None:
            return decoded
    if last_error:
        raise last_error
    raise JSONDecodeError("No JSON object found", raw, 0)


def decode_embedded_json(text):
    decoder = JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def search_code_blocks(text):
    blocks = []
    start = 0
    while True:
        open_pos = text.find("```", start)
        if open_pos < 0:
            break
        close_pos = text.find("```", open_pos + 3)
        if close_pos < 0:
            break
        blocks.append(text[open_pos + 3:close_pos])
        start = close_pos + 3
    return blocks


def scan_json_objects(text):
    objects = []
    start = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:index + 1])
                start = None
    return objects


def normalize_metadata(meta):
    def first_value(*keys, default=""):
        for key in keys:
            value = meta.get(key)
            if value not in (None, "", []):
                return value
        return default

    def list_value(*keys, default=None):
        value = first_value(*keys, default=default or [])
        if isinstance(value, str):
            return [x.strip() for x in value.replace("，", ",").replace("、", ",").split(",") if x.strip()]
        return list(value or [])

    meanings = [
        clamp_text(item, 4)
        for item in list_value("sticker_meanings", "meanings", "meaning_words", "words", default=DEFAULT_MEANINGS)
        if clamp_text(item, 4)
    ]
    if len(meanings) < len(DEFAULT_MEANINGS):
        meanings.extend(DEFAULT_MEANINGS[len(meanings):])
    return {
        "name": safe_name(first_value("name", "title", "album_name", "sticker_name"), 8),
        "intro": clamp_text(first_value("intro", "description", "summary", "album_intro"), 80),
        "copyright": clamp_text(first_value("copyright", "copyright_info", default="原创"), 10),
        "type": first_value("type", "sticker_type", default="静态表情"),
        "character": first_value("character", "content", "role", "subject", default="原创主体"),
        "style_tags": list_value("style_tags", "styles", "style", default=["日常", "搞笑"])[:2],
        "theme_tags": list_value("theme_tags", "themes", "theme_category", default=["万能通用", "群聊必备"])[:2],
        "price": first_value("price", default="免费"),
        "download_region": first_value("download_region", "region", default="中国大陆"),
        "sticker_meanings": meanings[:24],
        "reward_enabled": meta.get("reward_enabled", True) is not False,
        "reward_guide_text": clamp_text(first_value("reward_guide_text", "reward_text", "reward_guide", default="喜欢就赞赏一下"), 15),
    }


def apply_meanings_to_prompt(prompt, meanings):
    meaning_text = "、".join(meanings or DEFAULT_MEANINGS)
    if "{meanings}" in prompt:
        return prompt.replace("{meanings}", meaning_text)
    return f"{prompt}\n每格文案必须按顺序对应这组小表情含义词：{meaning_text}。"


def split_theme_and_meanings(theme):
    theme = (theme or "").strip()
    patterns = [
        r"(?:小表情)?含义词[：:]\s*(.+)$",
        r"每格文案必须按顺序对应(?:这)?(?:24个)?含义词[：:]\s*(.+)$",
    ]
    for pattern in patterns:
        m = search(pattern, theme)
        if not m:
            continue
        raw_meanings = m.group(1)
        clean_theme = theme[:m.start()].rstrip(" 。；;，,\n")
        meanings = [
            clamp_text(item, 4)
            for item in sub(r"[。；;].*$", "", raw_meanings).replace("\n", "、").split("、")
            if clamp_text(item, 4)
        ]
        return clean_theme or theme, meanings[:24]
    return theme, []


def build_theme_prompt(keyword="小狗早安问候", references=None):
    reference_text = ""
    if references:
        compact_refs = "、".join(references[:3])
        reference_text = f"参考热门：{compact_refs}。"
    return (
        f"{keyword or '小狗早安问候'}，先判断题材类型并严格沿用输入方向，生成微信图片提示词；"
        f"可为节日祝福卡、花束、宠物、人物关系、物件或场景梗，短梗大字清晰，好转发。"
        f"{reference_text}"
    )


def chatgpt_hot_theme(keyword="热门微信表情包", reference_images=None):
    reference_images = [str(Path(path).resolve()) for path in (reference_images or []) if Path(path).exists()]
    reference_note = reference_style_note(reference_images)
    prompt = f"""你是面向中国微信用户的爆款微信表情包选题策划和图片生成提示词策划。请联网搜索并总结截至 {date.today().isoformat()} 最新的热门表情包/贴纸风格趋势，然后基于用户输入生成一条简短、高质量、可直接再次发起图片生成的“总需求”。

用户当前关键词：{keyword}{reference_note}

要求：
1. 第一步先判断用户输入的题材类型：节日祝福/贺卡海报、宠物问候、人物关系、动物萌宠、花束礼物、物件拟人、职业身份、纯文字祝福、情绪斗图、生活场景梗等。
2. 必须围绕用户输入方向生成图片提示词，不要套用固定人物模板。除非用户明确输入人物、亲子、情侣、职业人物或某类人群，否则不要主动加入人物。
3. 如果用户输入“母亲节祝福”“生日快乐”“早上好”“健康平安”等，应优先生成微信祝福图片/贺卡/花束/节日元素/手写大字方向；如果输入“小狗”“猫咪”等宠物，应生成宠物表情包方向。
4. 如果用户输入很短或很泛，要自动补全为一个有记忆点的原创题材、主体、使用场景、视觉风格和文案语气，但主体类型必须继承用户输入。
5. 优先贴合中国微信用户真实使用习惯：亲友问候、节日祝福、群发转发、私聊接话、群聊刷屏、工作敷衍、催促回复、阴阳怪气、卖萌认怂、拒绝背锅、情绪发疯等高频场景。
6. 参考热门方向，但不要抄袭现有IP、明星、品牌、影视角色、网络红人脸、具体表情包形象或平台已有爆款名称。
7. 爆款感要来自“题材准确 + 微信转发感 + 短梗大字 + 低理解成本 + 可重复使用 + 情绪回应感”，避免小众冷梗、长句、过度文艺、营销腔。
8. theme 控制在55到90个中文字符，只写图片生成重点：题材类型 + 主体元素 + 场景/用途 + 画风 + 中文文案语气。不要只写选题标题。
9. theme 不要写平台规则、尺寸、生成步骤、含义词列表、版权提醒。
10. meanings 必须是24个小表情含义词，每个4个汉字以内，要按题材生成。宠物问候可包含早安、午安、晚安、想你、加油；节日祝福可包含快乐、平安、健康、感恩、好运；斗图可包含收到、笑死、已读、别催、无语、救命等。
11. 只返回一个单行 JSON 对象，不要 Markdown，不要代码块，不要解释，不要列表，不要前后缀文字。

参考风格理解：
- 类似“母亲节祝福”应输出红粉色节日贺卡、鲜花、爱心、柔和3D/插画、醒目中文祝福字的微信祝福图片方向，可有人物母女，也可纯花束贺卡，取决于用户输入。
- 类似“小狗球球”应输出白色小狗手绘萌宠、粉色点缀、黑色手写中文短句、多格日常问候的微信表情方向。

JSON格式：{{"theme":"90字以内中文图片生成总需求","meanings":["收到","开心"],"references":["热门风格关键词1","热门风格关键词2","热门风格关键词3"]}}
"""
    bot = ensure_bot()
    bot.page.get(CHATGPT_URL)
    if reference_images:
        STATE.append_log(f"已打开 ChatGPT，正在结合 {len(reference_images)} 张参考图搜索热门表情包风格。")
    else:
        STATE.append_log("已打开 ChatGPT，正在让 ChatGPT 搜索最新热门表情包风格。")
    answer = bot.ask(prompt, reference_images=reference_images)
    answer = (answer or "").strip()
    if answer.startswith("[image answer"):
        answers = bot._assistant_elements()
        if answers:
            answer = answers[-1].text.strip() or answer
    try:
        data = extract_json(answer)
        theme = (data.get("theme") or "").strip()
        meanings = [clamp_text(str(x), 4) for x in data.get("meanings", []) if clamp_text(str(x), 4)]
        references = [str(x).strip() for x in data.get("references", []) if str(x).strip()]
        if theme:
            return {
                "theme": clamp_text(theme, 80),
                "meanings": meanings[:24],
                "references": references,
                "raw": answer,
            }
    except Exception as e:
        STATE.append_log(f"ChatGPT 热门风格结果不是标准 JSON，已改用摘要回退：{e}")
        STATE.append_log(f"待解析内容前200字：{answer[:200]!r}")

    fallback_refs = [clamp_text(answer, 60)] if answer else []
    return {
        "theme": build_theme_prompt(keyword, references=fallback_refs),
        "meanings": list(DEFAULT_MEANINGS),
        "references": fallback_refs,
        "raw": answer,
    }


def run_search_theme(keyword, supplied_meanings=None, reference_images=None):
    try:
        with STATE.lock:
            STATE.state = "searching"
            STATE.current = "联网参考热门"
            STATE.hot_theme = None
        result = chatgpt_hot_theme(keyword, reference_images=reference_images)
        if supplied_meanings and not result.get("meanings"):
            result["meanings"] = supplied_meanings
        with STATE.lock:
            STATE.hot_theme = {
                "theme": result.get("theme") or "",
                "meanings": result.get("meanings") or [],
                "references": result.get("references") or [],
            }
            STATE.state = "idle"
            STATE.current = "热门参考已回填"
        STATE.append_log("联网参考热门已完成，已回填总需求和含义词。")
    except Exception as e:
        with STATE.lock:
            STATE.state = "error"
            STATE.current = "联网参考热门失败"
        STATE.append_log(f"联网参考热门失败：{e}")


class BatchState:
    def __init__(self):
        self.lock = Lock()
        self.state = "idle"
        self.done = 0
        self.total = 0
        self.current = ""
        self.log = []
        self.images = []
        self.metadata = None
        self.hot_theme = None
        self.theme = ""
        self.worker = None
        self.bot = None
        self.config = None

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "done": self.done,
                "total": self.total,
                "current": self.current,
                "log": self.log[-200:],
                "images": list(self.images),
                "metadata": dict(self.metadata) if self.metadata else None,
                "hot_theme": dict(self.hot_theme) if self.hot_theme else None,
                "theme": self.theme,
            }

    def append_log(self, text):
        with self.lock:
            self.log.append(text)


STATE = BatchState()


def history_file():
    path = Path(STATE.config["image_dir"]) / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_history_items():
    path = history_file()
    if not path.exists():
        return []
    try:
        data = loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return data
    return data.get("history", []) if isinstance(data, dict) else []


def write_history_items(items):
    history_file().write_text(dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def history_summary(item):
    metadata = item.get("metadata") or {}
    images = item.get("images") or []
    return {
        "id": item.get("id"),
        "created_at": item.get("created_at", ""),
        "name": metadata.get("name") or item.get("theme") or "未命名历史",
        "theme": item.get("theme") or "",
        "image_count": len(images),
    }


def current_history_item():
    with STATE.lock:
        if not STATE.images and not STATE.metadata:
            return None
        metadata = dict(STATE.metadata) if STATE.metadata else None
        images = [dict(img) for img in STATE.images]
        log = list(STATE.log)
        hot_theme = dict(STATE.hot_theme) if STATE.hot_theme else None
        theme = STATE.theme
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return {
        "id": stamp,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": theme,
        "metadata": metadata,
        "images": images,
        "log": log,
        "hot_theme": hot_theme,
    }


def save_current_history():
    item = current_history_item()
    if not item:
        return None
    items = load_history_items()
    items.insert(0, item)
    write_history_items(items[:80])
    return item


def clear_current_state():
    with STATE.lock:
        STATE.state = "idle"
        STATE.done = 0
        STATE.total = 0
        STATE.current = ""
        STATE.log = []
        STATE.images = []
        STATE.metadata = None
        STATE.hot_theme = None
        STATE.theme = ""


def load_history_state(history_id):
    for item in load_history_items():
        if item.get("id") == history_id:
            with STATE.lock:
                STATE.state = "idle"
                STATE.done = len(item.get("images") or [])
                STATE.total = len(item.get("images") or [])
                STATE.current = "已加载历史记录"
                STATE.log = list(item.get("log") or [])
                STATE.images = [dict(img) for img in item.get("images") or []]
                STATE.metadata = dict(item.get("metadata") or {}) if item.get("metadata") else None
                STATE.hot_theme = dict(item.get("hot_theme") or {}) if item.get("hot_theme") else None
                STATE.theme = item.get("theme") or ""
            return item
    return None


def ensure_bot():
    if STATE.bot is not None and not bot_connected(STATE.bot):
        STATE.append_log("浏览器页面连接已断开，已准备重新连接。")
        STATE.bot = None

    if STATE.bot is None:
        STATE.bot = ChatGPTWebClient(
            user_data_path=STATE.config["user_data_path"],
            address=STATE.config["address"],
            timeout=STATE.config["timeout"],
            image_dir=STATE.config["image_dir"],
        )
    return STATE.bot


def bot_connected(bot):
    try:
        bot.page.run_js("return document.readyState;")
        return True
    except Exception:
        return False


def reconnect_bot(open_chatgpt=True):
    if STATE.bot is not None and bot_connected(STATE.bot):
        bot = STATE.bot
    else:
        STATE.bot = None
        bot = ensure_bot()

    if open_chatgpt:
        bot.page.get(CHATGPT_URL)
    return bot


def build_prompts(theme, templates):
    templates = templates if len(templates or []) >= 8 else DEFAULT_TEMPLATES
    return [template.replace("{theme}", theme) for template in templates]


def image_url(path):
    p = Path(path).resolve()
    try:
        rel = p.relative_to(prepared_dir().resolve())
        return "/images/wechat_ready/" + rel.as_posix()
    except ValueError:
        pass
    return "/images/" + p.name


ASSET_SPECS = {
    "sticker": {"size": (240, 240), "suffix": ".png", "max_kb": 500, "fit": "contain"},
    "banner": {"size": (750, 400), "suffix": ".png", "max_kb": 500, "fit": "cover"},
    "cover": {"size": (240, 240), "suffix": ".png", "max_kb": 500, "fit": "contain"},
    "icon": {"size": (50, 50), "suffix": ".png", "max_kb": 100, "fit": "contain"},
    "reward_guide": {"size": (750, 560), "suffix": ".png", "max_kb": 500, "fit": "cover"},
    "reward_thanks": {"size": (750, 750), "suffix": ".png", "max_kb": 500, "fit": "cover"},
}


def asset_kind(label):
    if "赞赏引导图" in label:
        return "reward_guide"
    if "赞赏致谢图" in label:
        return "reward_thanks"
    if "横幅" in label:
        return "banner"
    if "封面" in label:
        return "cover"
    if "图标" in label:
        return "icon"
    return "sticker"


def prepared_dir():
    path = Path(STATE.config["image_dir"]) / "wechat_ready"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_image_for_wechat(src_path, label, index):
    kind = asset_kind(label)
    spec = ASSET_SPECS[kind]
    src = Path(src_path)
    out = prepared_dir() / f"{index:02d}_{kind}_{src.stem}{spec['suffix']}"
    image = Image.open(src)
    image = ImageOps.exif_transpose(image)

    if spec["fit"] == "cover":
        image = ImageOps.fit(image.convert("RGB"), spec["size"], method=Image.Resampling.LANCZOS)
        image.save(out, "PNG", optimize=True)
    else:
        image = image.convert("RGBA")
        image.thumbnail(spec["size"], Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", spec["size"], (255, 255, 255, 0))
        left = (spec["size"][0] - image.width) // 2
        top = (spec["size"][1] - image.height) // 2
        canvas.alpha_composite(image, (left, top))
        canvas.save(out, "PNG", optimize=True)

    return str(out.absolute())


def split_sprite_sheet(src_path, rows=4, cols=6):
    src = Path(src_path)
    image = Image.open(src)
    image = ImageOps.exif_transpose(image).convert("RGBA")
    width, height = image.size
    cell_w = width // cols
    cell_h = height // rows
    out_dir = prepared_dir() / f"split_{src.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for row in range(rows):
        for col in range(cols):
            index = row * cols + col + 1
            left = col * cell_w
            top = row * cell_h
            crop = image.crop((left, top, left + cell_w, top + cell_h))
            crop = ImageOps.fit(crop, (240, 240), method=Image.Resampling.LANCZOS)
            out = out_dir / f"{index:02d}_sticker.png"
            crop.save(out, "PNG", optimize=True)
            paths.append(str(out.absolute()))

    return paths


def label_from_template(template, index):
    if "：" in template:
        return template.split("：", 1)[0]
    if ":" in template:
        return template.split(":", 1)[0]
    return f"第 {index} 张"


def reference_style_note(reference_images):
    if not reference_images:
        return ""
    return (
        "\n已上传参考图。请先分析参考图的题材类型、主体、画风、配色、构图、中文文字风格和微信使用场景；"
        "生成文案和图片时只借鉴风格与表达方式，不复制原图具体IP、署名、水印、人物脸或已有表情包名称。"
    )


def run_batch(theme, templates, mode="sprite24", sticker_prompt="", supplied_meanings=None, reference_images=None):
    clean_theme, theme_meanings = split_theme_and_meanings(theme)
    reference_images = [str(Path(path).resolve()) for path in (reference_images or []) if Path(path).exists()]
    theme_for_prompt = clean_theme + reference_style_note(reference_images)
    supplied_meanings = [clamp_text(item, 4) for item in (supplied_meanings or []) if clamp_text(item, 4)]
    if mode == "sprite24":
        asset_templates = templates[:5] if len(templates or []) >= 5 else DEFAULT_TEMPLATES[-5:]
        sprite_template = sticker_prompt or SPRITE_SHEET_PROMPT
        active_templates = [sprite_template, *asset_templates]
        prompts = [sprite_template.replace("{theme}", theme_for_prompt), *[template.replace("{theme}", theme_for_prompt) for template in asset_templates]]
    else:
        active_templates = templates if len(templates or []) >= 8 else DEFAULT_TEMPLATES
        prompts = build_prompts(theme_for_prompt, active_templates)
    with STATE.lock:
        STATE.state = "running"
        STATE.done = 0
        STATE.total = len(prompts)
        STATE.current = "生成提交文案"
        STATE.images = []
        STATE.log = []
        STATE.metadata = None
        STATE.hot_theme = None
        STATE.theme = clean_theme

    try:
        bot = ensure_bot()
        bot.page.get(CHATGPT_URL)
        STATE.append_log("已打开 ChatGPT。如果未登录，请先在浏览器中登录。")
        if reference_images:
            STATE.append_log(f"已加载参考图 {len(reference_images)} 张，将用于分析画风、主体和文案风格。")
        sleep(2)

        copy_answer = bot.ask(COPY_PROMPT.format(theme=theme_for_prompt), reference_images=reference_images)
        metadata_source = extract_json(copy_answer)
        if supplied_meanings or theme_meanings:
            metadata_source["sticker_meanings"] = supplied_meanings or theme_meanings
        metadata = normalize_metadata(metadata_source)
        with STATE.lock:
            STATE.metadata = metadata
        STATE.append_log(f"文案已生成：{metadata['name']} / {metadata['intro']}")
        if mode == "sprite24" and prompts:
            prompts[0] = apply_meanings_to_prompt(prompts[0], metadata.get("sticker_meanings") or DEFAULT_MEANINGS)
        elif mode != "sprite24":
            sticker_meanings = metadata.get("sticker_meanings") or DEFAULT_MEANINGS
            prompts = [
                apply_meanings_to_prompt(prompt, [sticker_meanings[i - 1]])
                if asset_kind(label_from_template(active_templates[i - 1], i)) == "sticker" and i <= len(sticker_meanings)
                else prompt
                for i, prompt in enumerate(prompts, start=1)
            ]

        for index, prompt in enumerate(prompts, start=1):
            template = active_templates[index - 1]
            label = label_from_template(template, index)
            if mode == "sprite24" and index == 1:
                label = "24宫格表情图"
            with STATE.lock:
                STATE.current = label
            STATE.append_log(f"[{index}/{len(prompts)}] 发送提示词：{prompt}")

            def image_progress(message, step=index, total=len(prompts), current_label=label):
                STATE.append_log(f"[{step}/{total}] 等待图片完成（{current_label}）：{message}")

            answer = bot.ask(prompt, expect_images=True, progress_callback=image_progress, reference_images=reference_images)
            new_paths = list(bot.last_saved_images)

            if mode == "sprite24" and index == 1:
                if not new_paths:
                    raise RuntimeError("24宫格表情图未获取到图片，无法裁剪。")
                split_paths = split_sprite_sheet(new_paths[0])
                spec = ASSET_SPECS["sticker"]
                with STATE.lock:
                    for sticker_index, ready_path in enumerate(split_paths, start=1):
                        STATE.images.append({
                            "label": f"表情图{sticker_index:02d}",
                            "kind": "sticker",
                            "path": ready_path,
                            "source_path": new_paths[0],
                            "url": image_url(ready_path),
                            "size": f"{spec['size'][0]}*{spec['size'][1]}",
                            "format": "PNG",
                            "max_kb": spec["max_kb"],
                        })
                    STATE.done = index
                STATE.append_log(f"[{index}/{len(prompts)}] 已把24宫格裁剪为24张微信规格表情图。文本回复：{answer or '[无文本]'}")
                continue

            with STATE.lock:
                for path in new_paths:
                    ready_path = prepare_image_for_wechat(path, label, len(STATE.images) + 1)
                    kind = asset_kind(label)
                    spec = ASSET_SPECS[kind]
                    STATE.images.append({
                        "label": label,
                        "kind": kind,
                        "path": ready_path,
                        "source_path": path,
                        "url": image_url(ready_path),
                        "size": f"{spec['size'][0]}*{spec['size'][1]}",
                        "format": "PNG",
                        "max_kb": spec["max_kb"],
                    })
                STATE.done = index
            STATE.append_log(f"[{index}/{len(prompts)}] 完成，已转为微信规格。文本回复：{answer or '[无文本]'}")

        with STATE.lock:
            STATE.state = "done"
            STATE.current = "全部完成"
        STATE.append_log("全部提示词已完成，图片已汇总。")

    except Exception as e:
        with STATE.lock:
            STATE.state = "error"
            STATE.current = "出错"
        STATE.append_log(f"错误：{e}")


def pick_image_paths(images):
    result = {
        "stickers": [],
        "banner": None,
        "cover": None,
        "icon": None,
        "reward_guide": None,
        "reward_thanks": None,
    }
    for img in images:
        label = img.get("label", "")
        kind = img.get("kind") or asset_kind(label)
        path = img.get("path")
        if not path:
            continue
        if kind == "banner":
            result["banner"] = path
        elif kind == "cover":
            result["cover"] = path
        elif kind == "icon":
            result["icon"] = path
        elif kind == "reward_guide":
            result["reward_guide"] = path
        elif kind == "reward_thanks":
            result["reward_thanks"] = path
        else:
            result["stickers"].append(path)

    if not result["stickers"]:
        result["stickers"] = [img["path"] for img in images if img.get("path")]
    return result


def run_sync_wechat(metadata):
    try:
        with STATE.lock:
            STATE.state = "syncing"
            STATE.current = "同步微信表情开放平台"
            if metadata:
                STATE.metadata = normalize_metadata({**(STATE.metadata or {}), **metadata})
            metadata = dict(STATE.metadata or {})
            images = list(STATE.images)

        if not metadata:
            raise RuntimeError("请先生成或填写提交文案。")

        bot = ensure_bot()
        try:
            page = bot.page.browser.new_tab(WECHAT_STICKER_URL)
        except Exception:
            bot.page.get(WECHAT_STICKER_URL)
            page = bot.page

        STATE.append_log("已打开微信表情开放平台，请先完成登录。")
        with STATE.lock:
            STATE.state = "idle"
            STATE.current = "微信平台已打开，请登录后可再次同步"
        sleep(5)
        fill_wechat_form(page, metadata, pick_image_paths(images))

        with STATE.lock:
            STATE.state = "done"
            STATE.current = "微信平台已预填"
        STATE.append_log("已尽量完成自动填写。请在微信平台页面人工复核后再提交。")

    except Exception as e:
        with STATE.lock:
            STATE.state = "idle"
            STATE.current = "同步未完成，可登录后重试"
        STATE.append_log(f"同步未完成，可登录或手动处理后再次点击同步：{e}")


def js_string(value):
    return dumps(value or "", ensure_ascii=False)


def meaning_input_count(page):
    js = """
    return [...document.querySelectorAll(".sticker_imgs .meaning-word input, .meaning-word input")]
      .filter(el => el.offsetParent !== null || el.getClientRects().length > 0)
      .length;
    """
    try:
        return int(page.run_js(js) or 0)
    except Exception:
        return 0


def current_sticker_count(page):
    js = """
    const meaningCount = [...document.querySelectorAll(".sticker_imgs .meaning-word input, .meaning-word input")]
      .filter(el => el.offsetParent !== null || el.getClientRects().length > 0)
      .length;
    const blobImageCount = [...document.querySelectorAll(".sticker_imgs img")]
      .filter(img => (img.src || "").startsWith("blob:"))
      .length;
    return Math.max(meaningCount, blobImageCount);
    """
    try:
        return int(page.run_js(js) or 0)
    except Exception:
        return meaning_input_count(page)


def wait_sticker_count(page, expected_count, timeout=45):
    for _ in range(timeout):
        count = current_sticker_count(page)
        if count >= expected_count:
            return count
        sleep(1)
    return current_sticker_count(page)


def wait_meaning_inputs(page, expected_count, timeout=90):
    for _ in range(timeout):
        count = meaning_input_count(page)
        if count >= expected_count:
            return count
        sleep(1)
    return meaning_input_count(page)


def image_file_inputs(page):
    result = []
    for ele in page.eles("css:input[type='file']", timeout=2):
        accept = (ele.attr("accept") or "").lower()
        if "image/" in accept and "pdf" not in accept:
            result.append(ele)
    return result


def sticker_upload_input(page):
    scoped = page.eles("css:.sticker_imgs input[type='file']", timeout=2)
    candidates = []
    for ele in scoped:
        accept = (ele.attr("accept") or "").lower()
        if "image/" in accept and "pdf" not in accept:
            candidates.append(ele)
    if candidates:
        multi = [ele for ele in candidates if ele.attr("multiple")]
        return (multi or candidates)[-1]

    inputs = image_file_inputs(page)
    multi = [ele for ele in inputs if ele.attr("multiple")]
    return (multi or inputs[:1] or [None])[0]


def non_sticker_upload_inputs(page):
    sticker_ele = sticker_upload_input(page)
    result = []
    for ele in image_file_inputs(page):
        if sticker_ele and ele == sticker_ele:
            continue
        if ele.attr("multiple"):
            continue
        result.append(ele)
    return result


def fill_sticker_meanings(page, meanings):
    js = f"""
    const meanings = {dumps(meanings or [], ensure_ascii=False)};
    function setValue(el, value) {{
      if (!el || !value) return false;
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", {{bubbles:true}}));
      el.dispatchEvent(new Event("change", {{bubbles:true}}));
      return true;
    }}
    const inputs = [...document.querySelectorAll(".sticker_imgs .meaning-word input, .meaning-word input")]
      .filter(el => el.offsetParent !== null || el.getClientRects().length > 0);
    inputs.forEach((input, index) => setValue(input, meanings[index] || meanings[meanings.length - 1] || ""));
    return inputs.length;
    """
    try:
        return int(page.run_js(js) or 0)
    except Exception as e:
        STATE.append_log(f"含义词填写失败，需要手动检查，原因：{e}")
        return 0


def fill_wechat_form(page, meta, paths):
    js = f"""
    const meta = {{
      name: {js_string(meta.get("name"))},
      intro: {js_string(meta.get("intro"))},
      copyright: {js_string(meta.get("copyright"))},
      character: {js_string(meta.get("character"))},
      styles: {dumps(meta.get("style_tags") or [], ensure_ascii=False)},
      themes: {dumps(meta.get("theme_tags") or [], ensure_ascii=False)},
      meanings: {dumps(meta.get("sticker_meanings") or [], ensure_ascii=False)},
      rewardEnabled: {dumps(meta.get("reward_enabled", True))},
      rewardGuideText: {js_string(meta.get("reward_guide_text"))}
    }};

    function setValue(el, value) {{
      if (!el || !value) return false;
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", {{bubbles:true}}));
      el.dispatchEvent(new Event("change", {{bubbles:true}}));
      return true;
    }}
    function byPlaceholder(text) {{
      return [...document.querySelectorAll("input, textarea")].find(el => (el.placeholder || "").includes(text));
    }}
    function byTitle(title) {{
      const titles = [...document.querySelectorAll(".title, label, span, div")]
        .filter(el => (el.innerText || "").trim() === title);
      for (const titleEl of titles) {{
        const formItem = titleEl.closest(".form_item") || titleEl.closest("section") || titleEl.parentElement;
        const input = formItem?.querySelector("input:not([type='file']):not([type='checkbox']):not([type='radio']), textarea");
        if (input) return input;
      }}
      return null;
    }}
    setValue(byPlaceholder("表情专辑名称"), meta.name);
    setValue(byPlaceholder("特点和故事"), meta.intro);
    setValue(byPlaceholder("版权信息"), meta.copyright);
    setValue(byTitle("赞赏引导语"), meta.rewardGuideText);

    function clickText(text, checked = true) {{
      const nodes = [...document.querySelectorAll("label, span, div, p")];
      const node = nodes.find(n => (n.innerText || "").trim() === text);
      if (node) {{
        const input = node.querySelector("input") || node.closest("label")?.querySelector("input");
        if (input && typeof input.checked === "boolean") {{
          if (input.checked !== checked) input.click();
        }} else {{
          node.click();
        }}
        return true;
      }}
      return false;
    }}
    clickText("静态表情");
    clickText("免费");
    clickText("中国大陆");
    for (const s of meta.styles) clickText(s);
    for (const t of meta.themes) clickText(t);
    clickText(meta.character);
    clickText("接受赞赏", meta.rewardEnabled);

    """
    page.run_js(js)
    sleep(1)

    sticker_files = [str(Path(p).absolute()) for p in paths["stickers"] if p and Path(p).exists()]
    base_sticker_count = current_sticker_count(page)
    if sticker_files:
        try:
            input_ele = sticker_upload_input(page)
            if not input_ele:
                raise RuntimeError("未找到表情上传 input")
            input_ele.input(sticker_files)
            STATE.append_log(f"已一次性选择上传 {len(sticker_files)} 张表情图。")
        except Exception as e:
            STATE.append_log(f"表情图批量上传失败，需要手动上传并复核，原因：{e}")

    asset_inputs = non_sticker_upload_inputs(page)[:5]
    upload_sets = [
        [paths["banner"]] if paths["banner"] else [],
        [paths["cover"]] if paths["cover"] else [],
        [paths["icon"]] if paths["icon"] else [],
        [paths["reward_guide"]] if meta.get("reward_enabled", True) and paths["reward_guide"] else [],
        [paths["reward_thanks"]] if meta.get("reward_enabled", True) and paths["reward_thanks"] else [],
    ]
    for input_ele, files in zip(asset_inputs, upload_sets):
        files = [str(Path(p).absolute()) for p in files if p and Path(p).exists()]
        if not files:
            continue
        try:
            input_ele.input(files)
            STATE.append_log(f"已上传：{', '.join(files)}")
            sleep(1)
        except Exception as e:
            STATE.append_log(f"上传失败，需要手动上传：{files}，原因：{e}")

    page.run_js(js)
    if sticker_files:
        expected_count = base_sticker_count + len(sticker_files)
        wait_meaning_inputs(page, expected_count, timeout=120)
    filled_count = fill_sticker_meanings(page, meta.get("sticker_meanings") or [])
    if sticker_files and filled_count < len(sticker_files):
        STATE.append_log(f"含义词输入框数量为 {filled_count}，少于表情图数量 {len(sticker_files)}，请在页面人工复核未出现的表情。")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_text(INDEX_HTML, "text/html; charset=utf-8")
        elif parsed.path == "/api/status":
            self.send_json(STATE.snapshot())
        elif parsed.path == "/api/history":
            self.send_json({"history": [history_summary(item) for item in load_history_items()]})
        elif parsed.path.startswith("/images/"):
            self.send_image(parsed.path)
        elif parsed.path == "/download/all":
            self.send_zip()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/start":
            self.start_batch()
        elif parsed.path == "/api/sync_wechat":
            self.sync_wechat()
        elif parsed.path == "/api/reconnect":
            self.reconnect()
        elif parsed.path == "/api/search_theme":
            self.search_theme()
        elif parsed.path == "/api/upload_references":
            self.upload_references()
        elif parsed.path == "/api/save_history":
            self.save_history()
        elif parsed.path == "/api/clear_current":
            self.clear_current()
        elif parsed.path == "/api/load_history":
            self.load_history()
        else:
            self.send_error(404)

    def assert_not_busy(self):
        if STATE.state in ("running", "syncing", "reconnecting", "searching"):
            self.send_json({"ok": False, "error": "task is already running"}, 409)
            return False
        return True

    def save_history(self):
        if not self.assert_not_busy():
            return
        item = save_current_history()
        if not item:
            self.send_json({"ok": False, "error": "当前没有可保存的生成结果"}, 400)
            return
        self.send_json({"ok": True, "item": history_summary(item)})

    def clear_current(self):
        if not self.assert_not_busy():
            return
        clear_current_state()
        self.send_json({"ok": True})

    def load_history(self):
        if not self.assert_not_busy():
            return
        data = self.read_json()
        item = load_history_state(data.get("id") or "")
        if not item:
            self.send_json({"ok": False, "error": "历史记录不存在"}, 404)
            return
        self.send_json({"ok": True, "item": history_summary(item)})

    def start_batch(self):
        data = self.read_json()
        theme = data.get("theme", "").strip()
        clean_theme, _ = split_theme_and_meanings(theme)
        templates = [x.strip() for x in data.get("templates", []) if x.strip()]
        mode = data.get("mode", "sprite24")
        sticker_prompt = data.get("stickerPrompt", "").strip()
        sticker_meanings = [x.strip() for x in data.get("stickerMeanings", []) if x.strip()]
        reference_images = [x for x in data.get("referenceImages", []) if x and Path(x).exists()]
        if not clean_theme:
            self.send_json({"ok": False, "error": "theme is required"}, 400)
            return
        with STATE.lock:
            if STATE.state in ("running", "syncing", "reconnecting", "searching"):
                self.send_json({"ok": False, "error": "task is already running"}, 409)
                return
            STATE.state = "running"
            STATE.current = "启动生成任务"
            STATE.worker = Thread(target=run_batch, args=(theme, templates, mode, sticker_prompt, sticker_meanings, reference_images), daemon=True)
            STATE.worker.start()
        self.send_json({"ok": True})

    def upload_references(self):
        data = self.read_json()
        files = data.get("files") or []
        if len(files) > 6:
            self.send_json({"ok": False, "error": "参考图最多上传6张"}, 400)
            return

        ref_dir = Path(STATE.config["image_dir"]) / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        allowed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
        }
        for index, item in enumerate(files, start=1):
            data_url = item.get("data") or ""
            content_type = (item.get("type") or "").lower()
            suffix = allowed.get(content_type)
            if not suffix or "," not in data_url:
                continue
            try:
                raw = b64decode(data_url.split(",", 1)[1], validate=True)
            except Exception:
                continue
            if not raw or len(raw) > 8 * 1024 * 1024:
                continue
            path = ref_dir / f"reference_{int(time())}_{index}{suffix}"
            path.write_bytes(raw)
            saved.append(str(path.resolve()))

        if not saved:
            self.send_json({"ok": False, "error": "没有可用的参考图"}, 400)
            return
        self.send_json({"ok": True, "paths": saved})

    def reconnect(self):
        with STATE.lock:
            if STATE.state in ("running", "syncing", "reconnecting", "searching"):
                self.send_json({"ok": False, "error": "task is already running"}, 409)
                return
            STATE.state = "reconnecting"
            STATE.current = "手动重连浏览器"
        try:
            reconnect_bot(open_chatgpt=True)
            with STATE.lock:
                if STATE.state in ("error", "reconnecting"):
                    STATE.state = "idle"
                STATE.current = "浏览器已重连"
            STATE.append_log("已手动重连浏览器页面，并打开 ChatGPT。")
            self.send_json({"ok": True})
        except Exception as e:
            with STATE.lock:
                STATE.state = "error"
                STATE.current = "重连失败"
            STATE.append_log(f"手动重连失败：{e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def sync_wechat(self):
        data = self.read_json()
        metadata = data.get("metadata") or {}
        with STATE.lock:
            if STATE.state in ("running", "syncing", "reconnecting", "searching"):
                self.send_json({"ok": False, "error": "task is already running"}, 409)
                return
            STATE.state = "syncing"
            STATE.current = "同步微信表情开放平台"
            STATE.worker = Thread(target=run_sync_wechat, args=(metadata,), daemon=True)
            STATE.worker.start()
        self.send_json({"ok": True})

    def search_theme(self):
        data = self.read_json()
        keyword = (data.get("keyword") or "热门微信表情包").strip()[:80]
        sticker_meanings = [x.strip() for x in data.get("stickerMeanings", []) if x.strip()]
        reference_images = [x for x in data.get("referenceImages", []) if x and Path(x).exists()]
        with STATE.lock:
            if STATE.state in ("running", "syncing", "reconnecting", "searching"):
                self.send_json({"ok": False, "error": "task is already running"}, 409)
                return
            STATE.state = "searching"
            STATE.current = "联网参考热门"
            STATE.hot_theme = None
            STATE.worker = Thread(target=run_search_theme, args=(keyword, sticker_meanings, reference_images), daemon=True)
            STATE.worker.start()
        self.send_json({"ok": True})

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return loads(self.rfile.read(length).decode("utf-8") or "{}")

    def send_text(self, text, content_type="text/plain; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_text(dumps(data, ensure_ascii=False), "application/json; charset=utf-8", status)

    def send_image(self, request_path):
        rel = unquote(request_path).removeprefix("/images/").replace("\\", "/")
        parts = [p for p in rel.split("/") if p]
        if len(parts) >= 2 and parts[0] == "wechat_ready":
            base = (Path(STATE.config["image_dir"]) / "wechat_ready").resolve()
            path = (base / Path(*parts[1:])).resolve()
            if base not in path.parents and path != base:
                self.send_error(404)
                return
        else:
            path = Path(STATE.config["image_dir"]) / Path(rel).name
        if not path.exists():
            self.send_error(404)
            return
        content_type = guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_zip(self):
        snapshot = STATE.snapshot()
        buf = BytesIO()
        with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
            used = set()
            for index, img in enumerate(snapshot["images"], start=1):
                path = Path(img["path"])
                if not path.exists():
                    continue
                label = str(img.get("label") or f"image_{index}").replace("/", "_").replace("\\", "_")
                arcname = f"{index:02d}_{label}{path.suffix or '.png'}"
                while arcname in used:
                    arcname = f"{index:02d}_{label}_{len(used)}{path.suffix or '.png'}"
                used.add(arcname)
                zf.write(path, arcname)
        body = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="wechat_sticker_assets.zip"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main():
    parser = ArgumentParser(description="Local WeChat sticker submission preparation console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--address", help="Existing Chrome debug address, for example 127.0.0.1:9222.")
    parser.add_argument("--user-data-path", default="chatgpt_profile")
    parser.add_argument("--image-dir", default="chatgpt_images")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    Path(args.image_dir).mkdir(parents=True, exist_ok=True)
    STATE.config = {
        "address": args.address,
        "user_data_path": args.user_data_path,
        "image_dir": args.image_dir,
        "timeout": args.timeout,
    }

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Web console: {url}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
