
<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_qzone?name=astrbot_plugin_qzone&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_qzone

_✨ [astrbot](https://github.com/AstrBotDevs/AstrBot) QQ空间对接插件 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Zhalslar-blue)](https://github.com/Zhalslar)

</div>

## 🤝 介绍

QQ空间对接插件, 可自动发说说、表白墙投稿审核

## 📦 安装

- 直接在astrbot的插件市场搜索astrbot_plugin_qzone，点击安装，等待完成即可

- 也可以克隆源码到插件文件夹：

```bash
# 克隆仓库到插件目录
cd /AstrBot/data/plugins
git clone https://github.com/Zhalslar/astrbot_plugin_qzone

# 控制台重启AstrBot
```

## ⌨️ 配置

请前往插件配置面板进行配置

## 🐔 使用说明

### 命令表

| 命令       | 参数              | 说明                              |
|------------|-------------------|---------------------------------|
| 发说说     | 文字+图片(可选)    | 直接发布说说，无需管理员审核      |
| 投稿       | 文字+图片(可选)    | 投稿内容，提交管理员审核          |
| 查看稿件   | 稿件ID（整数）     | 查询指定稿件内容                  |
| 通过       | (引用稿件消息)     | 审核通过引用的稿件，发布到QQ空间  |
| 不通过     | (引用稿件消息) + 理由（可选） | 审核不通过引用的稿件，并告知原因 |

### 效果图

![257528e19908e70160afde6f0dd6b9d2](https://github.com/user-attachments/assets/7aa706c2-6c50-4740-b57b-e61b7a232adf)

## 🤝 TODO

- [x] 发说说
- [x] 校园表白墙功能：投稿、审核投稿
- [ ] 空间点赞、评论

## 👥 贡献指南

- 🌟 Star 这个项目！（点右上角的星星，感谢支持！）
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码

## 📌 注意事项

- 想第一时间得到反馈的可以来作者的插件反馈群（QQ群）：460973561（不点star不给进）
