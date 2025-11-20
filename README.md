
<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_qzone?name=astrbot_plugin_qzone&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_qzone

_✨ [astrbot](https://github.com/AstrBotDevs/AstrBot) QQ空间对接插件 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Zhalslar-blue)](https://github.com/Zhalslar)

</div>

> **警告**：V2.0.0+ 已上线公测，llm模块开发中，功能尚处于测试阶段，请勿用于生产环境。

## 🤝 介绍

QQ空间对接插件, 可自动发说说、表白墙投稿审核、查看说说、点赞、评论等

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

| 命令   | 参数                      | 说明                 | 权限要求 |
| ---- | ----------------------- | ------------------ | ---- |
| 发说说  | 文字 + 图片（可选）             | 立即发布一条 QQ 空间说说     | 管理员  |
| 写说说  | 主题（可选）+ 图片（可选）          | 由 AI 生成内容并立即发布     | 管理员  |
| 写稿   | 主题（可选）+ 图片（可选）          | AI 生成内容并保存为草稿（不发布） | 所有人  |
| 通过稿件 | 稿件 ID（默认最新）             | 将草稿/投稿审核通过并发布到空间   | 管理员  |
| 投稿   | 文字 + 图片（可选）             | 向表白墙投稿，进入待审核列表     | 所有人  |
| 查看稿件 | 稿件 ID（可选，默认最新）          | 查看本地数据库中指定稿件详情     | 所有人  |
| 拒绝稿件 | 稿件 ID + 原因（可选）          | 拒绝指定稿件并可附理由        | 管理员  |
| 查看说说 | @某人（可选）+ 序号/范围（可选，默认 1） | 拉取并渲染指定用户的第 N 条说说  | 所有人  |
| 点赞说说 | @某人（可选）+ 序号/范围（可选，默认 1） | 给指定说说点赞            | 所有人  |
| 评论说说 | @某人（可选）+ 序号/范围（可选，默认 1） | AI 生成评论并发送到对应说说    | 管理员  |
| 查看访客 | 无                       | 获取并渲染最近访客列表        | 管理员  |

- 特殊用法1：@群友 也可直接 @QQ号 ，这样就可以查看任何人的说说了
- 特殊用法2：序号不仅可以填数字，也能填范围，如“查看说说 2~5”，表示查看第 2~5 条说说”
- “查看说说 2”是指查看bot的好友们最近发的两条说说； 而“查看说说@某群友 2”则表示查看这位群友的第 2 条说说

### 效果图

![257528e19908e70160afde6f0dd6b9d2](https://github.com/user-attachments/assets/7aa706c2-6c50-4740-b57b-e61b7a232adf)

## 💡 TODO

- [x] 发说说
- [x] 校园表白墙功能：投稿、审核投稿
- [x] 点赞说说（接口显示成功，但实测点赞无效）
- [x] 评论说说
- [x] 定时自动发说说、日记
- [x] 定时自动评论、点赞好友的说说
- [x] LLM发说说
- [ ] LLM配图
- [ ] 更丰富的说说主题

## 👥 贡献指南

- 🌟 Star 这个项目！（点右上角的星星，感谢支持！）
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码

## 📌 注意事项

- 想第一时间得到反馈的可以来作者的插件反馈群（QQ群）：460973561（不点star不给进）

## 🤝 鸣谢

- 部分代码参考了[CampuxBot项目](https://github.com/idoknow/CampuxBot)，由作者之一的Soulter推荐

- [QQ 空间爬虫之爬取说说](https://kylingit.com/blog/qq-空间爬虫之爬取说说/)
  感谢这篇博客提供的思路。

- [一个QQ空间爬虫项目](https://github.com/wwwpf/QzoneExporter)

- [QQ空间](https://qzone.qq.com/) 网页显示本地数据时使用的样式与布局均来自于QQ空间。
