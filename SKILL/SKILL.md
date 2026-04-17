# arXiv论文数据API

## 触发条件
用户请求通过URL参数获取arXiv论文数据，或需要实现类似API功能时。

## 功能说明
通过URL参数获取JSON格式的arXiv论文数据。当URL中包含参数时，页面直接返回JSON而非渲染网页。

## URL参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `category` | arXiv类别 | `cs.CV`, `cs.AI`, `all` |
| `author` | 作者姓名 | `Smith` |
| `keywords` | 关键词，逗号分隔 | `vision,learning` |

## 筛选逻辑

```
category AND (keywords OR author)
```

- category: 硬筛选，只返回指定类别
- keywords: 在标题和摘要中搜索
- author: 在作者字段中搜索
- keywords与author是"或"关系

## 使用示例

```
?category=cs.CV
?author=Smith
?keywords=vision,learning
?category=cs.CV&author=Smith&keywords=deep
```

## JSON响应

```json
{
  "category": "cs.CV",
  "author": "Smith",
  "keywords": ["deep"],
  "count": 10,
  "papers": [
    {
      "id": "2401.00001",
      "title": "标题",
      "authors": "作者1, 作者2",
      "categories": ["cs.CV"],
      "summary": "摘要",
      "date": "2024-01-01",
      "url": "https://arxiv.org/abs/2401.00001"
    }
  ]
}
```

## 实现要点

1. 页面加载时解析URL参数
2. 数据加载后应用筛选
3. 复用现有筛选逻辑
4. JSON模式清空页面输出纯JSON
5. 网页模式保持原有逻辑

## 相关文件

- `js/app.js`: 主逻辑文件
