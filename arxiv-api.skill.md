---
title: arXiv论文数据API
description: 通过URL参数获取JSON格式的arXiv论文数据
trigger: 用户请求通过URL参数获取arXiv论文数据，或需要实现类似功能时
---

## 技能说明

本技能提供通过URL参数获取JSON格式arXiv论文数据的API功能。当URL中包含特定参数时，页面直接返回JSON数据而非渲染网页。

## 支持的URL参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `category` | string | 按arXiv类别筛选 | `cs.CV`, `cs.AI`, `all` |
| `json` | string | API模式别名，作用同category | `cs.CV` |
| `author` | string | 按作者姓名筛选 | `Smith` |
| `keywords` | string | 按关键词筛选，多个用逗号分隔 | `vision,learning` |

## 筛选逻辑

### 布尔表达式
```
category AND (keywords OR author)
```

### 详细说明
- **category**: 硬筛选，只返回指定类别的论文
- **keywords**: 在论文标题和摘要中搜索，多个关键词之间是"或"关系
- **author**: 在作者字段中搜索
- **keywords与author**: 两者是"或"关系，任一匹配即可

### 匹配规则
- 关键词之间：`some` - 任意一个关键词匹配即匹配
- 作者之间：`some` - 任意一个作者匹配即匹配
- 关键词与作者之间：`||` - 关键词匹配或作者匹配即匹配

## API行为

### JSON模式（提供URL参数）
- 直接返回JSON数据，不渲染网页
- **只返回匹配的论文**
- 匹配的论文按category筛选后返回

### 网页模式（无URL参数）
- 保持原有网页渲染逻辑
- 显示所有论文，匹配的排前面

## JSON响应格式

```json
{
  "category": "cs.CV",
  "author": "Smith",
  "keywords": ["deep", "learning"],
  "count": 10,
  "papers": [
    {
      "id": "2401.00001",
      "title": "论文标题",
      "authors": "作者1, 作者2",
      "categories": ["cs.CV", "cs.AI"],
      "summary": "论文摘要（TL;DR）",
      "date": "2024-01-01",
      "url": "https://arxiv.org/abs/2401.00001"
    }
  ]
}
```

## 使用示例

```javascript
// 按类别获取
https://example.com/?category=cs.CV

// 按作者获取
https://example.com/?author=Smith

// 按关键词获取
https://example.com/?keywords=vision,learning

// 组合筛选
https://example.com/?category=cs.CV&author=Smith&keywords=deep

// 使用json参数（API风格）
https://example.com/?json=cs.CV&keywords=learning
```

## 缓存问题

如遇304缓存问题，可添加时间戳参数绕过：
```
?category=cs.CV&_t=1234567890
```

## 实现要点

1. 在页面加载时解析URL参数
2. 数据加载完成后应用筛选条件
3. 复用现有的筛选逻辑方法
4. JSON模式下清空页面内容并输出纯JSON
5. 网页模式不更新URL参数，避免影响现有功能

## 相关文件

- `js/app.js`: 主逻辑文件，包含URL参数解析和JSON输出功能
