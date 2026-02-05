
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

## 🐔 使用说明（QzonePlugin）

### 一、基础说明

- **默认查看的是“好友动态流”**
- **@某人 / @QQ号**：表示查看该用户的 QQ 空间
- **序号从 0 开始**
  - `0` = 最新一条
  - `-1` = 最后一条
- 支持 **范围语法**：`2~5`
- 机器人在需要评论 / 回复时，会 **自动排除自己的评论**


### 二、命令一览表

| 命令 | 别名 | 权限 | 参数 | 功能说明 |
|----|----|----|----|----|
| 查看访客 | - | ADMIN | - | 查看 QQ 空间最近访客列表 |
| 看说说 | 查看说说 | ALL | `[@用户] [序号/范围]` | 查看说说（自动拉取完整详情） |
| 评说说 | 评论说说、读说说 | ALL | `[@用户] [序号/范围]` | 给说说评论（可配置自动点赞） |
| 赞说说 | - | ALL | `[@用户] [序号/范围]` | 给说说点赞 |
| 发说说 | - | ADMIN | `<文本> [图片]` | 立即发布一条说说 |
| 写说说 | 写稿 | ADMIN | `<主题> [图片]` | AI 生成草稿，保存为待审核稿件 |
| 删说说 | - | ADMIN | `<序号>` | 删除自己发布的说说 |
| 回评 | 回复评论 | ALL | `<稿件ID> [评论序号]` | 回复评论（默认回复最后一条非自己评论） |
| 投稿 | - | ALL | `<文本> [图片]` | 向表白墙投稿 |
| 匿名投稿 | - | ALL | `<文本> [图片]` | 匿名投稿到表白墙 |
| 看稿 | 查看稿件 | ADMIN | `[稿件ID]` | 查看稿件（默认最新） |
| 过稿 | 通过稿件、通过投稿 | ADMIN | `<稿件ID>` | 审核并发布稿件 |
| 拒稿 | 拒绝稿件、拒绝投稿 | ADMIN | `<稿件ID> [原因]` | 拒绝稿件 |
| 撤稿 | - | ALL | `<稿件ID>` | 撤回自己投稿的稿件 |

## 三、范围参数使用示例

```text
看说说
看说说 2
看说说 1~3
看说说 @某人
看说说 @某人 0
```

### 效果图

## 💡 TODO

- [x] 发说说
- [x] 校园表白墙功能：投稿、审核投稿
- [x] 点赞说说（接口显示成功，但实测点赞无效）
- [x] 评论说说
- [x] 定时自动发说说、日记
- [x] 定时自动评论、点赞好友的说说
- [x] LLM发说说
- [ ] LLM配图
- [ ] 自动上网冲浪写说说

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
