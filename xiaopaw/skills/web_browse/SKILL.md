---
name: web_browse
description: "浏览网页、提取网页内容、截图、填写表单、浏览器自动化。支持静态页面（快速 Markdown 转换）和动态 JavaScript 页面（完整浏览器）。适合：阅读文章、实时数据查询、网页截图、表单提交、任何需要浏览器的操作。"
license: Proprietary. LICENSE.txt has complete terms
---

# Web Browse Skill — 网页浏览与内容提取

## 概述

本 Skill 提供完整的网页浏览和自动化能力，支持两种模式：

| 模式 | 适用场景 | 主要工具 |
|------|---------|---------|
| **快速提取** | 静态页面、纯文本抓取、URL 转 Markdown | `sandbox_convert_to_markdown` |
| **浏览器全功能** | 动态页面、JavaScript 渲染、截图、表单填写、交互 | `browser_*` 系列工具 |

---

## 工具速查

### 快速模式：sandbox_convert_to_markdown

将任意 HTTP/HTTPS/file URL 直接转换为 Markdown，无需打开浏览器，速度最快。

```
用途：快速获取页面文字内容（不含截图）
参数：uri="https://example.com"
返回：页面 Markdown 文本
```

**适合场景**：新闻文章、文档页面、静态内容抓取

---

### 浏览器全功能模式：browser_* 系列

#### 导航
```
browser_navigate       url="https://example.com"        # 打开页面
browser_go_back                                          # 返回上一页
browser_go_forward                                       # 前进
browser_close                                            # 任务完成后关闭浏览器
```

#### 内容提取
```
browser_get_markdown                                     # 获取页面 Markdown（最推荐）
browser_get_text                                         # 获取页面纯文本
browser_read_links                                       # 获取页面所有链接
```

#### 截图
```
browser_screenshot     name="截图名"  fullPage=true      # 截取页面截图
browser_screenshot     selector="CSS选择器"               # 截取特定元素
```

#### 页面交互（先获取元素索引，再操作）
```
# 第一步：获取页面上所有可点击/输入/选择的元素
browser_get_clickable_elements

# 第二步：根据 index 操作
browser_click           index=3                           # 点击元素
browser_form_input_fill index=5  value="搜索内容"         # 填写表单
browser_select          index=2  value="选项值"           # 下拉选择
browser_hover           index=1                           # 悬停
browser_press_key       key="Enter"                       # 按键（Enter/Tab/Escape等）
```

#### 滚动与标签页
```
browser_scroll          amount=500                        # 向下滚动 500px
browser_scroll          amount=-500                       # 向上滚动
browser_tab_list                                          # 列出所有标签页
browser_new_tab         url="https://..."                 # 新标签页打开
browser_switch_tab      index=1                           # 切换标签页
browser_close_tab                                         # 关闭当前标签页
```

#### JavaScript 执行
```
browser_evaluate        script="() => document.title"    # 执行 JS，返回结果
```

---

## 标准操作流程

### 流程 1：快速内容提取（静态页面）

```
1. sandbox_convert_to_markdown(uri="目标URL")
2. 从返回的 Markdown 中提取所需信息
3. 整理结果写入输出
```

### 流程 2：搜索并获取结果

```
1. browser_navigate(url="https://www.google.com")
2. browser_get_clickable_elements()  → 找到搜索框 index
3. browser_form_input_fill(index=X, value="搜索词")
4. browser_press_key(key="Enter")
5. browser_get_markdown()  → 获取搜索结果页内容
6. browser_read_links()    → 获取结果链接列表
7. (可选) browser_navigate(url="感兴趣的结果链接")
8. browser_get_markdown()  → 获取详细页面内容
9. browser_close()
```

### 流程 3：网页截图

```
1. browser_navigate(url="目标URL")
2. browser_scroll(amount=0)  → 滚动到顶部确认页面加载
3. browser_screenshot(name="page", fullPage=true)
4. 截图文件保存在沙盒，可通过 sandbox_file_operations 读取（base64）后输出
5. browser_close()
```

### 流程 4：填写并提交表单

```
1. browser_navigate(url="表单页面URL")
2. browser_get_clickable_elements()  → 列出所有可交互元素
3. browser_form_input_fill(index=X, value="...")  → 填写各字段
4. browser_select(index=Y, value="选项")          → 填写下拉
5. browser_click(index=Z)                          → 点击提交按钮
6. browser_get_text()  → 确认提交结果
7. browser_close()
```

---

## 重要注意事项

1. **优先用 sandbox_convert_to_markdown**：对静态内容页面，它比打开浏览器快 3-5 倍，且不消耗浏览器资源
2. **browser_get_clickable_elements 只调用一次**：获取元素列表后根据 index 操作，不要重复调用
3. **任务完成必须调用 browser_close()**：释放浏览器资源
4. **截图文件位置**：`browser_screenshot` 产生的截图存储在沙盒内，名称为 `{name}.png`；可用 `sandbox_file_operations(action="read", path="路径", encoding="base64")` 读取
5. **遇到验证码/反爬**：先尝试 `sandbox_convert_to_markdown`；若被拦截，通知用户需要手动处理
6. **输出文件**：将提取的内容写入 `{session_dir}/outputs/` 目录（Markdown、JSON、截图等）

---

## 输出格式要求

任务结果必须包含以下字段：

```json
{
  "errcode": 0,
  "errmsg": "success",
  "url": "实际访问的URL",
  "content_type": "text|markdown|screenshot|links",
  "content": "提取的内容（文本/Markdown格式）",
  "links": ["可选，页面链接列表"],
  "output_files": ["可选，写入outputs/目录的文件路径"]
}
```

失败时：
```json
{
  "errcode": 1,
  "errmsg": "错误信息：具体原因。建议：尝试xxx方法"
}
```
